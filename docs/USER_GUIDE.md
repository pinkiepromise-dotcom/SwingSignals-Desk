# SwingSignals Desk v4: Rainbow Robotics (277810)

A dashboard that runs on your own computer. After each trading day it checks the strategies (S1, S2, S4, S5, the S6 ensemble, and the S3 shock filter), tells you
**BUY / SELL / HOLD / WAIT and which strategy triggered it**, and keeps a record of the trades you place yourself.

> The app never places orders. You trade at your broker, then press **I bought** or **I sold** to record the trade.

## 1. Install (once)
1. Install **Python 3.10 or newer** from https://www.python.org/downloads/. On Windows, tick **"Add Python to PATH"**.
2. Unzip this folder somewhere permanent, e.g. `Documents\SwingSignalsApp`.
3. Start it:
   - **Windows:** double-click `start_windows.bat`
   - **Mac / Linux:** open a terminal in the folder and run `./start_mac_linux.sh`

   The first start installs the packages it needs (about 1–2 minutes). The dashboard then opens at **http://127.0.0.1:8765**.

## 2. Make it run automatically
- **Windows:** after the first start, double-click `install_autostart_windows.bat`. From then on it starts silently every time you log in.
  Bookmark http://127.0.0.1:8765. To stop it starting automatically: `uninstall_autostart_windows.bat`.
- **Mac:** run `./install_autostart_mac.sh`.

While your computer is on, the app updates itself at **16:10, 18:40, 20:10, 21:40 and 08:40 KST** (change them in Settings).
It also updates when it starts, re-checks every 20 minutes any source that is behind or failed, and you can press **Update now** at any time.
If investor flows for the day aren't out yet, the signal is shown as *Provisional* and alerts wait until the data is complete.
Run `python check_data.py` for a full data-health report.
Keep the browser tab open (and allow alerts in Settings) to get a desktop notification when a new BUY/SELL signal appears.

## 3. First-time settings (the Settings button)
| Setting | Why |
|---|---|
| Starting cash for this stock | Lets the app show your cash and total account return % |
| Commission % | Your broker's fee (default 0.015%) |
| Sell tax % | KOSDAQ securities transaction tax in 2026 is 0.20% (charged on sells) |
| Risk per trade % / Max position % | Position sizing (v3). Default 2% of capital at risk per trade, at most 50% in one position |
| OpenDART API key | Needed for the DART warning exit. Get a free key at https://opendart.fss.or.kr. Use a **new** key, not the one from the research session |
| KRX short-selling data | Either import the KRX CSV (공매도 종합정보) here, drop it into `data/krx_inbox/`, or enter a data.krx.co.kr login (experimental) |

Everything is stored only on your computer: `config.json` (settings) and `data/journal.db` (your trades).

## 4. How to read the dashboard
- **Headline:** tonight's call. "Act at the open on" is the next trading day.
- **One row per strategy:** its call, the reason, and a checklist of its entry conditions (green = met).
  S4 is the lead strategy, S5 the backup. In v3, S3 no longer trades: it shows **BLOCK** for 5 days after a news/Samsung shock
  day (S2 can't buy then) and **CLEAR** otherwise. S6 is an ensemble vote of S1/S2/S4/S5, shown for comparison.
- **Sizing line** under each strategy: the suggested share count if it triggers, from your capital and the risk settings.
- **Recent form:** each strategy's model result over the last 12 months. Information only: it does not change the lead strategy.
- **HOLD** means the strategy's own model is in a trade. It shows its entry price and its trailing-stop level.
- **My account:** shares held, average cost, market value, open P&L %, realised P&L, fees and tax, cash, and account return.
- **My trades:** every buy and sell, with the price change vs your average cost and the net P&L after fees and tax.
- **Signal log:** every BUY/SELL call. Mark each one as done (log the trade) or skipped.

### New in v2
- **Candlestick chart** with period buttons (3M · 6M · 2026 · 1Y · All) and a strategy picker (S1–S5).
  Each model trade of the chosen strategy is shaded (red = win, blue = loss) with its return. Hover any day for its details.
- **Indicator panels** you can switch on and off: RSI, the S5 score split into its 8 factors, cumulative investor flows
  (foreign / institutions / retail), and short selling (ratio + balance).
- **Trade drill-down:** click a shaded trade (or a row in *Model trades*) to see it day by day: price, RSI, score,
  foreign flow, short ratio, trailing-stop level, DART warnings.
- **DART filings** list: warning-type filings (the ones that trigger exits) are tagged.
- **What-if lab:** change the trailing stop, maximum hold and trading cost, and the test period.
  It shows equity curves and stats for every strategy against buy & hold. It never changes the live signals.

### New in v4 (redesign)
- Tabs along the top: **Signals, Chart, Lab, History, Account, Alerts, Macro, Journal** (keys 1–8). Strategies are listed on the left
  (↑/↓ to switch). Your account is pinned at the bottom left. Market read and data health are on the right (Ctrl+Alt+R hides them).
- **Alerts** tab: set threshold rules and Telegram delivery, then press *Send test alert*. **Journal** tab: notes and stars per trade.
- **Settings → Appearance**: dark mode and Korean price colours (red = up).
- The trading rules and backtest numbers are identical to v3.2.

### New in v3 (see CHANGELOG.md for test results)
- **Change stock** (header): load any 6-digit KRX code. First load downloads ~3 years of prices and investor flows.
  Each stock has its own data folder (`data/tickers/<code>/`) and trade journal. The rules were built on 277810, so other
  stocks show an *Untested stock* warning; robot-news and Samsung inputs are off for them.
- **ATR trailing stop** (3× the 14-day average daily range below the high) replaces the fixed 15% stop.
- **DART severity score:** each filing type has a weight (−3 to +3); the 5-day score drives the exit and is an S5 factor.
- **KOSDAQ mood** (risk-on / neutral / risk-off) shown in Market read.
- **What-if lab switches:** v3 / v2 / Custom buttons plus a switch for every upgrade, so you can compare the versions yourself.
- All upgrades are switches in `CFG` at the top of `app/engine.py`. `V2_CFG` reproduces v2 exactly.

## 5. Data sources
| Data | Source | Updated |
|---|---|---|
| Prices: 277810, KOSDAQ, Doosan Robotics, Robotis | Naver Finance (Yahoo Finance as fallback) | Each run |
| Investor flows (foreign / institution / retail) | Naver Finance | Each run |
| News counts | GDELT | Each run (slow because of rate limits) |
| Filings | OpenDART | Each run (needs your key) |
| Short selling | KRX CSV import, KRX export found in Downloads, or KRX login | When you import |

If a source is out of date, a note appears under "Market read". Short-balance data works with the 2-day delay the model expects.
If you don't import short data for a while, only the "heavy short selling" exit stops working; the other rules keep running.

## 6. Notes
- v3 is built on the frozen v4 rules tested on 2026. With all v3 switches off (`V2_CFG`, or *v2* in the lab) it reproduces all v2 trades exactly.
- Signals use only data from completed trading days (after 15:45 KST).
- Update the holiday list in `app/updater.py` (`HOLIDAYS`) each December. Add new scheduled events to `data/events_manual.csv`.
- This is a research tool, not investment advice.
