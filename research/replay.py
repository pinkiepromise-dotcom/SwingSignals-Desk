"""Walk-forward replay of FROZEN SwingSignals v4 rules on 2026 (out-of-sample).
Every evening (after close of day t) the engine is rebuilt from ONLY the data that was public by then,
decides BUY / SELL / HOLD / WAIT, and the order is executed at the OPEN of day t+1.
Publication lags: prices, flows, short VOLUME -> same evening; short BALANCE -> +2 trading days (KRX T+2);
DART -> filing date (acted on next open); news -> counts up to day t; calendar -> only events known in advance.
Rules / thresholds are identical to v4 (not re-tuned). Only change: exits also execute at next open (stricter)."""
import pandas as pd, numpy as np, json, sys
STOP, MAX_HOLD, COST = 0.15, 40, 0.003
START = pd.Timestamp('2025-12-30')          # first decision evening (last 2025 session) -> first order Jan 2 2026

def rd(f, **k): return pd.read_csv(f, index_col=0, parse_dates=True, **k)
px = rd('px.csv'); kq = rd('kq.csv').Close
peers = pd.concat({'doosan': rd('doosan.csv').Close, 'robotis': rd('robotis.csv').Close}, axis=1)
fl = pd.concat([rd('../swing/flows_277810.csv'), rd('flows_raw.csv')]); fl = fl[~fl.index.duplicated(keep='last')].sort_index()
nw = pd.concat([rd('../swing/news_gdelt_daily.csv').loc[:'2025-05-31'], rd('news_raw.csv')]); nw = nw[~nw.index.duplicated(keep='last')].sort_index()
sh = rd('short_raw.csv')
dart = pd.concat([pd.read_csv('../swing/events_dart.csv', parse_dates=['date'])[['date', 'report_nm', 'flr_nm']],
                  pd.read_csv('dart_raw.csv', parse_dates=['date'])[['date', 'report_nm', 'flr_nm']]]).drop_duplicates()
man = pd.read_csv('../swing/events_manual.csv', parse_dates=['date'])
# scheduled dates: v4 frozen list + 2026 equivalents known in advance (statutory report deadlines, prior-year prelim-earnings pattern)
SCHED_EXTRA = ['2024-02-27', '2024-05-14', '2024-08-14', '2024-11-14', '2025-02-27', '2025-05-15', '2025-08-14', '2025-11-14', '2024-03-28', '2025-03-19',
               '2026-02-27', '2026-03-31', '2026-05-15', '2026-08-14', '2026-11-16']
sched_all = pd.concat([man[man.type.isin(['conference', 'earnings', 'agm'])].date, pd.Series(pd.to_datetime(SCHED_EXTRA))]).sort_values()
SAM = pd.to_datetime(['2024-02-07', '2024-03-21', '2024-12-31', '2025-03-19'])

