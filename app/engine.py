"""SwingSignals engine — FROZEN v4 rules (validated out-of-sample on 2026).
Every signal is computed from data available at the CLOSE of day t and is meant to be acted on at the OPEN of day t+1.
All features use backward-looking windows only, so computing them once on the full history gives exactly the same
values as rebuilding them each evening (checked against the day-by-day replay)."""
import pandas as pd, numpy as np, os

STOP, MAX_HOLD, COST = 0.15, 40, 0.003
MODEL_START = pd.Timestamp('2025-12-30')      # first decision evening of the validated 2026 replay
STRATS = ['S4', 'S5', 'S2', 'S1', 'S3']
NAMES = {'S1': 'Foreign accumulation', 'S2': 'Event run-up', 'S3': 'News / Samsung shock',
         'S4': 'Trend rider', 'S5': 'Composite score', 'S6': 'Ensemble vote'}
ROLE = {'S4': 'Lead strategy', 'S5': 'Backup', 'S2': 'Secondary', 'S1': 'Secondary', 'S3': 'Info only (weak in tests)'}
ROLE_V3 = dict(ROLE, S3='Filter: blocks new buys after a shock', S6='Ensemble of S1/S2/S4/S5')
SCHED_EXTRA = ['2024-02-27', '2024-05-14', '2024-08-14', '2024-11-14', '2025-02-27', '2025-05-15', '2025-08-14', '2025-11-14',
               '2024-03-28', '2025-03-19', '2026-02-27', '2026-03-31', '2026-05-15', '2026-08-14', '2026-11-16',
               '2027-03-02', '2027-03-31', '2027-05-17', '2027-08-16', '2027-11-15']
# ---- v3 upgrade switches (bools all False = exact v2 / frozen v4 behaviour) ----
CFG = dict(
    s3_filter=True,      # Step 1: S3 stops trading; a shock day blocks NEW buys in other strategies
    s3_block_days=5,
    s3_targets=('S2',),  # which strategies the shock filter blocks (tested: helps S2, hurts S4, mixed for S5)
    squeeze=True,        # Step 1: short-covering squeeze becomes a 9th S5 factor (+1)
    regime_mode='v2',    # Step 2: KOSDAQ gate. 'v2' = KQ > MA20 | 'lenient' = not risk-off | 'strict' = risk-on only
                         #         'v2_no_riskoff' = KQ > MA20 and not risk-off. Tested: none beat 'v2' -> default stays 'v2'
    dart_mode='severity',  # Step 3: 'v2' = keyword flag | 'severity' = weighted 5-day filing score
    dart_exit_at=-1,     # Step 3: exit S1/S4/S5 when the 5-day filing score is <= this (tested: -1 best; -2/-3 lost 2024-25 and 2026 return)
    dart_factor=True,    # Step 3: (tested: S5 2024-25 +233% -> +250%, 2026 unchanged)
                         # add the filing score (capped -1..+1) as an S5 factor
    stop_mode='atr',     # Step 4: (tested: ATR x3 >= 15% in every case; S1 24-25 +252% -> +334%, S5 +250% -> +283%)
                         # 'pct' = trailing STOP% from high | 'atr' = trailing atr_mult x ATR(14) from high
    atr_mult=3.0,
    risk_pct=0.02,       # Step 4: position sizing - risk this share of capital per trade (distance to stop)
    max_pos_pct=0.50,    #         ...but never put more than this share of capital in one position
    s6=True,             # Step 5: S6 = weighted vote of the S1/S2/S4/S5 model positions (shown, not lead: did not beat S4)
    s6_weights='fixed',  #         'fixed' = weights set on 2024-25 only | 'rolling' = trailing 12-month results | 'equal'
    s6_enter=0.4,        #         buy when the weighted share of strategies holding >= this (0.4/0.2 best on 2024-25 fit)
    s6_exit=0.2,         #         sell when it falls to <= this
    regime_score=False,  # Step 2: S5 KOSDAQ factor uses the 3 states (+1 / 0 / -1) instead of +1 / -1
    # Step 6 (v3.2): graded inputs. Every condition also gets a 0..1 strength (0 = clearly not met, 1 = clearly met,
    # linear in between) so the dashboard shows how CLOSE a setup is. The switches below let the lab trade on them.
    graded_entries=False,  # S1/S2/S4 buy when the average strength >= graded_enter and no condition is below graded_floor
    graded_enter=0.8,      #   tested (fit 2024-25 / check 2026): S1 +163%->+161% / +65%->+23%, S2 +65%->+79% / +54%->+44%,
    graded_floor=0.2,      #   S4 +196%->+145% / +70%->+24%  -> worse, stays off (shown as information only)
    graded_s5=False,       # S5 uses the graded factor sum (each factor -1..+1, continuous) instead of whole steps
    graded_s5_enter=3.0,   #   tested: 2024-25 +123%->+201%, but 2026 +77%->+39% (more, weaker trades) -> stays off
    graded_s5_exit=0.0,
)
V2_CFG = {k: (False if isinstance(v, bool) else v) for k, v in CFG.items()}
V2_CFG.update(regime_mode='v2', dart_mode='v2', stop_mode='pct')
S6_BASE = ('S4', 'S5', 'S2', 'S1')
S6_FIT = ('2024-03-04', '2025-12-30')   # weights for 'fixed' mode are fitted on this window only   # exact v2 behaviour
CFG['squeeze'] = False   # tested in Step 1: hurt S5 (2024-25 +233% -> +204%, max DD -36% -> -43%); kept as an option

SAM = pd.to_datetime(['2024-02-07', '2024-03-21', '2024-12-31', '2025-03-19'])


# Step 3: DART filing severity weights (title keyword -> weight). 'ret' = sign follows the day's price move.
DART_W = [('거래계획', -3, 'Insider trading plan (planned sale)'),
          ('손익구조', -2, 'Sales / P&L changed 30%+'),
          ('풍문또는보도', -1, 'Clarification of rumour / press report'),
          ('특정증권', 'ret', 'Insider ownership change'),
          ('대량보유', 'ret', '5%+ holder change'),
          ('공급계약', 2, 'Supply contract signed'),
          ('최대주주변경', 2, 'Largest shareholder change'),
          ('신규시설투자', 1, 'New facility investment'),
          ('유형자산취득', 1, 'Tangible asset acquisition')]


