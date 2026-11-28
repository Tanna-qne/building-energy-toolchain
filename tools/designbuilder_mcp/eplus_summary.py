"""把 EnergyPlus 的 eplustbl.htm 整理成看得懂的摘要: 總用電、單位面積能耗、分項、峰值、溫度沒達標的時數。

    python tools/eplus_summary.py <eplustbl.htm 或其所在資料夾>      # 印出摘要, 並寫一份 summary.md 在旁邊

只用標準函式庫, 公制、英制報表都換成 kWh、kW、m²。給 MCP 工具與報告共用: summarize() 回傳 dict, to_markdown() 轉文字。
"""
import html
import re
import sys
from pathlib import Path

TO_KWH = {"GJ": 277.7778, "MJ": 0.277778, "kWh": 1.0, "MWh": 1000.0, "kBtu": 0.293071, "MBtu": 293.071, "J": 1 / 3.6e6}
TO_KW = {"W": 0.001, "kW": 1.0, "kBtuh": 0.293071, "Btu/h": 0.000293071}
TO_M2 = {"m2": 1.0, "ft2": 0.09290304}
END_USE_ZH = {
    "Heating": "供暖", "Cooling": "製冷", "Interior Lighting": "室內照明", "Exterior Lighting": "室外照明",
    "Interior Equipment": "室內設備(插座等)", "Exterior Equipment": "室外設備", "Fans": "風機", "Pumps": "水泵",
    "Heat Rejection": "冷卻塔散熱", "Humidification": "加濕", "Heat Recovery": "熱回收", "Water Systems": "熱水",
    "Refrigeration": "冷藏冷凍", "Generators": "發電機",
}
FUEL_ZH = {"Electricity": "電", "Natural Gas": "天然氣", "District Cooling": "區域供冷", "District Heating": "區域供熱",
           "District Heating Water": "區域供熱", "District Heating Steam": "區域蒸汽", "Propane": "丙烷",
           "Fuel Oil No 1": "燃油", "Fuel Oil No 2": "燃油", "Diesel": "柴油", "Gasoline": "汽油", "Coal": "煤",
           "Other Fuel 1": "其他燃料", "Other Fuel 2": "其他燃料"}
UNMET_LIMIT_H = 300  # ASHRAE 90.1 附錄 G 對「未滿足時數」的上限, 只作參考


def tables(text):
    """{'報告_對象_表名': [[儲存格...], ...]}; 依 EnergyPlus 寫在每張表前的 <!-- FullName:... --> 註解。"""
    out = {}
    for m in re.finditer(r"<!-- FullName:(.*?)-->\s*<table.*?>(.*?)</table>", text, re.S):
        rows = []
        for tr in re.findall(r"<tr>(.*?)</tr>", m.group(2), re.S):
            rows.append([html.unescape(re.sub(r"<.*?>", "", c)).strip() for c in re.findall(r"<td.*?>(.*?)</td>", tr, re.S)])
        out[m.group(1).strip()] = rows
    return out


def _num(s):
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def _unit(header):
    m = re.search(r"\[(.*?)\]", header)
    return m.group(1) if m else ""


def _row(rows, label):
    return next((r for r in rows if r and r[0] == label), None)


def summarize(htm):
    htm = Path(htm)
    if htm.is_dir():
        htm = htm / "eplustbl.htm"
    text = htm.read_text(encoding="latin-1")
    t = tables(text)
    s = {"source": str(htm)}
    m = re.search(r"<b>[^<]*\*\*\s*([^<]+)</b>", text)  # 執行期間那行: "名稱 (01-01:31-12) ** 氣象站"
    if m:
        s["weather"] = m.group(1).strip()

    site = t.get("Annual Building Utility Performance Summary_Entire Facility_Site and Source Energy", [])
    if site:
        u = _unit(site[0][1])
        r = _row(site, "Total Site Energy")
        if r:
            s["site_kWh"] = _num(r[1]) * TO_KWH.get(u, 1)
    area = t.get("Annual Building Utility Performance Summary_Entire Facility_Building Area", [])
    if area:
        k = TO_M2.get(_unit(area[0][1]), 1)
        for label, key in (("Total Building Area", "floor_m2"), ("Net Conditioned Building Area", "conditioned_m2")):
            r = _row(area, label)
            if r:
                s[key] = _num(r[1]) * k

    eu = t.get("Annual Building Utility Performance Summary_Entire Facility_End Uses", [])
    if eu:
        head = eu[0]
        fuels = [(i, h.split(" [")[0], TO_KWH.get(_unit(h))) for i, h in enumerate(head) if i and TO_KWH.get(_unit(h))]
        uses, by_fuel = [], {}
        for r in eu[1:]:
            if not r or r[0] in ("", "Total End Uses"):
                continue
            parts = {f: _num(r[i]) * k for i, f, k in fuels if _num(r[i])}
            total = sum(parts.values())
            if total > 0:
                uses.append({"end_use": r[0], "name": END_USE_ZH.get(r[0], r[0]), "kWh": total, "by_fuel": parts})
                for f, v in parts.items():
                    by_fuel[f] = by_fuel.get(f, 0) + v
        uses.sort(key=lambda x: -x["kWh"])
        s["end_uses"], s["by_fuel_kWh"] = uses, by_fuel
        s.setdefault("site_kWh", sum(by_fuel.values()))

    dem = t.get("Demand End Use Components Summary_Entire Facility_End Uses", [])
    if dem:
        head = dem[0]
        i = next((i for i, h in enumerate(head) if h.startswith("Electricity")), None)
        tot, when = _row(dem, "Total End Uses"), _row(dem, "Time of Peak")
        if i is not None and tot and _num(tot[i]):
            s["peak_elec_kW"] = _num(tot[i]) * TO_KW.get(_unit(head[i]), 1)
            if when:
                s["peak_elec_time"] = when[i]

    unmet = t.get("Annual Building Utility Performance Summary_Entire Facility_Comfort and Setpoint Not Met Summary", [])
    for label, key in (("Time Setpoint Not Met During Occupied Heating", "unmet_heating_h"),
                       ("Time Setpoint Not Met During Occupied Cooling", "unmet_cooling_h")):
        r = _row(unmet, label)
        if r:
            s[key] = _num(r[1])
    if s.get("site_kWh") and s.get("floor_m2"):
        s["eui_kWh_m2"] = s["site_kWh"] / s["floor_m2"]
    return s