def features(asof):
    """Build the full v4 feature table using ONLY information public at the close of `asof`."""
    o = px.loc[:asof, ['Open', 'High', 'Low', 'Close', 'Volume']].copy()
    d = o.copy()
    k = kq.loc[:asof]
    d['ret'] = d.Close.pct_change(); d['kosdaq_ret'] = k.pct_change().reindex(d.index)
    d['excess_ret'] = d.ret - 1.4 * d.kosdaq_ret
    d['vol_ratio'] = d.Volume / d.Volume.rolling(20).mean().shift(1)
    d['dist_ma60'] = d.Close / d.Close.rolling(60).mean() - 1
    d['range_60d'] = d.Close.rolling(60).max() / d.Close.rolling(60).min() - 1
    f = fl.loc[:asof]; d = d.join(f[['foreign_net', 'inst_net', 'retail_net']])
    d['foreign_5d_pct_vol'] = d.foreign_net.rolling(5).sum() / d.Volume.rolling(5).sum()
    n = nw.loc[:asof]
    for c in n:
        s = n[c].reindex(d.index).fillna(0); d['news_' + c + '_z'] = (s - s.rolling(60).mean()) / s.rolling(60).std(); d['news_' + c] = s
    d['ma20'] = d.Close.rolling(20).mean(); d['ma60'] = d.Close.rolling(60).mean()
    delta = d.Close.diff(); up = delta.clip(lower=0).rolling(14).mean(); dn = (-delta.clip(upper=0)).rolling(14).mean(); d['rsi'] = 100 - 100 / (1 + up / dn)
    d['kq'] = k; d['kq_ma20'] = k.rolling(20).mean()
    d['peer_ret5'] = peers.loc[:asof].pct_change(5).mean(axis=1).reindex(d.index)
    d['ret5'] = d.Close.pct_change(5)
    d['retail_lev'] = d.retail_net.rolling(10).sum() / d.Volume.rolling(10).sum()
    d['retail_lev_hot'] = (d.retail_lev > 0.10) & (d.dist_ma60 > 0.25)
    s = sh.loc[:asof]
    d['short_ratio'] = (s.short_vol.reindex(d.index) / d.Volume * 100).where(d.index >= '2025-03-31')
    d['short_hot'] = (d.short_ratio > d.short_ratio.rolling(60, min_periods=20).quantile(.8)).fillna(False)
    bal = s.short_balance_qty.replace(0, np.nan).reindex(d.index).shift(2)          # T+2 publication lag
    d['short_cover'] = ((bal.pct_change(10) < -0.20) & (d.Close > d.ma20)).fillna(False)
    d['samsung_event'] = d.index.isin(SAM) | (d.news_rr_samsung >= 3)
    d['samsung_recent'] = d.samsung_event.rolling(10, min_periods=1).max().astype(bool)
    d['news_hot'] = (d.news_rainbow_all_z > 2) | (d.news_nvidia_robot_z > 2) | (d.news_humanoid_z > 2.5)
    d['kosdaq_ok'] = d.kq > d.kq_ma20
    e = dart[dart.date <= asof]
    def flag(m):
        g = pd.Series(False, index=d.index)
        for x in e[m].date:
            i = d.index.searchsorted(x)
            if i < len(d): g.iloc[i] = True
        return g
    neg = flag(e.report_nm.str.contains('손익구조|거래계획')) | (flag(e.report_nm.str.contains('특정증권')) & (d.ret < 0))
    d['dart_neg'] = neg.rolling(5, min_periods=1).max().astype(bool)
    d['days_to_sched'] = [(sched_all[sched_all >= t].min() - t).days if (sched_all >= t).any() else np.nan for t in d.index]
    return d

def signals(d):
    """v4 rules, verbatim."""
    S = {}
    base = d.range_60d < 0.60
    S['S1'] = ((d.foreign_5d_pct_vol > 0.08) & (d.vol_ratio > 1.8) & (d.ret > 0.03) & base & (d.Close > d.ma20),
               (d.foreign_5d_pct_vol < -0.05) & (d.Close < d.ma20))
    pre = d.days_to_sched.between(3, 14) & (d.Close > d.ma20) & d.kosdaq_ok
    S['S2'] = (pre & (d.ret5 > 0), (d.days_to_sched > 20) | (d.days_to_sched < 1) | (d.Close < d.ma20 * 0.97))
    shock = (d.news_hot | d.samsung_event) & (d.excess_ret > 0.04) & (d.ret > d.peer_ret5.fillna(0) / 5 + 0.03)
    S['S3'] = (shock & d.kosdaq_ok, (d.vol_ratio > 2.5) & (d.ret < -0.03) | (d.Close < d.ma20))
    overheat = (d.rsi > 80) & (d.dist_ma60 > 0.35) | d.retail_lev_hot | (d.short_hot & (d.Close < d.ma20))
    S['S4'] = ((d.Close > d.ma60) & (d.ma20 > d.ma60) & d.kosdaq_ok & (d.rsi.between(50, 72)) & ~d.retail_lev_hot,
               overheat | (d.Close < d.ma60) | (~d.kosdaq_ok & (d.Close < d.ma20)))
    sc = pd.DataFrame(index=d.index)
    sc['flow'] = np.sign(d.foreign_5d_pct_vol.fillna(0).where(d.foreign_5d_pct_vol.abs() > 0.03, 0))
    sc['kosdaq'] = np.where(d.kosdaq_ok, 1, -1)
    sc['short'] = np.where(d.short_hot & (d.Close < d.ma20), -1, 0)
    sc['leverage'] = np.where(d.retail_lev_hot, -1, 0)
    sc['samsung'] = np.where(d.samsung_recent, 1, 0)
    sc['calendar'] = np.where(d.days_to_sched.between(0, 14), 1, 0)
    sc['news'] = np.where(d.news_hot & (d.ret5 > 0), 1, 0)
    sc['tech'] = np.where((d.Close > d.ma20) & (d.ma20 > d.ma60), 1, np.where(d.Close < d.ma60, -1, 0)) + np.where(d.vol_ratio > 2, np.sign(d.ret), 0)
    score = sc.sum(axis=1)
    S['S5'] = (score >= 4, score <= 0)
    for k in ('S1', 'S4', 'S5'): S[k] = (S[k][0], S[k][1] | d.dart_neg)
    return {k: (a.fillna(False), b.fillna(False)) for k, (a, b) in S.items()}, score