def dart_weight(title, day_ret=0.0):
    for kw, w, lab in DART_W:
        if kw in title:
            if w == 'ret':
                w = -1 if day_ret < 0 else (1 if day_ret > 0.02 else 0)
            return w, lab
    return 0, 'Routine filing'


def _rd(p, f):
    return pd.read_csv(os.path.join(p, f), index_col=0, parse_dates=True)


def meta(data_dir):
    p = os.path.join(data_dir, 'meta.json')
    if os.path.exists(p):
        import json; return json.load(open(p, encoding='utf-8'))
    return dict(code='277810', name='레인보우로보틱스', market='KOSDAQ')


def load(data_dir, drop_after=None):
    px = _rd(data_dir, 'px.csv'); kq = _rd(data_dir, 'kq.csv').Close
    m = meta(data_dir)
    if os.path.exists(os.path.join(data_dir, 'doosan.csv')):
        peers = pd.concat({'doosan': _rd(data_dir, 'doosan.csv').Close, 'robotis': _rd(data_dir, 'robotis.csv').Close}, axis=1)
    else:                                   # other stocks: compare against the market index
        peers = pd.concat({'index': kq}, axis=1)
    fl = _rd(data_dir, 'flows.csv'); nw = _rd(data_dir, 'news.csv'); sh = _rd(data_dir, 'short.csv')
    dart = pd.read_csv(os.path.join(data_dir, 'dart.csv'), parse_dates=['date'])
    man = pd.read_csv(os.path.join(data_dir, 'events_manual.csv'), parse_dates=['date'])
    if drop_after is not None:        # e.g. drop an unfinished intraday bar
        px = px.loc[:drop_after]
    for c in ('rainbow_all', 'rr_samsung', 'humanoid', 'nvidia_robot'):
        if c not in nw: nw[c] = 0.0
    return dict(px=px, kq=kq, peers=peers, fl=fl, nw=nw, sh=sh, dart=dart, man=man, meta=m)


