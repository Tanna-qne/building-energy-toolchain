"""DesignBuilder MCP over HTTP (streamable HTTP), for Dify and other MCP clients that cannot start a local process.

    <base Python 3.12> tools/designbuilder_mcp/http_server.py [--port 8765] [--public-host my-tunnel.example.com]

Security (Dify Cloud reaches this computer through a tunnel, so this endpoint is on the internet):
- listens only on 127.0.0.1; the tunnel forwards to it, and nothing else on the network can connect;
- every request needs the header "Authorization: Bearer <token>". The token comes from DBMCP_TOKEN, or is
  generated once and kept in db_models/dbmcp/_http_token.txt (gitignored);
- file paths are limited to db_models/dbmcp/ plus the folders in DBMCP_ALLOWED_DIRS (see server._guard);
- db_open (opens a window on this desktop) is not offered over HTTP.
Long DesignBuilder runs go through db_job_start / db_job_status.
"""
import argparse
import asyncio
import hmac
import os
import secrets
import sys

os.environ["DBMCP_HTTP"] = "1"  # before importing server: turns on the path limits

import server  # noqa: E402  registers the tools
import dblibrary  # noqa: E402
import workflow  # noqa: E402
import uvicorn  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402

TOKEN_FILE = server.WORK / "_http_token.txt"
HTTP_EXCLUDED = ("db_open",)


def load_token():
    token = os.environ.get("DBMCP_TOKEN", "").strip()
    if token:
        return token
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="ascii").strip()
    token = secrets.token_urlsafe(32)
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token, encoding="ascii")
    return token


class BearerToken:
    """ASGI middleware: refuse every HTTP request without the right bearer token (constant-time compare)."""

    def __init__(self, app, token):
        self.app, self.token = app, token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            auth = dict(scope["headers"]).get(b"authorization", b"")
            given = auth[7:].strip() if auth[:7].lower() == b"bearer " else b""
            if not hmac.compare_digest(given, self.token):
                await JSONResponse({"error": "missing or wrong bearer token"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


# ---- REST endpoints for Dify's HTTP request nodes (a form workflow cannot call MCP tools in a loop easily) ----

MAX_WAIT_S = 45  # long poll; stays below Dify's HTTP node read timeout and tunnel timeouts


def _message(e):
    return str(e).strip("'\"")


async def start_workflow(request):
    """POST /api/workflow, body = the form (all values strings). Checked first, so mistakes come back at once."""
    try:
        form = await request.json()
        if not isinstance(form, dict):
            raise ValueError("body 要是 JSON 物件")
        workflow.form_to_params(form)
    except (ValueError, KeyError) as e:
        return JSONResponse({"ok": False, "error": _message(e)}, status_code=400)
    job = server.db_job_start("db_workflow", {"form": form})
    return JSONResponse({"ok": True, **job})


async def job_status(request):
    """GET /api/jobs/{job_id}?wait=45: waits up to `wait` seconds for the job to finish before answering."""
    job_id = request.path_params["job_id"]
    wait = min(float(request.query_params.get("wait", MAX_WAIT_S)), MAX_WAIT_S)
    deadline = asyncio.get_running_loop().time() + wait
    while True:
        try:
            job = server.db_job_status(job_id)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": _message(e)}, status_code=404)
        if job["status"] in ("done", "failed") or asyncio.get_running_loop().time() >= deadline:
            job["finished"] = job["status"] in ("done", "failed")
            return JSONResponse(job)
        await asyncio.sleep(2)


async def options(request):
    """GET /api/options: the choices the form can offer (all from the local DesignBuilder library)."""
    # Many nearby stations share one weather file; list only the station the file was recorded at (same WMO)
    local = []
    for p in sorted(dblibrary.WEATHER_DIR.glob("*.epw")):
        for r in dblibrary.table("LocationTemplates").values():
            w = dblibrary.table("HourlyWeather").get(r.get("HourlyWeatherData", ""), {})
            if w.get("Filename", "").lower() == p.name.lower() and w.get("WMO") and w.get("WMO") == r.get("WMO"):
                local.append({"id": r["Id"], "name": r["LocationName"], "climate_zone": r.get("ClimaticRegion", "")})
    return JSONResponse({"uses": list(dblibrary.SPACE_TYPES), "building_types": list(dblibrary.BUILDING_TYPES),
                         "shapes": list(workflow.SHAPE_FIELDS) + ["podium_tower"], "locations_with_weather": local})


def build_app(token, public_hosts=()):
    for name in HTTP_EXCLUDED:
        server.mcp.remove_tool(name)
    # FastMCP only accepts localhost Host headers (DNS rebinding protection); add the tunnel's host name
    sec = server.mcp.settings.transport_security
    for host in public_hosts:
        sec.allowed_hosts += [host, f"{host}:*"]
        sec.allowed_origins += [f"https://{host}"]
    app = server.mcp.streamable_http_app()
    app.router.routes += [Route("/api/workflow", start_workflow, methods=["POST"]),
                          Route("/api/jobs/{job_id}", job_status, methods=["GET"]),
                          Route("/api/options", options, methods=["GET"])]
    return BearerToken(app, token)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--public-host", action="append", default=[],
                    help="host name of the tunnel in front of this server (repeatable)")
    args = ap.parse_args()
    token = load_token()
    app = build_app(token, args.public_host)
    print(f"DesignBuilder MCP: http://127.0.0.1:{args.port}{server.mcp.settings.streamable_http_path}")
    print(f"token: {'DBMCP_TOKEN' if os.environ.get('DBMCP_TOKEN') else TOKEN_FILE}")
    print(f"files allowed under: {'; '.join(str(d) for d in server.ALLOWED_DIRS)}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
