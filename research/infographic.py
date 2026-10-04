import pandas as pd, numpy as np, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt, matplotlib.dates as mdates
from matplotlib.patches import FancyBboxPatch

L = pd.read_csv('replay_2026_daily_log.csv', parse_dates=['date']).set_index('date').loc['2026-01-02':]
T = pd.read_csv('replay_2026_trades.csv', parse_dates=['entry', 'exit'])
BG, INK, MUTED, GRID = '#0f1419', '#e8eaed', '#8b949e', '#222a33'
GREEN, RED, GOLD, BLUE = '#2ecc71', '#ff5c5c', '#f5b841', '#4ea1ff'
COL = {'S1': '#a78bfa', 'S2': '#38bdf8', 'S3': '#f472b6', 'S4': GOLD, 'S5': '#34d399'}
NAME = {'S1': 'S1 Foreign accumulation', 'S2': 'S2 Event calendar', 'S3': 'S3 News/Samsung shock', 'S4': 'S4 Trend rider', 'S5': 'S5 Composite score'}
plt.rcParams.update({'font.family': 'DejaVu Sans', 'text.color': INK, 'axes.labelcolor': MUTED, 'xtick.color': MUTED, 'ytick.color': MUTED})

fig = plt.figure(figsize=(16, 20), facecolor=BG)
gs = fig.add_gridspec(5, 1, height_ratios=[0.9, 4.2, 1.9, 2.4, 2.3], hspace=0.28, left=0.06, right=0.97, top=0.95, bottom=0.03)
fig.text(0.06, 0.975, 'Rainbow Robotics (277810) — 2026 day-by-day replay', fontsize=26, weight='bold')
fig.text(0.06, 0.957, 'Frozen v4 rules · each evening only data public by then · orders at next open · 0.3% cost · 2 Jan → 23 Sep 2026 (179 sessions)',
         fontsize=13, color=MUTED)

# --- KPI tiles
ax0 = fig.add_subplot(gs[0]); ax0.axis('off')
bh = L.close / L.open.iloc[0]
s4 = L.S4_equity
kpis = [('S4 Trend rider', f"{s4.iloc[-1]-1:+.0%}", f"max drawdown {(s4/s4.cummax()-1).min():.0%}", GOLD),
        ('Buy & hold', f"{bh.iloc[-1]-1:+.0%}", f"max drawdown {(bh/bh.cummax()-1).min():.0%}", RED),
        ('S4 trades', f"{(T.strategy=='S4').sum()}", f"{(T[T.strategy=='S4'].ret>0).mean():.0%} winners", INK),
        ('Time in market (S4)', f"{((L.S4=='HOLD')|(L.S4=='SELL')).mean():.0%}", 'cash since 17 Mar', INK),
        ('Crash avoided', '−57%', 'May 13 → Jul 30 fall, all in cash', GREEN)]
for i, (t, v, s, c) in enumerate(kpis):
    x = i / 5 + 0.004
    ax0.add_patch(FancyBboxPatch((x, 0.05), 0.19, 0.9, boxstyle='round,pad=0.005,rounding_size=0.02', fc='#161d25', ec=GRID, transform=ax0.transAxes))
    ax0.text(x + 0.015, 0.72, t, fontsize=12, color=MUTED, transform=ax0.transAxes)
    ax0.text(x + 0.015, 0.33, v, fontsize=30, weight='bold', color=c, transform=ax0.transAxes)
    ax0.text(x + 0.015, 0.12, s, fontsize=10.5, color=MUTED, transform=ax0.transAxes)

# --- Main price chart with S4 decisions
ax = fig.add_subplot(gs[1], facecolor=BG)
ax.plot(L.index, L.close / 1000, color=INK, lw=1.8, zorder=3)
ax.plot(L.index, L.ma60 / 1000, color=BLUE, lw=1, ls='--', alpha=.7, label='MA60')
t4 = T[T.strategy == 'S4']
for _, r in t4.iterrows():
    ax.axvspan(r.entry, r.exit, color=GOLD, alpha=.13, zorder=1)
    ax.scatter(r.entry, r.entry_px / 1000, marker='^', s=180, color=GREEN, ec='white', lw=1, zorder=5)
    ax.scatter(r.exit, r.exit_px / 1000, marker='v', s=180, color=RED, ec='white', lw=1, zorder=5)
notes = [('2026-01-27', 650, 'SELL +44%\noverheated\n(RSI>80, +35% vs MA60)', (0, 70)),
         ('2026-03-03', 846, 'SELL +26%\nDART warning filing\n(Feb 27: P&L change ≥30%)', (-95, 25)),
         ('2026-03-17', 737, 'SELL −8%\nheavy shorting below MA20\n→ cash for the rest of 2026', (70, 10)),
         ('2026-01-13', 450, 'BUY 450k\ntrend + KOSDAQ ok', (-20, -70)),
         ('2026-02-06', 670, 'BUY 670k', (-55, -55))]
for d, y, txt, off in notes:
    ax.annotate(txt, (pd.Timestamp(d), y), xytext=off, textcoords='offset points', fontsize=10.5, color=INK, ha='center',
                bbox=dict(boxstyle='round,pad=0.35', fc='#1c2530', ec=GOLD, lw=.8), arrowprops=dict(arrowstyle='-', color=GOLD, lw=.8))