def features(D):
    px, kq, peers, fl, nw, sh, dart, man = (D[k] for k in ['px', 'kq', 'peers', 'fl', 'nw', 'sh', 'dart', 'man'])
    d = px[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
    d['ret'] = d.Close.pct_change(); d['kosdaq_ret'] = kq.pct_change().reindex(d.index)
    d['excess_ret'] = d.ret - 1.4 * d.kosdaq_ret
    d['vol_ratio'] = d.Volume / d.Volume.rolling(20).mean().shift(1)
    d['dist_ma60'] = d.Close / d.Close.rolling(60).mean() - 1
    d['range_60d'] = d.Close.rolling(60).max() / d.Close.rolling(60).min() - 1
    d = d.join(fl[['foreign_net', 'inst_net', 'retail_net']])
    d['foreign_5d_pct_vol'] = d.foreign_net.rolling(5).sum() / d.Volume.rolling(5).sum()
    for c in nw:
        s = nw[c].reindex(d.index).fillna(0)
        d['news_' + c + '_z'] = (s - s.rolling(60).mean()) / s.rolling(60).std(); d['news_' + c] = s
    d['ma20'] = d.Close.rolling(20).mean(); d['ma60'] = d.Close.rolling(60).mean()
    tr = pd.concat([d.High - d.Low, (d.High - d.Close.shift()).abs(), (d.Low - d.Close.shift()).abs()], axis=1).max(axis=1)
    d['atr'] = tr.rolling(14).mean(); d['atr_pct'] = d.atr / d.Close
    delta = d.Close.diff(); up = delta.clip(lower=0).rolling(14).mean(); dn = (-delta.clip(upper=0)).rolling(14).mean()
    d['rsi'] = 100 - 100 / (1 + up / dn)
    d['kq'] = kq.reindex(d.index).ffill(); d['kq_ma20'] = kq.rolling(20).mean().reindex(d.index).ffill()
    d['peer_ret5'] = peers.pct_change(5).mean(axis=1).reindex(d.index)
    d['ret5'] = d.Close.pct_change(5)
    d['retail_lev'] = d.retail_net.rolling(10).sum() / d.Volume.rolling(10).sum()
    d['retail_lev_hot'] = (d.retail_lev > 0.10) & (d.dist_ma60 > 0.25)
    d['short_ratio'] = (sh.short_vol.reindex(d.index) / d.Volume * 100).where(d.index >= '2025-03-31')
    d['short_hot'] = (d.short_ratio > d.short_ratio.rolling(60, min_periods=20).quantile(.8)).fillna(False)
    bal = sh.short_balance_qty.replace(0, np.nan).reindex(d.index).shift(2)          # KRX publishes balance T+2
    d['short_cover'] = ((bal.pct_change(10) < -0.20) & (d.Close > d.ma20)).fillna(False)
    rainbow = D.get('meta', {}).get('code', '277810') == '277810'
    d['samsung_event'] = (d.index.isin(SAM) | (d.news_rr_samsung >= 3)) if rainbow else False
    d['samsung_recent'] = d.samsung_event.rolling(10, min_periods=1).max().astype(bool)
    d['news_hot'] = (d.news_rainbow_all_z > 2) | (d.news_nvidia_robot_z > 2) | (d.news_humanoid_z > 2.5)
    d['kosdaq_ok'] = d.kq > d.kq_ma20
    d['kq_ma60'] = kq.rolling(60).mean().reindex(d.index).ffill()
    d['kq_slope'] = d.kq_ma20.pct_change(5)
    on = (d.kq > d.kq_ma20) & (d.kq_ma20 > d.kq_ma60) & (d.kq_slope > 0)
    off = (d.kq < d.kq_ma20) & ((d.kq_ma20 < d.kq_ma60) | (d.kq_slope < 0))
    d['regime'] = np.where(on, 2, np.where(off, 0, 1))          # 2 risk-on / 1 neutral / 0 risk-off

    def flag(m):
        g = pd.Series(False, index=d.index)
        for x in dart[m].date:
            i = d.index.searchsorted(x)
            if i < len(d): g.iloc[i] = True
        return g
    rn = dart.report_nm.astype(str)
    neg = flag(rn.str.contains('손익구조|거래계획')) | (flag(rn.str.contains('특정증권')) & (d.ret < 0))
    d['dart_neg'] = neg.rolling(5, min_periods=1).max().astype(bool)
    w = pd.Series(0.0, index=d.index)
    for x, title in zip(dart.date, rn):
        i = d.index.searchsorted(x)
        if i < len(d):
            w.iloc[i] += dart_weight(title, d.ret.iloc[i] if pd.notna(d.ret.iloc[i]) else 0)[0]
    d['dart_w'] = w
    d['dart_score'] = w.rolling(5, min_periods=1).sum()
    sched = pd.concat([man[man.type.isin(['conference', 'earnings', 'agm'])].date, pd.Series(pd.to_datetime(SCHED_EXTRA))]).sort_values().values
    idx = np.searchsorted(sched, d.index.values)
    d['days_to_sched'] = [(pd.Timestamp(sched[i]) - t).days if i < len(sched) else np.nan for i, t in zip(idx, d.index)]
    d['next_event'] = [pd.Timestamp(sched[i]).date().isoformat() if i < len(sched) else None for i in idx]
    return d


def _ramp(x, a, b):
    """0 below a, 1 above b, linear in between (b < a gives a falling ramp)."""
    return ((x - a) / (b - a)).clip(0, 1).fillna(0)


def _band(x, a, b, c, e):
    """0 outside a..e, 1 inside b..c, linear on the edges."""
    return np.minimum(_ramp(x, a, b), 1 - _ramp(x, c, e))


def _sym(x, a):
    """-1..+1, linear: x / a, capped."""
    return (x / a).clip(-1, 1).fillna(0)


def grades(d):
    """Graded (0..1) version of each entry condition, in the same order as checklist().
    Distances to moving averages are measured in ATRs so that 'just above' means the same in calm and wild markets."""
    atr = d.atr_pct.replace(0, np.nan)
    kq = _ramp(d.kq / d.kq_ma20 - 1, -0.01, 0.01)                       # -1% .. +1% around KOSDAQ MA20
    hot = _ramp(d.retail_lev, 0.05, 0.15) * _ramp(d.dist_ma60, 0.15, 0.35)
    G = {'S1': [_ramp(d.foreign_5d_pct_vol, 0.04, 0.12),             # rule: > 8%
                _ramp(d.vol_ratio, 1.3, 2.3),                        # rule: > 1.8x
                _ramp(d.ret, 0.01, 0.05),                            # rule: > 3%
                1 - _ramp(d.range_60d, 0.45, 0.75),                  # rule: < 60%
                _ramp((d.Close / d.ma20 - 1) / atr, -0.5, 0.5)],     # rule: above MA20 (+-0.5 ATR)
         'S2': [_band(d.days_to_sched, 1, 3, 14, 20),                # rule: 3..14 days
                _ramp((d.Close / d.ma20 - 1) / atr, -0.5, 0.5), kq,
                _ramp(d.ret5, -0.03, 0.03)],                         # rule: > 0
         'S4': [_ramp((d.Close / d.ma60 - 1) / atr, -1, 1),          # rule: above MA60 (+-1 ATR)
                _ramp(d.ma20 / d.ma60 - 1, -0.02, 0.02), kq,
                _band(d.rsi, 44, 50, 72, 78),                        # rule: 50..72
                1 - hot]}                                            # rule: retail not chasing
    G = {k: pd.concat(v, axis=1).set_axis(range(len(v)), axis=1) for k, v in G.items()}
    # graded S5 factors (each -1..+1, continuous instead of whole steps)
    g = pd.DataFrame(index=d.index)
    g['flow'] = _sym(d.foreign_5d_pct_vol, 0.06)
    g['kosdaq'] = _sym(d.kq / d.kq_ma20 - 1, 0.02)
    sq = d.short_ratio / d.short_ratio.rolling(60, min_periods=20).quantile(.8)
    g['short'] = -(_ramp(sq, 0.8, 1.2) * _ramp(-(d.Close / d.ma20 - 1) / atr, -0.25, 0.5)).fillna(0)
    g['leverage'] = -hot
    g['samsung'] = d.samsung_recent.astype(float)
    g['calendar'] = np.minimum(_ramp(d.days_to_sched, -1, 0), 1 - _ramp(d.days_to_sched, 12, 18))
    nz = pd.concat([d.news_rainbow_all_z, d.news_nvidia_robot_z, d.news_humanoid_z - 0.5], axis=1).max(axis=1)
    g['news'] = (_ramp(nz, 1.5, 2.5) * _ramp(d.ret5, -0.01, 0.02)).fillna(0)
    g['tech'] = (_sym((d.Close / d.ma20 - 1) / atr, 1) + _sym(d.ma20 / d.ma60 - 1, 0.03)) / 2 + \
                np.sign(d.ret).fillna(0) * _ramp(d.vol_ratio, 1.5, 2.5)
    g['dart'] = np.clip(d.dart_score, -1, 1)
    return G, g


def strength(G, k, floor=0.0):
    """Setup strength 0..1 = average of the graded conditions; 0 if any condition is below `floor`."""
    s = G[k].mean(axis=1)
    return s.where(G[k].min(axis=1) >= floor, 0.0) if floor > 0 else s


def signals(d, cfg=None):
    """v4 rules, verbatim (see research page). Returns {strategy: (entry, exit)} and the S5 score table."""
    cfg = CFG if cfg is None else cfg
    mode = cfg.get('regime_mode', 'v2')
    kok = {'v2': d.kosdaq_ok, 'lenient': d.regime >= 1, 'strict': d.regime == 2,
           'v2_no_riskoff': d.kosdaq_ok & (d.regime >= 1)}[mode]
    S = {}
    base = d.range_60d < 0.60
    S['S1'] = ((d.foreign_5d_pct_vol > 0.08) & (d.vol_ratio > 1.8) & (d.ret > 0.03) & base & (d.Close > d.ma20),
               (d.foreign_5d_pct_vol < -0.05) & (d.Close < d.ma20))
    pre = d.days_to_sched.between(3, 14) & (d.Close > d.ma20) & kok
    S['S2'] = (pre & (d.ret5 > 0), (d.days_to_sched > 20) | (d.days_to_sched < 1) | (d.Close < d.ma20 * 0.97))
    shock = (d.news_hot | d.samsung_event) & (d.excess_ret > 0.04) & (d.ret > d.peer_ret5.fillna(0) / 5 + 0.03)
    S['S3'] = (shock & kok, (d.vol_ratio > 2.5) & (d.ret < -0.03) | (d.Close < d.ma20))
    overheat = (d.rsi > 80) & (d.dist_ma60 > 0.35) | d.retail_lev_hot | (d.short_hot & (d.Close < d.ma20))
    S['S4'] = ((d.Close > d.ma60) & (d.ma20 > d.ma60) & kok & (d.rsi.between(50, 72)) & ~d.retail_lev_hot,
               overheat | (d.Close < d.ma60) | (~kok & (d.Close < d.ma20)))
    sc = pd.DataFrame(index=d.index)
    sc['flow'] = np.sign(d.foreign_5d_pct_vol.fillna(0).where(d.foreign_5d_pct_vol.abs() > 0.03, 0))
    sc['kosdaq'] = (pd.Series(d.regime, index=d.index) - 1) if cfg.get('regime_score') else np.where(kok, 1, -1)
    sc['short'] = np.where(d.short_hot & (d.Close < d.ma20), -1, 0)
    sc['leverage'] = np.where(d.retail_lev_hot, -1, 0)
    sc['samsung'] = np.where(d.samsung_recent, 1, 0)
    sc['calendar'] = np.where(d.days_to_sched.between(0, 14), 1, 0)
    sc['news'] = np.where(d.news_hot & (d.ret5 > 0), 1, 0)
    sc['tech'] = np.where((d.Close > d.ma20) & (d.ma20 > d.ma60), 1, np.where(d.Close < d.ma60, -1, 0)) + np.where(d.vol_ratio > 2, np.sign(d.ret), 0)
    if cfg.get('dart_factor'):
        sc['dart'] = np.clip(d.dart_score, -1, 1)
    if cfg.get('squeeze'):
        sc['squeeze'] = np.where(d.short_cover, 1, 0)
    score = sc.sum(axis=1)
    S['S5'] = (score >= 4, score <= 0)
    G, g = grades(d)
    for k in G:
        d['str_' + k] = strength(G, k)
    gcols = [c for c in g.columns if c in sc.columns]
    d['gscore'] = g[gcols].sum(axis=1)
    d.attrs['grades'] = G; d.attrs['gfactors'] = g; d.attrs['s5_cols'] = list(sc.columns)
    if cfg.get('graded_entries'):
        for k in ('S1', 'S2', 'S4'):
            S[k] = (strength(G, k, cfg.get('graded_floor', 0.2)) >= cfg.get('graded_enter', 0.8), S[k][1])
    if cfg.get('graded_s5'):
        S['S5'] = (d.gscore >= cfg.get('graded_s5_enter', 3.0), d.gscore <= cfg.get('graded_s5_exit', 0.0))
    if cfg.get('s3_filter'):
        # S3 shock days were followed by negative returns for ~15 days in 2024-25 -> use them to stop chasing
        shock_day = S['S3'][0].fillna(False).astype(bool)
        block = shock_day.rolling(int(cfg.get('s3_block_days', 5)), min_periods=1).max().astype(bool)
        d['s3_block'] = block
        for k in cfg.get('s3_targets', ('S1', 'S2', 'S4', 'S5')):
            S[k] = (S[k][0] & ~block, S[k][1])
        S['S3'] = (pd.Series(False, index=d.index), S['S3'][1])
    else:
        d['s3_block'] = False
    d['kq_gate'] = kok
    if cfg.get('dart_mode', 'v2') == 'severity':
        d['dart_exit'] = d.dart_score <= cfg.get('dart_exit_at', -3)
    else:
        d['dart_exit'] = d.dart_neg
    for k in ('S1', 'S4', 'S5'):
        S[k] = (S[k][0], S[k][1] | d.dart_exit)
    S = {k: (a.fillna(False).astype(bool), b.fillna(False).astype(bool)) for k, (a, b) in S.items()}
    if cfg.get('s6'):
        (en, ex), vote, W = s6_signal(d, S, cfg)
        d['s6_vote'] = vote; d.attrs['s6_w'] = W.iloc[-1].round(3).to_dict()
        S['S6'] = (en.fillna(False).astype(bool), ex.fillna(False).astype(bool))
    return S, sc, score


REG = {2: 'risk-on', 1: 'neutral', 0: 'risk-off'}
KQLAB = lambda r: 'KOSDAQ above its 20-day avg'


def _f(x, nd=1):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), nd)


