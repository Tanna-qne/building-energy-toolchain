"""參數 JSON -> DesignBuilder 原生模型(dsbXML)。用 tools/modeler 的 venv 執行:

    tools/modeler/.venv/Scripts/python.exe tools/designbuilder_mcp/params_to_dsbxml.py 參數.json 輸出.xml

建模沿用 build_idf.py(dragonfly + honeybee-energy), 轉檔用 Ladybug Tools 官方的 honeybee-designbuilder。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modeler"))
import build_idf  # noqa: E402  (會設好 HOME / APPDATA)
from honeybee_designbuilder.writer import model_to_dsbxml_file  # noqa: E402


def main():
    params, out = Path(sys.argv[1]), Path(sys.argv[2])
    try:
        p = {**build_idf.DEFAULTS, **json.loads(params.read_text(encoding="utf-8"))}
        build_idf.validate(p)
        _, hb_model, _ = build_idf.build(p)
    except (ValueError, json.JSONDecodeError) as e:
        raise SystemExit(f"參數錯誤: {e}")
    out.parent.mkdir(parents=True, exist_ok=True)
    model_to_dsbxml_file(hb_model, str(out))
    floor = sum(r.floor_area * r.multiplier for r in hb_model.rooms)
    print(json.dumps({"xml": str(out), "zones": len(hb_model.rooms), "floor_m2": round(floor, 1)}))


if __name__ == "__main__":
    main()
