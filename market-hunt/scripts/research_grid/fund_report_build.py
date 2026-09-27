"""Build the fund report (HTML -> PDF via headless Edge) from fund_report/report_data.json + the research JSONs.

  .venv/Scripts/python.exe scripts/research_grid/fund_report_data.py    # numbers + charts (slow, ~15 min)
  .venv/Scripts/python.exe scripts/research_grid/fund_report_build.py   # report.html -> Fund_Report_2026-09-27.pdf
"""
from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "fund_report"
R = json.loads((OUT / "report_data.json").read_text(encoding="utf-8"))
G = json.loads((HERE / "grid10_compare.json").read_text(encoding="utf-8"))
C = json.loads((HERE / "grid10_concentration.json").read_text(encoding="utf-8"))
GI = json.loads((HERE / "n200_cap_report.json").read_text(encoding="utf-8"))["info"]["8w cap 10% +deploy"]
SI = json.loads((HERE / "n750_cap_report.json").read_text(encoding="utf-8"))["info"]["Weekly 8w, cap 10% +deploy"]
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
REPORT_DATE, DATA_DATE = "27 September 2026", "25 September 2026"
PDF = OUT / "Fund_Report_2026-09-27.pdf"

FULL = "Full period (Oct 2020 – Sep 2026)"
Wn = R["windows"]
V = {k: v["median"] for k, v in G["Grid-10 weekly"].items()}
CW = C["Grid-10 weekly"]


