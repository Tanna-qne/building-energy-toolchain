"""讀 EnergyPlus 的 eplusout.eso / eplusout.mtr, 依選單取出逐時 / 逐日 / 逐月的結果, 整理成表格。

只讀模擬已經輸出的資料; 模型沒要求輸出的變數或頻率會列為「沒有」, 不推算、不補值。
ESO 格式: 「End of Data Dictionary」之前每行 "編號,欄數,鍵,名稱 [單位] !頻率", meter(.mtr)沒有鍵;
之後是資料: "1,環境名稱,..." 開始一個模擬期間, "2,..." 逐時 / 逐時步的時間, "3" 逐日, "4" 逐月, "5" 整個期間,
其他行是 "編號,值[,最小值,...]"。
"""
import re
from pathlib import Path

STAMP_OF = {"TimeStep": "2", "Hourly": "2", "Daily": "3", "Monthly": "4", "RunPeriod": "5", "Annual": "6"}
FREQS = {"hourly": "Hourly", "daily": "Daily", "monthly": "Monthly"}

# 選單: 中文項目 → (種類, EnergyPlus 名稱候選, 顯示單位)。meter 在 .mtr、變數在 .eso。
# 熱區類的鍵是熱區代號(或 "代號 IDEAL LOADS AIR"), 由 db_simulate 存的 zones.json 對回熱區名稱
ITEMS = {
    "總用電": ("meter", ("Electricity:Facility",), "kWh"),
    "照明用電": ("meter", ("InteriorLights:Electricity",), "kWh"),
    "設備用電": ("meter", ("InteriorEquipment:Electricity",), "kWh"),
    "供暖量": ("meter", ("DistrictHeating:Facility", "DistrictHeatingWater:Facility"), "kWh"),
    "供冷量": ("meter", ("DistrictCooling:Facility",), "kWh"),
    "天然氣": ("meter", ("NaturalGas:Facility",), "kWh"),
    "室外溫度": ("site", ("Site Outdoor Air Drybulb Temperature",), "°C"),
    "室溫": ("zone", ("Zone Mean Air Temperature",), "°C"),
    "操作溫度": ("zone", ("Zone Operative Temperature",), "°C"),
    "相對濕度": ("zone", ("Zone Air Relative Humidity",), "%"),
    "供暖負荷": ("zone", ("Zone Ideal Loads Supply Air Total Heating Rate",), "kW"),
    "供冷負荷": ("zone", ("Zone Ideal Loads Supply Air Total Cooling Rate",), "kW"),
}
SCALE = {("J", "kWh"): 1 / 3.6e6, ("W", "kW"): 1 / 1000}


def dictionary(path: Path):
    """{編號: (鍵, 名稱, 單位, 頻率)}。"""
    out = {}
    with open(path, encoding="latin-1") as f:
        for line in f:
            if line.startswith("End of Data Dictionary"):
                break
            if "!" not in line or not line[0].isdigit():
                continue
            head, tail = line.split("!", 1)
            parts = [p.strip() for p in head.split(",")]
            if parts[0] in STAMP_OF.values() or parts[0] == "1":
                continue
            key, name_unit = (parts[2], parts[3]) if len(parts) >= 4 else ("", parts[2])
            m = re.match(r"(.*?)\s*\[(.*)\]\s*$", name_unit)
            name, unit = (m[1], m[2]) if m else (name_unit, "")
            # DB 會在頻率後面加 ",ON"(例如 "!Hourly,ON")
            out[parts[0]] = (key, name, unit, tail.split()[0].split(",")[0])
    return out


def series(path: Path, ids):
    """讀資料段: {編號: [(時間標記, 值)]}。只取資料最多的那個模擬期間(年度模擬, 不是設計日)。"""
    ids = set(ids)
    envs, cur = [], None
    with open(path, encoding="latin-1") as f:
        for line in f:
            if line.startswith("End of Data Dictionary"):
                break
        for line in f:
            if line.startswith("End of Data"):
                break
            code, _, rest = line.partition(",")
            if code == "1":
                cur = {}
                envs.append((rest.split(",")[0].strip(), cur))
            elif code in ids and cur is not None:
                cur.setdefault(code, []).append(float(rest.split(",")[0]))
    if not envs:
        return {}, None, []
    title, best = max(envs, key=lambda e: sum(len(v) for v in e[1].values()))
    return best, title, [t for t, _ in envs]


