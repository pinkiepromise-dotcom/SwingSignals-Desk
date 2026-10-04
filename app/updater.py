"""Keeps the data folder current. Every source is optional except prices: if one fails, the rest still update
and the dashboard shows which source is stale."""
import os, io, re, json, time, zipfile, datetime as dt, glob, shutil
import xml.etree.ElementTree as ET
import pandas as pd, numpy as np, requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Use the operating system's certificate store (Windows/macOS). Antivirus "HTTPS scanning" (V3, Kaspersky, Avast...) and
# company proxies re-sign HTTPS with their own certificate, which Python's bundled list rejects -> every source fails.
try:
    import truststore; truststore.inject_into_ssl(); TRUSTSTORE = True
except Exception:
    TRUSTSTORE = False

def _session():
    s = requests.Session()
    r = Retry(total=3, connect=3, read=2, backoff_factor=1.5, status_forcelist=(500, 502, 503, 504), allowed_methods=None, raise_on_status=False)
    s.mount('https://', HTTPAdapter(max_retries=r)); s.mount('http://', HTTPAdapter(max_retries=r))
    return s
HTTP = _session()


def net_reason(e):
    """Turns a long requests/urllib3 error into one plain sentence with what to do."""
    t = f'{type(e).__name__}: {e}'; host = re.search(r"host='([^']+)'", t); host = host[1] if host else 'the server'
    if re.search(r'NameResolution|getaddrinfo|Name or service not known|nodename nor servname|No address associated', t):
        why = f'cannot look up {host} (DNS): no internet, Wi-Fi still reconnecting after sleep, or VPN/DNS blocking it'
    elif re.search(r'CERTIFICATE_VERIFY_FAILED|SSLError|certificate', t, re.I):
        why = f'HTTPS certificate for {host} was rejected: antivirus HTTPS scanning or a company proxy is intercepting traffic'
    elif re.search(r'ConnectTimeout|timed out|Read timed out', t):
        why = f'{host} did not answer in time: slow/unstable connection or a firewall dropping the request'
    elif re.search(r'ProxyError|Tunnel connection failed', t):
        why = f'the proxy refused the connection to {host}: check Windows proxy settings'
    elif re.search(r'ConnectionRefused|10061|Connection refused', t):
        why = f'{host} refused the connection: a firewall or security program is blocking Python'
    elif re.search(r'ConnectionReset|10054|RemoteDisconnected|Connection aborted', t):
        why = f'connection to {host} was cut: security software or an unstable network'
    elif re.search(r'NewConnectionError|Max retries exceeded|Network is unreachable|10051|10065', t):
        why = f'cannot connect to {host}: no network route (offline, VPN, or firewall)'
    else:
        return t[:240]
    return why

KST = dt.timezone(dt.timedelta(hours=9))
H = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://m.stock.naver.com/'}
CODE = '277810'
YF = {'px': '277810.KQ', 'kq': '^KQ11', 'doosan': '454910.KS', 'robotis': '108490.KQ'}
NAVER_CHART = {'px': ('item', '277810'), 'kq': ('index', 'KOSDAQ'), 'doosan': ('item', '454910'), 'robotis': ('item', '108490')}
GDELT = {'rainbow_all': '"Rainbow Robotics"', 'rr_samsung': '"Rainbow Robotics" Samsung',
         'humanoid': 'humanoid robot', 'nvidia_robot': 'Nvidia (humanoid OR robotics)'}
RAINBOW = '277810'


def set_ticker(code, market='KOSDAQ'):
    """Point every data source at another stock. Rainbow keeps its robot peers; other stocks use the market index."""
    global CODE, YF, NAVER_CHART
    CODE = code
    idx = 'KOSPI' if market == 'KOSPI' else 'KOSDAQ'
    suf = '.KS' if market == 'KOSPI' else '.KQ'
    if code == RAINBOW:
        YF = {'px': '277810.KQ', 'kq': '^KQ11', 'doosan': '454910.KS', 'robotis': '108490.KQ'}
        NAVER_CHART = {'px': ('item', '277810'), 'kq': ('index', 'KOSDAQ'), 'doosan': ('item', '454910'), 'robotis': ('item', '108490')}
    else:
        YF = {'px': code + suf, 'kq': '^KS11' if idx == 'KOSPI' else '^KQ11'}
        NAVER_CHART = {'px': ('item', code), 'kq': ('index', idx)}