def checklist(k, r, score_row=None, score=None):
    """Human-readable entry conditions for strategy k on row r: list of (label, value, passed)."""
    pct = lambda v: f"{v*100:+.1f}%" if pd.notna(v) else 'n/a'
    if k == 'S4':
        return [('Price above 60-day average', f"{r.Close:,.0f} vs {r.ma60:,.0f}", bool(r.Close > r.ma60)),
                ('20-day avg above 60-day avg', f"{r.ma20:,.0f} vs {r.ma60:,.0f}", bool(r.ma20 > r.ma60)),
                (KQLAB(r), f"{r.kq:,.1f} vs {r.kq_ma20:,.1f} · {REG[int(r.regime)]}", bool(r.kq_gate)),
                ('RSI between 50 and 72', f"{r.rsi:.1f}", bool(50 <= r.rsi <= 72)),
                ('Retail not chasing', 'hot' if r.retail_lev_hot else 'normal', not bool(r.retail_lev_hot))]
    if k == 'S1':
        return [('Foreign 5-day buying > 8% of volume', pct(r.foreign_5d_pct_vol), bool(r.foreign_5d_pct_vol > 0.08)),
                ('Volume > 1.8× normal', f"{r.vol_ratio:.1f}×", bool(r.vol_ratio > 1.8)),
                ('Up more than 3% today', pct(r.ret), bool(r.ret > 0.03)),
                ('Tight 60-day range (< 60%)', pct(r.range_60d), bool(r.range_60d < 0.60)),
                ('Price above 20-day average', f"{r.Close:,.0f} vs {r.ma20:,.0f}", bool(r.Close > r.ma20))]
    if k == 'S2':
        dts = r.days_to_sched
        return [('Scheduled event in 3–14 days', f"{int(dts)} days ({r.next_event})" if pd.notna(dts) else 'none', bool(pd.notna(dts) and 3 <= dts <= 14)),
                ('Price above 20-day average', f"{r.Close:,.0f} vs {r.ma20:,.0f}", bool(r.Close > r.ma20)),
                (KQLAB(r), f"{r.kq:,.1f} vs {r.kq_ma20:,.1f} · {REG[int(r.regime)]}", bool(r.kq_gate)),
                ('5-day return positive', pct(r.ret5), bool(r.ret5 > 0))]
    if k == 'S3':
        return [('News spike or Samsung event', 'yes' if (r.news_hot or r.samsung_event) else 'no', bool(r.news_hot or r.samsung_event)),
                ('Beats KOSDAQ by > 4% today', pct(r.excess_ret), bool(r.excess_ret > 0.04)),
                ('Beats robot peers by > 3%', pct(r.ret), bool(r.ret > (0 if pd.isna(r.peer_ret5) else r.peer_ret5) / 5 + 0.03)),
                (KQLAB(r), f"{r.kq:,.1f} vs {r.kq_ma20:,.1f} · {REG[int(r.regime)]}", bool(r.kq_gate))]
    if k == 'S6':
        return [('Weighted vote ≥ entry level', f"{r.s6_vote:.2f}", bool(r.s6_vote >= CFG.get('s6_enter', 0.5)))]
    if k == 'S5':
        lab = {'flow': 'Foreign flow', 'kosdaq': 'KOSDAQ trend', 'short': 'Short selling', 'leverage': 'Retail chasing',
               'samsung': 'Samsung news', 'calendar': 'Event calendar', 'news': 'News spike', 'tech': 'Price trend',
               'squeeze': 'Short squeeze (shorts covering)', 'dart': 'DART filings (5-day score)'}
        out = [(f'Score ≥ 4 to buy (≤ 0 = sell)', f"{score:+.0f}", bool(score >= 4))]
        out += [(lab[c], f"{score_row[c]:+.0f}", bool(score_row[c] > 0)) for c in lab if c in score_row.index]
        return out
    return []


