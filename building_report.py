"""把 scan2 的結果寫成一頁給非專業人士看的網頁: 先講結論, 再講每個措施值不值得做。

由 scan2.write_outputs() 呼叫; 也可以單獨跑: python building_report.py <results.csv> <輸出.html>
"""
import csv
import html
import sys
from pathlib import Path

MEASURES = {  # 結果裡的代號 -> 白話名稱與一句話說明
    "lights_-30%": ("換省電燈具", "把照明用電減少 30%,例如換成 LED。"),
    "film_shgc0.30": ("外窗貼隔熱膜", "在玻璃外側貼膜,擋掉一部分太陽熱;夏天冷氣省,冬天暖氣可能多用一點。"),
    "lights+film": ("燈具和貼膜一起做", "上面兩項同時做的效果。"),
    "infil_x0.5": ("加強門窗氣密", "把從縫隙漏進來的外氣減半,例如換氣密條、修門窗。"),
    "coolroof": ("屋頂塗反射漆", "在屋頂塗淺色反射塗料(冷屋頂),少吸太陽熱;夏天頂樓比較不熱,冬天暖氣可能多用一點。"),
    "equip_-20%": ("換節能設備", "電腦、影印機等插座用電減少 20%,例如汰換時選節能標章產品。"),
    "insulation": ("加強牆與屋頂隔熱", "外牆和屋頂加厚保溫(換成較新、較嚴格的建築節能規範),冬天少漏熱、夏天少進熱。"),
    "hvac_eff": ("換高效率空調", "同一種空調系統,換成符合最新能效標準(ASHRAE 2019)的設備,冷氣與風扇更省電。"),
}
GOOD_YEARS, OK_YEARS = 8, 15  # 回本年數的判讀門檻
TAG_BEST = ' <b class="tag">✓ 最省</b>'
GAS_UP_WHY = {  # 省電卻多用天然氣時的白話原因
    "lights_-30%": "(燈具發的熱變少,冬天要靠暖氣補回來)",
    "film_shgc0.30": "(冬天照進來的太陽熱也被擋掉,暖氣要多開)",
    "lights+film": "(燈具發的熱和照進來的太陽熱都變少,冬天要靠暖氣補回來)",
    "coolroof": "(屋頂冬天也少吸太陽熱,暖氣要多開)",
    "equip_-20%": "(設備發的熱變少,冬天要靠暖氣補回來)",
}
BUILDING_NAMES = {
    "SmallOffice": "小型辦公", "MediumOffice": "中型辦公", "LargeOffice": "大型辦公", "Retail": "零售賣場",
    "StripMall": "街邊商店", "SuperMarket": "超市", "PrimarySchool": "國小", "SecondarySchool": "中學",
    "College": "大學", "SmallHotel": "小型旅館", "LargeHotel": "大型旅館", "Hospital": "醫院",
    "Outpatient": "診所", "MidriseApartment": "中層公寓", "HighriseApartment": "高層公寓",
    "Warehouse": "倉庫", "QuickServiceRestaurant": "速食餐廳", "FullServiceRestaurant": "餐廳",
    "Laboratory": "實驗室", "Courthouse": "法院",
}
HVAC_NAMES = {"VAV": "中央空調(VAV)", "PSZAC": "箱型冷氣(每區一台)", "PTAC": "窗型 / 分離式冷氣"}
HEAT_NAMES = {"Boiler": "鍋爐熱水", "GasCoil": "瓦斯加熱", "GasHeaters": "瓦斯暖氣", "ElectricBaseboard": "電暖器",
              "ElectricCoil": "電熱", "ASHP": "熱泵", "DHW": "熱水", "PFP": "電熱", "BoilerBaseboard": "鍋爐暖氣片"}