def stock_info(code):
    """Name and market from Naver. Raises ValueError for an unknown code."""
    r = HTTP.get(f'https://m.stock.naver.com/api/stock/{code}/basic', headers=H, timeout=20)
    if r.status_code != 200: raise ValueError(f'Unknown stock code {code}')
    j = r.json(); ex = (j.get('stockExchangeType') or {}).get('nameEng', 'KOSDAQ')
    return dict(code=j.get('itemCode', code), name=j.get('stockName', code), market='KOSPI' if ex == 'KOSPI' else 'KOSDAQ')


def bootstrap_ticker(data_dir, code, log, since='2023-10-01'):
    """First-time download for a new stock: ~3 years of prices + index + investor flows; empty optional files."""
    info = stock_info(code); os.makedirs(data_dir, exist_ok=True)
    set_ticker(code, info['market'])
    start = pd.Timestamp(since)
    for k, (kind, c) in NAVER_CHART.items():
        df = _naver_chart(kind, c, start)
        if df.empty: raise RuntimeError(f'No price history for {k}')
        _cut(df.dropna(subset=['Close'])).to_csv(os.path.join(data_dir, f'{k}.csv'))
    rows = []; bd = now_kst().strftime('%Y%m%d')
    for _ in range(20):                                    # flows API pages back 60 sessions at a time
        j = HTTP.get(f'https://m.stock.naver.com/api/stock/{code}/trend', params={'pageSize': 60, 'bizdate': bd}, headers=H, timeout=20).json()
        if not j: break
        rows += j; last = min(x['bizdate'] for x in j)
        if last <= start.strftime('%Y%m%d'): break
        bd = (pd.Timestamp(last) - pd.Timedelta(days=1)).strftime('%Y%m%d'); time.sleep(0.3)
    df = pd.DataFrame(rows).drop_duplicates('bizdate')
    out = pd.DataFrame({'date': pd.to_datetime(df.bizdate), 'close': df.closePrice.map(_num), 'volume': df.accumulatedTradingVolume.map(_num),
                        'foreign_net': df.foreignerPureBuyQuant.map(_num), 'inst_net': df.organPureBuyQuant.map(_num),
                        'retail_net': df.individualPureBuyQuant.map(_num), 'foreign_hold_pct': df.foreignerHoldRatio.map(_num)}).set_index('date').sort_index()
    _cut(out).to_csv(os.path.join(data_dir, 'flows.csv'))
    for f, cols in [('short.csv', 'date,short_vol,short_balance_qty,short_balance_krw'), ('news.csv', 'date,' + ','.join(GDELT)),
                    ('dart.csv', 'date,report_nm,flr_nm,rcept_no'), ('events_manual.csv', 'date,type,note')]:
        p = os.path.join(data_dir, f)
        if not os.path.exists(p): open(p, 'w').write(cols + '\n')
    json.dump(info, open(os.path.join(data_dir, 'meta.json'), 'w'), ensure_ascii=False)
    log(f"Loaded {info['name']} ({code}, {info['market']}): {len(out)} days of flows")
    return info


# KRX market holidays (weekdays). Extend each year from krx.co.kr
HOLIDAYS = {'2026-01-01', '2026-02-16', '2026-02-17', '2026-02-18', '2026-03-02', '2026-05-01', '2026-05-05', '2026-05-25',
            '2026-06-03', '2026-08-17', '2026-09-24', '2026-09-25', '2026-10-05', '2026-10-09', '2026-12-25', '2026-12-31',
            '2027-01-01'}


def now_kst():
    return dt.datetime.now(KST)


_HOL_YEARS = {y for y in {int(x[:4]) for x in HOLIDAYS} if sum(x.startswith(str(y)) for x in HOLIDAYS) >= 8}   # only fully listed years
_XKRX = None


