"""SwingSignals Desk — local dashboard for Rainbow Robotics (277810) signals + your manual trade journal.
Run:  python app/server.py      then open http://127.0.0.1:8765"""
import os, re, sys, json, sqlite3, threading, time, traceback, datetime as dt, webbrowser
import pandas as pd, requests
from flask import Flask, jsonify, request, send_from_directory

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BASE_DATA = os.path.join(ROOT, 'data')       # Rainbow (277810) data lives here; other stocks in data/tickers/<code>/
DATA = BASE_DATA
DB = os.path.join(DATA, 'journal.db')
CFG = os.path.join(ROOT, 'config.json')
sys.path.insert(0, HERE)
import engine, updater

DEFAULT_CFG = dict(dart_key='', krx_id='', krx_pw='', starting_cash=0, commission_pct=0.015, sell_tax_pct=0.20,
                   port=8765, open_browser=True, update_times_kst=['16:10', '18:40', '20:10', '21:40', '08:40'],
                   watch_downloads=True, final_after_kst='21:30',
                   telegram_token='', telegram_chat_id='', desktop_alerts=True, risk_pct=2.0, max_pos_pct=50.0,
                   ticker='277810')
app = Flask(__name__, static_folder=os.path.join(HERE, 'static'))
STATE = dict(offline_since=None, retry_net_at=None, result=None, last_run=None, running=False, sources={}, log=[], quote=None, quote_at=0, queue=[], health={})
OLD_TIMES = [['16:10', '18:40']]          # v3 default: upgraded automatically to the new schedule
LOCK = threading.Lock()


def cfg():
    c = dict(DEFAULT_CFG)
    if os.path.exists(CFG): c.update(json.load(open(CFG, encoding='utf-8')))
    if c.get('update_times_kst') in OLD_TIMES: c['update_times_kst'] = DEFAULT_CFG['update_times_kst']
    return c


def save_cfg(c):
    """Atomic write (temp file + rename) so a crash never leaves a half-written config.json; raises if the folder is read-only."""
    tmp = CFG + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f: json.dump(c, f, indent=2, ensure_ascii=False); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, CFG)


def parse_num(v, label):
    """Accepts 10000000, 10,000,000, '10 000 000', 1000만, 1.5억, '₩10,000,000', 0.2%."""
    if isinstance(v, (int, float)): return float(v)
    t = str(v).strip().replace(',', '').replace(' ', '').replace('₩', '').replace('원', '').replace('%', '').replace('KRW', '').replace('krw', '')
    mult = 1
    for suf, m in (('억', 1e8), ('만', 1e4), ('천', 1e3)):
        if t.endswith(suf): t, mult = t[:-1], m; break
    try: return float(t) * mult
    except ValueError: raise ValueError(f"{label}: '{v}' is not a number")


def log(msg):
    line = f"{updater.now_kst():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True); STATE['log'] = (STATE['log'] + [line])[-200:]
    with open(os.path.join(DATA, 'app.log'), 'a', encoding='utf-8') as f: f.write(line + '\n')


def ticker_dir(code):
    return BASE_DATA if code == updater.RAINBOW else os.path.join(BASE_DATA, 'tickers', code)


def activate(code):
    """Switch the whole app (data folder, journal, updater) to another stock. Each stock keeps its own journal."""
    global DATA, DB
    d = ticker_dir(code)
    if not os.path.exists(os.path.join(d, 'px.csv')):
        updater.bootstrap_ticker(d, code, log)
    m = engine.meta(d); updater.set_ticker(code, m.get('market', 'KOSDAQ'))
    DATA, DB = d, os.path.join(d, 'journal.db'); init_db()
    with LOCK: STATE['result'] = None; STATE['sources'] = {}; STATE['quote'] = None; STATE['quote_at'] = 0; STATE['health'] = {}
    return m


# ---------------- database ----------------
def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c