def hvac_name(code):
    """VAV_Chiller_Boiler -> 中央空調(VAV),暖氣用鍋爐熱水。對不上就原樣顯示。"""
    parts = str(code).split("_")
    name = HVAC_NAMES.get(parts[0])
    if not name:
        return str(code)
    heat = next((HEAT_NAMES[p] for p in reversed(parts[1:]) if p in HEAT_NAMES), None)
    return f"{name},暖氣用{heat}" if heat else name


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def verdict(r):
    """(等級, 圖示, 標籤, 一句話)。等級對應 CSS 的狀態色。"""
    if str(r["success"]) != "True":
        return "skip", "–", "這次沒做", r.get("error") or "模擬沒有成功"
    saved, years = num(r["saving_cost"]), num(r["payback_years"])
    if saved is not None and saved <= 0:
        return "bad", "✕", "反而多花錢", "做了之後每年的電費加氣費比原本還高。"
    if years is None:
        return "info", "i", "只算了省多少", "沒有這項的施工成本資料,所以算不出幾年回本。"
    if years <= GOOD_YEARS:
        return "good", "✓", "值得考慮", f"大約 {years:.0f} 年就能把投資省回來。"
    if years <= OK_YEARS:
        return "ok", "!", "看情況", f"要 {years:.0f} 年左右才回本,看預算和設備壽命決定。"
    return "bad", "✕", "不太划算", f"要 {years:.0f} 年才回本,通常比設備壽命還長。"


SYMBOL = "US$"  # write_html() 依 costs.json 設定


def money(v):
    return f"{SYMBOL} {v:,.0f}"


def chart(cards):
    """每個措施一年省多少錢; 省錢藍色往右、多花錢紅色往左。"""
    return bars([(c["title"], c["saved"]) for c in cards if c["saved"] is not None],
                "每個措施一年省下的錢", signed=True)


def bars(data, aria, signed=False):
    """橫條圖。用 HTML 而不是 SVG: 窄螢幕時 SVG 會整張縮小, 字跟著變得看不清楚。
    signed: 值有正負(省 / 多花), 標籤加 +/−; 否則只是金額大小。"""
    if not data:
        return ""
    lo, hi = min(0, *(v for _, v in data)), max(0, *(v for _, v in data))
    span = (hi - lo) or 1
    zero = -lo / span * 100  # 0 在長條區的位置(%)
    out = [f'<div class="chart" role="img" aria-label="{html.escape(aria)}">']
    for name, v in data:
        left, width = (zero, v / span * 100) if v >= 0 else (zero + v / span * 100, -v / span * 100)
        if signed:
            tip = f"每年{'省' if v >= 0 else '多花'} {money(abs(v))}"
            label = f"{'+' if v >= 0 else '−'}{money(abs(v))}"
        else:
            tip = label = money(v)
        out.append(f'<div class="row" title="{html.escape(name)}:{tip}">'
                   f'<span class="lbl">{html.escape(name)}</span>'
                   f'<span class="track"><span class="zero" style="left:{zero:.1f}%"></span>'
                   f'<span class="bar {"pos" if v >= 0 else "neg"}" style="left:{left:.1f}%;width:{max(width, 0.5):.1f}%"></span></span>'
                   f'<span class="val">{label}</span></div>')
    out.append("</div>")
    return "\n".join(out)