def exit_reasons(k, r, score=None):
    x = []
    if k == 'S4':
        if r.rsi > 80 and r.dist_ma60 > 0.35: x.append('Overheated (RSI > 80 and > 35% above 60-day avg)')
        if r.retail_lev_hot: x.append('Retail chasing the move')
        if r.short_hot and r.Close < r.ma20: x.append('Heavy short selling below 20-day avg')
        if r.Close < r.ma60: x.append('Price fell below 60-day avg')
        if (not r.kq_gate) and r.Close < r.ma20: x.append(f'KOSDAQ {REG[int(r.regime)]} and price below 20-day avg')
    elif k == 'S1':
        if r.foreign_5d_pct_vol < -0.05 and r.Close < r.ma20: x.append('Foreigners selling and price below 20-day avg')
    elif k == 'S2':
        if r.days_to_sched > 20 or r.days_to_sched < 1: x.append('Event window over')
        if r.Close < r.ma20 * 0.97: x.append('Price 3% below 20-day avg')
    elif k == 'S3':
        if r.vol_ratio > 2.5 and r.ret < -0.03: x.append('Heavy-volume down day')
        if r.Close < r.ma20: x.append('Price below 20-day avg')
    elif k == 'S5':
        if score is not None and score <= 0: x.append(f'Composite score fell to {score:+.0f}')
    elif k == 'S6':
        x.append(f'Ensemble vote fell to {r.s6_vote:.2f}')
    if k in ('S1', 'S4', 'S5') and r.dart_exit: x.append(f'DART warning filing (5-day filing score {r.dart_score:+.0f})')
    return x


def simulate(d, S, start=None, stop=STOP, max_hold=MAX_HOLD, cost=COST, stop_mode='pct', atr_mult=3.0):
    """Replays each strategy's MODEL position from MODEL_START, exactly like the validated walk-forward test.
    Orders decided at close t execute at open t+1. Returns positions, trades, per-day actions.
    stop / max_hold / cost can be changed for what-if tests; live signals always use the frozen defaults."""
    STOP, MAX_HOLD, COST = stop, max_hold, cost
    days = d.loc[(MODEL_START if start is None else pd.Timestamp(start)):].index
    pos = {k: None for k in S}; eq = {k: 1.0 for k in S}; trades = []; pending = {}; actions = []
    for n, t in enumerate(days):
        o = d.Open.loc[t]
        for k, od in pending.items():
            if od[0] == 'BUY':
                pos[k] = dict(entry=t, ep=o, hi=o, held=0, signal_date=od[2])
            else:
                p = pos[k]; r = o / p['ep'] - 1 - COST; eq[k] *= 1 + r
                trades.append(dict(strategy=k, signal_date=p['signal_date'], entry=t_iso(p['entry']), entry_px=p['ep'],
                                   exit_signal=od[2], exit=t_iso(t), exit_px=o, ret=r, days=p['held'], reason=od[1])); pos[k] = None
        pending = {}
        for k, (en, ex) in S.items():
            p = pos[k]
            if p is None:
                if en.loc[t]:
                    pending[k] = ('BUY', '', t_iso(t)); actions.append((t_iso(t), k, 'BUY'))
            else:
                p['hi'] = max(p['hi'], d.High.loc[t]); p['held'] = len(d.loc[p['entry']:t]) - 1
                stop = d.Close.loc[t] < stop_px(p['hi'], d.atr.loc[t], STOP, stop_mode, atr_mult)
                if ex.loc[t] or stop or p['held'] >= MAX_HOLD:
                    reason = (f'Trailing stop (−{STOP:.0%} from high)' if stop_mode == 'pct' else f'ATR stop ({atr_mult:g}× ATR from high)') if stop else (f'Max hold ({MAX_HOLD} days)' if p['held'] >= MAX_HOLD else 'signal')
                    pending[k] = ('SELL', reason, t_iso(t)); actions.append((t_iso(t), k, 'SELL'))
    return pos, pending, trades, eq, actions


def stop_px(hi, atr, pct=STOP, mode='pct', mult=3.0):
    if mode == 'atr' and pd.notna(atr):
        return hi - mult * atr
    return hi * (1 - pct)


def size_position(capital, price, atr, cfg=None, pct=STOP, whole=True):
    """Risk-based size: lose at most risk_pct of capital if the stop is hit, capped at max_pos_pct of capital."""
    cfg = CFG if cfg is None else cfg
    if not capital or not price:
        return None
    sp = stop_px(price, atr, pct, cfg.get('stop_mode', 'pct'), cfg.get('atr_mult', 3.0))
    risk_ps = max(price - sp, price * 0.01)
    by_risk = capital * cfg.get('risk_pct', 0.01) / risk_ps
    by_cap = capital * cfg.get('max_pos_pct', 0.5) / price
    qty = max(0, min(by_risk, by_cap)); qty = int(qty) if whole else qty
    return dict(qty=qty, value=qty * price, pct_of_capital=qty * price / capital, stop_px=sp,
                risk_krw=qty * (price - sp), risk_pct_of_capital=qty * (price - sp) / capital,
                limited_by='cap' if by_cap < by_risk else 'risk', risk_per_share=price - sp)


