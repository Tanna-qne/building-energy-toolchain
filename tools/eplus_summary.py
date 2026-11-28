"""搬到 tools/designbuilder_mcp/eplus_summary.py(designbuilder-mcp 倉庫); 這裡轉接給原本的呼叫方式:

    python tools/eplus_summary.py <eplustbl.htm 或其所在資料夾>
    from eplus_summary import summarize, to_markdown
"""
import importlib.util
import sys
from pathlib import Path

_src = Path(__file__).resolve().parent / "designbuilder_mcp" / "eplus_summary.py"
_spec = importlib.util.spec_from_file_location("_dbmcp_eplus_summary", _src)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
globals().update({k: v for k, v in vars(_mod).items() if not k.startswith("__")})

if __name__ == "__main__":
    sys.argv[0] = str(_src)
    _mod.main()
