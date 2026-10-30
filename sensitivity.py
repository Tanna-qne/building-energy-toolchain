"""電價與成本敏感度: 用既有模擬結果算回收期, 產生可托管的靜態 HTML。

省電量(kWh)不變, 只有電價與成本變動 -> 不需要重跑模擬。
"""
import csv
import html
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from scan2.scan2 import COSTS  # noqa: E402

SRC = HERE / "scan2/results.csv"
OUT = HERE / "report"
PRICES = [2, 3, 4.5, 6, 8, 10, 12]
CAPEX_MULTS = [0.5, 0.75, 1.0, 1.5, 2.0]
BASE_PRICE = COSTS["price_twd_per_kwh"]
GAS_PRICE = COSTS["gas_twd_per_kwh"]  # 敏感度只變電價


def load():
    """回傳 (措施清單, 模型數, 成功的模擬次數)。"""
    with open(SRC, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    n_models = len({r["model"] for r in rows})
    n_runs = sum(1 for r in rows if r["success"] == "True")
    cases = []
    for r in rows:
        if r["case"] == "baseline" or not r["saving_elec_kwh"]:
            continue
        try:
            s_elec = float(r["saving_elec_kwh"])
            s_gas = float(r["saving_gas_kwh"] or 0)
            capex = float(r["capex_twd"] or 0)
        except ValueError:
            continue
        case = {"model": r["model"], "case": r["case"], "saving": s_elec, "saving_gas": s_gas,
                "capex": capex, "pct": float(r["saving_pct"] or 0)}
        if capex <= 0 or annual_twd(case, BASE_PRICE) <= 0:
            continue
        cases.append(case)
    return cases, n_models, n_runs


def annual_twd(case, price):
    return case["saving"] * price + case["saving_gas"] * GAS_PRICE


def payback(case, price, mult=1.0):
    """年省金額 <= 0 (電價低到抵不過多燒的天然氣) 時回收期為無限大。"""
    twd = annual_twd(case, price)
    return case["capex"] * mult / twd if twd > 0 else float("inf")


def cell(v):
    tone = "#dcfce7" if v <= 5 else "#fef9c3" if v <= 10 else "#fee2e2"
    return f'<td style="background:{tone}">{v:.1f}</td>' if v != float("inf") else f'<td style="background:{tone}">—</td>'


def median(values):
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def svg_chart(cases, width=820, height=400):
    """回收期 vs 電價: 每種措施一條線(跨模型取中位數), 細節看下面的表。"""
    pad_l, pad_r, pad_t, pad_b = 56, 150, 28, 48
    p_min, p_max = min(PRICES), max(PRICES)
    measures = sorted({c["case"] for c in cases})
    series = {m: [median([payback(c, p) for c in cases if c["case"] == m]) for p in PRICES]
              for m in measures}
    vals = [v for s in series.values() for v in s]
    y_max = min(max(vals), 60)
    def x(p):
        return pad_l + (p - p_min) / (p_max - p_min) * (width - pad_l - pad_r)
    def y(v):
        v = min(v, y_max)
        return height - pad_b - v / y_max * (height - pad_t - pad_b)

    colors = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c"]
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="回收期對電價">']
    for i in range(5):
        v = y_max * i / 4
        yy = y(v)
        parts.append(f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{width-pad_r}" y2="{yy:.1f}" stroke="#e5e7eb"/>')
        parts.append(f'<text x="{pad_l-8}" y="{yy+4:.1f}" font-size="11" text-anchor="end" fill="#6b7280">{v:.0f}</text>')
    parts.append(f'<text x="14" y="{pad_t+8}" font-size="12" fill="#374151">年</text>')
    for p in PRICES:
        parts.append(f'<text x="{x(p):.1f}" y="{height-pad_b+18}" font-size="11" text-anchor="middle" fill="#6b7280">{p:g}</text>')
    parts.append(f'<text x="{(pad_l+width-pad_r)/2:.0f}" y="{height-6}" font-size="12" text-anchor="middle" fill="#374151">電價 TWD/kWh</text>')
    for i, m in enumerate(measures):
        color = colors[i % len(colors)]
        pts = " ".join(f"{x(p):.1f},{y(v):.1f}" for p, v in zip(PRICES, series[m]))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2.5"/>')
        ly = pad_t + 6 + i * 20
        parts.append(f'<text x="{width-pad_r+10}" y="{ly}" font-size="11" fill="{color}">'
                     f'{html.escape(m)}</text>')
    parts.append(f'<text x="{width-pad_r+10}" y="{pad_t+6+len(measures)*20}" font-size="10" fill="#6b7280">'
                 f'(跨模型中位數)</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def table_html(cases):
    head = "".join(f"<th>{p:g}</th>" for p in PRICES)
    rows = []
    for c in cases:
        cells = []
        for p in PRICES:
            cells.append(cell(payback(c, p)))
        rows.append(f'<tr><td class="m">{html.escape(c["model"])}</td><td class="mid">{html.escape(c["case"])}</td>'
                    f'<td>{c["pct"]:.1f}%</td><td>{c["saving"]:.0f}</td><td>{c["saving_gas"]:.0f}</td><td>{c["capex"]:.0f}</td>'
                    + "".join(cells) + "</tr>")
    return f"""<table>
<thead><tr><th class="mid">模型</th><th class="mid">措施</th><th>省能源 %</th><th>年省電 kWh</th><th>年省氣 kWh</th><th>投資 TWD</th>{head}</tr></thead>
<tbody>{''.join(rows)}</tbody></table>"""


def capex_table(cases):
    """以基準電價, 看成本變動對回收期的影響。"""
    head = "".join(f"<th>×{m:g}</th>" for m in CAPEX_MULTS)
    rows = []
    for c in cases:
        cells = []
        for m in CAPEX_MULTS:
            cells.append(cell(payback(c, BASE_PRICE, m)))
        rows.append(f'<tr><td class="m">{html.escape(c["model"])}</td><td class="mid">{html.escape(c["case"])}</td>'
                    f'<td>{c["capex"]:.0f}</td>' + "".join(cells) + "</tr>")
    return f"""<table>
<thead><tr><th class="mid">模型</th><th class="mid">措施</th><th>基準投資 TWD</th>{head}</tr></thead>
<tbody>{''.join(rows)}</tbody></table>"""


def main():
    cases, n_models, n_runs = load()
    if not cases:
        raise SystemExit("scan2/results.csv 沒有可用資料")
    OUT.mkdir(exist_ok=True)

    best = min(cases, key=lambda c: payback(c, BASE_PRICE))
    html_doc = f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>節能措施回收期與敏感度</title>
<style>
 body{{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;background:#f8fafc;color:#0f172a}}
 main{{max-width:960px;margin:0 auto;padding:32px 20px 64px}}
 h1{{font-size:26px;margin:0 0 4px}} .sub{{color:#64748b;margin:0 0 28px;font-size:14px}}
 h2{{font-size:18px;margin:32px 0 10px}}
 table{{border-collapse:collapse;width:100%;font-size:13px;background:#fff}}
 th,td{{border:1px solid #e2e8f0;padding:6px 8px;text-align:right;white-space:nowrap}}
 th{{background:#f1f5f9;font-weight:600}} td.m,td.mid,th.mid{{text-align:left}}
 .card{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:16px}}
 .note{{font-size:13px;color:#64748b;line-height:1.7}}
 .kpi{{display:flex;gap:16px;flex-wrap:wrap;margin:16px 0 8px}}
 .kpi div{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:12px 16px;min-width:150px}}
 .kpi b{{display:block;font-size:20px}}
</style></head><body><main>
<h1>節能措施回收期與敏感度</h1>
<p class="sub">資料來源:EnergyPlus 模擬({n_models} 個模型、{n_runs} 次執行)+ 假設成本。省電與省氣量固定,只有電價與成本變動(天然氣固定 {GAS_PRICE:g} TWD/kWh)。</p>

<div class="kpi">
  <div><b>{BASE_PRICE:g}</b>TWD/kWh 基準電價</div>
  <div><b>{len(cases)}</b>個措施組合</div>
  <div><b>{payback(best, BASE_PRICE):.1f} 年</b>最短回收期({html.escape(best["model"])} · {html.escape(best["case"])})</div>
</div>

<h2>回收期對電價</h2>
<div class="card">{svg_chart(cases)}</div>
<p class="note">綠 ≤5 年、黃 ≤10 年、紅 &gt;10 年(下表)。線圖縱軸上限 60 年,超過的線會貼在頂端。</p>

<h2>電價敏感度(年)</h2>
{table_html(cases)}

<h2>成本敏感度(基準電價 {BASE_PRICE:g} TWD/kWh)</h2>
{capex_table(cases)}

<h2>怎麼讀</h2>
<p class="note">
回收期 = 投資 ÷ (年省電 kWh × 電價 + 年省氣 kWh × 氣價)。照明與貼膜通常會讓暖房多燒天然氣(年省氣為負),
所以電價翻倍時回收期砍得比一半更多;「—」表示年省金額 ≤ 0,回收不了。
成本倍數那張表則是線性:成本 ×1.5,回收期也 ×1.5。<br>
決策上只要看兩件事:這條線在你們電價下落在幾軸,以及成本報價樂觀時會漂到哪裡。
</p>
<p class="note">限制:只有電費與天然氣費、沒有碳排與維護;沒有折現率;成本為假設值,非當地報價。</p>
</main></body></html>
"""
    (OUT / "index.html").write_text(html_doc, encoding="utf-8")

    with open(OUT / "sensitivity.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["model", "case", "saving_elec_kwh", "saving_gas_kwh", "capex_twd"] + [f"payback@{p:g}" for p in PRICES])
        for c in cases:
            w.writerow([c["model"], c["case"], c["saving"], c["saving_gas"], c["capex"]] +
                       [round(payback(c, p), 1) for p in PRICES])
    print(f"寫出 {OUT/'index.html'} 與 sensitivity.csv, 共 {len(cases)} 個措施組合")


if __name__ == "__main__":
    main()