def _xkrx_holiday(d):
    """For years not in HOLIDAYS, ask exchange_calendars (if installed) so the app keeps working after December."""
    global _XKRX
    try:
        if _XKRX is None:
            import exchange_calendars as xc; _XKRX = xc.get_calendar('XKRX')
        return not _XKRX.is_session(d.normalize())
    except Exception:
        return False


def is_session(d):
    d = pd.Timestamp(d)
    if d.weekday() >= 5: return False
    if d.year in _HOL_YEARS: return d.date().isoformat() not in HOLIDAYS
    return not _xkrx_holiday(d)


def prev_session(before):
    d = pd.Timestamp(before) - pd.Timedelta(days=1)
    while not is_session(d): d -= pd.Timedelta(days=1)
    return d.date().isoformat()


def sessions_between(a, b):
    """Number of trading sessions after date a up to and including date b (0 if a >= b)."""
    if not a or a >= b: return 0
    n, d = 0, pd.Timestamp(a)
    while d.date().isoformat() < b:
        d += pd.Timedelta(days=1)
        if is_session(d): n += 1
    return n


def next_session(after):
    d = pd.Timestamp(after) + pd.Timedelta(days=1)
    while not is_session(d): d += pd.Timedelta(days=1)
    return d.date().isoformat()


def last_complete_session():
    """Latest session whose daily bar is complete (after 15:45 KST)."""
    n = now_kst(); d = pd.Timestamp(n.date())
    if not (is_session(d) and (n.hour, n.minute) >= (15, 45)):
        d -= pd.Timedelta(days=1)
        while not is_session(d): d -= pd.Timedelta(days=1)
    return d.date().isoformat()


def _merge_csv(path, new, index=True, keep_nonzero=()):
    """Append/overwrite rows by date. Columns in keep_nonzero keep the old value when the new one is 0 or blank
    (KRX exports show the short balance as 0 for the last 2 days because it is published T+2)."""
    if os.path.exists(path):
        old = pd.read_csv(path, index_col=0, parse_dates=True)
        for c in keep_nonzero:
            if c in new and c in old:
                o = old[c].reindex(new.index); bad = new[c].isna() | (new[c] == 0)
                new.loc[bad & o.notna() & (o != 0), c] = o[bad & o.notna() & (o != 0)]
        new = pd.concat([old, new]); new = new[~new.index.duplicated(keep='last')].sort_index()
    new.to_csv(path)
    return new


def _stamp(data_dir, name):
    """Remember when a source was last checked successfully (data/.checked.json)."""
    p = os.path.join(data_dir, '.checked.json')
    try: j = json.load(open(p))
    except Exception: j = {}
    j[name] = now_kst().isoformat(timespec='minutes'); json.dump(j, open(p, 'w'))


def checked_at(data_dir, name):
    try: return json.load(open(os.path.join(data_dir, '.checked.json'))).get(name)
    except Exception: return None


def _cut(df):
    """Drop any bar after the last complete session (unfinished intraday data)."""
    return df.loc[:last_complete_session()]


# ---------------- prices ----------------
def _naver_chart(kind, code, start):
    u = f'https://api.stock.naver.com/chart/domestic/{kind}/{code}/day'
    j = HTTP.get(u, params={'startDateTime': start.strftime('%Y%m%d') + '0000', 'endDateTime': now_kst().strftime('%Y%m%d') + '2359'},
                     headers=H, timeout=20).json()
    df = pd.DataFrame(j)
    df = pd.DataFrame({'date': pd.to_datetime(df.localDate), 'Open': df.openPrice, 'High': df.highPrice, 'Low': df.lowPrice,
                       'Close': df.closePrice, 'Volume': df.accumulatedTradingVolume}).set_index('date')
    return df