def model_section(name, rows, costs, meta):
    base = next((r for r in rows if r["case"] == "baseline"), None)
    if not base or str(base["success"]) != "True":
        return f"<section id=\"{html.escape(name)}\"><h2>{html.escape(name)}</h2><p>基準模擬沒有成功,無法比較:{html.escape(base.get('error', '') if base else '')}</p></section>"
    elec, gas, floor = num(base["elec_kwh"]) or 0, num(base["gas_kwh"]) or 0, num(base["floor_m2"])
    bill = elec * costs["price_per_kwh"] + gas * costs["gas_per_kwh"]

    cards = []
    for r in rows:
        if r["case"] not in MEASURES:
            continue
        title, what = MEASURES[r["case"]]
        level, icon, label, why = verdict(r)
        saved, capex, years = num(r["saving_cost"]), num(r["capex_cost"]), num(r["payback_years"])
        s_elec, s_gas = num(r["saving_elec_kwh"]) or 0, num(r["saving_gas_kwh"]) or 0
        at = []  # 電價上漲時的回本年數(用相對倍數, 換貨幣也適用)
        for k in (1.3, 1.8):
            t = s_elec * costs["price_per_kwh"] * k + s_gas * costs["gas_per_kwh"]
            if capex and t > 0:
                at.append(f"電價漲 {k - 1:.0%} 時約 {capex / t:.0f} 年")
        cards.append({"case": r["case"], "title": title, "what": what, "level": level, "icon": icon, "label": label, "why": why,
                      "saved": saved if level != "skip" else None, "capex": capex, "years": years,
                      "pct": num(r["saving_pct"]), "s_elec": s_elec, "s_gas": s_gas, "at": at,
                      "note": r.get("error", "") if level != "skip" else ""})

    ranked = [c for c in cards if c["level"] in ("good", "ok") and c["years"]]
    best = min(ranked, key=lambda c: c["years"]) if ranked else None
    if best:
        headline = (f"最划算的是「{best['title']}」:每年省 {money(best['saved'])},"
                    f"投資約 {money(best['capex'])},大約 {best['years']:.0f} 年回本。")
    else:
        headline = f"以目前的電價與成本假設,沒有措施能在 {OK_YEARS} 年內回本。"
        paying = [c for c in cards if c["years"] and c["saved"] and c["saved"] > 0]
        if paying:
            near = min(paying, key=lambda c: c["years"])
            headline += f"最接近的是「{near['title']}」,約 {near['years']:.0f} 年;電價上漲時會縮短(見下方各項)。"

    desc = ""
    if meta:
        shape = "L 形" if meta.get("shape") == "L" else "長方形"
        btype = str(meta.get("building_type", ""))
        desc = (f'{meta.get("stories", 1)} 層、{shape} {meta.get("width")} × {meta.get("depth")} 公尺、'
                f'{html.escape(BUILDING_NAMES.get(btype, btype))}、{html.escape(hvac_name(meta.get("hvac", "")))}。')

    parts = [f'<section id="{html.escape(name)}"><h2>{html.escape(name)}</h2>',
             f'<p class="desc">{desc}樓地板 {floor:,.0f} m²。</p>' if floor else "",
             # 基準的備註目前只有理想負載的換算說明; 這會影響所有數字, 所以放在結論上面
             f'<p class="desc">注意:{html.escape(base["error"])}(設備效率是假設值)。</p>' if base.get("error") else "",
             f'<div class="headline">{html.escape(headline)}</div>',
             "<h3>這棟建築一年用多少能源</h3>",
             '<div class="tiles">',
             f'<div class="tile"><span class="big">{elec:,.0f}</span> 度電<small>約 {money(elec * costs["price_per_kwh"])}</small></div>',
             f'<div class="tile"><span class="big">{gas:,.0f}</span> 度天然氣<small>約 {money(gas * costs["gas_per_kwh"])}(主要用在暖氣)</small></div>',
             f'<div class="tile"><span class="big">{money(bill)}</span> 一年能源費用</div>',
             f'<div class="tile"><span class="big">{(elec + gas) / floor:,.0f}</span> 度 / m²·年'
             f'<small>每平方公尺一年用多少能源(EUI),越低越省</small></div>' if floor else "",
             "</div>",
             "<h3>每個節能措施一年省多少錢</h3>", chart(cards),
             "<h3>逐項說明</h3>", '<div class="cards">']
    for c in cards:
        facts = []
        if c["level"] != "skip":
            facts.append(f"總能源少用 {c['pct']:.1f}%" if c["pct"] is not None and c["pct"] >= 0
                         else f"總能源多用 {abs(c['pct'] or 0):.1f}%")
            fact = (f"{'少' if c['s_elec'] >= 0 else '多'}用電 {abs(c['s_elec']):,.0f} 度、"
                    f"{'少' if c['s_gas'] >= 0 else '多'}用天然氣 {abs(c['s_gas']):,.0f} 度")
            if c["s_elec"] >= 0 and c["s_gas"] < 0:
                fact += GAS_UP_WHY.get(c["case"], "(室內的熱變少,冬天要靠暖氣補回來)")
            facts.append(fact)
            if c["capex"]:
                facts.append(f"投資約 {money(c['capex'])}")
            facts += c["at"]
        parts.append(f'<div class="card"><div class="top"><b>{html.escape(c["title"])}</b>'
                     f'<span class="badge {c["level"]}"><i>{c["icon"]}</i>{c["label"]}</span></div>'
                     f'<p>{html.escape(c["why"])}</p>'
                     f'<p class="muted">{html.escape(c["what"])}</p>'
                     + (f'<ul>{"".join(f"<li>{html.escape(f)}</li>" for f in facts)}</ul>' if facts else "")
                     + (f'<p class="muted">{html.escape(c["note"])}</p>' if c["note"] else "")
                     + "</div>")
    parts.append("</div></section>")
    return "\n".join(p for p in parts if p)