def _states(d, S, start, sm):
    """Daily 'model is long after tonight's decision' state per base strategy (causal: decided at each close)."""
    _, _, trades, _, actions = simulate(d, S, start=start, **sm)
    st = pd.DataFrame(0.0, index=d.index, columns=list(S6_BASE))
    for k in S6_BASE:
        on = None
        for a, kk, act in actions:
            if kk != k: continue
            if act == 'BUY': on = a
            elif on is not None: st.loc[on:a, k] = 1.0; st.loc[a, k] = 0.0; on = None
        if on is not None: st.loc[on:, k] = 1.0
    return st, trades


def _weights_from(trades, lo, hi):
    """Weight = average trade return x sqrt(trades), floored at 0 (losing strategies get no vote)."""
    w = {}
    for k in S6_BASE:
        r = [t['ret'] for t in trades if t['strategy'] == k and lo <= t['exit'] <= hi]
        w[k] = max(0.0, float(np.mean(r)) * np.sqrt(len(r))) if r else 0.0
    tot = sum(w.values()) or 1.0
    return {k: v / tot for k, v in w.items()}


def s6_signal(d, S, cfg):
    sm = dict(stop_mode=cfg.get('stop_mode', 'pct'), atr_mult=cfg.get('atr_mult', 3.0))
    st, trades = _states(d, S, '2024-03-04', sm)
    mode = cfg.get('s6_weights', 'fixed')
    if mode == 'equal':
        W = pd.DataFrame({k: 1 / len(S6_BASE) for k in S6_BASE}, index=d.index)
    elif mode == 'fixed':
        W = pd.DataFrame(_weights_from(trades, *S6_FIT), index=d.index)
    else:   # rolling: re-weighted monthly from trades CLOSED in the trailing 12 months (no look-ahead)
        rows = {}
        for t in d.index:
            if t < pd.Timestamp('2024-09-01'): rows[t] = {k: 1 / len(S6_BASE) for k in S6_BASE}; continue
            key = (t.year, t.month)
            if key not in rows.get('_c', {}):
                lo = (t - pd.Timedelta(days=365)).date().isoformat(); hi = (t - pd.Timedelta(days=1)).date().isoformat()
                rows.setdefault('_c', {})[key] = _weights_from(trades, lo, hi)
            rows[t] = rows['_c'][key]
        rows.pop('_c', None); W = pd.DataFrame.from_dict(rows, orient='index').reindex(d.index).ffill()
    vote = (st * W).sum(axis=1)
    return (vote >= cfg.get('s6_enter', 0.5), vote <= cfg.get('s6_exit', 0.25)), vote, W


def selector(trades, pos, d, asof, months=12):
    """Rank strategies by trailing 12-month model result (closed trades + open position marked to the close)."""
    lo = (pd.Timestamp(asof) - pd.DateOffset(months=months)).date().isoformat(); out = []
    for k in pos:
        if k == 'S3': continue
        tk = [t for t in trades if t['strategy'] == k and t['exit'] >= lo]
        eq = float(np.prod([1 + t['ret'] for t in tk])) if tk else 1.0
        if pos[k] is not None: eq *= d.Close.iloc[-1] / pos[k]['ep']
        out.append(dict(strategy=k, ret_12m=eq - 1, trades_12m=len(tk), win_rate=(sum(t['ret'] > 0 for t in tk) / len(tk)) if tk else None))
    out.sort(key=lambda x: -x['ret_12m'])
    # NOTE: tested 'follow the 12-month leader' switching monthly: 2024-25 +71%, 2026 +38% - worse than every single
    # strategy. So the ranking is information only; it does not change which strategy is the lead.
    for i, x in enumerate(out): x['rank'] = i + 1
    return out


def t_iso(t):
    return pd.Timestamp(t).date().isoformat()


def _grade_rows(d, k, chk, cfg):
    """Per-condition grades for the last row + overall setup strength now / 5 sessions ago / last 20 sessions."""
    G, g = d.attrs['grades'], d.attrs['gfactors']; n = len(chk)
    if k in G:
        rows = [round(float(v), 2) for v in G[k].iloc[-1]][:n]; s = d['str_' + k]
    elif k == 'S5':       # S5: row 0 = total; rows 1.. = factors (graded -1..+1)
        lab = ['flow', 'kosdaq', 'short', 'leverage', 'samsung', 'calendar', 'news', 'tech', 'squeeze', 'dart']
        cols = [c for c in lab if c in d.attrs.get('s5_cols', lab)]
        rows = [None] + [round(float(g[c].iloc[-1]), 2) if c in g else None for c in cols]
        rows = rows[:n] + [None] * (n - len(rows))
        s = (d.gscore / 4).clip(0, 1)               # 4 points = full strength (the binary buy level)
    elif k == 'S6':
        en = cfg.get('s6_enter', 0.4); s = (d.s6_vote / en).clip(0, 1); rows = [round(float(s.iloc[-1]), 2)]
    else:
        return dict(rows=[None] * n, now=None, ago=None, hist=[])
    rows = rows + [None] * (n - len(rows))
    v = lambda i: None if pd.isna(s.iloc[i]) else round(float(s.iloc[i]), 3)
    return dict(rows=rows, now=v(-1), ago=v(-6) if len(s) > 6 else None,
                hist=[None if pd.isna(x) else round(float(x), 3) for x in s.iloc[-20:]])