def update_prices(data_dir, log):
    """Primary: Naver (official KRX close). Fallback: Yahoo Finance."""
    start = pd.Timestamp(now_kst().date()) - pd.Timedelta(days=45)
    for k, sym in YF.items():
        df = None; naver_err = 'no rows returned'
        try:
            df = _naver_chart(*NAVER_CHART[k], start)
        except Exception as e:
            naver_err = net_reason(e); log(f'naver chart {k}: {naver_err}')
        if df is None or df.empty:
            try:
                import yfinance as yf
                x = yf.download(sym, start=start.date().isoformat(), progress=False, auto_adjust=True)
                if isinstance(x.columns, pd.MultiIndex): x.columns = [c[0] for c in x.columns]
                df = x[['Open', 'High', 'Low', 'Close', 'Volume']]; df.index = pd.to_datetime(df.index).tz_localize(None); df.index.name = 'date'
            except Exception as e:
                log(f'yfinance {sym}: {e}')
        if df is None or df.empty:
            raise RuntimeError(f'No price data for {k}. Naver: {naver_err}; Yahoo also failed')
        _merge_csv(os.path.join(data_dir, f'{k}.csv'), _cut(df.dropna(subset=['Close'])))
    for k in ('prices', 'kosdaq', 'peers'): _stamp(data_dir, k)
    return True


# ---------------- investor flows ----------------
def _num(s):
    s = str(s).replace(',', '').replace('+', '').replace('%', '')
    try: return float(s)
    except ValueError: return float('nan')


def update_flows(data_dir, log):
    j = HTTP.get(f'https://m.stock.naver.com/api/stock/{CODE}/trend', params={'pageSize': 60, 'bizdate': now_kst().strftime('%Y%m%d')},
                     headers=H, timeout=20).json()
    if not isinstance(j, list) or not j: raise RuntimeError('Naver returned no investor-flow rows')
    df = pd.DataFrame(j).drop_duplicates('bizdate')
    # a row without a closing price is Naver's provisional intraday estimate: skip it, the final row comes later
    df = df[df.closePrice.map(_num).notna()]
    out = pd.DataFrame({'date': pd.to_datetime(df.bizdate), 'close': df.closePrice.map(_num), 'volume': df.accumulatedTradingVolume.map(_num),
                        'foreign_net': df.foreignerPureBuyQuant.map(_num), 'inst_net': df.organPureBuyQuant.map(_num),
                        'retail_net': df.individualPureBuyQuant.map(_num), 'foreign_hold_pct': df.foreignerHoldRatio.map(_num)}).set_index('date').sort_index()
    _merge_csv(os.path.join(data_dir, 'flows.csv'), _cut(out)); _stamp(data_dir, 'flows')
    return True


# ---------------- news (GDELT) ----------------
_GDELT_LAST = [0.0]
NEWS_MIN_INTERVAL_MIN = 45          # a full news refresh is skipped if the last complete one is younger than this


def _gdelt(q, start, end, log, tries=3):
    """One GDELT timeline query. GDELT allows 1 request / 5 s per IP and answers 429 (plain text) when exceeded."""
    waits = [0, 30, 60]                # worst case ~2 min per query; the scheduler re-checks later anyway
    for i in range(tries):
        gap = time.time() - _GDELT_LAST[0]
        time.sleep(max(0, 7 - gap) + waits[i])
        _GDELT_LAST[0] = time.time()
        try:
            r = HTTP.get('https://api.gdeltproject.org/api/v2/doc/doc', params={'query': q, 'mode': 'timelinevolraw',
                             'startdatetime': start.strftime('%Y%m%d000000'), 'enddatetime': end.strftime('%Y%m%d235959'), 'format': 'json'}, timeout=60)
            if r.ok and r.text.lstrip().startswith('{'):
                tl = r.json().get('timeline') or []
                data = tl[0]['data'] if tl else []
                return pd.Series({pd.to_datetime(x['date'][:8]): x['value'] for x in data}, dtype=float)
            log(f'gdelt {q!r}: HTTP {r.status_code} {r.text[:60]!r} (try {i + 1}/{tries})')
        except Exception as e:
            log(f'gdelt {q!r}: {e} (try {i + 1}/{tries})')
    return None


