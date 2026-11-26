"""量體參數 → DesignBuilder 能匯入的 dsbXML、熱區清單(JSON)與各層平面圖(HTML)。用 tools/modeler 的 venv 執行:

    tools/modeler/.venv/Scripts/python.exe tools/designbuilder_mcp/massing.py 參數.json 輸出資料夾

只做常見量體: rect 矩形、L、U(開口朝北)、T(橫翼在北)、courtyard 回字、podium_tower 裙樓加塔樓。
每層切 4.5 m 周邊區加一個核心區(ASHRAE / DOE 原型建築的做法), 翼太窄時整層一區。
熱區名稱 "F03 North" / "F03 Core", 區塊(樓層)名稱 "F03"; 用途只寫進熱區清單, 由 MCP 匯入 DB 後再用 DB 範本庫套用。
座標: x 向東、y 向北, 原點在量體西南角(裙樓加塔樓時在裙樓西南角)。
"""
import html
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Ladybug Tools 會在 HOME / APPDATA 底下建資料夾, 指到 modeler 資料夾裡(同 build_idf.py)
os.environ["HOME"] = os.environ["APPDATA"] = str(HERE.parent / "modeler" / "home")

from ladybug_geometry.geometry3d import Face3D, Point3D, Vector3D  # noqa: E402
from dragonfly.building import Building  # noqa: E402
from dragonfly.model import Model  # noqa: E402
from dragonfly.windowparameter import SimpleWindowRatio  # noqa: E402
from honeybee_designbuilder.writer import model_to_dsbxml_file  # noqa: E402

SHAPES = {
    "rect": ("width", "depth"),
    "L": ("width", "depth", "notch_width", "notch_depth"),          # 東北角挖掉 notch
    "U": ("width", "depth", "notch_width", "notch_depth"),          # 北側中間挖掉 notch(開口朝北)
    "T": ("width", "depth", "stem_width", "bar_depth"),             # 北側橫翼 width × bar_depth, 南側直翼 stem_width 寬
    "courtyard": ("width", "depth", "court_width", "court_depth"),  # 中央天井
    "podium_tower": ("podium", "tower"),
}
DEFAULTS = {"stories": 1, "floor_height": 3.6, "wwr": 0.4, "perimeter_depth": 4.5, "uses": {}, "default_use": "辦公"}
TOWER_KEYS = ("width", "depth", "stories")


def _num(p, k, where=""):
    v = p.get(k)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
        raise ValueError(f"{where}{k} 要是大於 0 的數字, 收到 {v!r}")
    return float(v)


def _rect(x, y, w, d):
    return [(x, y), (x + w, y), (x + w, y + d), (x, y + d)]


