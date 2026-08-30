import sys, pathlib
b64 = pathlib.Path(sys.argv[1]).read_text().strip()
out = sys.argv[2]
HTML = """<title>TrueVX Regime Map</title>
<style>
  :root{
    --ground:#0E1422; --panel:#121A2B; --text:#E6ECF5; --mute:#7C8AA5;
    --grid:#243049; --teal:#34D6C8; --amber:#F2B33D;
    --mono:ui-monospace,"Cascadia Code","SF Mono",Consolas,monospace;
    --sans:system-ui,"Segoe UI",-apple-system,sans-serif;
  }
  *{box-sizing:border-box}
  body{background:var(--ground);color:var(--text);font-family:var(--sans);
    line-height:1.55;margin:0;padding:0;-webkit-font-smoothing:antialiased}
  .wrap{max-width:920px;margin:0 auto;padding:48px 24px 80px}
  .eyebrow{font-family:var(--mono);font-size:12px;letter-spacing:.32em;
    text-transform:uppercase;color:var(--mute);margin:0 0 14px}
  h1{font-size:clamp(28px,5vw,46px);line-height:1.05;font-weight:800;
    letter-spacing:-.02em;margin:0 0 18px}
  h1 .t{color:var(--teal)} h1 .a{color:var(--amber)}
  .thesis{font-size:18px;color:#C5CEDC;max-width:64ch;margin:0 0 36px}
  .thesis b{color:var(--text);font-weight:700}
  figure{margin:0 0 14px;background:var(--panel);border:1px solid var(--grid);
    border-radius:10px;padding:14px;overflow-x:auto}
  figure img{display:block;width:100%;height:auto;max-width:100%;border-radius:4px}
  figcaption{font-family:var(--mono);font-size:12px;color:var(--mute);
    padding:10px 4px 2px}
  h2{font-family:var(--mono);font-size:13px;letter-spacing:.18em;
    text-transform:uppercase;color:var(--mute);font-weight:600;
    border-top:1px solid var(--grid);padding-top:22px;margin:46px 0 16px}
  .glyph{color:var(--teal)}
  table{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:14px}
  th,td{text-align:left;padding:11px 12px;border-bottom:1px solid var(--grid)}
  th{color:var(--mute);font-weight:500;font-size:12px;letter-spacing:.08em;
    text-transform:uppercase}
  td.n{text-align:right;color:var(--text)}
  .reg{font-weight:700} .on{color:var(--teal)} .off{color:var(--amber)}
  .lag{color:var(--mute)}
  .row{display:flex;gap:18px;flex-wrap:wrap;margin:8px 0 0}
  .stat{flex:1 1 150px;background:var(--panel);border:1px solid var(--grid);
    border-radius:8px;padding:16px 18px}
  .stat .k{font-family:var(--mono);font-size:11px;letter-spacing:.14em;
    text-transform:uppercase;color:var(--mute)}
  .stat .v{font-family:var(--mono);font-size:26px;font-weight:700;margin-top:6px}
  .stat .v.t{color:var(--teal)} .stat .v.a{color:var(--amber)}
  .stat .d{font-size:13px;color:#AEB9CB;margin-top:4px}
  p.body{font-size:15.5px;color:#C5CEDC;max-width:68ch}
  p.body code{font-family:var(--mono);background:#1A2335;padding:1px 6px;
    border-radius:4px;color:var(--teal);font-size:13.5px}
  .verdict{border-left:3px solid var(--amber);padding:6px 0 6px 18px;
    margin:18px 0;font-size:16px}
  .verdict b{color:var(--amber)}
  ul.next{list-style:none;padding:0;margin:10px 0 0}
  ul.next li{font-size:15px;color:#C5CEDC;padding:8px 0 8px 24px;position:relative}
  ul.next li::before{content:"\\2192";position:absolute;left:0;color:var(--teal);
    font-family:var(--mono)}
  footer{margin-top:54px;font-family:var(--mono);font-size:11px;color:var(--mute);
    border-top:1px solid var(--grid);padding-top:18px}
</style>
<div class="wrap">
  <p class="eyebrow">RS Indicator · cluster discovery · 2020–2026</p>
  <h1>Not five zones.<br>Two axes: <span class="t">strength</span> &times; <span class="a">velocity</span>.</h1>
  <p class="thesis">The 0&ndash;100 <b>truevx_score</b> hides no natural cluster count &mdash; it&rsquo;s a smooth, right-skewed continuum. The real structure is <b>two orthogonal dimensions</b>, and ~40% of stocks sit off the diagonal. But a forward-return test settled which axis pays: <b>strength does, velocity doesn&rsquo;t.</b></p>

  <figure>
    <img alt="Phase portrait of truevx_score level versus score velocity, with a top histogram showing the skewed level distribution under five equal zones, and a main hexbin density split into four regime quadrants." src="data:image/png;base64,__B64__"/>
    <figcaption>// each point is one stock on one day · density is log-scaled · dashed amber lines split the 2&times;2 regime grid at level=60, velocity=0</figcaption>
  </figure>

  <h2><span class="glyph">&#9670;</span> The four regimes</h2>
  <table>
    <thead><tr><th>Regime</th><th>Strength</th><th>Velocity</th><th class="n">Share</th><th>Level-only sees</th></tr></thead>
    <tbody>
      <tr><td class="reg on">Fresh leaders</td><td>high RS</td><td class="on">rising</td><td class="n">31.3%</td><td>top zone</td></tr>
      <tr><td class="reg off">Fading leaders</td><td>high RS</td><td class="off">falling</td><td class="n">20.0%</td><td><i>also</i> top zone &mdash; blind to the roll-over</td></tr>
      <tr><td class="reg off">Early recovery</td><td>low RS</td><td class="off">rising</td><td class="n">19.6%</td><td>bottom zone &mdash; blind to the turn-up</td></tr>
      <tr><td class="reg lag">Laggards</td><td>low RS</td><td class="lag">falling</td><td class="n">29.0%</td><td>bottom zone</td></tr>
    </tbody>
  </table>

  <h2><span class="glyph">&#9670;</span> Why two axes, not one</h2>
  <div class="row">
    <div class="stat"><div class="k">PC1 · strength</div><div class="v t">57%</div><div class="d">level + curvature (corr 0.65) co-move on one axis</div></div>
    <div class="stat"><div class="k">PC2 · velocity</div><div class="v a">34%</div><div class="d">orthogonal — corr to level 0.32, to curvature &minus;0.01</div></div>
    <div class="stat"><div class="k">PC3 · noise</div><div class="v">9%</div><div class="d">negligible — the space is genuinely 2-D</div></div>
  </div>
  <p class="body" style="margin-top:20px">Velocity (<code>score − mean_short</code>) is almost independent of where the score sits. That makes it a real second factor &mdash; not a tiebreaker. It&rsquo;s the dimension that separates a leader still climbing from one rolling over, which the level alone cannot.</p>

  <h2><span class="glyph">&#9670;</span> Tested: do the regimes pay? <span style="color:var(--mute);font-weight:400">(1-month forward, PIT NIFTY500, no look-ahead)</span></h2>
  <table>
    <thead><tr><th>Regime</th><th class="n">mean fwd</th><th class="n">win%</th><th class="n">basket CAGR</th></tr></thead>
    <tbody>
      <tr><td class="reg off">Fading leaders (hi RS · falling)</td><td class="n">2.41%</td><td class="n">55.7</td><td class="n">26.5%</td></tr>
      <tr><td class="reg on">Fresh leaders (hi RS · rising)</td><td class="n">1.95%</td><td class="n">54.6</td><td class="n">23.7%</td></tr>
      <tr><td class="reg lag">Laggards (lo RS · falling)</td><td class="n">1.84%</td><td class="n">53.7</td><td class="n">20.3%</td></tr>
      <tr><td class="reg off">Early recovery (lo RS · rising)</td><td class="n">1.51%</td><td class="n">51.0</td><td class="n">16.4%</td></tr>
    </tbody>
  </table>
  <p class="body" style="margin-top:16px">High-RS regimes beat low-RS ones &mdash; that&rsquo;s the <b>strength</b> axis, i.e. plain momentum. But <b>fresh minus fading = &minus;0.21%/mo, t = &minus;0.91 (not significant)</b>: the velocity sign carries no edge, and is if anything mildly contrarian (the textbook 1-month reversal). Rising vs falling does not predict.</p>

  <h2><span class="glyph">&#9670;</span> Factor test <span style="color:var(--mute);font-weight:400">(Fama&ndash;Macbeth + quintiles, net of 22bps costs)</span></h2>
  <p class="body">Pushed harder than the 4-cell split. Controlling for level, the velocity slope is <b>&minus;0.196%/mo per SD, t = &minus;1.96</b> &mdash; a genuine but borderline <b>reversal</b> tilt (higher velocity &rarr; lower returns). Within high-RS, quintiles are <b>hump-shaped</b>: most-fading 2.09%, mid 2.25%, <b>most-rising just 1.52%</b>. The signal is real-direction &mdash; but it&rsquo;s an <b>avoid</b> rule, not a buy rule.</p>

  <h2><span class="glyph">&#9670;</span> Verdict</h2>
  <div class="verdict"><b>Real direction, no buildable trade.</b> &ldquo;Buy high-RS + fading&rdquo; is refuted: the fading basket matches the cohort gross (+0.05%) and trails it after costs (&minus;0.15%/mo; &minus;0.41%, t=&minus;2.31 with slippage) &mdash; 87%/mo turnover eats it. What&rsquo;s true is narrower: <b>avoid the most-rising / overheated high-RS names</b>; they reverse. The long-short that captures it (net +0.58%/mo, t=1.86) is still under the bar <i>and</i> rides a short leg India retail can&rsquo;t trade.</div>

  <h2><span class="glyph">&#9670;</span> Where this points</h2>
  <ul class="next">
    <li>Don&rsquo;t build a velocity <i>buy</i> overlay &mdash; tested, cost-killed. The win was learning this before building it.</li>
    <li>Keep ranking on strength/RS (the top-N momentum the simulator already runs).</li>
    <li>The one live thread: a negative screen that drops overheated (top-velocity) high-RS names. Suggestive (t&asymp;1.9) but not established &mdash; discount for multiple testing; confirm walk-forward before trusting.</li>
  </ul>

  <footer>// feature space [level, velocity, curvature] · 75,024 stock-dates for geometry · forward-return + factor test, PIT NIFTY500, 77 monthly snapshots, net of 22bps statutory costs · velocity = faint reversal tilt (t&minus;1.96), no buildable long-only edge</footer>
</div>
"""
pathlib.Path(out).write_text(HTML.replace("__B64__", b64), encoding="utf-8")
print("wrote", out, pathlib.Path(out).stat().st_size, "bytes")