def update_news(data_dir, log, force=False):
    if CODE != RAINBOW: return True          # news queries are Rainbow / robot specific
    last_ok = checked_at(data_dir, 'news')
    if not force and last_ok and (now_kst() - dt.datetime.fromisoformat(last_ok)).total_seconds() < NEWS_MIN_INTERVAL_MIN * 60:
        return True                          # refreshed recently: don't spend GDELT's rate limit again
    end = now_kst(); start = end - dt.timedelta(days=40)
    path = os.path.join(data_dir, 'news.csv'); got, failed = [], []
    for k, q in GDELT.items():
        if CODE != RAINBOW: return True      # user switched stock mid-update
        sr = _gdelt(q, start, end, log)
        if sr is None: failed.append(k); continue
        got.append(k)
        old = pd.read_csv(path, index_col=0, parse_dates=True) if os.path.exists(path) else pd.DataFrame()
        idx = old.index.union(sr.index); old = old.reindex(idx)
        old[k] = sr.reindex(idx).combine_first(old[k]) if k in old else sr.reindex(idx)
        old.index.name = 'date'; old.sort_index().to_csv(path)        # saved after every query: partial success is kept
    if failed:
        raise RuntimeError(f'GDELT returned {len(got)}/{len(GDELT)} queries (rate limit on {", ".join(failed)}); saved the rest, will retry later')
    _stamp(data_dir, 'news')
    return True


# ---------------- DART filings ----------------
def update_dart(data_dir, key, log):
    if not key: raise RuntimeError('No DART API key in Settings')
    cc_file = os.path.join(data_dir, '.corp_code')
    if os.path.exists(cc_file): cc = open(cc_file).read().strip()
    else:
        z = zipfile.ZipFile(io.BytesIO(HTTP.get('https://opendart.fss.or.kr/api/corpCode.xml', params={'crtfc_key': key}, timeout=60).content))
        cc = next(c.findtext('corp_code') for c in ET.fromstring(z.read('CORPCODE.xml')).iter('list') if c.findtext('stock_code') == CODE)
        open(cc_file, 'w').write(cc)
    j = HTTP.get('https://opendart.fss.or.kr/api/list.json', params={'crtfc_key': key, 'corp_code': cc,
                     'bgn_de': (now_kst() - dt.timedelta(days=60)).strftime('%Y%m%d'), 'end_de': now_kst().strftime('%Y%m%d'),
                     'page_count': 100}, timeout=30).json()
    if j.get('status') not in ('000', '013'): raise RuntimeError(f"DART: {j.get('status')} {j.get('message')}")
    rows = j.get('list', [])
    path = os.path.join(data_dir, 'dart.csv'); old = pd.read_csv(path); fresh = []
    if rows:
        new = pd.DataFrame(rows); new = pd.DataFrame({'date': pd.to_datetime(new.rcept_dt).dt.date.astype(str), 'report_nm': new.report_nm.str.strip(), 'flr_nm': new.flr_nm,
                            'rcept_no': new.rcept_no.astype(str)})
        if 'rcept_no' in old: old['rcept_no'] = old.rcept_no.astype(str).str.replace(r'\.0$', '', regex=True).replace('nan', '')
        seen = set(zip(old.date.astype(str), old.report_nm.astype(str).str.strip()))
        fresh = [dict(date=r.date, report_nm=r.report_nm, flr_nm=r.flr_nm) for r in new.itertuples() if (r.date, r.report_nm) not in seen]
        out = pd.concat([old, new]).drop_duplicates(['date', 'report_nm', 'flr_nm'], keep='last').sort_values('date'); out.to_csv(path, index=False)
    _stamp(data_dir, 'dart')
    return fresh                                   # filings not seen before (used for same-day DART alerts)


