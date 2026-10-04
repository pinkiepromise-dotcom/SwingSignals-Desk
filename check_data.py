"""Data health check: run  python check_data.py   (or .venv/bin/python check_data.py)
Prints, for every input the strategies use, the last date on disk, what should exist by now, and the state."""
import os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, 'app'))
import updater
cfg = {}
p = os.path.join(HERE, 'config.json')
if os.path.exists(p): cfg = json.load(open(p, encoding='utf-8'))
code = str(cfg.get('ticker') or updater.RAINBOW)
d = os.path.join(HERE, 'data') if code == updater.RAINBOW else os.path.join(HERE, 'data', 'tickers', code)
m = {}
try: m = json.load(open(os.path.join(d, 'meta.json'), encoding='utf-8'))
except Exception: pass
updater.set_ticker(code, m.get('market', 'KOSDAQ'))
h = updater.source_status(d, has_dart_key=bool(cfg.get('dart_key')), has_krx=bool(cfg.get('krx_id')))
print(f"Stock {code} · now {updater.now_kst():%Y-%m-%d %H:%M} KST · last completed session {updater.last_complete_session()}\n")
print(f"{'Source':16} {'Last on disk':12} {'Expected':10} {'Behind':6}  State     Last checked      Used by")
for k, v in h.items():
    print(f"{v['label']:16} {str(v['last']):12} {str(v['expected'] or '-'):10} {str(v['behind'] if v['behind'] is not None else '-'):6}  "
          f"{v['state']:9} {(v['checked'] or 'never').replace('T', ' ')[:16]:17} {v['uses']}")
bad = [v['label'] for v in h.values() if v['state'] in ('stale', 'setup')]
print('\nAll inputs current.' if not bad else '\nNeeds attention: ' + ', '.join(bad))
