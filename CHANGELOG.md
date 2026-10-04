# SwingSignals Desk: changelog

## v4.0 (2026-10-04): redesigned app (display only, trading rules unchanged)
- **New layout in three panels**: a strategy sidebar with an Account card pinned at the bottom, the main view in the middle, and a
  collapsible inspector on the right (Market read, Data health, Recent signals, DART). Toggle the inspector with Ctrl+Alt+R (⌃⌘R on Mac).
- **Eight tabs**: Signals · Chart · Lab · History · Account · Alerts · Macro · Journal. Keys 1–8 switch tabs, ↑/↓ (or j/k) switch strategy,
  Esc closes a sheet. The app remembers your tab, strategy, chart panels and panel heights.
- **Signals**: verdict bar floating over the price, then Overview / Conditions / Score / Flows. Overview has a strength ring, model return,
  setup-strength sparkline, 2026 record and recent form. Conditions shows pass/fail marks, graded bars and the gap to each threshold.
- **Chart**: colour-coded strategy capsules, a setup-strength panel (new), a scrubber with a glass tooltip, drag handles to resize panels,
  and candles that grow in on first draw. Clicking a trade opens a day-by-day sheet. Its stop column now follows the ATR stop when v3 uses it.
- **Lab**: settings grouped as Exit Rules / Entry Filters / Cost Model / Signals Version. The best strategy row is marked.
  New "Risk-sized" column. Same /api/whatif, same frozen v3 defaults.
- **Alerts (new)**: threshold rules on S5 score, setup strengths, S6 vote, RSI, close, foreign flow, KOSDAQ gap, days to event, DART score.
  Checked on the server after each final evening update, at most once per rule per day. Sent through desktop and/or Telegram.
  Three starter rules (S5 score ≥ 4 is on).
- **Macro (new)**: KOSDAQ level and regime, market RSI, foreign flow, stock vs robot peers, volatility (ATR), next event.
  Foreign flow is for this stock only.
- **Journal (new)**: notes and 0–5 stars on every model trade and your own trades. Saves automatically to journal.db.
- **Appearance**: light / dark / follow system, plus a Korean price-colour option (red = up, blue = down) in Settings.
- **Engine/server**: display-only chart fields for Macro (kq_ma20/60, kq_rsi, peer5, atr_pct, ret5). New tables alerts_fired and journal.
  New endpoints /api/alerts and /api/journal. Signals, trades and backtests are unchanged: the 2026 Lab run at frozen v3 still gives
  S1 +64.9%, S2 +53.7%, S4 +69.6%, S5 +76.6%, S6 +72.9%.

## v3.2 (2026-10-02): graded signal inputs (Step 6)
- **Every condition now has a strength from 0 to 1**, not just a yes/no. Example: S4's "RSI between 50 and 72" is 1.0 between 50 and 72,
  goes down in a straight line to 0 at 44 or 78, and is 0 outside. Distances to the 20- and 60-day averages are measured in ATRs, so
  "just above" means the same in quiet and wild markets. The KOSDAQ check ramps from 1% below its 20-day average to 1% above.
- **Setup strength per strategy** (S1, S2, S4, S5, S6) = the average of its graded conditions, shown in each lane with a 20-day mini chart
  and a trend line ("▲ building (63% 5 days ago)"). This shows how close a setup is and whether it is getting closer, before it fires.
- **Condition pills show a small bar**: how far each condition is toward "clearly met". S5 factors show −1…+1, centred.
- **Lab switches (off by default, tested):**
  | | S1 24-25 / 2026 | S2 | S4 | S5 |
  |---|---|---|---|---|
  | Current rules | +163% / +65% | +65% / +54% | +196% / +70% | +123% / +77% |
  | Graded entries (strength ≥ 80%, no condition < 20%) | +161% / +23% | +79% / +44% | +145% / +24% | — |
  | Graded S5 score (buy ≥ 3, sell ≤ 0) | — | — | — | +201%* / +39% |
  *The best of 16 settings tried on 2024-25, so expect it to look better than it is.
  Graded entries fire earlier, on weaker setups, which means more trades and worse results; the 2026 check confirms it.
  **Live buy/sell rules stay exactly as before**: the default settings reproduce every v3.1 trade.
- Setup strength did not tell winners from losers among past trades: every S1/S2/S4 entry already had a strength of 0.78–1.0.
  So it is not used to size positions either.

## v3.1.2 (2026-10-02): connection errors
- **Plain error messages.** "Max retries exceeded" errors now say what went wrong and what to do: DNS/no internet, certificate rejected by
  antivirus HTTPS scanning, firewall blocking, timeout, proxy. The full message is shown instead of being cut off at 90 characters.