# ---------------- short selling (KRX) ----------------
def import_krx_csv(path, data_dir):
    """KRX '개별종목 공매도 종합정보' CSV export (9 columns: date, short vol x4, short value x4 ... ) OR the
    공매도 거래/잔고 layout. Header text is often garbled (cp949), so columns are mapped by position."""
    df = pd.read_csv(path, encoding='cp949', encoding_errors='replace', dtype=str)
    dates = pd.to_datetime(df.iloc[:, 0].str.strip().str.replace('/', '-'), errors='coerce')   # separate series: works on pandas 2 and 3
    num = lambda s: pd.to_numeric(s.astype(str).str.replace(',', ''), errors='coerce')
    if df.shape[1] == 9:           # 일자, 거래량(전체, 업틱룰적용, 업틱룰예외, 순보유잔고수량), 거래대금(전체, 업틱, 예외, 순보유잔고금액)
        out = pd.DataFrame({'date': dates, 'short_vol': num(df.iloc[:, 1]), 'short_balance_qty': num(df.iloc[:, 4]),
                            'short_balance_krw': num(df.iloc[:, 8])})
    else:
        raise RuntimeError(f'Unrecognised KRX CSV layout ({df.shape[1]} columns). Use 공매도 종합정보 export.')
    out = out.dropna(subset=['date']).set_index('date').sort_index()
    _merge_csv(os.path.join(data_dir, 'short.csv'), out, keep_nonzero=('short_balance_qty', 'short_balance_krw'))
    return len(out)


def _krx_matches(path, data_dir):
    """A KRX export carries no stock code. Accept it only if its short volumes agree with the ones we already
    have on >= 3 overlapping days, so another stock's file can't slip in."""
    try:
        df = pd.read_csv(path, encoding='cp949', encoding_errors='replace')
        if df.shape[1] != 9: return False
        d = pd.to_datetime(df.iloc[:, 0].astype(str).str.replace('/', '-'), errors='coerce')
        v = pd.Series(pd.to_numeric(df.iloc[:, 1].astype(str).str.replace(',', ''), errors='coerce').values, index=d)
        old = pd.read_csv(os.path.join(data_dir, 'short.csv'), index_col=0, parse_dates=True).short_vol
        common = v.index.intersection(old.index)
        return len(common) >= 3 and (v[common] == old[common]).mean() >= 0.9
    except Exception:
        return False


def scan_downloads(data_dir, log, folder=None, days=10):
    """Optional: pick up KRX '공매도 종합정보' exports (data_XXXX_YYYYMMDD.csv) from the Downloads folder."""
    folder = folder or os.path.join(os.path.expanduser('~'), 'Downloads')
    inbox = os.path.join(data_dir, 'krx_inbox'); os.makedirs(os.path.join(inbox, 'imported'), exist_ok=True)
    done = set(os.listdir(os.path.join(inbox, 'imported'))); n = 0
    for f in glob.glob(os.path.join(folder, 'data_*_*.csv')):
        b = os.path.basename(f)
        if b in done or time.time() - os.path.getmtime(f) > days * 86400: continue
        if _krx_matches(f, data_dir):
            shutil.copy(f, os.path.join(inbox, b)); n += 1; log(f'short: picked up {b} from Downloads')
    return n


def update_short(data_dir, krx_id, krx_pw, log):
    inbox = os.path.join(data_dir, 'krx_inbox'); os.makedirs(inbox, exist_ok=True)
    done = os.path.join(inbox, 'imported'); os.makedirs(done, exist_ok=True)
    n = 0
    for f in sorted(glob.glob(os.path.join(inbox, '*.csv'))):
        n += import_krx_csv(f, data_dir); shutil.move(f, os.path.join(done, os.path.basename(f)))
    if n: _stamp(data_dir, 'short'); return True
    if not (krx_id and krx_pw): raise RuntimeError('Drop a KRX CSV into data/krx_inbox, or add KRX login in Settings')
    os.environ['KRX_ID'], os.environ['KRX_PW'] = krx_id, krx_pw
    from pykrx import stock                        # experimental: needs a data.krx.co.kr account
    s = (now_kst() - dt.timedelta(days=40)).strftime('%Y%m%d'); e = now_kst().strftime('%Y%m%d')
    v = stock.get_shorting_volume_by_date(s, e, CODE); b = stock.get_shorting_balance_by_date(s, e, CODE)
    vc = [c for c in v.columns if '공매도' in c and '거래량' not in c] or [v.columns[0]]
    out = pd.DataFrame({'short_vol': v[vc[0]]})
    bq = [c for c in b.columns if '잔고수량' in c]; bk = [c for c in b.columns if '잔고금액' in c]
    out['short_balance_qty'] = b[bq[0]] if bq else np.nan; out['short_balance_krw'] = b[bk[0]] if bk else np.nan
    out.index = pd.to_datetime(out.index); out.index.name = 'date'
    _merge_csv(os.path.join(data_dir, 'short.csv'), _cut(out), keep_nonzero=('short_balance_qty', 'short_balance_krw'))
    _stamp(data_dir, 'short')
    return True