def _peak_time(x):
    """'02-JAN-08:00' → '1/2 08:00'。"""
    m = re.match(r"(\d+)-([A-Z]{3})-(\d+:\d+)", x or "")
    months = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()
    return f"{months.index(m.group(2)) + 1}/{int(m.group(1))} {m.group(3)}" if m and m.group(2) in months else x


def to_markdown(s, title="模擬結果摘要"):
    lines = [f"## {title}", ""]
    site = s.get("site_kWh")
    if site is None:
        return f"## {title}\n\n讀不到全年能耗表(eplustbl.htm 可能不完整):{s['source']}\n"
    head = f"全年用能 **{site:,.0f} kWh**"
    if s.get("eui_kWh_m2"):
        head += f",樓地板 {s['floor_m2']:,.0f} m²,單位面積 **{s['eui_kWh_m2']:.0f} kWh/m²·年**"
    lines.append(head + "。")
    uses = s.get("end_uses", [])
    if uses:
        top = uses[0]
        lines.append(f"最大的一項是**{top['name']}**,佔 {100 * top['kWh'] / site:.0f}%。")
    fuels = s.get("by_fuel_kWh", {})
    if len(fuels) > 1:
        lines.append("按能源分:" + "、".join(f"{FUEL_ZH.get(f, f)} {v:,.0f} kWh({100 * v / site:.0f}%)"
                                         for f, v in sorted(fuels.items(), key=lambda x: -x[1])) + "。")
    if any(f.startswith("District") for f in fuels):
        lines.append("> 模型裡有「區域供冷 / 供熱」:這代表空調用的是理想負載(DesignBuilder 的 Simple HVAC 常見),"
                     "數字是建築需要的冷熱量,不是空調設備實際耗的電。")
    if s.get("peak_elec_kW"):
        lines.append(f"用電峰值 **{s['peak_elec_kW']:,.1f} kW**,出現在 {_peak_time(s.get('peak_elec_time'))}。")
    uh, uc = s.get("unmet_heating_h"), s.get("unmet_cooling_h")
    if uh is not None or uc is not None:
        worst = max(uh or 0, uc or 0)
        verdict = "空調容量足夠" if worst <= UNMET_LIMIT_H else "空調容量可能不足,或設定溫度達不到"
        lines.append(f"有人時溫度沒達到設定的時數:供暖 {uh or 0:.0f} 小時、製冷 {uc or 0:.0f} 小時"
                     f"({verdict};ASHRAE 90.1 附錄 G 以 {UNMET_LIMIT_H} 小時為上限)。")
    if s.get("weather"):
        lines.append(f"氣象檔:{s['weather']}。")
    if uses:
        lines += ["", "| 分項 | kWh/年 | 佔比 | 能源 |", "|---|---:|---:|---|"]
        for u in uses:
            fu = "、".join(FUEL_ZH.get(f, f) for f in u["by_fuel"])
            lines.append(f"| {u['name']} | {u['kWh']:,.0f} | {100 * u['kWh'] / site:.1f}% | {fu} |")
    return "\n".join(lines) + "\n"


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    s = summarize(sys.argv[1])
    md = to_markdown(s)
    out = Path(s["source"]).with_name("summary.md")
    out.write_text(md, encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(md)
    print(f"(已寫入 {out})")


if __name__ == "__main__":
    main()