def compare_section(models, costs):
    """兩個以上方案時放在最前面: 一句話說哪個最省, 費用長條圖, 對照表。"""
    items = []
    for name, rs in models.items():
        base = next((r for r in rs if r["case"] == "baseline" and str(r["success"]) == "True"), None)
        if not base:
            continue
        elec, gas, floor = num(base["elec_kwh"]) or 0, num(base["gas_kwh"]) or 0, num(base["floor_m2"])
        bill = elec * costs["price_per_kwh"] + gas * costs["gas_per_kwh"]
        good = []
        for r in rs:
            if r["case"] in MEASURES and verdict(r)[0] in ("good", "ok") and num(r["payback_years"]):
                good.append((num(r["payback_years"]), MEASURES[r["case"]][0]))
        best = min(good) if good else None
        items.append({"name": name, "bill": bill, "floor": floor, "per_m2": bill / floor if floor else None,
                      "eui": (elec + gas) / floor if floor else None,
                      "best": f"{best[1]}(約 {best[0]:.0f} 年回本)" if best else "沒有 15 年內回本的措施"})
    if len(items) < 2:
        return ""

    same_size = len({round(i["floor"] or 0) for i in items}) == 1
    key = "bill" if same_size else "per_m2"  # 面積不同時用每 m² 費用比才公平
    lo = min(items, key=lambda i: i[key] or float("inf"))
    hi = max(items, key=lambda i: i[key] or 0)
    if same_size:
        headline = (f"一年能源費用最低的是「{lo['name']}」:{money(lo['bill'])},"
                    f"比最高的「{hi['name']}」每年少 {money(hi['bill'] - lo['bill'])}"
                    f"(少 {1 - lo['bill'] / hi['bill']:.0%})。")
    else:
        headline = (f"各方案面積不同,用每平方公尺的能源費用比較:最低的是「{lo['name']}」"
                    f"({SYMBOL} {lo['per_m2']:,.2f}/m²·年),最高的是「{hi['name']}」({SYMBOL} {hi['per_m2']:,.2f}/m²·年)。")

    low_eui = min(items, key=lambda i: i["eui"] or float("inf"))
    if low_eui is not lo:  # 總能源最少不等於最省錢, 一般人會被這兩個方向相反的數字搞混
        headline += (f"注意:總能源用得最少的其實是「{low_eui['name']}」(EUI {low_eui['eui']:,.0f}),"
                     f"但它用電的比例較高,電比天然氣貴,所以費用反而不是最低。")

    rows_html = "".join(
        f'<tr><td class="m"><a href="#{html.escape(i["name"])}">{html.escape(i["name"])}</a>'
        f'{TAG_BEST if i is lo else ""}</td>'
        f'<td>{i["floor"]:,.0f}</td><td>{money(i["bill"])}</td>'
        f'<td>{SYMBOL} {i["per_m2"]:,.2f}</td><td>{i["eui"]:,.0f}</td><td class="m">{html.escape(i["best"])}</td></tr>'
        for i in items)
    return f"""<section><h2>方案比較</h2>
<div class="headline">{html.escape(headline)}</div>
<h3>每個方案一年的能源費用</h3>
{bars([(i["name"], i["bill"]) for i in items], "每個方案一年的能源費用")}
<h3>對照表</h3>
<div class="scroll"><table class="cmp"><thead><tr><th class="m">方案</th><th>樓地板 m²</th><th>一年能源費用</th>
<th>每 m² 費用</th><th>EUI 度/m²·年</th><th class="m">最划算的措施</th></tr></thead><tbody>{rows_html}</tbody></table></div>
<p class="muted">點方案名稱可以跳到該方案的詳細說明。</p></section>"""