# ---------------- data health ----------------
# lag = trading sessions a source normally trails the last completed session; due = KST time it is usually out by
SOURCES = [
    # key, label, file, lag, due (HH:MM, same evening), engine inputs that depend on it
    ('prices', 'Prices', 'px.csv', 0, '16:00', 'every strategy'),
    ('kosdaq', 'KOSDAQ index', 'kq.csv', 0, '16:00', 'KOSDAQ gate S1/S2/S4, regime'),
    ('peers', 'Robot peers', 'doosan.csv', 0, '16:00', 'S3 shock filter (peer return)'),
    ('flows', 'Investor flows', 'flows.csv', 0, '19:30', 'S1, S5 flow factor, retail-chasing exit'),
    ('short', 'Short selling', 'short.csv', 1, '09:00', 'heavy-short exit, S5 short factor (balance is T+2)'),
    ('news', 'News', 'news.csv', 0, '16:00', 'S3 shock filter, S5 news factor'),
]


def _last_date(path, col=None):
    try:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        s = df[col] if col else df.dropna(how='all')
        if col is None and 'close' in df: s = df.dropna(subset=['close'])          # flows: only final rows count
        return (s.dropna().index.max() if col else s.index.max()).date().isoformat()
    except Exception:
        return None


def source_status(data_dir, has_dart_key=True, has_krx=False):
    """Per-source freshness against what *should* exist by now (not just 'is it today').
    state: ok | pending (today's data isn't due yet) | behind | stale | setup"""
    n = now_kst(); lcs = last_complete_session(); today = n.date().isoformat(); out = {}
    for key, label, f, lag, due, uses in SOURCES:
        p = os.path.join(data_dir, f)
        if key == 'peers' and not os.path.exists(p): continue
        if key == 'news' and CODE != RAINBOW: continue
        if key == 'news':                     # the oldest of the 4 query columns decides
            try:
                df = pd.read_csv(p, index_col=0, parse_dates=True)
                last = min(df[c].dropna().index.max() for c in GDELT if c in df).date().isoformat()
            except Exception: last = None
        else:
            last = _last_date(p)
        exp = lcs
        for _ in range(lag): exp = prev_session(exp)
        behind = sessions_between(last, exp)
        if behind == 0: state = 'ok'
        elif behind == 1 and lag == 0 and lcs == today and n.strftime('%H:%M') < due: state = 'pending'
        elif key == 'short' and not has_krx: state = 'setup' if behind > 3 else 'behind'
        elif behind <= 2: state = 'behind'
        else: state = 'stale'
        out[key] = dict(label=label, last=last, expected=exp, behind=behind, state=state, uses=uses,
                        checked=checked_at(data_dir, key))
    try: last = pd.read_csv(os.path.join(data_dir, 'dart.csv')).date.max()
    except Exception: last = None
    chk = checked_at(data_dir, 'dart')
    if not has_dart_key: st = 'setup'
    elif not chk: st = 'stale'
    else:
        age_h = (n - dt.datetime.fromisoformat(chk)).total_seconds() / 3600
        st = 'ok' if age_h < 26 else 'stale'
    out['dart'] = dict(label='DART filings', last=last, expected=None, behind=None, state=st, checked=chk,
                       uses='DART severity exit S1/S4/S5, S5 DART factor')
    return out


# ---------------- live quote ----------------
def live_quote():
    j = HTTP.get(f'https://polling.finance.naver.com/api/realtime/domestic/stock/{CODE}', headers=H, timeout=10).json()['datas'][0]
    return dict(price=_num(j['closePrice']), change_pct=_num(j['fluctuationsRatio']), status=j.get('marketStatus'),
                open=_num(j.get('openPrice')), high=_num(j.get('highPrice')), low=_num(j.get('lowPrice')), time=j.get('localTradedAt'))