def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS signals(id INTEGER PRIMARY KEY, signal_date TEXT, act_on TEXT, strategy TEXT, action TEXT,
            close REAL, reasons TEXT, status TEXT DEFAULT 'pending', created_at TEXT, UNIQUE(signal_date, strategy));
        CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY, trade_date TEXT, side TEXT, qty INTEGER, price REAL,
            fee REAL, tax REAL, strategy TEXT, signal_id INTEGER, note TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS cash(id INTEGER PRIMARY KEY, flow_date TEXT, amount REAL, note TEXT);
        CREATE TABLE IF NOT EXISTS alerts_fired(rule_id TEXT, date TEXT, value REAL, msg TEXT, created_at TEXT, PRIMARY KEY(rule_id, date));
        CREATE TABLE IF NOT EXISTS journal(key TEXT PRIMARY KEY, note TEXT, stars INTEGER, updated_at TEXT);
        """)


def record_signals(res):
    if not res.get('final', True):
        log('Signals are provisional (waiting for: ' + ', '.join(res.get('missing', [])) + '); not logged or alerted yet'); return
    ctx = res['context']; act_on = updater.next_session(ctx['date'])
    new = []; lines = []
    with db() as c:
        for k in res['calls']:
            if k['action'] in ('BUY', 'SELL'):
                cur = c.execute("INSERT OR IGNORE INTO signals(signal_date, act_on, strategy, action, close, reasons, created_at) VALUES(?,?,?,?,?,?,?)",
                                (ctx['date'], act_on, k['strategy'], k['action'], ctx['close'], '; '.join(k['reasons']), updater.now_kst().isoformat()))
                if cur.rowcount:
                    new.append(f"{k['action']} {k['strategy']}")
                    lines.append(f"{'🔴 BUY' if k['action'] == 'BUY' else '🔵 SELL'} {k['strategy']} {k['name']}: " + '; '.join(k['reasons'])[:220])
    if new:
        log('NEW SIGNAL: ' + ', '.join(new))
        # only alert for signals from the latest completed session (not when replaying old history on first start)
        if ctx['date'] >= updater.last_complete_session():
            text = (f"SwingSignals {updater.CODE} (close {ctx['date']}: {ctx['close']:,.0f})\n" + '\n'.join(lines) +
                    f"\nAct at the open on {act_on}. Log your fill in the dashboard.")
            threading.Thread(target=send_alerts, args=('SwingSignals: ' + ', '.join(new), text), daemon=True).start()


# ---------------- custom alert rules (Alerts tab) ----------------
ALERT_METRICS = {
    's5_score': ('S5 composite score', ''), 'graded_score': ('Graded S5 score', ''),
    'strength_S4': ('S4 Trend rider setup strength', '%'), 'strength_S5': ('S5 Composite setup strength', '%'),
    'strength_S2': ('S2 Event run-up setup strength', '%'), 'strength_S1': ('S1 Foreign accumulation setup strength', '%'),
    's6_vote': ('S6 ensemble vote', ''), 'rsi': ('RSI (14)', ''), 'close': ('Closing price', 'KRW'),
    'foreign5': ('Foreign 5-day buying', '% of volume'), 'kosdaq_gap': ('KOSDAQ vs its 20-day average', '%'),
    'days_to_event': ('Days to next scheduled event', 'days'), 'dart_score': ('DART 5-day filing score', ''),
}
DEFAULT_ALERTS = [dict(id='a1', metric='s5_score', op='>=', value=4.0, on=True),
                  dict(id='a2', metric='strength_S4', op='>=', value=90.0, on=False),
                  dict(id='a3', metric='rsi', op='>=', value=80.0, on=False)]


def metric_values(res):
    if not res: return {}
    c = res['context']
    out = dict(s5_score=c.get('score'), graded_score=c.get('graded_score'), s6_vote=c.get('s6_vote'), rsi=c.get('rsi'),
               close=c.get('close'), foreign5=c.get('foreign_5d_pct_vol'), dart_score=c.get('dart_score'), days_to_event=c.get('days_to_event'))
    if c.get('kosdaq') and c.get('kosdaq_ma20'): out['kosdaq_gap'] = round((c['kosdaq'] / c['kosdaq_ma20'] - 1) * 100, 2)
    for k in res['calls']:
        if k.get('strength') is not None: out['strength_' + k['strategy']] = round(k['strength'] * 100, 1)
    return {k: v for k, v in out.items() if v is not None}


def check_alerts(res):
    """Fires each enabled rule at most once per session date, only for the latest completed session and final data."""
    if not res or not res.get('final', True): return
    date = res['context']['date']
    if date < updater.last_complete_session(): return
    vals = metric_values(res); hits = []
    with db() as con:
        for r in cfg().get('alert_rules', DEFAULT_ALERTS):
            v = vals.get(r.get('metric'))
            if not r.get('on') or v is None or r.get('metric') not in ALERT_METRICS: continue
            if (v >= r['value']) if r.get('op') == '>=' else (v <= r['value']):
                msg = f"{ALERT_METRICS[r['metric']][0]} is {v:g} (rule: {r['op']} {r['value']:g})"
                cur = con.execute("INSERT OR IGNORE INTO alerts_fired(rule_id, date, value, msg, created_at) VALUES(?,?,?,?,?)",
                                  (r['id'], date, v, msg, updater.now_kst().isoformat(timespec='minutes')))
                if cur.rowcount: hits.append(msg)
    if hits:
        log('ALERT: ' + '; '.join(hits))
        threading.Thread(target=send_alerts, args=('SwingSignals alert', f"SwingSignals {updater.CODE} (close {date})\n" + '\n'.join(hits)), daemon=True).start()


def send_alerts(title, text):
    c = cfg(); out = {}
    if c.get('desktop_alerts', True): out['desktop'] = updater.desktop_notify(title, text.split('\n', 1)[-1][:230])
    if c.get('telegram_token') and c.get('telegram_chat_id'):
        try: out['telegram'] = updater.send_telegram(c['telegram_token'], c['telegram_chat_id'], text)
        except Exception as e: out['telegram'] = str(e); log(f'Telegram failed: {e}')
    log(f'Alerts sent: {out}'); return out


# ---------------- pipeline ----------------
ALL_SOURCES = ('prices', 'flows', 'dart', 'short', 'news')


def run_update(sources=ALL_SOURCES, force_news=False):
    sources = tuple(sources)
    if updater.CODE != updater.RAINBOW: sources = tuple(s for s in sources if s != 'news')
    with LOCK:
        if STATE['running']:                       # don't drop it: run it right after the current update
            q = STATE['queue']; [q.append(s) for s in sources if s not in q]; return
        STATE['running'] = True
    c = cfg()
    try:
        while True:
            steps = dict(prices=lambda: updater.update_prices(DATA, log), flows=lambda: updater.update_flows(DATA, log),
                         dart=lambda: updater.update_dart(DATA, c.get('dart_key'), log),
                         short=lambda: updater.update_short(DATA, c.get('krx_id'), c.get('krx_pw'), log),
                         news=lambda: updater.update_news(DATA, log, force=force_news))
            if STATE['result'] is None: evaluate()          # show saved data immediately
            if not updater.online():                       # e.g. just woke from sleep: don't flood every source with errors
                time.sleep(20)
                if not updater.online():
                    now = updater.now_kst().isoformat(timespec='minutes'); STATE['offline_since'] = STATE.get('offline_since') or now
                    log('No internet connection (Naver unreachable): update postponed, retrying in 2 minutes')
                    with LOCK: q = STATE['queue']; [q.append(s) for s in sources if s not in q]
                    STATE['retry_net_at'] = time.time() + 120
                    break
            STATE['offline_since'] = None
            code0 = updater.CODE
            if 'short' in sources and c.get('watch_downloads', True):
                try: updater.scan_downloads(DATA, log)
                except Exception as e: log(f'downloads scan: {e}')
            for s in sources:
                if s == 'news': evaluate()                     # news (GDELT) is slow & rate-limited: evaluate before it
                if s == 'news' and updater.CODE != updater.RAINBOW: continue
                STATE['step'] = s
                try:
                    r = steps[s](); STATE['sources'][s] = dict(ok=True, at=updater.now_kst().isoformat(timespec='minutes'), msg='updated')
                    if s == 'dart' and r: dart_alert(r)
                except Exception as e:
                    msg = updater.net_reason(e) if isinstance(e, requests.exceptions.RequestException) else str(e)
                    STATE['sources'][s] = dict(ok=False, at=updater.now_kst().isoformat(timespec='minutes'), msg=msg[:300])
                    log(f'{s} update failed: {e}')
            if updater.CODE == code0: evaluate()
            with LOCK:
                if not STATE['queue']: break
                sources = tuple(STATE['queue']); STATE['queue'] = []
    finally:
        STATE['running'] = False; STATE['step'] = None


def dart_alert(filings):
    """New DART filings are alerted the moment they are found, because warning filings mean same-day exits."""
    rows = []
    for f in filings:
        w, lab = engine.dart_weight(str(f['report_nm']))
        if w < 0: rows.append(f"{f['date']} {f['report_nm']} ({lab}, weight {w:+d})")
    if rows:
        log('DART warning filing: ' + '; '.join(rows))
        threading.Thread(target=send_alerts, args=('SwingSignals: DART warning filing',
                         f"SwingSignals {updater.CODE}: new warning filing\n" + '\n'.join(rows) + "\nS1/S4/S5 exit rule may trigger at tonight's close."), daemon=True).start()


def health():
    c = cfg()
    h = updater.source_status(DATA, has_dart_key=bool(c.get('dart_key')), has_krx=bool(c.get('krx_id')))
    STATE['health'] = h
    return h


def evaluate():
    try:
        res = engine.evaluate(DATA, drop_after=updater.last_complete_session())
        res['act_on'] = updater.next_session(res['context']['date'])
        res['freshness'] = data_freshness(res['context']['date'])
        h = health(); res['health'] = h
        core_ok = all(h.get(k, {}).get('state') in ('ok', None) for k in ('prices', 'flows'))
        late = updater.now_kst().strftime('%H:%M') >= cfg().get('final_after_kst', '21:30') or res['context']['date'] < updater.last_complete_session()
        res['final'] = bool(core_ok or late)
        res['missing'] = [h[k]['label'] for k in ('prices', 'flows') if h.get(k, {}).get('state') not in ('ok', None)]
        res['lagging'] = [v['label'] for k, v in h.items() if k not in ('prices', 'flows') and v['state'] in ('behind', 'stale', 'setup')]
        add_sizing(res)
        with LOCK: STATE['result'] = res; STATE['last_run'] = updater.now_kst().isoformat(timespec='seconds')
        record_signals(res)
        try: check_alerts(res)
        except Exception as e: log(f'alert rules failed: {e}')
        log(f"Evaluated {res['context']['date']}: " + ', '.join(f"{k['strategy']} {k['action']}" for k in res['calls']))
    except Exception:
        log('evaluate failed: ' + traceback.format_exc()[-600:])


def add_sizing(res):
    """Suggested share count for each strategy's next buy, from the account's capital and the risk settings."""
    c = cfg(); ec = dict(engine.CFG, risk_pct=float(c.get('risk_pct', 2.0)) / 100, max_pos_pct=float(c.get('max_pos_pct', 50.0)) / 100)
    try:
        a = account(); capital = a.get('total_value') or float(c.get('starting_cash') or 0)
    except Exception:
        capital = float(c.get('starting_cash') or 0)
    ctx = res['context']; z = engine.size_position(capital, ctx['close'], ctx.get('atr'), ec)
    res['sizing'] = dict(capital=capital or None, risk_pct=ec['risk_pct'], max_pos_pct=ec['max_pos_pct'],
                         stop_mode=ec.get('stop_mode'), atr_mult=ec.get('atr_mult'), suggestion=z)
    for k in res['calls']:
        k['sizing'] = z if k['action'] in ('BUY', 'WAIT') and k['strategy'] != 'S3' else None


def data_freshness(sig_date):
    out = {}
    for name, f in [('Prices', 'px.csv'), ('Investor flows', 'flows.csv'), ('Short selling', 'short.csv'), ('News', 'news.csv')]:
        try:
            last = pd.read_csv(os.path.join(DATA, f), index_col=0, parse_dates=True).index.max().date().isoformat()
        except Exception: last = None
        out[name] = dict(last=last, fresh=bool(last and last >= sig_date))
    try: out['DART filings'] = dict(last=pd.read_csv(os.path.join(DATA, 'dart.csv')).date.max(), fresh=True)
    except Exception: pass
    return out


SRC_OF = dict(prices='prices', kosdaq='prices', peers='prices', flows='flows', short='short', news='news', dart='dart')


def scheduler():
    """Updates after the close each trading day, on startup, and re-checks every 20 minutes (08:00-23:30 KST) any source
    that errored OR is still behind (e.g. Naver posts final investor flows ~18:00-20:00). Catches up after sleep."""
    done = set(); last_retry = 0
    evaluate()
    run_update()
    while True:
        try:
            n = updater.now_kst(); today = n.date().isoformat(); c = cfg(); hm = n.strftime('%H:%M')
            for hhmm in c['update_times_kst']:
                key = today + hhmm
                if key not in done and hm >= hhmm and (updater.is_session(today) or hhmm < '12:00'):
                    done.add(key); log(f'Scheduled update {hhmm} KST'); run_update()
            if STATE.get('retry_net_at') and time.time() >= STATE['retry_net_at'] and not STATE['running']:
                STATE['retry_net_at'] = None; q = STATE['queue'] or list(ALL_SOURCES); STATE['queue'] = []
                log('Network retry'); run_update(q)
            if '08:00' <= hm <= '23:30' and time.time() - last_retry > 20 * 60 and not STATE['running']:
                h = health()
                need = {SRC_OF[k] for k, v in h.items() if v['state'] in ('behind', 'stale')}
                need |= {k for k, v in STATE['sources'].items() if not v['ok'] and 'Settings' not in v['msg'] and 'krx_inbox' not in v['msg']}
                if need:
                    last_retry = time.time(); log('Re-checking: ' + ', '.join(sorted(need)))
                    run_update([s for s in ALL_SOURCES if s in need])
        except Exception:
            log('scheduler: ' + traceback.format_exc()[-300:])
        time.sleep(30)


# ---------------- account ----------------
def account():
    c = cfg()
    with db() as con:
        tr = [dict(r) for r in con.execute("SELECT * FROM trades ORDER BY trade_date, id")]
        flows = [dict(r) for r in con.execute("SELECT * FROM cash ORDER BY flow_date, id")]
    qty = 0; cost = 0.0; realized = 0.0; fees = 0.0; bought = 0.0; sold = 0.0; rows = []; by_strat = {}
    cash = float(c.get('starting_cash') or 0) + sum(f['amount'] for f in flows)
    for t in tr:
        gross = t['qty'] * t['price']; f = (t['fee'] or 0) + (t['tax'] or 0); fees += f
        row = dict(t, gross=gross, fees_total=f)
        if t['side'] == 'BUY':
            cost += gross + f; qty += t['qty']; bought += gross; cash -= gross + f
            row.update(avg_cost_after=cost / qty if qty else 0, pnl=None, pnl_pct=None)
        else:
            avg = cost / qty if qty else 0
            basis = avg * t['qty']; pnl = gross - f - basis
            cost -= basis; qty -= t['qty']; realized += pnl; sold += gross; cash += gross - f
            row.update(avg_cost_before=avg, pnl=pnl, pnl_pct=(gross - f) / basis - 1 if basis else None, price_change_pct=t['price'] / avg - 1 if avg else None)
            s = by_strat.setdefault(t['strategy'] or 'Manual', dict(trades=0, pnl=0.0, wins=0)); s['trades'] += 1; s['pnl'] += pnl; s['wins'] += pnl > 0
        row.update(position_after=qty, realized_to_date=realized)
        rows.append(row)
    q = STATE.get('quote') or {}
    res = STATE.get('result')
    last_px = q.get('price') or (res['context']['close'] if res else None)
    mv = qty * last_px if last_px else 0
    avg = cost / qty if qty else 0
    unreal = mv - cost if qty else 0
    start = float(c.get('starting_cash') or 0) + sum(f['amount'] for f in flows)
    total_value = cash + mv if start else None
    return dict(position=dict(qty=qty, avg_cost=avg, cost_basis=cost, last_price=last_px, market_value=mv, unrealized=unreal,
                              unrealized_pct=(mv / cost - 1) if cost and qty else None),
                realized=realized, total_pnl=realized + unreal, fees=fees, total_bought=bought, total_sold=sold,
                cash=cash if start else None, capital=start or None, total_value=total_value,
                total_return_pct=(total_value / start - 1) if start else None,
                by_strategy=by_strat, trades=list(reversed(rows)), cash_flows=flows)


# ---------------- routes ----------------
@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/api/state')
def api_state():
    with LOCK: res = STATE['result']
    with db() as c:
        sig = [dict(r) for r in c.execute("SELECT * FROM signals ORDER BY signal_date DESC, id DESC LIMIT 300")]
    cc = cfg()
    return jsonify(offline_since=STATE.get('offline_since'), result=res, last_run=STATE['last_run'], running=STATE['running'], sources=STATE['sources'], signals=sig, health=STATE.get('health') or health(),
                   account=account(), now_kst=updater.now_kst().isoformat(timespec='minutes'),
                   market_open_today=updater.is_session(updater.now_kst().date()), log=STATE['log'][-30:],
                   settings=dict(ticker=updater.CODE, risk_pct=cc.get('risk_pct', 2.0), max_pos_pct=cc.get('max_pos_pct', 50.0), starting_cash=cc['starting_cash'], commission_pct=cc['commission_pct'], sell_tax_pct=cc['sell_tax_pct'],
                                 update_times_kst=cc['update_times_kst'], has_dart_key=bool(cc['dart_key']), has_krx=bool(cc['krx_id']), config_path=CFG,
                                 has_telegram=bool(cc.get('telegram_token') and cc.get('telegram_chat_id')), desktop_alerts=bool(cc.get('desktop_alerts', True))))


@app.route('/api/quote')
def api_quote():
    if time.time() - STATE['quote_at'] > 50:
        try: STATE['quote'] = updater.live_quote(); STATE['quote_at'] = time.time()
        except Exception as e: return jsonify(error=str(e), quote=STATE['quote'])
    return jsonify(quote=STATE['quote'])


@app.route('/api/whatif')
def api_whatif():
    """What-if lab: same frozen rules, different risk settings / period. Never touches live signals."""
    a = request.args
    try:
        c = dict(engine.V2_CFG if a.get('preset') == 'v2' else engine.CFG)
        b = lambda k: a.get(k) in ('1', 'true', 'on')
        for k in ('s3_filter', 'squeeze', 'regime_score', 'dart_factor', 's6', 'graded_entries', 'graded_s5'):
            if k in a: c[k] = b(k)
        for k in ('regime_mode', 'dart_mode', 'stop_mode', 's6_weights'):
            if a.get(k): c[k] = a[k]
        for k in ('atr_mult', 's3_block_days', 'dart_exit_at'):
            if a.get(k): c[k] = float(a[k]) if k == 'atr_mult' else int(float(a[k]))
        if a.get('s3_targets') is not None: c['s3_targets'] = tuple(x for x in a['s3_targets'].split(',') if x)
        res = engine.whatif(DATA, start=a.get('start', '2026-01-02'), stop=float(a.get('stop', 15)) / 100,
                            max_hold=int(a.get('max_hold', 40)), cost=float(a.get('cost', 0.3)) / 100,
                            drop_after=updater.last_complete_session(), cfg=c)
        res['params']['cfg'] = {k: (list(v) if isinstance(v, tuple) else v) for k, v in c.items()}
        return jsonify(res)
    except Exception as e:
        return jsonify(error=str(e)), 400


@app.route('/api/health')
def api_health():
    return jsonify(health=health(), sources=STATE['sources'], running=STATE['running'], step=STATE.get('step'),
                   last_complete_session=updater.last_complete_session(), now_kst=updater.now_kst().isoformat(timespec='minutes'))


@app.route('/api/update', methods=['POST'])
def api_update():
    threading.Thread(target=run_update, kwargs=dict(force_news=True), daemon=True).start(); return jsonify(ok=True)


def fees_for(side, qty, price):
    c = cfg(); gross = qty * price
    fee = round(gross * float(c['commission_pct']) / 100)
    tax = round(gross * float(c['sell_tax_pct']) / 100) if side == 'SELL' else 0
    return fee, tax


@app.route('/api/trades', methods=['POST'])
def api_add_trade():
    j = request.json; side = j['side'].upper(); qty = int(j['qty']); price = float(j['price'])
    if qty <= 0 or price <= 0: return jsonify(error='Quantity and price must be above zero'), 400
    if side == 'SELL' and qty > account()['position']['qty']:
        return jsonify(error=f"You hold {account()['position']['qty']} shares — can't sell {qty}"), 400
    fee, tax = fees_for(side, qty, price)
    if j.get('fee') not in (None, ''): fee = float(j['fee'])
    if j.get('tax') not in (None, ''): tax = float(j['tax'])
    with db() as c:
        c.execute("INSERT INTO trades(trade_date, side, qty, price, fee, tax, strategy, signal_id, note, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (j['date'], side, qty, price, fee, tax, j.get('strategy') or 'Manual', j.get('signal_id'), j.get('note', ''), updater.now_kst().isoformat()))
        if j.get('signal_id'): c.execute("UPDATE signals SET status='acted' WHERE id=?", (j['signal_id'],))
    log(f"Logged {side} {qty} @ {price:,.0f} ({j.get('strategy')})")
    return jsonify(ok=True)


@app.route('/api/trades/<int:tid>', methods=['DELETE'])
def api_del_trade(tid):
    with db() as c:
        r = c.execute("SELECT signal_id FROM trades WHERE id=?", (tid,)).fetchone()
        c.execute("DELETE FROM trades WHERE id=?", (tid,))
        if r and r['signal_id']: c.execute("UPDATE signals SET status='pending' WHERE id=?", (r['signal_id'],))
    return jsonify(ok=True)


@app.route('/api/signals/<int:sid>', methods=['POST'])
def api_signal_status(sid):
    with db() as c: c.execute("UPDATE signals SET status=? WHERE id=?", (request.json['status'], sid))
    return jsonify(ok=True)


@app.route('/api/cash', methods=['POST'])
def api_cash():
    j = request.json
    with db() as c: c.execute("INSERT INTO cash(flow_date, amount, note) VALUES(?,?,?)", (j['date'], float(j['amount']), j.get('note', '')))
    return jsonify(ok=True)


@app.route('/api/settings', methods=['POST'])
def api_settings():
    c = cfg(); j = request.get_json(silent=True) or {}
    labels = dict(starting_cash='Starting cash', commission_pct='Commission %', sell_tax_pct='Sell tax %', risk_pct='Risk per trade %', max_pos_pct='Max position %')
    limits = dict(starting_cash=(0, 1e13), commission_pct=(0, 5), sell_tax_pct=(0, 5), risk_pct=(0.1, 20), max_pos_pct=(1, 100))
    try:
        for k, lab in labels.items():
            if k in j and str(j[k]).strip() not in ('', 'None'):
                x = parse_num(j[k], lab); lo, hi = limits[k]
                if not lo <= x <= hi: raise ValueError(f"{lab}: {x:g} is outside {lo:g}–{hi:g}")
                c[k] = x
            elif k == 'starting_cash' and j.get('clear_cash'): c[k] = 0
        if str(j.get('update_times_kst') or '').strip():
            times = []
            for x in re.split(r'[,;\s]+', str(j['update_times_kst']).strip()):
                if not x: continue
                m = re.fullmatch(r'(\d{1,2})[:.]?(\d{2})', x)
                if not m or int(m[1]) > 23 or int(m[2]) > 59: raise ValueError(f"Auto-update times: '{x}' is not a time like 18:40")
                times.append(f"{int(m[1]):02d}:{m[2]}")
            c['update_times_kst'] = sorted(set(times))
        for k in ('dart_key', 'krx_id', 'krx_pw', 'telegram_token', 'telegram_chat_id'):
            if str(j.get(k) or '').strip(): c[k] = str(j[k]).strip()
        for k in ('dart_key', 'krx'):
            if j.get('clear_' + k): c.update({'dart_key': ''} if k == 'dart_key' else {'krx_id': '', 'krx_pw': ''})
        if 'desktop_alerts' in j: c['desktop_alerts'] = bool(j['desktop_alerts'])
        if j.get('clear_telegram'): c['telegram_token'] = c['telegram_chat_id'] = ''
    except ValueError as e:
        return jsonify(error=str(e)), 400
    try:
        save_cfg(c)
    except OSError as e:
        return jsonify(error=f"Could not write {CFG}: {e.strerror or e}. Unzip the app to a normal folder (e.g. Documents), not inside the zip or Program Files."), 500
    back = cfg()                                   # read back from disk to prove it stuck
    if any(back.get(k) != c.get(k) for k in labels) or back.get('dart_key') != c.get('dart_key'):
        return jsonify(error='Saved file did not match what was entered. Is another copy of the app running from a different folder?'), 500
    log(f"settings saved to {CFG}")
    with LOCK: res = STATE['result']
    if res:
        try: add_sizing(res)
        except Exception as e: log(f'sizing refresh failed: {e}')
    return jsonify(ok=True, config_path=CFG, settings=dict(starting_cash=back['starting_cash'], update_times_kst=back['update_times_kst'],
                   has_dart_key=bool(back['dart_key']), has_krx=bool(back['krx_id'])))


@app.errorhandler(Exception)
def api_error(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException): return e
    log(f"error on {request.path}: {e!r}")
    return jsonify(error=f"{type(e).__name__}: {e}"), 500


@app.route('/api/net_test', methods=['POST'])
def api_net_test():
    c = cfg(); res = updater.net_test(c.get('dart_key'), c.get('krx_id'), c.get('krx_pw'))
    log('Connection test: ' + '; '.join(f"{r['name']}={'ok' if r['ok'] else r['detail']}" for r in res))
    return jsonify(results=res)


@app.route('/api/alerts', methods=['GET', 'POST'])
def api_alerts():
    c = cfg()
    if request.method == 'POST':
        rules = []
        for r in (request.get_json(silent=True) or {}).get('rules', []):
            if r.get('metric') not in ALERT_METRICS or r.get('op') not in ('>=', '<='):
                return jsonify(error=f"Unknown alert type: {r.get('metric')}"), 400
            try: v = parse_num(r.get('value'), 'Alert threshold')
            except ValueError as e: return jsonify(error=str(e)), 400
            rules.append(dict(id=str(r.get('id') or f"a{int(time.time() * 1000)}")[:40], metric=r['metric'], op=r['op'], value=v, on=bool(r.get('on'))))
        c['alert_rules'] = rules
        try: save_cfg(c)
        except OSError as e: return jsonify(error=f'Could not write {CFG}: {e}'), 500
        with LOCK: res = STATE['result']
        threading.Thread(target=check_alerts, args=(res,), daemon=True).start()
    with db() as con:
        fired = [dict(x) for x in con.execute("SELECT * FROM alerts_fired ORDER BY date DESC, created_at DESC LIMIT 40")]
    with LOCK: res = STATE['result']
    return jsonify(rules=c.get('alert_rules', DEFAULT_ALERTS), fired=fired, values=metric_values(res),
                   metrics=[dict(key=k, label=v[0], unit=v[1]) for k, v in ALERT_METRICS.items()])


@app.route('/api/journal', methods=['GET', 'POST'])
def api_journal():
    if request.method == 'POST':
        j = request.get_json(silent=True) or {}
        key = str(j.get('key') or '')[:80]
        if not key: return jsonify(error='Missing trade key'), 400
        stars = max(0, min(5, int(j.get('stars') or 0)))
        with db() as c:
            c.execute("INSERT INTO journal(key, note, stars, updated_at) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                      "note=excluded.note, stars=excluded.stars, updated_at=excluded.updated_at",
                      (key, str(j.get('note') or '')[:5000], stars, updater.now_kst().isoformat(timespec='minutes')))
    with db() as c: items = {r['key']: dict(r) for r in c.execute("SELECT * FROM journal")}
    return jsonify(items=items)


@app.route('/api/test_alert', methods=['POST'])
def api_test_alert():
    res = STATE.get('result'); ctx = res['context'] if res else {}
    out = send_alerts('SwingSignals: test alert', f"SwingSignals {updater.CODE} test alert\nThis is how BUY / SELL signals will reach you. Last close {ctx.get('close', 0):,.0f} ({ctx.get('date', '')}).")
    bad = {k: v for k, v in out.items() if v is not True}
    if not out: return jsonify(error='No alert channel is on. Add Telegram or turn on Mac notifications.'), 400
    return (jsonify(error='; '.join(f'{k}: {v}' for k, v in bad.items())), 400) if bad else jsonify(ok=True, sent=list(out))


@app.route('/api/telegram_chat_id', methods=['POST'])
def api_tg_chat():
    """Finds your chat id: message your bot once, then press 'Find my chat ID'."""
    tok = (request.json or {}).get('token') or cfg().get('telegram_token')
    if not tok: return jsonify(error='Paste the bot token first'), 400
    import requests
    j = requests.get(f'https://api.telegram.org/bot{tok.strip()}/getUpdates', timeout=15).json()
    if not j.get('ok'): return jsonify(error='Telegram: ' + str(j.get('description'))), 400
    ids = [u['message']['chat']['id'] for u in j['result'] if 'message' in u]
    if not ids: return jsonify(error='No messages yet: open your bot in Telegram, press Start / send "hi", then try again'), 400
    return jsonify(chat_id=str(ids[-1]))


@app.route('/api/krx_upload', methods=['POST'])
def api_krx_upload():
    f = request.files['file']; p = os.path.join(DATA, 'krx_inbox'); os.makedirs(p, exist_ok=True)
    path = os.path.join(p, f"upload_{int(time.time())}.csv"); f.save(path)
    try:
        threading.Thread(target=run_update, args=(['short'],), daemon=True).start()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(error=str(e)), 400


@app.route('/api/ticker', methods=['POST'])
def api_ticker():
    code = str((request.json or {}).get('code', '')).strip()
    if not (code.isdigit() and len(code) == 6): return jsonify(error='Enter a 6-digit KRX code, e.g. 277810'), 400
    if STATE['running'] and STATE.get('step') != 'news':
        return jsonify(error='Prices are updating; try again in a few seconds'), 409
    try:
        m = activate(code)
    except Exception as e:
        log(f'ticker {code} failed: {e}'); return jsonify(error=str(e)[:200]), 400
    c = cfg(); c['ticker'] = code; save_cfg(c)
    evaluate()
    if not STATE['running']: threading.Thread(target=run_update, daemon=True).start()
    return jsonify(ok=True, meta=m)


@app.route('/api/tickers')
def api_tickers():
    out = [dict(engine.meta(BASE_DATA), active=updater.CODE == updater.RAINBOW)]
    tdir = os.path.join(BASE_DATA, 'tickers')
    if os.path.isdir(tdir):
        for c in sorted(os.listdir(tdir)):
            if os.path.exists(os.path.join(tdir, c, 'meta.json')): out.append(dict(engine.meta(os.path.join(tdir, c)), active=updater.CODE == c))
    return jsonify(tickers=out)


@app.route('/api/export')
def api_export():
    a = account(); df = pd.DataFrame(a['trades'])
    path = os.path.join(DATA, 'my_trades_export.csv'); df.to_csv(path, index=False, encoding='utf-8-sig')
    return send_from_directory(DATA, 'my_trades_export.csv', as_attachment=True)


if __name__ == '__main__':
    os.makedirs(DATA, exist_ok=True); init_db()
    if not os.path.exists(CFG): save_cfg(DEFAULT_CFG)
    c = cfg(); port = int(c['port'])
    try:
        activate(str(c.get('ticker') or updater.RAINBOW))
    except Exception as e:
        log(f"could not open {c.get('ticker')}: {e}; falling back to 277810"); activate(updater.RAINBOW)
    threading.Thread(target=scheduler, daemon=True).start()
    if c.get('open_browser') and '--no-browser' not in sys.argv:
        threading.Timer(2.0, lambda: webbrowser.open(f'http://127.0.0.1:{port}')).start()
    log(f'SwingSignals Desk running at http://127.0.0.1:{port}')
    app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False, threaded=True)