- **Offline detection.** Before each update the app checks that Naver answers. If not (e.g. right after waking from sleep, before Wi-Fi is back),
  it shows one "No internet connection" banner and retries every 2 minutes instead of marking every source as failed.
- **Automatic retries.** Every request retries up to 3 times with back-off on connection errors and 5xx answers.
- **System certificates.** Uses the Windows/macOS certificate store (`truststore`), so antivirus software that scans HTTPS (AhnLab V3, Kaspersky,
  Avast, ESET…) or a company proxy no longer breaks every source.
- **Settings → Test connection.** Checks Naver, OpenDART, KRX and GDELT, checks whether DART accepts your key, and checks pykrx for KRX login.

## v3.1.1 (2026-10-02): Settings save fix
- **Starting cash didn't save when typed with commas.** The box was a browser number field. Typing `10,000,000` made the browser send an empty
  value, and the server skipped it, so nothing was stored. It's now a text field: `10000000`, `10,000,000`, `1000만` and `1.5억` all work, and it
  reformats with commas.
- **Bad input now shows an error.** Wrong values (e.g. `abc`, `25:99`) give a red "Not saved: …" message instead of a silent server error.
  Saving also shows "Saving…" and then a confirmation listing what was stored (cash, DART key ✓, KRX login ✓).
- **Keys and passwords:** after saving, these boxes are deliberately blank and the label shows **(saved)**. Leaving them blank keeps the saved value.
- **No lost typing:** clicking outside the dialog or pressing Esc with unsaved changes now asks before discarding. Enter saves.
- **Safer config file:** `config.json` is written atomically and read back to confirm. If the folder is read-only (e.g. running from inside the
  zip) the error says so. The dialog shows the full config.json path.

## v3.1 (2026-10-01): data updates you can trust
Signal rules are unchanged (same `CFG` / `V2_CFG`). Only how data is fetched, checked and scheduled changed.

**Why updates looked broken in v3**
1. **Investor flows:** Naver posts the final foreign/institution/retail numbers in the evening (not there at 18:45 on 2026-10-01).
   It first shows a provisional row with no close; v3 saved that row and then never fetched again because the schedule ended at 18:40.
2. **News:** GDELT allows 1 request per 5 s per IP and answers HTTP 429. v3 threw away all queries if one failed, so 3 good answers were lost.
3. **Retries were dropped:** the hourly retry only ran at minute :30, ran only for sources that raised an error (not ones that ran fine but
   returned old data), and any update requested while another was running was silently skipped.
4. **The status bar marked every source red unless it had today's date**, even sources that aren't out yet (short balance is T+2).
5. **KRX import:** the short balance of the last 2 days is 0 in every export (T+2), and a new import overwrote real balances with that 0.
   On pandas 3 the import failed completely (date column type error).
6. **Holidays:** hard-coded for 2026 only; from 2027 every holiday would count as a trading day.

**Fixes**
- **Scheduler:** updates at 16:10, 18:40, 20:10, 21:40 and 08:40 KST (old default upgraded automatically). Every 20 min between 08:00 and 23:30
  it re-checks any source that is *behind schedule* or errored, not only errored ones. Requests made during a run are queued, not dropped.
- **Signals wait for complete data:** if prices or flows for the session are missing, the call is shown as *Provisional* and is logged/alerted
  only once flows arrive (or at 21:30 at the latest, `final_after_kst`).
- **Health model** (`updater.source_status`, `/api/health`): each source has an expected lag and publish time.
  States are ● ok · ◷ not published yet · ◐ behind (auto re-check) · ○ stale · ◐ needs setup. Hover a source to see what it feeds.
- **GDELT:** requests spaced ≥ 7 s apart with back-off on 429; each query is saved as soon as it succeeds; scheduled runs skip GDELT
  if it was refreshed < 45 min ago (Update now forces it).
- **Flows:** provisional rows without a close are ignored; an empty answer is reported as an error.
- **DART:** newly found warning filings send an alert right away (same-day exit rule).
- **KRX short data:** a 0/blank balance no longer overwrites a real one. New: the app picks up KRX exports (`data_XXXX_YYYYMMDD.csv`) from
  your Downloads folder, but only if the file's short volumes match the stored ones on ≥ 3 days (so another stock's file is rejected).
  Turn off with `watch_downloads: false`. Fixed the pandas 3 import error.
- **Holidays:** years not in `HOLIDAYS` use `exchange_calendars` (XKRX), added to requirements.
- **`check_data.py`:** one-command report of every input: last date, expected date, sessions behind, last check.