# missed/avoided areas
ax.annotate('Rally to 878k missed:\nRSI >80 & KOSDAQ below MA20\n→ entry rules never fired', (pd.Timestamp('2026-05-13'), 878), xytext=(60, 5),
            textcoords='offset points', fontsize=10.5, color=MUTED, ha='left', bbox=dict(boxstyle='round,pad=0.35', fc='#1c2530', ec=MUTED, lw=.6),
            arrowprops=dict(arrowstyle='-', color=MUTED, lw=.6))
ax.annotate('−57% slide sat out\n(price below MA60)', (pd.Timestamp('2026-07-10'), 420), xytext=(0, -65), textcoords='offset points',
            fontsize=11, color=GREEN, ha='center', weight='bold', bbox=dict(boxstyle='round,pad=0.35', fc='#12251b', ec=GREEN, lw=.8),
            arrowprops=dict(arrowstyle='-', color=GREEN, lw=.8))
ax.set_ylim(300, 1010); ax.set_ylabel('Price (₩ thousand)', fontsize=11)
ax.set_title('S4 decisions on the price  ▲ buy (next open)  ▼ sell  ▒ holding', loc='left', fontsize=15, color=INK, pad=10, weight='bold')
ax.legend(loc='upper right', frameon=False, labelcolor=MUTED)

# --- Decision lanes for all strategies
ax2 = fig.add_subplot(gs[2], facecolor=BG, sharex=ax)
for i, k in enumerate(['S5', 'S4', 'S3', 'S2', 'S1']):
    ax2.axhline(i, color=GRID, lw=8, zorder=0, solid_capstyle='butt')
    for j, (_, r) in enumerate(T[T.strategy == k].sort_values('entry').iterrows()):
        c = GREEN if r.ret > 0 else RED
        ax2.plot([r.entry, r.exit], [i, i], color=c, lw=9, solid_capstyle='butt', zorder=2)
        ax2.text(r.entry + (r.exit - r.entry) / 2, i + (0.33 if j % 2 == 0 else -0.45), f"{r.ret:+.0%}", fontsize=9, ha='center', color=c)
    e = L[k + '_equity'].iloc[-1] - 1
    ax2.text(1.0, i, f" {e:+.0%}", transform=ax2.get_yaxis_transform(), va='center', fontsize=12, weight='bold', color=COL[k])
ax2.set_yticks(range(5)); ax2.set_yticklabels([NAME[k] for k in ['S5', 'S4', 'S3', 'S2', 'S1']], fontsize=11)
ax2.set_ylim(-.6, 4.7)
ax2.set_title('All 5 strategies — every trade (green = win, red = loss) · total return at right', loc='left', fontsize=15, weight='bold', pad=8)

# --- Equity curves
ax3 = fig.add_subplot(gs[3], facecolor=BG, sharex=ax)
ax3.plot(L.index, (bh - 1) * 100, color=RED, lw=2.2, label=f'Buy & hold {bh.iloc[-1]-1:+.0%}')
for k in ['S1', 'S2', 'S3', 'S5', 'S4']:
    e = L[k + '_equity']; ax3.plot(L.index, (e - 1) * 100, color=COL[k], lw=3 if k == 'S4' else 1.4, label=f'{NAME[k]} {e.iloc[-1]-1:+.0%}')
ax3.axhline(0, color=MUTED, lw=.6)
ax3.set_ylabel('Return %'); ax3.legend(ncol=3, frameon=False, labelcolor=INK, fontsize=10.5, loc='upper left')
ax3.set_title('Equity curves (out-of-sample, rules not re-tuned)', loc='left', fontsize=15, weight='bold', pad=8)

# --- Signal context: short ratio + composite score
ax4 = fig.add_subplot(gs[4], facecolor=BG, sharex=ax)
ax4.bar(L.index, L.score, color=[GREEN if s >= 4 else (RED if s <= 0 else '#3a4654') for s in L.score], width=1.0)
ax4.axhline(4, color=GREEN, lw=.8, ls='--'); ax4.axhline(0, color=RED, lw=.8, ls='--')
ax4.set_ylabel('S5 score'); ax4.set_ylim(-5, 7)
ax4b = ax4.twinx(); ax4b.plot(L.index, L.short_ratio, color=GOLD, lw=1.1, alpha=.85); ax4b.set_ylabel('Short ratio % (gold)', color=GOLD); ax4b.tick_params(colors=GOLD)
ax4.set_title('Daily evidence: composite score (buy ≥4 / sell ≤0) and short-selling ratio', loc='left', fontsize=15, weight='bold', pad=8)

for a in [ax, ax2, ax3, ax4]:
    a.grid(color=GRID, lw=.6); [s.set_visible(False) for s in a.spines.values()]
    a.xaxis.set_major_locator(mdates.MonthLocator()); a.xaxis.set_major_formatter(mdates.DateFormatter('%b'))
ax4b.spines[:].set_visible(False)
fig.text(0.06, 0.012, 'Look-ahead guards: signal at close t → trade at open t+1 · short balance lagged 2 days (KRX T+2) · only pre-announced events · rolling z-scores · v4 thresholds frozen. '
         'Leverage = retail-flow proxy. Not investment advice.', fontsize=10, color=MUTED)
fig.savefig('replay_2026_infographic.png', dpi=110, facecolor=BG)
print('ok')