def allocation_png():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    col = {"Value core": "#1baa7a", "Gold": "#c98a00", "Nasdaq-100": "#7a5cc9", "Growth": "#eb6834", "Satellite": "#d0457a"}
    fig, ax = plt.subplots(figsize=(11, 1.55), facecolor="white")
    left, tot = 0, R["total"] / 1e5
    for n, w in R["weights"].items():
        ax.barh(0, w, left=left, color=col[n], edgecolor="white", linewidth=2, height=0.62)
        lab = f"{n}\n{w * 100:.0f}% · ₹{w * tot:.2f} L"
        if w >= 0.1:
            ax.text(left + w / 2, 0, lab, ha="center", va="center", fontsize=9, color="white", fontweight="bold")
        else:
            ax.text(left + w / 2, -0.52 if n == "Gold" else 0.52, lab.replace("\n", "  "), ha="center",
                    va="top" if n == "Gold" else "bottom", fontsize=8.4, color="#1f1f1e")
        left += w
    ax.set_xlim(0, 1)
    ax.set_ylim(-0.95, 0.95)
    ax.axis("off")
    fig.savefig(OUT / "allocation.png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)


allocation_png()


def img(name: str) -> str:
    return "data:image/png;base64," + base64.b64encode((OUT / name).read_bytes()).decode()


def f1(x, sign=False):
    return f"{x:+.1f}" if sign else f"{x:.1f}"


def rng(row):
    return f"{row['min']:.1f} · <b>{row['median']:.1f}</b> · {row['max']:.1f}" if row["max"] - row["min"] > 0.05 else f"<b>{row['median']:.1f}</b>"


def table(head, rows, cls=""):
    h = "".join(f"<th>{x}</th>" for x in head)
    b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>'


def heat(v):
    """diverging blue (gain) / orange (loss), neutral at 0"""
    a = min(abs(v) / 12, 1)
    if v >= 0:
        return f"background: rgba(42,120,214,{0.08 + 0.55 * a:.2f})"
    return f"background: rgba(235,104,52,{0.08 + 0.6 * a:.2f})"


# ---------------------------------------------------------------- derived numbers
fund = Wn[FULL]["Whole fund"]
L = R["total"] / 1e5
lo_L, hi_L = R["final_range_L"]
att = R["attribution_L"]
years = R["yearly"]
books = R["books"]

scen_rates = [("Poor decade (index-like)", 10), ("Planning case", 15), ("Good case", 20), ("Back-test repeat (unlikely)", 30)]
scen = [[n, f"{r}%"] + [f"₹{L * (1 + r / 100) ** y:,.0f} L" for y in (1, 3, 5, 10)] for n, r in scen_rates]

win_rows = []
for wn in Wn:
    row = Wn[wn]
    win_rows.append([wn, rng(row["Whole fund"]), f"{row['Whole fund']['dd_worst']:.1f}",
                     f"{row['Nifty 50']['median']:.1f}", f"{row['Equal-weight Nifty 50']['median']:.1f}",
                     f"{row['Whole fund']['median'] - row['Nifty 50']['median']:+.1f}"])

sleeve_rows = []
for n in ["Value core", "Growth", "Satellite", "Gold", "Nasdaq-100", "Equal-weight Nifty 50", "Nifty 50"]:
    a = Wn[FULL][n]
    sleeve_rows.append([n, rng(a), f"{a['dd_worst']:.1f}", f"{a['sharpe']:.2f}",
                        f"{Wn['Oct 2020 – Jun 2023'][n]['median']:.1f}", f"{Wn['Jul 2023 – Sep 2026'][n]['median']:.1f}",
                        f"{Wn['Last 12 months'][n]['median']:.1f}"])

yr_rows = [[f"{y}" + (" (Oct–Dec)" if y == "2020" else " (Jan–Sep)" if y == "2026" else ""),
            f"<b>{v['Whole fund']:+.1f}</b>", f"{v['Value core']:+.1f}", f"{v['Growth']:+.1f}", f"{v['Satellite']:+.1f}",
            f"{v['Gold']:+.1f}", f"{v['Nasdaq-100']:+.1f}", f"{v['Nifty 50']:+.1f}", f"{v['Equal-weight Nifty 50']:+.1f}"]
           for y, v in years.items()]

# monthly heat map
months = R["monthly"]
yrs_m = sorted({k[:4] for k in months})
mnames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
hm = "<table class='heat'><thead><tr><th></th>" + "".join(f"<th>{m}</th>" for m in mnames) + "<th>Year</th></tr></thead><tbody>"
for y in yrs_m:
    hm += f"<tr><th>{y}</th>"
    for i in range(1, 13):
        k = f"{y}-{i:02d}"
        hm += f"<td style='{heat(months[k])}'>{months[k]:+.1f}</td>" if k in months else "<td></td>"
    hm += f"<td class='yr'><b>{years[y]['Whole fund']:+.1f}</b></td></tr>"
hm += "</tbody></table>"

dd_rows = [[d["peak"], d["trough"], d["recovered"], f"<b>{d['depth']:.1f}</b>", f"{d['nifty']:.1f}"] for d in R["drawdowns"]]
st_rows = [[k, f"<b>{v['fund_median']:.1f}</b> ({v['fund_worst']:.1f})", f"{v['Value core']:.1f}", f"{v['Growth']:.1f}",
            f"{v['Satellite']:.1f}", f"{v['Gold']:.1f}", f"{v['Nifty 50']:.1f}", f"{v['Equal-weight Nifty 50']:.1f}"]
           for k, v in R["stress"].items()]
corr_names = ["Value core", "Growth", "Satellite", "Gold", "Nasdaq-100", "Nifty 50"]
corr_rows = [[a] + [f"{R['corr'][a][b]:.2f}" for b in corr_names] for a in corr_names]
att_total = sum(att.values())
att_rows = [[k, (f"₹{v:,.1f} L" if v >= 0 else f"−₹{-v:,.1f} L"), (f"{v / att_total * 100:.0f}%" if abs(v / att_total) >= 0.005 else "<1%"), f"₹{R['weights'][k] * L:.2f} L" if k in R["weights"] else "—"]
            for k, v in att.items()]
rec_rows = [[k, f"<b>{v['Whole fund']:+.1f}%</b>", f"{v['Nifty 50']:+.1f}%", f"{v['Equal-weight Nifty 50']:+.1f}%"]
            for k, v in R["recent"].items()]


def book_rows(name):
    b = books[name]
    rows = []
    for h in b["holdings"]:
        r = [h["symbol"], f"{h['weight']:.1f}%", f"₹{h['weight'] / 100 * R['weights'][name] * L:.2f} L"]
        if name == "Value core":
            gs = h.get("grid_state")
            r += [h.get("held_since", ""), "up" if gs == 1 else "down" if gs == -1 else "–", f"{h['ret22']:+.1f}%"]
        else:
            r += ["yes" if h.get("alpha_eligible") else "no", f"{h['ret22']:+.1f}%"]
        rows.append(r)
    return rows


vb, gb, sb = books["Value core"], books["Growth"], books["Satellite"]

# ---------------------------------------------------------------- HTML
CSS = """
@page { size: A4; margin: 16mm 15mm 16mm 15mm; }
:root { --ink:#1f1f1e; --muted:#6b6a63; --line:#e6e5df; --soft:#f6f5f1; --blue:#2a78d6; --green:#1baa7a;
        --orange:#eb6834; --pink:#d0457a; --gold:#c98a00; --violet:#7a5cc9; --warn:#b4541a; }
* { box-sizing: border-box; }
body { font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif; color: var(--ink); font-size: 10.2pt;
       line-height: 1.45; margin: 0; background: #fff; }
h1 { font-size: 26pt; margin: 0 0 6pt; letter-spacing: -0.5pt; }
h2 { font-size: 16pt; margin: 0 0 8pt; padding-bottom: 4pt; border-bottom: 2px solid var(--ink); break-after: avoid; }
h3 { font-size: 11.5pt; margin: 14pt 0 5pt; break-after: avoid; }
p { margin: 0 0 7pt; }
.section { break-before: page; }
.muted { color: var(--muted); }
.small { font-size: 8.6pt; }
table { width: 100%; border-collapse: collapse; margin: 4pt 0 10pt; font-size: 8.9pt; break-inside: avoid; }
th { text-align: left; font-weight: 600; color: var(--muted); border-bottom: 1.2px solid var(--ink); padding: 3.5pt 5pt; }
td { border-bottom: 1px solid var(--line); padding: 3.5pt 5pt; vertical-align: top; }
td:not(:first-child), th:not(:first-child) { text-align: right; }
table.rules td:not(:first-child), table.rules th:not(:first-child), table.text td, table.text th { text-align: left; }
table.rules td:first-child { width: 24%; font-weight: 600; }
table.heat td, table.heat th { text-align: center; padding: 3pt 2pt; font-size: 8.2pt; border-bottom: 1px solid #fff; }
table.heat td.yr { background: var(--soft); }
img { width: 100%; display: block; margin: 4pt 0 6pt; }
.cover { height: 255mm; display: flex; flex-direction: column; justify-content: space-between; }
.cover .band { border-left: 6px solid var(--blue); padding-left: 14pt; margin-top: 40mm; }
.cover .sub { font-size: 13pt; color: var(--muted); }
.badge { display: inline-block; font-size: 8.5pt; font-weight: 600; letter-spacing: .4pt; text-transform: uppercase;
         border: 1.2px solid var(--warn); color: var(--warn); padding: 2pt 7pt; border-radius: 3pt; margin-top: 10pt; }
.tiles { display: grid; grid-template-columns: repeat(4, 1fr); gap: 7pt; margin: 8pt 0 12pt; }
.tile { background: var(--soft); border-radius: 5pt; padding: 8pt 9pt; }
.tile .v { font-size: 17pt; font-weight: 700; line-height: 1.1; }
.tile .l { font-size: 8.2pt; color: var(--muted); margin-top: 2pt; }
.callout { border-left: 3px solid var(--blue); background: #f2f7fd; padding: 7pt 10pt; margin: 8pt 0 10pt; break-inside: avoid; }
.warn { border-left-color: var(--warn); background: #fdf4ee; }
ul { margin: 2pt 0 8pt 14pt; padding: 0; } li { margin-bottom: 3pt; }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 12pt; }
.toc td { border: none; padding: 2.5pt 0; font-size: 10.5pt; } .toc td:last-child { color: var(--muted); }
.sw { display:inline-block; width:9pt; height:9pt; border-radius:2pt; margin-right:4pt; vertical-align:-1pt; }
.footer-note { font-size: 8pt; color: var(--muted); border-top: 1px solid var(--line); padding-top: 5pt; margin-top: 10pt; }
"""

toc = [("1", "Executive summary"), ("2", "The fund at a glance"), ("3", "How the fund operates"),
       ("4", "Sleeve 1 — Value core (Grid-10 weekly, Nifty 50)"), ("5", "Sleeve 2 — Growth (Nifty 200)"),
       ("6", "Sleeve 3 — Satellite (Nifty 500 + Microcap 250)"), ("7", "Sleeves 4 & 5 — Gold and Nasdaq-100"),
       ("8", "Performance record (back-test)"), ("9", "Risk: drawdowns, stress periods, correlation"),
       ("10", "Current state of the books"), ("11", "Expected outcomes"), ("12", "Rules and regulations"),
       ("13", "Risks, limitations and honest caveats"), ("14", "Implementation status and road-map"),
       ("A", "Appendix — methodology, costs, data, glossary, research trail")]

html = f"""<title>Fund Strategy &amp; Operating Report — {REPORT_DATE}</title><style>{CSS}</style>

<div class="cover">
  <div class="band">
    <div class="muted" style="font-size:10pt; letter-spacing:1pt; text-transform:uppercase">tvagent · Quantitative equity fund</div>
    <h1>Fund Strategy &amp; Operating Report</h1>
    <div class="sub">Five-sleeve fund · ₹45 L · value core + growth + satellite + gold + Nasdaq-100</div>
    <div class="badge">Back-tested — not yet live</div>
  </div>
  <div>
    <table class="toc">{''.join(f'<tr><td style="width:8%">{a}</td><td>{b}</td></tr>' for a, b in toc)}</table>
    <p class="small muted" style="margin-top:14pt">Report date {REPORT_DATE} · market data to {DATA_DATE} ·
    all performance figures are simulated (point-in-time back-test with costs), not realised returns.</p>
  </div>
</div>

<div class="section">
<h2>1 · Executive summary</h2>
<p>The fund splits ₹45 L across five sleeves that behave differently from each other: a large-cap <b>value core</b> that buys
short-term dips in Nifty 50 leaders and lets winners run, two <b>momentum books</b> (growth and satellite) that buy the
strongest trending stocks each week, and two <b>diversifiers</b> — gold and the US Nasdaq-100 — that historically rise when
Indian equities fall. Sleeve weights are restored once a month. Every rule is mechanical; no discretionary override.</p>

<div class="tiles">
  <div class="tile"><div class="v">{fund['median']:.1f}%</div><div class="l">median yearly return, Oct 2020 – Sep 2026 (range {fund['min']:.1f}–{fund['max']:.1f}%)</div></div>
  <div class="tile"><div class="v">{fund['dd_worst']:.1f}%</div><div class="l">worst peak-to-trough fall (any trading-day combination)</div></div>
  <div class="tile"><div class="v">{fund['sharpe']:.2f}</div><div class="l">Sharpe ratio (Nifty 50: {Wn[FULL]['Nifty 50']['sharpe']:.2f})</div></div>
  <div class="tile"><div class="v">₹{R['median_final_L']:.0f} L</div><div class="l">₹45 L grown to (range ₹{lo_L:.0f}–{hi_L:.0f} L); Nifty 50 ≈ ₹{L * (1 + Wn[FULL]['Nifty 50']['median'] / 100) ** 5.98:.0f} L</div></div>
</div>

<h3>Key findings</h3>
<ul>
<li><b>The whole fund beat Nifty 50 by ~{fund['median'] - Wn[FULL]['Nifty 50']['median']:.0f} points a year</b> in the test window with a
worst fall of {fund['dd_worst']:.0f}% — similar to Nifty 50's own {Wn[FULL]['Nifty 50']['dd_worst']:.0f}%. Diversification is doing real work:
the momentum books alone fell up to {Wn[FULL]['Satellite']['dd_worst']:.0f}%.</li>
<li><b>It kept working after the easy years.</b> Jul 2023 – Sep 2026, when Nifty 50 made {Wn['Jul 2023 – Sep 2026']['Nifty 50']['median']:.1f}%/yr,
the fund made {Wn['Jul 2023 – Sep 2026']['Whole fund']['median']:.1f}%/yr. Last 12 months: {Wn['Last 12 months']['Whole fund']['median']:+.1f}% vs Nifty 50 {Wn['Last 12 months']['Nifty 50']['median']:+.1f}%.</li>
<li><b>Returns rotate between sleeves.</b> Value led 2020–21, momentum led 2023–24, gold carried 2025 (+{years['2025']['Gold']:.0f}%), satellite and
Nasdaq carried 2026. No single sleeve wins every year — which is why they are held together.</li>
<li><b>Timing luck is small.</b> Across all 125 combinations of trading weekday the full-period result stays within
{fund['min']:.1f}–{fund['max']:.1f}%/yr; the trading day can be any weekday.</li>
<li><b>Plan for less than the back-test.</b> 2020–26 was a strong period for Indian mid and small caps. A prudent planning
figure is <b>15–20%/yr before tax</b>, with falls of 20–25% in ordinary corrections and possibly 40–50% in a 2008-style crash.</li>
</ul>

<div class="callout warn"><b>Status.</b> Nothing here is live. The market-hunt engine cannot yet run the three stock books
(weekly tranches, caps, Grid-10) and has an open cost bug that overstates portal returns. Section 14 lists what must be built
before real money follows these rules.</div>
</div>

<div class="section">
<h2>2 · The fund at a glance</h2>
<img src="{img('allocation.png')}">
{table(["Sleeve", "Weight", "Capital", "What it holds", "Style", "Checked", "Names"], [
    ["<span class='sw' style='background:var(--green)'></span>Value core", "28%", "₹12.60 L", "Nifty 50 stocks", "Buy dips, let winners run", "Weekly", "10"],
    ["<span class='sw' style='background:var(--gold)'></span>Gold", "8%", "₹3.60 L", "Gold in ₹", "Crash hedge", "Monthly reset", "1"],
    ["<span class='sw' style='background:var(--violet)'></span>Nasdaq-100", "4%", "₹1.80 L", "US large-cap tech in ₹", "Global / USD diversifier", "Monthly reset", "1"],
    ["<span class='sw' style='background:var(--orange)'></span>Growth", "35%", "₹15.75 L", "Nifty 200 stocks", "Momentum, weekly tranches", "Weekly", f"~{GI['names_avg']:.0f} ({GI['names_min']}–{GI['names_max']})"],
    ["<span class='sw' style='background:var(--pink)'></span>Satellite", "25%", "₹11.25 L", "Nifty 500 + Microcap 250", "Momentum, weekly tranches", "Weekly", f"~{SI['names_avg']:.0f} ({SI['names_min']}–{SI['names_max']})"],
], "text")}
<h3>Why these five pieces</h3>
<ul>
<li><b>Value core (28%)</b> — the steady equity base. Large, liquid companies; low trading (≈{V['turnover_pa']:.1f}× a year); it
earns from the tendency of Nifty 50 leaders to recover after a bad month.</li>
<li><b>Growth (35%) and Satellite (25%)</b> — the return engine. They own whatever is trending hardest right now, across large,
mid, small and micro caps, and rotate out when the trend fades. High return, deeper falls.</li>
<li><b>Gold (8%)</b> — insurance. Between 2008 and 2026 gold (in ₹) rose during every one of the ten largest Nifty falls tested.
Correlation with Indian equities ≈ {R['corr']['Gold']['Nifty 50']:.2f}.</li>
<li><b>Nasdaq-100 (4%)</b> — exposure to US technology and to the US dollar, which tends to strengthen when India sells off.</li>
</ul>
<p>The split — 40% core (value + gold + Nasdaq), 35% growth, 25% satellite — is the <b>aggressive</b> mix chosen on 27 Sep 2026,
accepting falls of about −35% inside the momentum books in exchange for higher long-run growth.</p>
</div>

<div class="section">
<h2>3 · How the fund operates</h2>
<h3>Two clocks: weekly inside the stock books, monthly across sleeves</h3>
{table(["Cycle", "What happens", "Who / what"], [
    ["Every trading day (after close)", "Refresh prices and TrueVX indicators; nothing is traded", "Data pipeline (`refresh_all.py`)"],
    ["Once a week — any weekday", "Each stock book runs its own rules: sells first, then buys, then top-ups", "Value core, Growth, Satellite"],
    ["First trading week of each month", "Restore sleeve weights 28 / 8 / 4 / 35 / 25 by moving cash between sleeves", "Whole fund"],
    ["When a stock leaves its index", "Sold at the next open in whichever book holds it", "All stock books"],
], "text")}
<h3>The weekly cycle, step by step</h3>
<ol>
<li><b>Signal from the previous close.</b> All rankings (22-day return, grid state, Alpha filter) use the close <i>before</i> the trading day. No same-day look-ahead.</li>
<li><b>Generate the order list</b> for each book independently.</li>
<li><b>Sell first</b> (exits, expired tranches, trims above cap) so the proceeds fund the buys.</li>
<li><b>Buy</b> new names and top up under-weight holdings, at or soon after the open.</li>
<li><b>Deploy leftover cash</b> so each book stays fully invested (no idle cash).</li>
<li><b>Reconcile</b> broker holdings against the system book.</li>
</ol>
<div class="callout"><b>Flexible trading day.</b> The weekday does not have to be fixed. Tested with a random weekday each week
(20 runs), the value core returned a median 22.1%/yr vs 21.9% with a fixed weekday, same turnover. Rules: once per week,
every week; signal from the previous close; never choose the day by how the market looks; run the full rule set in one go.</div>
<h3>The monthly sleeve reset</h3>
<p>Market moves pull the sleeves away from their target weights. In the first trading week of every month the fund
moves cash from sleeves above target to sleeves below target. Inside a stock sleeve, new cash is spread across existing
holdings in proportion to their size (the way the back-test models it); cash taken out is raised the same way. The test
charged 0.15% on the rupees moved; over six years this cost about ₹{-att['Monthly re-balancing cost']:.1f} L in total.</p>
</div>

<div class="section">
<h2>4 · Sleeve 1 — Value core: Grid-10 weekly on Nifty 50</h2>
<p><b>Idea.</b> Nifty 50 leaders pull against each other: money rotates from last month's winners into last month's
losers. The book buys the biggest recent losers — but only those whose long-term strength has turned up (so it avoids
falling knives) — and then <b>lets winners run</b>, selling a stock only once its trend has broken <i>and</i> it has bounced.</p>
<h3>The grid state (trend filter)</h3>
<p>For each stock take TrueVX <code>mean_long</code> (0–100, measured against Nifty 50) and split the scale into 5-point bands
(0–5, 5–10 … 95–100). The <b>grid state</b> is the direction of the last band change: <b>up</b> after moving into a higher band,
<b>down</b> after moving into a lower band. It stays until the next band change; wiggles inside a band change nothing.</p>
{table(["Rule", "Detail"], [
    ["Universe", "Nifty 50 members on the trading date (point-in-time)"],
    ["Frequency", "Once a week, any weekday"],
    ["Eligible to buy", "Grid state = up"],
    ["Sell", "Grid state = down <b>and</b> positive 22-day return. A down-state stock with a negative month is kept until it bounces."],
    ["Forced sell", "Stock leaves Nifty 50 → sold at the next open"],
    ["Buy", "Fill free slots up to 10 names with the <b>lowest 22-day return</b> among eligible names not already held"],
    ["Sizing target", "Equity ÷ 10 (≈10% each)"],
    ["Using sale money", "New name up to target → top up holdings below target → any rest spread equally across all ten. If money is short, every gap is filled pro-rata."],
    ["Trimming", "<b>Never.</b> Winners keep their size and may grow beyond 20%."],
    ["Cash", "Always fully invested"],
], "rules")}
<h3>What happens when a big winner is sold — a real example</h3>
<p>8 Nov 2021: TATASTEEL, grown to 17.8% of the book, was sold. The proceeds bought the new name COALINDIA (7.6%) and
topped up the laggards — HINDUNILVR 3.3% → 8.3%, UPL 5.4% → 8.9%, GRASIM 8.7% → 10.0% — while the remaining winner
HINDALCO stayed at 13.4%. A new stock never inherits the old stock's 18–20%; it starts at 10% or less.</p>
<h3>Back-tested behaviour (Oct 2020 – Sep 2026, median of 5 weekday variants)</h3>
{table(["Measure", "Value core", "Measure", "Value core"], [
    ["Yearly return", f"{V['cagr']:.1f}%  ({G['Grid-10 weekly']['cagr']['min']:.1f}–{G['Grid-10 weekly']['cagr']['max']:.1f})", "Closed trades (6 yrs)", f"{V['closed_trades']:.0f}"],
    ["Worst fall", f"{V['max_dd']:.1f}%", "Win rate", f"{V['win_rate']:.0f}%"],
    ["Sharpe", f"{V['sharpe']:.2f}", "Average win / loss", f"+{V['avg_win']:.1f}% / {V['avg_loss']:.1f}%"],
    ["Turnover", f"{V['turnover_pa']:.1f}× a year", "Profit factor", f"{V['profit_factor']:.1f}"],
    ["Buys / sells a year", f"{V['buys_pa']:.0f} / {V['sells_pa']:.0f}", "Largest holding, typical / peak", f"{CW['distribution']['largest_median']:.0f}% / {max(x['peak_weight_pct'] for x in CW['grew_past_15']):.0f}%"],
    ["Holding period, median / mean / max", f"{V['hold_median']:.0f} / {V['hold_mean']:.0f} / {V['hold_max']:.0f} days", "Top-3 holdings, typical / max", f"{CW['distribution']['top3_median']:.0f}% / {CW['distribution']['top3_max']:.0f}%"],
])}
<p><b>Why Grid-10 weekly won the value slot.</b> It matched the older monthly loser book on return (21.9 vs 21.3%/yr) with a
third of the trading (3.4× vs 10×), the shallowest falls of the candidates, and a profit factor twice as high — its
profits come from a few long-held winners rather than many small trades. The Nifty 100 version was rejected: its extra
return all came from 2020–23, it doubled the drawdown, and Nifty 51–100 stocks are already covered by growth and satellite.</p>
<div class="callout warn"><b>Concentration.</b> Because winners are never trimmed, one stock can reach ~22% of the sleeve.
The largest single-stock hits in the test: ADANIENT at 8.7% weight falling 59% (Jan 2023) cost −5.8% of the sleeve;
TATASTEEL at 22% falling 17% cost −3.9%. Rule of thumb: <i>weight × fall = hit to the sleeve</i>.</div>
</div>

<div class="section">
<h2>5 · Sleeve 2 — Growth: Nifty 200 weekly momentum</h2>
<p><b>Idea.</b> Buy what is strongest right now, in small weekly slices, and keep each slice for eight weeks. Buying a
little every week spreads entry timing (a 7-stock monthly book showed 10+ points of pure timing luck); the eight-week
clock gives each trend time to work; the 10% cap stops any one stock from dominating.</p>
<h3>The Alpha filter</h3>
<p>A stock is <b>Alpha-eligible</b> only if all four TrueVX readings (measured against Nifty 50) are strong at the previous close:
<code>truevx_score &gt; 90</code>, <code>mean_short &gt; 80</code>, <code>mean_mid &gt; 60</code>, <code>mean_long &gt; 50</code>.</p>
{table(["Rule", "Detail"], [
    ["Universe", "Nifty 200 members on the trading date (point-in-time)"],
    ["Frequency", "Once a week, any weekday"],
    ["Pick", "Top <b>5</b> by 22-day return among Alpha-eligible stocks"],
    ["Tranche size", "Equity ÷ 40 = <b>2.5%</b> per pick. A stock already held and picked again gets another tranche."],
    ["Holding clock", "Each tranche is sold after <b>8 weekly rebalances</b>, unless the stock is picked again that week (the tranche rolls over)"],
    ["Cap", "Never buy above <b>10%</b> of the sleeve; every week trim any holding above 10% back to 10%"],
    ["Leftover cash", "Top up holdings in rank order, each up to 10%, as fresh tranches on the 8-week clock"],
    ["Forced sell", "Stock leaves Nifty 200 → sold at the next open"],
], "rules")}
{table(["Measure", "Growth", "Measure", "Growth"], [
    ["Yearly return", rng(Wn[FULL]['Growth']) + "%", "Names held", f"{GI['names_avg']:.0f} ({GI['names_min']}–{GI['names_max']})"],
    ["Worst fall", f"{Wn[FULL]['Growth']['dd_worst']:.1f}%", "Turnover", f"{GI['turnover_pa']:.1f}× a year"],
    ["Sharpe", f"{Wn[FULL]['Growth']['sharpe']:.2f}", "Trims a year", f"{GI['trims_pa']:.0f}"],
    ["Oct 2020 – Jun 2023", f"{Wn['Oct 2020 – Jun 2023']['Growth']['median']:.1f}%", "Holding period, median", f"{GI['hold_median_days']:.0f} sessions"],
    ["Jul 2023 – Sep 2026", f"{Wn['Jul 2023 – Sep 2026']['Growth']['median']:.1f}%", "Largest holding, peak", f"{GI['peak_weight'] * 100:.0f}%"],
])}
<p class="small muted">Return cells show worst · <b>median</b> · best across the five trading weekdays.</p>
</div>

<div class="section">
<h2>6 · Sleeve 3 — Satellite: Nifty 500 + Microcap 250 weekly momentum</h2>
<p>Exactly the growth rules applied to a much wider universe — the Nifty Total-Market-750 style set (Nifty 500 plus
Microcap 250). It reaches small and micro caps where trends run hardest, and pays for it with the deepest falls in the fund.</p>
{table(["Rule", "Detail"], [
    ["Universe", "NIFTY500MICRO750 members on the trading date (point-in-time)"],
    ["Everything else", "Identical to Growth: Alpha filter, top 5 by 22-day return weekly, 2.5% tranches, 8-week clock, 10% cap with weekly trim, leftover-cash deployment, forced sell on index exit"],
], "rules")}
{table(["Measure", "Satellite", "Measure", "Satellite"], [
    ["Yearly return", rng(Wn[FULL]['Satellite']) + "%", "Names held", f"{SI['names_avg']:.0f} ({SI['names_min']}–{SI['names_max']})"],
    ["Worst fall", f"{Wn[FULL]['Satellite']['dd_worst']:.1f}%", "Turnover", f"{SI['turnover_pa']:.1f}× a year"],
    ["Sharpe", f"{Wn[FULL]['Satellite']['sharpe']:.2f}", "Trims a year", f"{SI['trims_pa']:.0f}"],
    ["Oct 2020 – Jun 2023", f"{Wn['Oct 2020 – Jun 2023']['Satellite']['median']:.1f}%", "Holding period, median", f"{SI['hold_median_days']:.0f} sessions"],
    ["Jul 2023 – Sep 2026", f"{Wn['Jul 2023 – Sep 2026']['Satellite']['median']:.1f}%", "Largest holding, peak", f"{SI['peak_weight_daily'] * 100:.0f}%"],
])}
<div class="callout warn"><b>Satellite-specific risks.</b> Most of its gain came in 2020–23 (≈{Wn['Oct 2020 – Jun 2023']['Satellite']['median']:.0f}%/yr) and it fell
{-years['2025']['Satellite']:.0f}% in 2025. 43 stocks that left the universe and were later delisted have no price data, so the
test carries some survivorship bias. Micro caps are thin: a sleeve of ₹11 L is fine, but capacity is limited — slippage
rises quickly with size.</div>
</div>

<div class="section">
<h2>7 · Sleeves 4 &amp; 5 — Gold and Nasdaq-100</h2>
{table(["", "Gold (8%)", "Nasdaq-100 (4%)"], [
    ["Tested as", "TradingView TVC:GOLD × USD/INR, minus 0.5%/yr", "TradingView NASDAQ:NDX × USD/INR, minus 0.5%/yr"],
    ["Role", "Crash insurance; rises when Indian equities fall", "US tech growth + US-dollar exposure"],
    ["Yearly return 2020–26", f"{Wn[FULL]['Gold']['median']:.1f}%", f"{Wn[FULL]['Nasdaq-100']['median']:.1f}%"],
    ["Worst fall", f"{Wn[FULL]['Gold']['dd_worst']:.1f}%", f"{Wn[FULL]['Nasdaq-100']['dd_worst']:.1f}%"],
    ["Correlation with Nifty 50 (daily)", f"{R['corr']['Gold']['Nifty 50']:.2f}", f"{R['corr']['Nasdaq-100']['Nifty 50']:.2f}"],
    ["Traded", "Only at the monthly sleeve reset", "Only at the monthly sleeve reset"],
], "text")}
<p><b>Why gold, not more bonds or commodities.</b> In the 2008–2026 study, gold priced in rupees rose in all ten of the
largest Nifty falls; silver, crude, copper and broad commodity indices fell <i>with</i> equities. Adding a 30% gold slice to the
Nifty 50 value book over 2008–2026 cut the worst fall from about −61% to −44%. The US dollar hedges too, which is part
of what the Nasdaq-100 slice brings.</p>
<div class="callout warn"><b>Instrument not yet chosen.</b> The back-test used price series, not a product you can buy. The
live fund will need an Indian-listed gold instrument (for example a gold ETF) and a Nasdaq-100 route (for example an
Indian Nasdaq-100 ETF or fund-of-fund). Check tracking error, expense ratio and — for the Nasdaq product — any premium
to NAV caused by overseas-investment limits before choosing.</div>
</div>

<div class="section">
<h2>8 · Performance record (back-test, Oct 2020 – Sep 2026)</h2>
<p>₹45 L invested on 1 Oct 2020. Line = median of 125 weekday combinations (5 value × 5 growth × 5 satellite); shaded band =
best to worst combination. Lower panel: fall from the previous peak.</p>
<img src="{img('fund_equity.png')}">
{table(["Window", "Fund %/yr (worst · median · best)", "Fund worst fall %", "Nifty 50", "EW Nifty 50", "Fund − Nifty 50"], win_rows)}
<p class="small muted">Periods under one year (2026 YTD) are simple returns, not annualised. Nifty 50 is the price index (dividends excluded, as for the fund).</p>

<h3>Each sleeve on its own</h3>
<img src="{img('sleeves.png')}">
{table(["Sleeve", "%/yr full period", "Worst fall", "Sharpe", "2020–23", "2023–26", "Last 12 m"], sleeve_rows)}
</div>

<div class="section">
<h3 style="margin-top:0">Calendar-year returns</h3>
<img src="{img('yearly.png')}">
{table(["Year", "Fund", "Value", "Growth", "Satellite", "Gold", "Nasdaq", "Nifty 50", "EW N50"], yr_rows)}
<h3>Monthly returns of the fund (%)</h3>
{hm}
<p class="small muted">{R['month_stats']['positive_pct']:.0f}% of months positive · best month {R['month_stats']['best']:+.1f}% ({R['month_stats']['best_when']}) ·
worst month {R['month_stats']['worst']:+.1f}% ({R['month_stats']['worst_when']}).</p>
<h3>Where the money came from (median path, ₹45 L start)</h3>
{table(["Source", "Profit", "Share of profit", "Allocated"], att_rows)}
<p class="small muted">Growth and satellite supplied ~{(att['Growth'] + att['Satellite']) / att_total * 100:.0f}% of the profit on 60% of the capital; the core (value + gold + Nasdaq)
supplied the rest while holding the fund's falls down.</p>
</div>

<div class="section">
<h2>9 · Risk: drawdowns, stress periods, correlation</h2>
<h3>Trailing 12-month return</h3>
<img src="{img('rolling12.png')}">
<p>Median 12-month return {R['rolling12']['median']:.0f}%; the weakest 12-month stretch was {R['rolling12']['min']:+.1f}% (to {R['rolling12']['min_when']});
only {R['rolling12']['pct_negative']:.0f}% of 12-month windows were negative. Longest time below a previous peak: {R['longest_underwater_days']} trading days (~{R['longest_underwater_days'] / 21:.0f} months).</p>
<h3>The five deepest falls of the fund (median path)</h3>
{table(["Peak", "Trough", "Recovered", "Fund fall %", "Nifty 50 fall %"], dd_rows)}
<h3>Stress periods — worst fall inside each window (%)</h3>
{table(["Period", "Fund median (worst)", "Value", "Growth", "Satellite", "Gold", "Nifty 50", "EW N50"], st_rows)}
<h3>How the sleeves move together (daily correlation)</h3>
{table([""] + corr_names, corr_rows)}
<p>Gold and Nasdaq-100 are nearly uncorrelated with every Indian sleeve — they are the true diversifiers. Value, growth and
satellite are all Indian equity and move together (0.55–0.75); they diversify by <i>style</i> (buying dips vs buying strength),
which shows up across years more than across days.</p>
</div>

<div class="section">
<h2>10 · Current state of the books (as of {DATA_DATE})</h2>
<p>What each stock book would hold today if it had run from Oct 2020 (Monday-trading variant). Weights are shares of each
sleeve; rupee amounts assume the target allocation of ₹45 L. These are <b>model books</b>, not live holdings.</p>
<h3>Recent performance of the fund model</h3>
{table(["Period", "Fund", "Nifty 50", "EW Nifty 50"], rec_rows)}
<h3>Value core — {len(vb['holdings'])} holdings</h3>
{table(["Stock", "Weight", "₹", "Held since", "Grid state", "22-day return"], book_rows('Value core'))}
<p class="small muted">Down-state holdings with a negative month (e.g. NESTLEIND, MARUTI) are kept until they bounce — that is the sell rule working as designed.</p>
</div>

<div class="section">
<h3 style="margin-top:0">Growth — {len(gb['holdings'])} holdings · {gb['eligible_today']} Alpha-eligible stocks in Nifty 200 today</h3>
{table(["Stock", "Weight", "₹", "Alpha-eligible now", "22-day return"], book_rows('Growth'))}
<p class="small muted">Next week's top-5 candidates at the last close: {', '.join(gb['top5_today'])}.</p>
<h3>Satellite — {len(sb['holdings'])} holdings · {sb['eligible_today']} Alpha-eligible stocks today</h3>
{table(["Stock", "Weight", "₹", "Alpha-eligible now", "22-day return"], book_rows('Satellite'))}
<p class="small muted">Next week's top-5 candidates at the last close: {', '.join(sb['top5_today'])}.</p>
</div>

<div class="section">
<h2>11 · Expected outcomes</h2>
<p>The back-test window (Oct 2020 – Sep 2026) was unusually good for Indian equities, especially small and mid caps.
Over 2008–2020 the Nifty 50 reversal idea behind the value core only matched an equal-weight Nifty 50, and the momentum
books cannot be tested before 2020 (the TrueVX history they need starts in 2018). Treat the {fund['median']:.0f}%/yr as the
optimistic end, not the expectation.</p>
{table(["What to expect", "Planning range", "Evidence"], [
    ["Long-run return, before tax", "<b>15–20% a year</b>", f"Back-test {fund['median']:.0f}%/yr in a strong market; value-core edge over equal-weight Nifty 50 disappeared in 2008–20; momentum edges are regime-dependent"],
    ["Ordinary correction", "Fund −15% to −25%", f"Five falls of −9% to −20% in 2020–26; worst combination {fund['dd_worst']:.0f}%"],
    ["Severe crash (2008-type)", "Fund −40% to −50% (judgement)", "Not in the test window. Nifty fell ~60% in 2008; the value book alone fell −62%; gold offset part of it"],
    ["Bad years", "About 1 year in 4 flat or negative", f"{R['rolling12']['pct_negative']:.0f}% of 12-month windows negative in 2020–26 — expect more in a normal market"],
    ["Time to recover a fall", "Months to 2 years", f"Longest in test ≈ {R['longest_underwater_days'] / 21:.0f} months"],
    ["After tax", "Roughly 3–6 points lower", "Growth/satellite gains are almost all short-term (see §12)"],
], "text")}
<h3>What ₹45 L could become (illustrative compounding, before tax)</h3>
{table(["Scenario", "Rate", "1 year", "3 years", "5 years", "10 years"], scen)}
<p class="small muted">Compounding illustration only — not a forecast. Real returns arrive unevenly, with losing years mixed in.</p>
</div>

<div class="section">
<h2>12 · Rules and regulations</h2>
<h3>Trading rules (binding)</h3>
{table(["Rule", "Detail"], [
    ["Mechanical only", "Every buy and sell comes from the published rules. No discretionary overrides, no 'this one looks too expensive'."],
    ["Signal timing", "Signals from the previous close; orders at or after the next open. Never trade on a signal computed from the same day's price."],
    ["Weekly discipline", "Each stock book is checked exactly once per week (any weekday). Do not skip weeks; do not check twice."],
    ["Order sequence", "Sells before buys; the full rule set in one session."],
    ["Monthly reset", "Sleeve weights restored to 28 / 8 / 4 / 35 / 25 in the first trading week of every month."],
    ["Index changes", "A stock that leaves its book's index is sold at the next open."],
    ["No stop-losses", "Tested: per-stock stop-losses cost return and <i>deepened</i> falls in grinding declines. Risk is controlled by diversification, not stops."],
    ["No leverage, no derivatives, no short selling", "Cash equities, ETFs and funds only. Always fully invested."],
    ["Position limits", "Growth & satellite: 10% cap with weekly trim. Value core: no cap (winners run), by design."],
], "rules")}
<h3>Governance (proposed — for approval)</h3>
<ul>
<li><b>Change control.</b> A rule changes only after a back-test with the same honest method (point-in-time, costs, all timing
variants) shows the change helps in <i>both</i> halves of the history. Changes are dated in the research log.</li>
<li><b>Review cadence.</b> Quarterly review of each sleeve against its expected range; an annual review of the allocation.</li>
<li><b>Kill-switch review.</b> A sleeve that falls more than 1.5× its worst back-tested fall (value −30%, growth −43%, satellite −54%) triggers a
formal review before the next rebalance — review, not automatic liquidation.</li>
<li><b>Record keeping.</b> Every order, fill and reconciliation stored; broker exports reconciled against the system book weekly.</li>
<li><b>Security.</b> Broker credentials held only in Windows Credential Manager; loaded on rebalance day and wiped after.</li>
</ul>
<h3>Regulatory and tax notes (verify with a professional)</h3>
<ul>
<li><b>Own money only.</b> Managing or pooling other people's money for a fee needs SEBI registration (for example as a portfolio
manager or investment adviser). This report assumes a personal/family account.</li>
<li><b>Costs.</b> Statutory charges (STT, exchange, SEBI, stamp duty, GST) are modelled at {R['charges']['buy_pct']:.3f}% per buy and
{R['charges']['sell_pct']:.3f}% per sell, plus {R['charges']['slippage_bp']} bp slippage, with zero brokerage. A broker that charges brokerage lowers returns.</li>
<li><b>Tax.</b> Under the rules in force from July 2024, listed-equity gains held under 12 months are short-term (20%) and over
12 months long-term (12.5% above ₹1.25 L a year). Growth and satellite hold positions ~2 months, so their gains are almost
entirely short-term; the value core holds a median ~3.5 months with some long-held winners. All returns in this report are <b>before tax</b>.
Rates and treatment of foreign-equity funds and gold products change — confirm current rules before acting.</li>
</ul>
</div>

<div class="section">
<h2>13 · Risks, limitations and honest caveats</h2>
{table(["Risk", "Why it matters", "Mitigation"], [
    ["Regime dependence", "2020–26 favoured Indian momentum and small caps. Much of satellite's gain came in 2020–23.", "Plan on 15–20%; diversified sleeves; quarterly review"],
    ["Back-test ≠ live", "Real fills, gaps, halts and partial fills differ from open-price fills.", "Paper-trade first; reconcile every week"],
    ["Survivorship", "43 delisted satellite-universe stocks lack prices.", "Small for large caps; treat satellite numbers as optimistic"],
    ["Concentration (value core)", "No trimming → a single stock can reach ~22%.", "Accepted by design; top-3 rarely above 48%"],
    ["Correlated equity sleeves", "Value, growth and satellite are all Indian equity (0.55–0.75 correlation).", "Gold and Nasdaq slices; style diversification"],
    ["Liquidity (satellite)", "Micro caps trade thinly; slippage grows with size.", "Satellite capped at ₹11 L; revisit before scaling"],
    ["Model / data errors", "A wrong price or indicator produces wrong trades.", "Data validation in the refresh pipeline; corporate-action checks"],
    ["Tax drag", "High turnover in momentum books → short-term gains.", "Accepted; value core is the low-turnover anchor"],
    ["Engine gaps", "Portal cannot yet run these books; sell-side costs missing in engine.", "Fix before live (section 14)"],
    ["Dividends", "Excluded from fund and benchmarks alike (~1–1.5%/yr for large caps).", "Slightly understates absolute returns"],
], "text")}
<p><b>What the numbers are <i>not</i>.</b> They are not realised returns, not a forecast and not a guarantee. Every figure comes
from a simulation that uses only information available at the time, charges realistic costs, and reports the full range of
trading-day outcomes rather than the luckiest one — but it is still a simulation of one six-year period.</p>
</div>

<div class="section">
<h2>14 · Implementation status and road-map</h2>
{table(["#", "Item", "Status", "Why it matters"], [
    ["1", "Rules for all five sleeves", "✓ Decided (27 Sep 2026)", "This report"],
    ["2", "Whole-fund back-test with Grid-10 weekly value core", "✓ Done", "Section 8"],
    ["3", "Engine warm-up bug (#3: alphabetical day-one picks)", "✓ Fixed at source, tested", "Day-one portfolios were wrong"],
    ["4", "Engine sell-side charges & slippage (#1) and bugs #2, #4, #5, #6", "Open", "Portal overstates returns until fixed"],
    ["5", "Engine support: Grid-10 rules", "Not started", "Value core cannot run on the portal"],
    ["6", "Engine support: weekly tranches, 8-week clock, 10% cap, trim, cash deployment", "Not started", "Growth and satellite cannot run on the portal"],
    ["7", "Choose gold and Nasdaq-100 instruments", "Not started", "Tested on price series only"],
    ["8", "Paper trading (suggest 2–3 months)", "Not started", "Proves live fills match the model"],
    ["9", "Transition plan from the current two live sleeves", "Not started", "Move capital without a tax or timing shock"],
    ["10", "Go live, then quarterly reviews", "—", ""],
], "text")}
</div>

<div class="section">
<h2>Appendix</h2>
<h3>A1 · Method</h3>
<ul>
<li><b>Point-in-time universes.</b> Each date uses index membership as it stood that day, including names later removed.</li>
<li><b>No look-ahead.</b> Signals from the previous close; fills at the next open.</li>
<li><b>Costs.</b> Statutory charges on both sides via the platform's brokerage calculator ({R['charges']['buy_pct']:.3f}% buy / {R['charges']['sell_pct']:.3f}% sell), 5 bp slippage, 0.15% on rupees moved at the monthly sleeve reset, 0.5%/yr holding cost for gold and Nasdaq-100.</li>
<li><b>Warm-up.</b> Indicators loaded from well before the start so the first trades are real signals.</li>
<li><b>Timing luck.</b> Every weekday variant run (5 per weekly book); whole fund = all 125 combinations; the report shows worst · median · best.</li>
<li><b>Benchmarks.</b> Nifty 50 price index and an equal-weight Nifty 50 (point-in-time members, daily rebalanced, no costs).</li>
</ul>
<h3>A2 · Data sources</h3>
<p>NSE daily prices (market-hunt database, with corporate-action adjustments); NSE month-end index constituent files for
point-in-time membership; TrueVX indicators computed against Nifty 50; TradingView series via tvdatafeed for TVC:GOLD,
NASDAQ:NDX and FX_IDC:USDINR.</p>
<h3>A3 · Glossary</h3>
{table(["Term", "Meaning"], [
    ["22-day return", "Price change over the last 22 trading sessions (~1 month)"],
    ["TrueVX", "Relative-strength indicator family (0–100) measured against Nifty 50: truevx_score and its short / mid / long averages"],
    ["Grid state", "Direction (up/down) of the last 5-point band change in mean_long"],
    ["Alpha filter", "truevx_score > 90, mean_short > 80, mean_mid > 60, mean_long > 50"],
    ["Tranche", "A 2.5% slice bought on one weekly check, sold 8 checks later unless renewed"],
    ["Drawdown / worst fall", "Largest fall from a previous peak"],
    ["Sharpe ratio", "Return per unit of volatility (annualised); higher is smoother"],
    ["Profit factor", "Rupees made on winning trades ÷ rupees lost on losing trades"],
    ["Turnover", "Rupees traded in a year ÷ average sleeve value"],
    ["Timing luck", "How much the result changes with the trading day chosen"],
    ["Equal-weight Nifty 50", "All Nifty 50 members held in equal amounts — the fair comparison for an equal-weight book"],
], "text")}
<h3>A4 · Research trail — what was tested and rejected</h3>
{table(["Idea", "Verdict"], [
    ["Momentum / Alpha books on Nifty 50", "Rejected — lost to equal-weight Nifty 50 in every form"],
    ["Monthly 20-stock loser book (old value core)", "Replaced by Grid-10 weekly — same return, 3× the trading"],
    ["Grid-10 monthly", "Rejected — deepest falls, 2 years below peak, wider timing luck"],
    ["Grid-10 on Nifty 100", "Rejected — gain only in 2020–23, drawdown to −40%, overlaps growth/satellite"],
    ["Per-stock stop-losses", "Rejected — cost return and deepened falls in slow declines"],
    ["Silver, crude, copper, broad commodities as hedges", "Rejected — fell with equities in crashes"],
    ["Relative-strength moving-average filters", "Rejected — no crash protection"],
    ["Capping value-core winners (15%)", "Declined — winners are left to run by choice"],
    ["Daily one-stock tranches", "Rejected — diluted to ~21 names, lower return"],
], "text")}
<h3>A5 · Reproducing this report</h3>
<p class="small">All code in <code>market-hunt/scripts/research_grid/</code>: <code>grid10.py</code>, <code>grid10_compare.py</code>,
<code>grid10_concentration.py</code>, <code>n200_cap.py</code>, <code>n750_cap.py</code>, <code>combine_all.py</code>,
<code>fund_report_data.py</code> (numbers and charts) and <code>fund_report_build.py</code> (this document).</p>
<div class="footer-note">Prepared {REPORT_DATE}. Simulated results; past or simulated performance does not guarantee future returns.
Not investment advice. Figures before tax.</div>
</div>
"""

head, body = html.split("</style>", 1)
(OUT / "report.html").write_text(f"<!doctype html><html><head><meta charset='utf-8'>{head}</style></head><body>{body}</body></html>",
                                 encoding="utf-8")
subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={PDF}",
                (OUT / "report.html").as_uri()], check=True, timeout=180)
print(PDF, PDF.stat().st_size)