Tested on 2026-10-01 at 18:40–19:05 KST against live Naver, GDELT and a real KRX export: prices/KOSDAQ/peers updated to 10-01,
flows to 09-30 (10-01 not yet posted, shown as "due later" and signals held as provisional), GDELT 2 of 4 queries succeeded and were saved,
KRX import and the zero-balance protection worked. DART and the KRX login path were not tested (no key/account here).

## v3 (2026-10-01)
Every change is a switch in `CFG` (top of `app/engine.py`). `V2_CFG` turns them all off and reproduces v2 exactly
(16 trades in 2026, 54 since Mar 2024, checked trade by trade).

Results below: return / max drawdown / trades, 100% of capital per trade.
"Mar 2024 → today" includes 2026; the weights and settings were chosen on data before 2026, so the 2026 column is the fairer test.
Trade counts are small (1–15), so read the differences as directional.

| Strategy | v2, Mar 2024 → today | v3, Mar 2024 → today | v2, 2026 YTD | v3, 2026 YTD |
|---|---|---|---|---|
| S4 Trend rider (lead) | +426.8% / −20.5% / 15 | +426.8% / −20.5% / 15 | +69.6% / −10.3% / 5 | +69.6% / −10.3% / 5 |
| S5 Composite score | +233.2% / −36.4% / 12 | +282.6% / −30.6% / 10 | +67.8% / −13.4% / 3 | +76.6% / −13.4% / 2 |
| S2 Event run-up | +113.6% / −24.0% / 14 | +145.0% / −13.9% / 12 | +53.7% / −11.0% / 4 | +53.7% / −11.0% / 4 |
| S1 Foreign accumulation | +252.0% / −17.8% / 6 | +334.3% / −17.8% / 6 | +33.7% / −13.4% / 1 | +64.9% / −13.4% / 1 |
| S3 News shock | −9.8% / −53.6% / 7 | filter only | −17.6% / −27.5% / 3 | filter only |
| S6 Ensemble (new) | n/a | +425.9% / −26.8% / 11 | n/a | +72.9% / −13.4% / 3 |
| Buy & hold | +168.8% / −57.6% | | −7.3% / −57.6% | |

### Kept (on by default)
- **S3 → shock filter** (`s3_filter`, `s3_targets=('S2',)`, 5 days). S3 no longer trades; a shock day blocks new S2 buys.
  S2 drawdown −24% → −14%. Blocking S4/S5 as well was tested and made them worse.
- **DART severity score** (`dart_mode='severity'`, `dart_exit_at=-1`, `dart_factor=True`). Filing weights −3…+3 summed over 5 days;
  exit S1/S4/S5 at ≤ −1; the score is also a 10th S5 factor. Exiting only at ≤ −3 was tested and was worse (late exits).
- **ATR trailing stop** (`stop_mode='atr'`, `atr_mult=3`). Equal or better than the 15% stop for every strategy; results barely
  change between 2.5× and 4×, so 3× isn't a lucky pick.
- **Position sizing** (`risk_pct=0.02`, `max_pos_pct=0.5`, editable in Settings). Suggested shares per signal. At 2% risk positions
  average 9–17% of capital, so returns and drawdowns are roughly a tenth of the full-position numbers. Raise it if you size bigger.
- **S6 ensemble** (`s6`, fixed weights from pre-2026 trades: S1 38% · S5 29% · S4 21% · S2 12%; buy ≥ 0.40, sell ≤ 0.20). Shown for
  comparison; it did not beat S4 on drawdown, so S4 stays the lead. Monthly re-weighting was tested and was the worst variant.
- **Recent-form ranking.** Information only. "Follow the 12-month leader" was tested: +71% (Sep 2024–2025), +38% (2026), worse
  than every single strategy.
- **Product:** ticker picker with per-stock data and journal, what-if lab switches with v2 / v3 / Custom buttons,
  KOSDAQ mood in Market read, DART weights in the filings list, BLOCK / CLEAR status for S3.

### Built but off by default (tested worse)
- **Short-squeeze factor in S5** (`squeeze`). S5 drawdown −36% → −43%.
- **3-state KOSDAQ gate** (`regime_mode` lenient / strict / v2_no_riskoff, `regime_score`). None beat "KOSDAQ above its 20-day average".

### Not done yet
- Accumulation detector, ETF flow, GDELT velocity and ELW IV modules: need the five `.py` files re-uploaded; the accumulation
  detector also needs a KRX login so it stops falling back to random data.
- AI integration (AI-written signal summaries etc.): needs an API key / model choice.
