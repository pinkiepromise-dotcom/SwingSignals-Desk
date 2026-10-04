# Research (Generation 2)

| File | What it does |
|---|---|
| `strategies.py` | Builds S1–S5 on 2024–2025 data (training) and backtests them: next-open fills, 0.3% round-trip cost, 15% trailing stop, 40-day max hold |
| `replay.py` | Walk-forward replay of the FROZEN v4 rules on 2026: each evening sees only data public by then, trades at the next open |
| `infographic.py` | Draws the 2026 replay dashboard (`figures/replay_2026_infographic.png`) |
| `data/` | Master table (485 days × 53 columns), features, investor flows, short selling, GDELT news, DART filings, event calendar |
| `results/` | Training summary, per-trade logs, the 2026 daily decision log (180 rows) |

Note: scripts were run in a research sandbox; some input paths (e.g. `../swing/`) refer to that layout. Copy the CSVs from `data/` next to the script to re-run.
The app (`app/engine.py`) carries later v3 upgrades (ATR stop, DART severity, S3 as filter), so its 2026 numbers differ slightly from `results/` (frozen v4 research rules).
