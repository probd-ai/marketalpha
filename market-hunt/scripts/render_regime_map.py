"""Render the TrueVX phase portrait (level x velocity) + level-zone margin.

Instrument-readout styling to match the artifact: ink-navy ground, phosphor-teal
strength axis, amber velocity axis, monospace. Emits a base64 PNG to stdout.
"""
import sys, io, base64
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, LogNorm

GROUND="#0E1422"; PANEL="#121A2B"; TEXT="#E6ECF5"; MUTE="#7C8AA5"
GRID="#243049"; TEAL="#34D6C8"; AMBER="#F2B33D"

df = pd.read_parquet(sys.argv[1])
lv = df["level"].to_numpy(); ve = df["vel"].to_numpy()

cmap = LinearSegmentedColormap.from_list("phos", [GROUND, "#1B3A4B", TEAL])
plt.rcParams.update({"font.family":"monospace","text.color":TEXT,
    "axes.edgecolor":GRID,"xtick.color":MUTE,"ytick.color":MUTE,
    "axes.labelcolor":TEXT,"font.size":11})

fig = plt.figure(figsize=(9.2,7.6), dpi=150, facecolor=GROUND)
gs = fig.add_gridspec(2,1,height_ratios=[1,3.4],hspace=0.06)

# --- top: level distribution with the 5 EQUAL zones shaded --------------
axT = fig.add_subplot(gs[0]); axT.set_facecolor(PANEL)
axT.hist(lv,bins=100,color=TEAL,alpha=0.85,linewidth=0)
zpct=[13.5,16.1,19.0,20.9,30.4]
for i,(a,b) in enumerate(zip([0,20,40,60,80],[20,40,60,80,100])):
    axT.axvspan(a,b,color=AMBER if i%2 else MUTE,alpha=0.07)
    axT.text((a+b)/2, axT.get_ylim()[1]*0.82, f"{zpct[i]:.0f}%",
             ha="center",color=MUTE,fontsize=9)
axT.set_xlim(0,100); axT.set_yticks([])
axT.set_title("the 0–100 score is right-skewed — equal zones hold 13% … 30%, not 20% each",
              color=TEXT,fontsize=11,loc="left",pad=8)
for s in axT.spines.values(): s.set_visible(False)

# --- main: level x velocity phase portrait ------------------------------
ax = fig.add_subplot(gs[1]); ax.set_facecolor(PANEL)
hb = ax.hexbin(lv,ve,gridsize=46,cmap=cmap,norm=LogNorm(),mincnt=1,linewidths=0)
ax.axvline(60,color=AMBER,lw=1.1,ls=(0,(5,4)),alpha=0.8)
ax.axhline(0,color=AMBER,lw=1.1,ls=(0,(5,4)),alpha=0.8)
quad=[(82,42,"FRESH LEADERS","high RS · rising","31.3%",TEAL),
      (82,-46,"FADING LEADERS","high RS · falling","20.0%",AMBER),
      (18,42,"EARLY RECOVERY","low RS · rising","19.6%",AMBER),
      (18,-46,"LAGGARDS","low RS · falling","29.0%",MUTE)]
for x,y,t,sub,pct,c in quad:
    ax.text(x,y,t,ha="center",color=c,fontsize=12.5,fontweight="bold")
    ax.text(x,y-7,sub,ha="center",color=MUTE,fontsize=9)
    ax.text(x,y+9,pct,ha="center",color=c,fontsize=11)
ax.set_xlim(0,100); ax.set_ylim(-70,70)
ax.set_xlabel("STRENGTH  →  truevx_score (level)",color=TEAL,fontsize=11)
ax.set_ylabel("VELOCITY  →  score − mean_short",color=AMBER,fontsize=11)
ax.tick_params(length=0)
for sp in ["top","right"]: ax.spines[sp].set_visible(False)
ax.text(0.5,-0.13,"density (log) of 75,024 stock-dates, NIFTY universe 2020–2026",
        transform=ax.transAxes,ha="center",color=MUTE,fontsize=9)

buf=io.BytesIO(); fig.savefig(buf,format="png",facecolor=GROUND,bbox_inches="tight")
print(base64.b64encode(buf.getvalue()).decode())