def write_html(rows, out_path: Path, costs, meta=None):
    """rows: scan2 的結果列(可含多個模型)。meta: {模型名: 建模參數} 讓報告寫出建築描述。"""
    global SYMBOL
    SYMBOL = costs.get("symbol", costs["currency"])
    sym = SYMBOL + " "
    meta = meta or {}
    models = {}
    for r in rows:
        models.setdefault(r["model"], []).append({k: ("" if v is None else v) for k, v in r.items()})
    sections = compare_section(models, costs) + "\n".join(
        model_section(n, rs, costs, meta.get(n)) for n, rs in models.items())
    doc = f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>節能評估報告</title>
<style>
:root{{--bg:#f6f6f4;--surface:#fcfcfb;--line:#e4e3df;--ink:#0b0b0b;--ink2:#52514e;--muted:#6f6e69;
 --pos:#2a78d6;--neg:#e34948;--good:#0ca30c;--warn:#fab219;--bad:#d03b3b;--info:#2a78d6;--skip:#8a8984}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#121211;--surface:#1a1a19;--line:#33332f;
 --ink:#f0efec;--ink2:#c3c2b7;--muted:#9d9c95;--pos:#3987e5;--neg:#e66767;--info:#3987e5}}}}
:root[data-theme="dark"]{{--bg:#121211;--surface:#1a1a19;--line:#33332f;--ink:#f0efec;--ink2:#c3c2b7;--muted:#9d9c95;
 --pos:#3987e5;--neg:#e66767;--info:#3987e5}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.7 system-ui,-apple-system,"Segoe UI","Microsoft JhengHei",sans-serif}}
main{{max-width:880px;margin:0 auto;padding:28px 16px 64px}}
h1{{font-size:24px;margin:0 0 4px}} h2{{font-size:20px;margin:36px 0 6px}} h3{{font-size:16px;margin:26px 0 10px;color:var(--ink2)}}
.desc,.muted,small{{color:var(--muted)}}
.headline{{background:var(--surface);border:1px solid var(--line);border-left:4px solid var(--pos);border-radius:10px;padding:14px 16px;font-size:17px;font-weight:600}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}}
.tile{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 14px;color:var(--ink2)}}
.tile .big{{display:block;font-size:22px;font-weight:700;color:var(--ink)}} .tile small{{display:block;font-size:12px}}
.chart{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:8px 14px}}
.chart .row{{display:grid;grid-template-columns:minmax(6em,9em) 1fr auto;align-items:center;gap:10px;padding:7px 0;border-radius:6px}}
.chart .row:hover{{background:var(--bg)}} .chart .lbl{{color:var(--ink2);font-size:14px}}
.chart .val{{color:var(--ink2);font-size:14px;font-variant-numeric:tabular-nums;min-width:8.5em;text-align:right}}
.chart .row:hover .val{{color:var(--ink)}}
.chart .track{{position:relative;height:18px}} .chart .zero{{position:absolute;top:-6px;bottom:-6px;width:1px;background:var(--line)}}
.chart .bar{{position:absolute;top:0;height:100%;border-radius:4px}} .chart .pos{{background:var(--pos)}} .chart .neg{{background:var(--neg)}}
.cards{{display:grid;gap:10px}}
.card{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 16px}}
.card .top{{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}}
.card p{{margin:6px 0}} .card ul{{margin:6px 0 0;padding-left:20px;color:var(--ink2)}}
.badge{{display:inline-flex;align-items:center;gap:6px;font-size:13px;font-weight:600;color:var(--ink);
 border:1px solid var(--line);border-radius:999px;padding:2px 10px}}