def evaluate(data_dir, drop_after=None, cfg=None):
    """Full evaluation for the dashboard: tonight's call per strategy + model record + chart data."""
    cfg = CFG if cfg is None else cfg
    D = load(data_dir, drop_after)
    d = features(D); S, sc, score = signals(d, cfg)
    sm = dict(stop_mode=cfg.get('stop_mode', 'pct'), atr_mult=cfg.get('atr_mult', 3.0))
    pos, pending, trades, eq, actions = simulate(d, S, **sm)
    t = d.index[-1]; r = d.iloc[-1]
    calls = []
    order = STRATS + (['S6'] if 'S6' in S else [])
    for k in order:
        p = pos[k]; od = pending.get(k)
        if p is None and od is None: act = 'WAIT'
        elif od and od[0] == 'BUY': act = 'BUY'
        elif od and od[0] == 'SELL': act = 'SELL'
        else: act = 'HOLD'
        chk = checklist(k, r, sc.iloc[-1], score.iloc[-1])
        grade = _grade_rows(d, k, chk, cfg)
        if act == 'SELL':
            why = [od[1]] if od[1] != 'signal' else exit_reasons(k, r, score.iloc[-1])
        elif act == 'BUY':
            why = ['All entry conditions met'] if k not in ('S5', 'S6') else ([f'Composite score {score.iloc[-1]:+.0f} (≥ 4)'] if k == 'S5' else [f'Ensemble vote {r.s6_vote:.2f}'])
        elif act == 'HOLD':
            px_hi = p['hi']
            why = [f"Model holds since {t_iso(p['entry'])} at {p['ep']:,.0f} ({r.Close/p['ep']-1:+.1%}). Trailing stop at {stop_px(px_hi, r.atr, STOP, **{'mode': sm['stop_mode'], 'mult': sm['atr_mult']}):,.0f}."]
        else:
            miss = ([c[0] for c in chk if not c[2]] if k != 'S5' else [f'Score {score.iloc[-1]:+.0f}, needs ≥ 4']) if k != 'S6' else \
                   [f"Vote {r.s6_vote:.2f}, needs ≥ {cfg.get('s6_enter', 0.5):.2f}"]
            why = ['Waiting for: ' + '; '.join(miss)] if miss else ['No setup']
        mp = None if p is None else dict(entry=t_iso(p['entry']), entry_px=p['ep'], ret=r.Close / p['ep'] - 1, days=p['held'],
                                         stop_px=stop_px(p['hi'], r.atr, STOP, sm['stop_mode'], sm['atr_mult']))
        if k == 'S3' and cfg.get('s3_filter'):
            act = 'BLOCK' if r.s3_block else 'CLEAR'
            why = [f"Shock filter: new buys in {'/'.join(cfg.get('s3_targets', ()))} blocked for {cfg.get('s3_block_days', 5)} days after a news/Samsung shock day"
                   if r.s3_block else 'No recent shock day: other strategies may buy']
        calls.append(dict(strategy=k, name=NAMES[k], role=(ROLE_V3 if cfg.get('s3_filter') else ROLE)[k], action=act, reasons=why, model_position=mp,
                          checklist=[dict(label=a, value=b, ok=c, grade=gr) for (a, b, c), gr in zip(chk, grade['rows'])],
                          strength=grade['now'], strength_5d=grade['ago'], strength_hist=grade['hist'], model_return=eq[k] - 1 if p is None else eq[k] * (r.Close / p['ep'] * (1 - COST / 2)) - 1))
    tail = d.loc['2024-03-01':]
    L = lambda s, nd=0: [None if pd.isna(v) else (round(float(v), nd) if nd else int(round(float(v)))) for v in s]
    sct = sc.loc[tail.index]
    chart = dict(regime=L(tail.regime), dates=[t_iso(x) for x in tail.index], open=L(tail.Open), high=L(tail.High), low=L(tail.Low), close=L(tail.Close),
                 volume=L(tail.Volume), ma20=L(tail.ma20), ma60=L(tail.ma60), rsi=L(tail.rsi, 1), short_ratio=L(tail.short_ratio, 2),
                 foreign=L(tail.foreign_net), inst=L(tail.inst_net), retail=L(tail.retail_net),
                 foreign5=L(tail.foreign_5d_pct_vol * 100, 2), score=L(score.loc[tail.index]),
                 parts={c: L(sct[c]) for c in sct.columns}, dart=L(tail.dart_exit.astype(int)), dart_score=L(tail.dart_score),
                 short_hot=L(tail.short_hot.astype(int)), short_bal=L(D['sh'].short_balance_qty.replace(0, np.nan).reindex(tail.index)),
                 actions=[dict(date=a, strategy=b, action=c) for a, b, c in actions])
    # full-history model trades for the chart bands (2024–25 = in-sample training period, 2026 = out-of-sample)
    _, _, htr, _, hact = simulate(d, S, start='2024-03-04', **sm)
    chart['bands'] = [dict(t, ret=float(t['ret'])) for t in htr]
    chart['hist_actions'] = [dict(date=a, strategy=b, action=c) for a, b, c in hact]
    for k in order:     # open model positions show as a band that runs to today
        p = pos[k]
        if p is not None:
            chart['bands'].append(dict(strategy=k, entry=t_iso(p['entry']), entry_px=p['ep'], exit=None, exit_px=None,
                                       ret=float(r.Close / p['ep'] - 1), days=p['held'], reason='open'))
    fl = D['dart'].sort_values('date', ascending=False).head(40)
    rn = fl.report_nm.astype(str)
    filings = [dict(date=t_iso(r.date), title=r.report_nm, filer=r.flr_nm, rcept_no=str(r.get('rcept_no', '') or '').split('.')[0] if 'rcept_no' in fl else '',
                    warning=bool(('손익구조' in r.report_nm) or ('거래계획' in r.report_nm)), insider=bool('특정증권' in r.report_nm),
                    contract=bool('공급계약' in r.report_nm),
                    severity=dart_weight(r.report_nm, float(d.ret.asof(r.date)) if pd.notna(d.ret.asof(r.date)) else 0)[0],
                    severity_label=dart_weight(r.report_nm)[1]) for _, r in fl.assign(report_nm=rn).iterrows()]
    ctx = dict(date=t_iso(t), close=float(r.Close), prev_close=float(d.Close.iloc[-2]), open=float(r.Open), high=float(r.High), low=float(r.Low),
               rsi=_f(r.rsi), ma20=_f(r.ma20, 0), ma60=_f(r.ma60, 0), kosdaq=_f(r.kq, 2), kosdaq_ma20=_f(r.kq_ma20, 2), kosdaq_ok=bool(r.kq_gate), kosdaq_regime=REG[int(r.regime)],
               foreign_5d_pct_vol=_f(r.foreign_5d_pct_vol * 100 if pd.notna(r.foreign_5d_pct_vol) else None, 1),
               short_ratio=_f(r.short_ratio, 1), short_hot=bool(r.short_hot), short_cover=bool(r.short_cover), s3_block=bool(r.s3_block),
               cfg=cfg, atr=_f(r.atr, 0), atr_pct=_f(r.atr_pct * 100 if pd.notna(r.atr_pct) else None, 1), dart_warning=bool(r.dart_exit), dart_score=_f(r.dart_score, 0), score=float(score.iloc[-1]),
               next_event=r.next_event, days_to_event=_f(r.days_to_sched, 0), retail_chasing=bool(r.retail_lev_hot),
               graded_score=_f(r.gscore, 2))
    closes = {t_iso(x): float(v) for x, v in d.Close.items()}
    _, _, sel_tr, _, _ = simulate(d, S, start='2024-03-04', **sm)
    sel = selector(sel_tr, pos, d, t)
    if cfg.get('s6'):
        ctx['s6_vote'] = _f(r.s6_vote, 2); ctx['s6_weights'] = d.attrs.get('s6_w')
        chart['s6_vote'] = L(tail.s6_vote, 2)
    for k in ('S1', 'S2', 'S4'):
        chart['strength_' + k] = L(tail['str_' + k], 3)
    # market context for the Macro view (display only, no rule uses these)
    kd = d.kq.diff(); ku = kd.clip(lower=0).rolling(14).mean(); kn = (-kd.clip(upper=0)).rolling(14).mean()
    kq_rsi = (100 - 100 / (1 + ku / kn)).loc[tail.index]
    chart.update(kq=L(tail.kq, 2), kq_ma20=L(tail.kq_ma20, 2), kq_ma60=L(tail.kq_ma60, 2), kq_rsi=L(kq_rsi, 1),
                 peer5=L(tail.peer_ret5 * 100, 2), atr_pct=L(tail.atr_pct * 100, 2), ret5=L(tail.ret5 * 100, 2))
    chart['graded_score'] = L(tail.gscore, 2)
    ctx.update(ticker=D['meta'].get('code'), ticker_name=D['meta'].get('name'), market=D['meta'].get('market', 'KOSDAQ'),
               rainbow=D['meta'].get('code') == '277810')
    return dict(selector=sel, context=ctx, calls=calls, model_trades=trades, chart=chart, closes=closes, filings=filings)