def plates(p):
    """→ (每層的 [(外框點, [洞的點])], 每層樓高, 最窄翼寬)。"""
    shape = p.get("shape", "rect")
    if shape not in SHAPES:
        raise ValueError(f"shape 只支援 {', '.join(SHAPES)}, 收到 {shape!r}")
    missing = [k for k in SHAPES[shape] if k not in p]
    if missing:
        raise ValueError(f"{shape} 要給 {', '.join(SHAPES[shape])}; 缺少 {', '.join(missing)}")
    fh = _num(p, "floor_height")
    if shape == "podium_tower":
        pod, tow = p["podium"], p["tower"]
        for k in TOWER_KEYS:
            _num(pod, k, "podium."), _num(tow, k, "tower.")
        pw, pd, tw, td = pod["width"], pod["depth"], tow["width"], tow["depth"]
        tx = (pw - tw) / 2 + tow.get("offset_x", 0)
        ty = (pd - td) / 2 + tow.get("offset_y", 0)
        if tx < -1e-6 or ty < -1e-6 or tx + tw > pw + 1e-6 or ty + td > pd + 1e-6:
            raise ValueError("塔樓超出裙樓範圍; 調整 tower 的 width / depth / offset_x / offset_y")
        pfh, tfh = pod.get("floor_height", fh), tow.get("floor_height", fh)
        floors = [[(_rect(0, 0, pw, pd), [])]] * int(pod["stories"]) + [[(_rect(tx, ty, tw, td), [])]] * int(tow["stories"])
        heights = [pfh] * int(pod["stories"]) + [tfh] * int(tow["stories"])
        return floors, heights, min(pw, pd, tw, td)
    w, d = _num(p, "width"), _num(p, "depth")
    stories = p.get("stories", 1)
    if isinstance(stories, bool) or not isinstance(stories, int) or stories < 1:
        raise ValueError(f"stories 要是 1 以上的整數, 收到 {stories!r}")
    holes = []
    if shape == "rect":
        pts, narrow = _rect(0, 0, w, d), min(w, d)
    elif shape == "L":
        nw, nd = _num(p, "notch_width"), _num(p, "notch_depth")
        if not (nw < w and nd < d):
            raise ValueError("L 形的 notch_width / notch_depth 要小於 width / depth")
        pts, narrow = [(0, 0), (w, 0), (w, d - nd), (w - nw, d - nd), (w - nw, d), (0, d)], min(w - nw, d - nd)
    elif shape == "U":
        nw, nd = _num(p, "notch_width"), _num(p, "notch_depth")
        if not (nw < w and nd < d):
            raise ValueError("U 形的 notch_width / notch_depth 要小於 width / depth")
        x0 = (w - nw) / 2
        pts = [(0, 0), (w, 0), (w, d), (x0 + nw, d), (x0 + nw, d - nd), (x0, d - nd), (x0, d), (0, d)]
        narrow = min(x0, d - nd)
    elif shape == "T":
        sw, bd = _num(p, "stem_width"), _num(p, "bar_depth")
        if not (sw < w and bd < d):
            raise ValueError("T 形的 stem_width 要小於 width、bar_depth 要小於 depth")
        x0 = (w - sw) / 2
        pts = [(x0, 0), (x0 + sw, 0), (x0 + sw, d - bd), (w, d - bd), (w, d), (0, d), (0, d - bd), (x0, d - bd)]
        narrow = min(sw, bd)
    else:  # courtyard
        cw, cd = _num(p, "court_width"), _num(p, "court_depth")
        if not (cw < w and cd < d):
            raise ValueError("天井的 court_width / court_depth 要小於 width / depth")
        pts = _rect(0, 0, w, d)
        holes = [_rect((w - cw) / 2, (d - cd) / 2, cw, cd)]
        narrow = min((w - cw) / 2, (d - cd) / 2)
    return [[(pts, holes)]] * stories, [fh] * stories, narrow


def floor_label(n):
    return f"F{n:02d}"


def floor_use(uses, default, n):
    """uses: {"1": "大廳", "2-5": "辦公", "6-": "會議"}; 沒寫到的樓層用 default。"""
    for key, use in uses.items():
        m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d*)\s*)?", str(key))
        if not m:
            raise ValueError(f"uses 的樓層寫法看不懂: {key!r}(例如 \"1\"、\"2-5\"、\"6-\")")
        lo = int(m[1])
        hi = lo if "-" not in str(key) else (int(m[2]) if m[2] else 10 ** 6)
        if lo <= n <= hi:
            return use
    return default