def stamps(path: Path, code, env_title):
    """某個模擬期間的時間標記序列(逐時 / 逐日 / 逐月)。"""
    out, inside = [], False
    with open(path, encoding="latin-1") as f:
        for line in f:
            if line.startswith("End of Data Dictionary"):
                break
        for line in f:
            if line.startswith("End of Data"):
                break
            c, _, rest = line.partition(",")
            if c == "1":
                inside = rest.split(",")[0].strip() == env_title
            elif inside and c == code:
                v = [x.strip() for x in rest.split(",")]
                # 逐時步輸出時同一小時有多個時間行; 只留整點(EndMinute = 60)
                if code == "2" and float(v[6]) < 60:
                    continue
                if code == "2":
                    out.append((int(v[1]), int(v[2]), int(v[4])))  # 月, 日, 時
                elif code == "3":
                    out.append((int(v[1]), int(v[2])))             # 月, 日
                else:
                    out.append((int(v[1]),))                       # 月
    return out


def _match(zone, block, selectors):
    if not selectors or "*" in selectors:
        return True
    s = {x.strip().lower() for x in selectors}
    return zone.lower() in s or block.lower() in s


def extract(sim_dir: Path, items, freq="hourly", zones=None, zone_map=None):
    """→ (欄名, 時間欄名, 列, 沒有的項目 [(項目, 有的頻率)], 核對 [(項目, 逐時加總, 全年值)], 模擬期間名稱)。
    zones: 熱區名稱或區塊名稱清單(只影響熱區類項目); zone_map: {熱區代號: {block, zone, idf}}。"""
    if freq not in FREQS:
        raise ValueError(f"freq 只能是 {', '.join(FREQS)}")
    bad = [i for i in items if i not in ITEMS]
    if bad:
        raise ValueError(f"不認得的項目:{'、'.join(bad)};可用的有:{'、'.join(ITEMS)}")
    want, zone_map = FREQS[freq], zone_map or {}
    files = {"eso": sim_dir / "eplusout.eso", "mtr": sim_dir / "eplusout.mtr"}
    dicts = {k: dictionary(p) if p.exists() else {} for k, p in files.items()}
    cols, missing = [], []
    order = {h: n for n, h in enumerate(zone_map)}
    for item in items:
        kind, names, unit = ITEMS[item]
        src = "mtr" if kind == "meter" else "eso"
        lower = {n.lower() for n in names}
        found = [(i, *v) for i, v in dicts[src].items() if v[1].lower() in lower]
        at = [x for x in found if x[4] == want]
        if not at:
            missing.append((item, sorted({x[4] for x in found})))
            continue
        picked = []
        for i, key, name, u, fr in at:
            label, rank = f"{item} ({unit})", 0
            if kind == "zone":
                handle = key.split()[0]
                z = zone_map.get(handle)
                zname, block = (z["zone"], z["block"]) if z else (key, "")
                if not _match(zname, block, zones):
                    continue
                label, rank = f"{zname} {item} ({unit})", order.get(handle, len(order))
            picked.append((rank, label, src, i, SCALE.get((u, unit), 1.0)))
        if kind == "zone" and not picked:
            missing.append((item, ["沒有符合的熱區"]))
        cols += [c[1:] for c in sorted(picked)]
    if not cols:
        return [], [], [], missing, [], None
    data, title = {}, None
    for src in ("eso", "mtr"):
        ids = [c[2] for c in cols if c[1] == src]
        if ids:
            got, title, _ = series(files[src], ids)
            data.update({(src, k): v for k, v in got.items()})
    first = cols[0]
    times = stamps(files[first[1]], STAMP_OF[want], title)
    n = len(times)
    rows = [list(t) + [round(data.get((c[1], c[2]), [None] * n)[r] * c[3], 4)
                       if r < len(data.get((c[1], c[2]), [])) else None for c in cols] for r, t in enumerate(times)]
    time_cols = {"hourly": ["月", "日", "時(1–24,該小時結束)"], "daily": ["月", "日"], "monthly": ["月"]}[freq]
    # meter 的逐時(逐日、逐月)加總應等於全年值; 不一致代表少讀或讀錯
    checks = []
    for c in cols:
        if c[1] != "mtr":
            continue
        name = dicts["mtr"][c[2]][1]
        rp_id = next((i for i, v in dicts["mtr"].items() if v[1] == name and v[3] == "RunPeriod"), None)
        if rp_id:
            total = series(files["mtr"], [rp_id])[0].get(rp_id, [None])[0]
            checks.append((c[0], sum(x for x in data[("mtr", c[2])]) * c[3], total * c[3] if total is not None else None))
    return [c[0] for c in cols], time_cols, rows, missing, checks, title
