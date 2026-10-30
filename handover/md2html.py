"""HANDOVER.md -> 單檔 HTML(只處理本文件用到的語法)。"""
import html
import sys
from pathlib import Path


def inline(text):
    text = html.escape(text)
    while "**" in text:
        text = text.replace("**", "<strong>", 1)
        if "**" in text:
            text = text.replace("**", "</strong>", 1)
    parts = text.split("`")
    out = []
    for i, p in enumerate(parts):
        out.append(f"<code>{p}</code>" if i % 2 else p)
    return "".join(out)


def convert(lines):
    out = []
    in_code = False
    in_table = False
    in_list = False
    first_row = False
    for raw in lines:
        line = raw.rstrip()
        if line.startswith("```"):
            if not in_code:
                if in_table:
                    out.append("</table>")
                    in_table = False
                if in_list:
                    out.append("</ul>")
                    in_list = False
            out.append("</pre>" if in_code else "<pre>")
            in_code = not in_code
            continue
        if in_code:
            out.append(html.escape(line))
            continue
        if line.startswith("|"):
            if in_list:
                out.append("</ul>")
                in_list = False
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= set("-: ") and c for c in cells):
                continue
            if not in_table:
                out.append("<table>")
                in_table = True
                first_row = True
            cell_tag = "th" if first_row else "td"
            first_row = False
            cells_html = "".join("<" + cell_tag + ">" + inline(c) + "</" + cell_tag + ">" for c in cells)
            out.append("<tr>" + cells_html + "</tr>")
            continue
        if in_table:
            out.append("</table>")
            in_table = False
        stripped = line.strip()
        if in_list and not stripped[:2] in ("- ", "* "):
            out.append("</ul>")
            in_list = False
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            level = min(level, 4)
            tag = "h" + str(level)
            out.append("<" + tag + ">" + inline(stripped[level:].strip()) + "</" + tag + ">")
        elif stripped == "---":
            out.append("<hr>")
        elif stripped[:2] in ("- ", "* "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append("<li>" + inline(stripped[2:]) + "</li>")
        elif stripped:
            out.append("<p>" + inline(stripped) + "</p>")
    if in_table:
        out.append("</table>")
    if in_list:
        out.append("</ul>")
    if in_code:
        out.append("</pre>")
    return chr(10).join(out)


def main():
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    body = convert(src.read_text(encoding="utf-8").splitlines())
    style = (
        "@page{size:A4;margin:16mm 14mm}"
        "body{font-family:Microsoft JhengHei,system-ui,sans-serif;font-size:10.5pt;line-height:1.6;color:#111}"
        "h1{font-size:20pt;border-bottom:2px solid #111;padding-bottom:6px}"
        "h2{font-size:14pt;margin-top:20px;border-bottom:1px solid #ddd;padding-bottom:3px}"
        "h3{font-size:12pt;margin-top:14px}"
        "code{background:#f3f4f6;padding:1px 4px;border-radius:3px;font-family:Consolas,monospace;font-size:9pt}"
        "pre{background:#f8fafc;border:1px solid #e5e7eb;padding:8px 10px;white-space:pre-wrap;font-family:Consolas,monospace;font-size:8.5pt;line-height:1.45}"
        "table{border-collapse:collapse;width:100%;margin:8px 0;font-size:9pt}"
        "th,td{border:1px solid #d1d5db;padding:4px 6px;text-align:left;vertical-align:top}"
        "th{background:#f3f4f6}ul{margin:6px 0 6px 18px;padding:0}hr{border:0;border-top:1px solid #ddd;margin:14px 0}"
    )
    doc = "<!doctype html><html lang=zh-Hant><head><meta charset=utf-8><style>" + style + "</style></head><body>" + body + "</body></html>"
    dst.write_text(doc, encoding="utf-8")
    print(str(dst) + " " + str(len(doc)) + " bytes")


if __name__ == "__main__":
    main()