# ---------------- alerts ----------------
def send_telegram(token, chat_id, text):
    r = HTTP.post(f'https://api.telegram.org/bot{token}/sendMessage',
                      json={'chat_id': chat_id, 'text': text, 'disable_web_page_preview': True}, timeout=15)
    j = r.json()
    if not j.get('ok'): raise RuntimeError(f"Telegram: {j.get('description', r.status_code)}")
    return True


def desktop_notify(title, text):
    """Native notification that works even with the browser closed (macOS: Notification Center, Windows: toast, Linux: notify-send)."""
    import sys, subprocess
    try:
        if sys.platform == 'darwin':
            esc = lambda x: x.replace('\\', '\\\\').replace('"', '\\"')
            subprocess.run(['osascript', '-e', f'display notification "{esc(text)}" with title "{esc(title)}" sound name "Glass"'], timeout=10)
        elif sys.platform.startswith('win'):
            ps = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
                  "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
                  f"$x=$t.GetElementsByTagName('text');$x.Item(0).AppendChild($t.CreateTextNode('{title.replace(chr(39), '')}'))>$null;"
                  f"$x.Item(1).AppendChild($t.CreateTextNode('{text.replace(chr(39), '')}'))>$null;"
                  "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('SwingSignals').Show([Windows.UI.Notifications.ToastNotification]::new($t))")
            subprocess.run(['powershell', '-NoProfile', '-Command', ps], timeout=15, creationflags=0x08000000)
        else:
            subprocess.run(['notify-send', title, text], timeout=10)
        return True
    except Exception:
        return False


# ---------------- connection test ----------------
NET_HOSTS = {'Naver (prices, flows)': 'https://m.stock.naver.com/api/stock/277810/basic',
             'OpenDART (filings)': 'https://opendart.fss.or.kr/api/list.json',
             'KRX (short selling)': 'https://data.krx.co.kr/',
             'GDELT (news)': 'https://api.gdeltproject.org/api/v2/doc/doc?query=robot&mode=artlist&maxrecords=1&format=json'}


def online(timeout=6):
    """Fast check used before each update run: True if Naver answers."""
    try: requests.head('https://m.stock.naver.com/', timeout=timeout, headers=H); return True
    except Exception: return False


def net_test(dart_key='', krx_id='', krx_pw=''):
    out = []
    for name, url in NET_HOSTS.items():
        t0 = time.time()
        try:
            r = requests.get(url, headers=H, timeout=12, allow_redirects=False)
            out.append(dict(name=name, ok=r.status_code < 500, detail=f'HTTP {r.status_code} in {time.time() - t0:.1f}s'))
        except Exception as e:
            out.append(dict(name=name, ok=False, detail=net_reason(e)))
    if dart_key and out[1]['ok']:
        try:
            j = requests.get('https://opendart.fss.or.kr/api/list.json', params={'crtfc_key': dart_key, 'page_count': 1}, timeout=15).json()
            ok = j.get('status') in ('000', '013')
            out.append(dict(name='DART key', ok=ok, detail='key accepted' if ok else f"DART says {j.get('status')}: {j.get('message')} (020 = daily limit, 010/011 = wrong or unregistered key)"))
        except Exception as e:
            out.append(dict(name='DART key', ok=False, detail=net_reason(e)))
    if krx_id:
        try:
            import pykrx  # noqa
            out.append(dict(name='KRX login', ok=True, detail='pykrx installed; login is tried during the short-selling update'))
        except Exception:
            out.append(dict(name='KRX login', ok=False, detail='pykrx is not installed: run  pip install pykrx  in the app folder (or use the CSV export instead)'))
    out.append(dict(name='System certificates', ok=TRUSTSTORE, detail='using Windows/macOS certificate store' if TRUSTSTORE else 'truststore not installed: pip install truststore (needed if antivirus scans HTTPS)'))
    return out