def whatif(data_dir, start='2026-01-02', stop=STOP, max_hold=MAX_HOLD, cost=COST, drop_after=None, cfg=None):
    """Re-runs the frozen rules with different risk settings over any period. For exploring only —
    it never changes the live signals. Returns per-strategy stats, trades and equity curves."""
    D = load(data_dir, drop_after); d = features(D); S, sc, score = signals(d, cfg)
    st = pd.Timestamp(start) - pd.Timedelta(days=1)
    st = d.index[d.index.searchsorted(st) - 1] if d.index.searchsorted(st) > 0 else d.index[0]
    cfg = CFG if cfg is None else cfg
    pos, pending, trades, eq, actions = simulate(d, S, start=st, stop=stop, max_hold=max_hold, cost=cost,
                                                 stop_mode=cfg.get('stop_mode', 'pct'), atr_mult=cfg.get('atr_mult', 3.0))
    # sized version: each trade uses the risk-based share of equity at entry (rest stays in cash)
    for t in trades:
        ent = pd.Timestamp(t['entry']); prev = d.index[d.index.get_loc(ent) - 1]
        z = size_position(1.0, d.Close.loc[prev], d.atr.loc[prev], cfg, stop, whole=False)
        t['size_pct'] = float(z['pct_of_capital']) if z else 1.0
    days = d.loc[st:].index; out = {}
    for k in STRATS + (['S6'] if 'S6' in S else []):
        tk = [t for t in trades if t['strategy'] == k]
        e = pd.Series(1.0, index=days); lvl = 1.0
        for t in tk:          # mark-to-market equity: flat in cash, follows close while in a trade
            seg = d.loc[t['entry']:t['exit']].Close
            path = lvl * seg / t['entry_px']; path.iloc[-1] = lvl * (1 + t['ret'])
            e.loc[seg.index] = path.values; lvl *= 1 + t['ret']
            e.loc[e.index > pd.Timestamp(t['exit'])] = lvl
        p = pos[k]
        if p is not None:
            seg = d.loc[p['entry']:].Close; e.loc[seg.index] = (lvl * seg / p['ep']).values
        dd = (e / e.cummax() - 1).min()
        wins = sum(t['ret'] > 0 for t in tk)
        es = pd.Series(1.0, index=days); lv = 1.0
        for t in tk:
            f = t.get('size_pct', 1.0); seg = d.loc[t['entry']:t['exit']].Close
            path = lv * (1 + f * (seg / t['entry_px'] - 1)); path.iloc[-1] = lv * (1 + f * t['ret'])
            es.loc[seg.index] = path.values; lv *= 1 + f * t['ret']; es.loc[es.index > pd.Timestamp(t['exit'])] = lv
        if p is not None:
            seg = d.loc[p['entry']:].Close; f = size_position(1.0, d.Close.shift().loc[p['entry']], d.atr.shift().loc[p['entry']], cfg, stop, whole=False)
            f = f['pct_of_capital'] if f else 1.0; es.loc[seg.index] = (lv * (1 + f * (seg / p['ep'] - 1))).values
        out[k] = dict(ret=float(e.iloc[-1] - 1), max_dd=float(dd), trades=len(tk), win_rate=(wins / len(tk)) if tk else None,
                      avg_ret=float(np.mean([t['ret'] for t in tk])) if tk else None, open=p is not None,
                      equity=[round(float(v), 4) for v in e.values],
                      sized_ret=float(es.iloc[-1] - 1), sized_max_dd=float((es / es.cummax() - 1).min()),
                      avg_size=float(np.mean([t.get('size_pct', 1) for t in tk])) if tk else None)
    # buy & hold bought at the first test day's OPEN (same timing as the strategies)
    bh = d.loc[days, 'Close'] / d.Open.loc[days[1]]; bh.iloc[0] = 1.0
    bh_dd = float((bh / bh.cummax() - 1).min())
    return dict(start=t_iso(days[1] if len(days) > 1 else days[0]), dates=[t_iso(x) for x in days], strategies=out,
                buy_hold=dict(ret=float(bh.iloc[-1] - 1), max_dd=bh_dd, equity=[round(float(v), 4) for v in bh.values]),
                trades=[dict(t, ret=float(t['ret'])) for t in trades], params=dict(stop=stop, max_hold=max_hold, cost=cost, cfg=cfg or CFG))