def _direction(room):
    """周邊區的朝向: 最長的室外牆面朝哪個方向; 沒有室外牆的是核心區。"""
    best, best_len = None, 0
    for seg, bc, ori in zip(room.floor_segments, room.boundary_conditions, room.segment_orientations()):
        if bc.__class__.__name__ == "Outdoors" and seg.length > best_len:
            best, best_len = ori, seg.length
    if best is None:
        return "Core"
    return ("North", "East", "South", "West")[int(((best + 45) % 360) // 90)]


def build(p):
    p = {**DEFAULTS, **p}
    name = str(p.get("name", ""))
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", name):
        raise ValueError(f"name 只能用英文、數字、底線、減號(會變成檔名), 收到 {name!r}")
    if isinstance(p["wwr"], bool) or not isinstance(p["wwr"], (int, float)) or not 0 <= p["wwr"] < 1:
        raise ValueError(f"wwr(窗牆比)要在 0 到 1 之間, 例如 0.4, 收到 {p['wwr']!r}")
    floors, heights, narrow = plates(p)
    depth = float(p["perimeter_depth"])
    # 翼寬要容得下兩側周邊區再加至少 3 m 核心, 不然切出很窄的核心區; 這時整層一區
    offset = depth if narrow >= 2 * depth + 3 else 0
    geo, z = [], 0.0
    for plate, h in zip(floors, heights):
        geo.append([Face3D([Point3D(x, y, z) for x, y in pts],
                           holes=[[Point3D(x, y, z) for x, y in hole] for hole in holes] or None)
                    for pts, holes in plate])
        z += h
    merged = Building.from_all_story_geometry(name, geo, heights, perimeter_offset=offset, tolerance=0.01)
    # 相同的樓層會被合併成一層加倍數; 逐層展開, 每層都是真的熱區(DB 沒有倍數, 人也要逐層精修)。
    # dragonfly 的 separate_mid_floors 不會全部拆開(實測 4 層的塔樓剩一層 ×2), 所以自己複製
    stories = []
    for s in sorted(merged.unique_stories, key=lambda s: s.floor_height):
        m, s.multiplier = s.multiplier, 1
        stories.append(s)
        for i in range(1, m):
            c = s.duplicate()
            c.identifier = f"{s.identifier}_{i}"
            for r in c.room_2ds:
                r.identifier = f"{r.identifier}_{i}"
            c.move(Vector3D(0, 0, s.floor_to_floor_height * i))
            stories.append(c)
    bldg = Building(name, stories)
    stories = sorted(bldg.unique_stories, key=lambda s: s.floor_height)
    if len(stories) != len(heights) or any(s.multiplier != 1 for s in stories):
        raise ValueError(f"內部錯誤: 拆出 {len(stories)} 層, 應為 {len(heights)} 層")
    stories[0].set_ground_contact(True)
    for lower, upper in zip(stories, stories[1:]):
        upper.set_ground_contact(False)
        lower.set_top_exposed_by_story_above(upper)
    stories[-1].set_top_exposed(True)
    bldg.set_outdoor_window_parameters(SimpleWindowRatio(p["wwr"]))

    zones = []
    for n, story in enumerate(stories, start=1):
        story.display_name = floor_label(n)
        use = floor_use(p["uses"], p["default_use"], n)
        dirs = [_direction(r) if offset else "Zone" for r in story.room_2ds]
        count = {}
        for room, d in zip(story.room_2ds, dirs):
            count[d] = count.get(d, 0) + 1
            # 同一層同朝向有好幾區(U、回字)時每個都編號
            room.display_name = f"{floor_label(n)} {d}" + (f" {count[d]}" if dirs.count(d) > 1 else "")
            zones.append({"name": room.display_name, "floor": n, "block": floor_label(n), "direction": d,
                          "use": use, "area_m2": round(room.floor_area, 1),
                          # 標籤放在區內最寬處(U 形核心區的頂點平均點會落在別區)
                          "label_at": [round(c, 2) for c in tuple(room.floor_geometry.pole_of_inaccessibility(0.05))[:2]],
                          "polygon": [[round(pt.x, 3), round(pt.y, 3)] for pt in room.floor_geometry.boundary],
                          "holes": [[[round(pt.x, 3), round(pt.y, 3)] for pt in h]
                                    for h in (room.floor_geometry.holes or [])]})
    hb = Model(name, [bldg], units="Meters", tolerance=0.01, angle_tolerance=1.0).to_honeybee(
        "Building", use_multiplier=False, solve_ceiling_adjacencies=True, tolerance=0.01)[0]
    block_of = {z_["name"]: z_["block"] for z_ in zones}
    for room in hb.rooms:
        room.story = block_of[room.display_name]  # DB 區塊 = 樓層, 名稱 F01, F02 ...
    return p, hb, zones, offset


def plan_html(p, zones, offset):
    """各層平面圖(SVG)與熱區表, 讓人一眼看出量體、分區與用途。"""
    pts = [pt for z_ in zones for pt in z_["polygon"]]
    minx, miny = min(x for x, _ in pts), min(y for _, y in pts)
    maxx, maxy = max(x for x, _ in pts), max(y for _, y in pts)
    scale = min(560 / max(maxx - minx, 1), 380 / max(maxy - miny, 1))

    def xy(x, y):
        return f"{(x - minx) * scale + 20:.1f},{(maxy - y) * scale + 20:.1f}"

    palette = {}
    colors = ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2", "#edc948", "#b07aa1", "#9c755f"]
    for z_ in zones:
        palette.setdefault(z_["use"], colors[len(palette) % len(colors)])
    floors = sorted({z_["floor"] for z_ in zones})
    # 相同平面與用途的樓層只畫一次
    groups, key_of = [], {}
    for f in floors:
        fz = [z_ for z_ in zones if z_["floor"] == f]
        key = json.dumps([(z_["polygon"], z_["use"], z_["name"][4:]) for z_ in fz])
        if key in key_of:
            groups[key_of[key]][0].append(f)
        else:
            key_of[key] = len(groups)
            groups.append(([f], fz))
    w, h = (maxx - minx) * scale + 40, (maxy - miny) * scale + 40
    parts = []
    for fl, fz in groups:
        label = floor_label(fl[0]) if len(fl) == 1 else f"{floor_label(fl[0])}–{floor_label(fl[-1])}"
        svg = [f'<svg viewBox="0 0 {w:.0f} {h:.0f}" width="{w:.0f}" height="{h:.0f}">']
        for z_ in fz:
            # 帶洞的區(回字形的核心區)用 even-odd 路徑, 洞裡的其他區才不會被蓋掉
            d = " ".join("M " + " L ".join(xy(x, y) for x, y in ring) + " Z" for ring in [z_["polygon"]] + z_["holes"])
            svg.append(f'<path d="{d}" fill-rule="evenodd" fill="{palette[z_["use"]]}" fill-opacity="0.35" '
                       f'stroke="#333" stroke-width="1"/>')
            cx_, cy_ = xy(*z_["label_at"]).split(",")
            svg.append(f'<text x="{cx_}" y="{cy_}" text-anchor="middle" font-size="11">{html.escape(z_["name"][4:])}</text>')
            svg.append(f'<text x="{cx_}" y="{float(cy_) + 13:.1f}" text-anchor="middle" font-size="10" '
                       f'fill="#555">{z_["area_m2"]:.0f} m²</text>')
        svg.append(f'<text x="{w - 30:.0f}" y="18" font-size="12">N ↑</text></svg>')
        uses = sorted({z_["use"] for z_ in fz})
        parts.append(f"<h2>{label}(用途:{'、'.join(uses)})</h2>" + "".join(svg))
    rows = "".join(f"<tr><td>{html.escape(z_['name'])}</td><td>{z_['use']}</td><td>{z_['direction']}</td>"
                   f"<td style='text-align:right'>{z_['area_m2']:.1f}</td></tr>" for z_ in zones)
    legend = " ".join(f'<span style="background:{c};opacity:.6;padding:2px 8px;margin-right:6px">{html.escape(u)}</span>'
                      for u, c in palette.items())
    total = sum(z_["area_m2"] for z_ in zones)
    note = (f"每層切 {offset:g} m 周邊區加核心區" if offset else
            f"翼寬不足以切周邊區({p['perimeter_depth']:g} m × 2 + 3 m 核心),每層一區")
    return f"""<!doctype html><meta charset="utf-8"><title>{html.escape(p['name'])} 分區圖</title>
<style>body{{font-family:system-ui,sans-serif;margin:16px;color:#222}}table{{border-collapse:collapse}}
td,th{{border:1px solid #ccc;padding:3px 8px}}h2{{font-size:15px;margin:18px 0 4px}}</style>
<h1 style="font-size:18px">{html.escape(p['name'])}:{p['shape']},{len(floors)} 層,{len(zones)} 個熱區,{total:,.0f} m²</h1>
<p>{note};窗牆比 {p['wwr']:.0%}。x 向東、y 向北。{legend}</p>{''.join(parts)}
<h2>熱區表</h2><table><tr><th>熱區</th><th>用途</th><th>朝向</th><th>面積 m²</th></tr>{rows}</table>"""


def main():
    params, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    try:
        p, hb, zones, offset = build(json.loads(params.read_text(encoding="utf-8")))
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        raise SystemExit(f"參數錯誤: {e}")
    out_dir.mkdir(parents=True, exist_ok=True)
    xml = out_dir / f"{p['name']}.xml"
    model_to_dsbxml_file(hb, str(xml))
    plans = out_dir / f"{p['name']}_分區圖.html"
    plans.write_text(plan_html(p, zones, offset), encoding="utf-8")
    info = {"xml": str(xml), "plans": str(plans), "zones": zones, "perimeter_offset": offset,
            "floors": max(z_["floor"] for z_ in zones), "floor_m2": round(sum(z_["area_m2"] for z_ in zones), 1)}
    (out_dir / f"{p['name']}_zones.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in info.items() if k != "zones"} | {"zone_count": len(zones)}, ensure_ascii=False))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