def why_s4(r):
    x = []
    if r.rsi > 80 and r.dist_ma60 > 0.35: x.append('overheated (RSI>80 & >35% above MA60)')
    if r.retail_lev_hot: x.append('retail chasing (leverage proxy hot)')
    if r.short_hot and r.Close < r.ma20: x.append('heavy shorting below MA20')
    if r.Close < r.ma60: x.append('price below MA60')
    if (not r.kosdaq_ok) and r.Close < r.ma20: x.append('KOSDAQ weak & price below MA20')
    if r.dart_neg: x.append('DART warning filing')
    return '; '.join(x)

if __name__ == '__main__':
    days = px.loc[START:].index
    pos = {k: None for k in ['S1', 'S2', 'S3', 'S4', 'S5']}; cash = {k: 1.0 for k in pos}
    log, trades, pending = [], [], {}
    for n, t in enumerate(days):
        # 1) execute yesterday evening's orders at today's open
        o = px.Open.loc[t]
        for k, od in pending.items():
            if od == 'BUY': pos[k] = dict(entry=t, ep=o, hi=o, held=0)
            elif od[0] == 'SELL':
                p = pos[k]; r = o / p['ep'] - 1 - COST; cash[k] *= (1 + r)
                trades.append(dict(strategy=k, entry=p['entry'].date(), entry_px=p['ep'], exit=t.date(), exit_px=o, ret=r, days=p['held'], reason=od[1])); pos[k] = None
        pending = {}
        # 2) evening: rebuild everything from data public up to t, decide
        d = features(t); S, score = signals(d); r = d.iloc[-1]
        row = dict(date=t.date(), close=r.Close, open=o, score=score.iloc[-1], rsi=r.rsi, ma20=r.ma20, ma60=r.ma60, kosdaq_ok=bool(r.kosdaq_ok),
                   short_ratio=r.short_ratio, foreign_5d_pct_vol=r.foreign_5d_pct_vol, dart_neg=bool(r.dart_neg))
        last = n == len(days) - 1
        for k, (en, ex) in S.items():
            p = pos[k]
            if p is None:
                act = 'BUY' if en.iloc[-1] else 'WAIT'
                if act == 'BUY': pending[k] = 'BUY'
                why = ''
            else:
                if p['entry'] != t or True: p['hi'] = max(p['hi'], px.High.loc[t]); p['held'] = len(px.loc[p['entry']:t]) - 1
                stop = r.Close < p['hi'] * (1 - STOP)
                if ex.iloc[-1] or stop or p['held'] >= MAX_HOLD:
                    reason = 'trailing stop' if stop else ('max hold' if p['held'] >= MAX_HOLD else 'signal')
                    act = 'SELL'; pending[k] = ('SELL', reason)
                    why = reason if k != 'S4' or reason != 'signal' else why_s4(r)
                else: act, why = 'HOLD', ''
            row[k] = act; row[k + '_why'] = why
            row[k + '_equity'] = cash[k] * ((r.Close / p['ep'] * (1 - COST / 2)) if p is not None else 1)
        log.append(row)
        if n % 20 == 0: print(t.date(), {k: row[k] for k in pos}, round(row['S4_equity'], 3), file=sys.stderr)
    L = pd.DataFrame(log); L.to_csv('replay_2026_daily_log.csv', index=False)
    T = pd.DataFrame(trades); T.to_csv('replay_2026_trades.csv', index=False)
    json.dump({k: ({'entry': str(v['entry'].date()), 'ep': v['ep']} if v else None) for k, v in pos.items()} | {'orders_for_next_session': {k: (v if isinstance(v, str) else v[0]) for k, v in pending.items()}},
              open('replay_2026_open_positions.json', 'w'), indent=1)
    print(T.round(3).to_string()); print(json.load(open('replay_2026_open_positions.json')))