.badge i{{font-style:normal;display:inline-grid;place-items:center;width:18px;height:18px;border-radius:50%;color:#fff;font-size:12px}}
.badge.good i{{background:var(--good)}} .badge.ok i{{background:var(--warn);color:#0b0b0b}} .badge.bad i{{background:var(--bad)}}
.badge.info i{{background:var(--info)}} .badge.skip i{{background:var(--skip)}}
.notes{{color:var(--ink2);font-size:14px}} .notes li{{margin:4px 0}}
.scroll{{overflow-x:auto}} table.cmp{{border-collapse:collapse;width:100%;background:var(--surface);font-size:14px}}
table.cmp th,table.cmp td{{border:1px solid var(--line);padding:6px 10px;text-align:right;white-space:nowrap}}
table.cmp th{{color:var(--ink2);font-weight:600}} table.cmp .m{{text-align:left}} table.cmp a{{color:var(--pos)}}
.tag{{font-size:12px;border:1px solid var(--good);color:var(--ink);border-radius:999px;padding:0 8px;margin-left:6px}}
</style></head><body><main>
<h1>節能評估報告</h1>
<p class="muted">EnergyPlus 模擬一整年的結果。先看每棟建築最上面那句結論,再看各項措施的說明。</p>
{sections}
<h2>這些數字怎麼來的</h2>
<ul class="notes">
<li>用電腦模擬這棟建築一整年每小時的用電與用氣(EnergyPlus),再分別套用每個措施重算一次,比較差多少。</li>
<li>價格假設({costs["currency"]}):電 {sym}{costs["price_per_kwh"]:g}/度、天然氣 {sym}{costs["gas_per_kwh"]:g}/度;
 貼膜 {sym}{costs["film_per_m2_glass"]:,}/m² 玻璃、換燈具 {sym}{costs["lighting_per_m2_floor"]:,}/m² 樓地板、
 屋頂反射漆 {sym}{costs["coolroof_per_m2_roof"]:,}/m² 屋頂、加強隔熱 {sym}{costs["insulation_per_m2_envelope"]:,}/m² 外牆加屋頂、
 換空調 {sym}{costs["hvac_per_m2_floor"]:,}/m² 樓地板。都是估計值,不是報價,改 costs.json 就會換。</li>
<li>「換高效率空調」的投資是整套更換的費用,所以回本很久。實務上多半是設備到了汰換年限才換,
 這時只需要算「高效率款比一般款貴多少」,回本會快很多。
 氣密與節能設備沒有成本資料,只算省多少。</li>
<li>回本年數 = 投資 ÷ 每年省的錢。沒有算利息、維修與設備壽命。
 判讀:{GOOD_YEARS} 年內「值得考慮」、{GOOD_YEARS}–{OK_YEARS} 年「看情況」、超過 {OK_YEARS} 年「不太划算」。</li>
<li>氣象資料目前用美國芝加哥(冬天冷、要開暖氣),換成台灣城市結果會不一樣。</li>
<li>「度」就是 kWh;天然氣也換算成 kWh,方便和電放在一起比較。</li>
</ul>
</main></body></html>
"""
    out_path.write_text(doc, encoding="utf-8")
    return out_path


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here))
    from scan2.scan2 import COSTS
    with open(sys.argv[1], encoding="utf-8-sig") as f:
        print(write_html(list(csv.DictReader(f)), Path(sys.argv[2]), COSTS))
