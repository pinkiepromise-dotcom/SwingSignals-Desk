"""SwingSignals - 5 trigger strategies for Rainbow Robotics (277810), backtest 2024-2025.
Signals are computed at the CLOSE of day t, executed at the OPEN of day t+1. Round-trip cost 0.30%.
Optional real data (auto-used if present): short_277810.csv (collect_short.py), margin_277810.csv (collect_margin.py)."""
import pandas as pd, numpy as np, yfinance as yf, os, warnings; warnings.filterwarnings('ignore')
COST=0.003
def load():
    d=pd.read_csv('features_277810.csv',index_col=0,parse_dates=True)
    o=yf.download('277810.KQ',start='2023-10-01',end='2026-01-01',progress=False,auto_adjust=True); o.columns=[c[0] for c in o.columns]
    kq=yf.download('^KQ11',start='2023-10-01',end='2026-01-01',progress=False,auto_adjust=True)['Close'].squeeze()
    peers=pd.concat({k:yf.download(v,start='2023-10-01',end='2026-01-01',progress=False,auto_adjust=True)['Close'].squeeze() for k,v in {'doosan':'454910.KS','robotis':'108490.KQ'}.items()},axis=1)
    full=o[['Open','High','Low','Close']].copy()
    full['ma20']=full.Close.rolling(20).mean(); full['ma60']=full.Close.rolling(60).mean()
    delta=full.Close.diff(); up=delta.clip(lower=0).rolling(14).mean(); dn=(-delta.clip(upper=0)).rolling(14).mean(); full['rsi']=100-100/(1+up/dn)
    full['kq']=kq; full['kq_ma20']=kq.rolling(20).mean(); full['kq_ret5']=kq.pct_change(5)
    full['peer_ret5']=peers.pct_change(5).mean(axis=1).reindex(full.index)
    d=d.join(full[['Open','High','Low','ma20','ma60','rsi','kq','kq_ma20','kq_ret5','peer_ret5']])
    d['ret5']=d.Close.pct_change(5)
    # --- retail leverage: real margin data if available, else proxy = retail net-buy share of volume (10d) ---
    if os.path.exists('margin_277810.csv'):
        m=pd.read_csv('margin_277810.csv',index_col=0,parse_dates=True).iloc[:,0]; d['retail_lev']=m.reindex(d.index).ffill()
        d['retail_lev_hot']=d.retail_lev>d.retail_lev.rolling(120,min_periods=40).quantile(.8)
    else:
        d['retail_lev']=d.retail_net.rolling(10).sum()/d.Volume.rolling(10).sum()
        d['retail_lev_hot']=(d.retail_lev>0.10)&(d.dist_ma60>0.25)       # retail chasing an extended move
    # --- short selling: real data if available (meaningful only after 2025-03-31 ban lift) ---
    if os.path.exists('short_277810.csv'):
        s=pd.read_csv('short_277810.csv',index_col=0,parse_dates=True); col='short_ratio' if 'short_ratio' in s else [c for c in s.columns if '비중' in c or 'ratio' in c.lower()][0]
        d['short_ratio']=s[col].reindex(d.index); d['short_hot']=(d.short_ratio>d.short_ratio.rolling(60,min_periods=20).quantile(.8)).fillna(False)
        bal=s['short_balance_qty'].reindex(d.index)
        d['short_cover']=((bal.pct_change(10)<-0.20)&(d.Close>d.ma20)).fillna(False)   # shorts covering into strength
    else: d['short_ratio']=np.nan; d['short_hot']=False
    if 'short_cover' not in d: d['short_cover']=False
    # --- Samsung events: curated list + GDELT "Rainbow Robotics Samsung" spikes ---
    sam=pd.to_datetime(['2024-02-07','2024-03-21','2024-12-31','2025-03-19'])
    d['samsung_event']=d.index.isin(sam)|(d.news_rr_samsung>=3)
    d['samsung_recent']=d.samsung_event.rolling(10,min_periods=1).max().astype(bool)
    d['news_hot']=(d.news_rainbow_all_z>2)|(d.news_nvidia_robot_z>2)|(d.news_humanoid_z>2.5)
    d['kosdaq_ok']=d.kq>d.kq_ma20
    # SCHEDULED events only (known in advance -> no look-ahead): conferences, earnings dates, AGM
    ev=pd.read_csv('events_manual.csv',parse_dates=['date']); sched=ev[ev.type.isin(['conference','earnings','agm'])].date
    sched=pd.concat([sched,pd.Series(pd.to_datetime(['2024-02-27','2024-05-14','2024-08-14','2024-11-14','2025-02-27','2025-05-15','2025-08-14','2025-11-14','2024-03-28','2025-03-19']))])
    # --- DART filings (collect_events.py with DART_KEY): warning filings -> exit for 5 days ---
    d['dart_neg']=False; d['dart_pos']=False
    if os.path.exists('events_dart.csv'):
        e=pd.read_csv('events_dart.csv',parse_dates=['date'])
        def flag(m):
            f=pd.Series(False,index=d.index)
            for x in e[m].date:
                i=d.index.searchsorted(x)
                if i<len(d): f.iloc[i]=True
            return f
        neg=flag(e.report_nm.str.contains('손익구조|거래계획'))|(flag(e.report_nm.str.contains('특정증권'))&(d.ret<0))
        d['dart_neg']=neg.rolling(5,min_periods=1).max().astype(bool)          # P&L shock, insider selling plan / insider filing on a down day
        d['dart_pos']=flag(e.report_nm.str.contains('공급계약')).rolling(10,min_periods=1).max().astype(bool)
    d['days_to_sched']=[(sched[sched>=t].min()-t).days if (sched>=t).any() else np.nan for t in d.index]
    return d

def backtest(d, entry, exit_, stop=0.15, max_hold=40, name=''):
    pos=False; trades=[]; eq=[1.0]; hi=0
    for i in range(1,len(d)):
        t0,t=d.index[i-1],d.index[i]; r=0
        if pos:
            r=d.Close.iloc[i]/(d.Open.iloc[i] if entered==t else d.Close.iloc[i-1])-1
            hi=max(hi,d.High.iloc[i])
        eq.append(eq[-1]*(1+r))
        if not pos and entry.iloc[i-1] and i<len(d)-1:
            pos=True; entered=t; ep=d.Open.iloc[i]; hi=ep; eq[-1]=eq[-2]*(d.Close.iloc[i]/ep)*(1-COST/2); held=0
        elif pos:
            held=(d.index.get_loc(t)-d.index.get_loc(entered))
            stop_hit=d.Close.iloc[i]<hi*(1-stop)
            if exit_.iloc[i] or stop_hit or held>=max_hold or i==len(d)-1:
                xp=d.Close.iloc[i]; eq[-1]*=(1-COST/2)
                trades.append(dict(strategy=name,entry=entered.date(),entry_px=ep,exit=t.date(),exit_px=xp,ret=xp/ep-1-COST,days=held,
                                   reason='stop' if stop_hit else ('time' if held>=max_hold else ('end' if i==len(d)-1 else 'signal'))))
                pos=False
    e=pd.Series(eq,index=d.index); tr=pd.DataFrame(trades)
    exposure=0 if tr.empty else tr.days.sum()/len(d)
    stats=dict(strategy=name,trades=len(tr),win_rate=(tr.ret>0).mean() if len(tr) else np.nan,avg_trade=tr.ret.mean() if len(tr) else np.nan,
               total_ret=e.iloc[-1]-1,max_dd=(e/e.cummax()-1).min(),exposure=exposure)
    return stats,tr,e

def strategies(d):
    S={}
    # 1) FOREIGN ACCUMULATION BREAKOUT  (investor flow + technical base)
    base=d.range_60d<0.60
    S['S1 Foreign accumulation breakout']=(
        (d.foreign_5d_pct_vol>0.08)&(d.vol_ratio>1.8)&(d.ret>0.03)&base&(d.Close>d.ma20),
        (d.foreign_5d_pct_vol<-0.05)&(d.Close<d.ma20))
    # 2) EVENT-CALENDAR RUN-UP (CES / GTC / Samsung events) - buy ~10 trading days before, sell day after
    pre=d.days_to_sched.between(3,14)&(d.Close>d.ma20)&d.kosdaq_ok
    S['S2 Event calendar run-up']=(pre&(d.ret5>0), (d.days_to_sched>20)|(d.days_to_sched<1)|(d.Close<d.ma20*0.97))
    # 3) NEWS/SAMSUNG SHOCK MOMENTUM (news keywords + Samsung + excess return vs KOSDAQ & robot peers)
    shock=(d.news_hot|d.samsung_event)&(d.excess_ret>0.04)&(d.ret>d.peer_ret5.fillna(0)/5+0.03)
    S['S3 News & Samsung shock momentum']=(shock&d.kosdaq_ok, (d.vol_ratio>2.5)&(d.ret<-0.03)|(d.Close<d.ma20))
    # 4) TREND RIDER WITH OVERHEAT EXIT (KOSDAQ regime + retail leverage + short selling + RSI)
    overheat=(d.rsi>80)&(d.dist_ma60>0.35) | d.retail_lev_hot | (d.short_hot&(d.Close<d.ma20))
    S['S4 Trend rider + overheat exit']=((d.Close>d.ma60)&(d.ma20>d.ma60)&d.kosdaq_ok&(d.rsi.between(50,72))&~d.retail_lev_hot,
                                         overheat|(d.Close<d.ma60)|(~d.kosdaq_ok&(d.Close<d.ma20)))
    # 5) COMPOSITE SCORE (all 8 families, +1 bullish / -1 bearish)
    sc=pd.DataFrame(index=d.index)
    sc['flow']=np.sign(d.foreign_5d_pct_vol.fillna(0).where(d.foreign_5d_pct_vol.abs()>0.03,0))
    sc['kosdaq']=np.where(d.kosdaq_ok,1,-1)
    sc['short']=np.where(d.short_hot&(d.Close<d.ma20),-1,0)   # short_cover bonus tested: hurt S5 -> sell-side only
    sc['leverage']=np.where(d.retail_lev_hot,-1,0)
    sc['samsung']=np.where(d.samsung_recent,1,0)
    sc['calendar']=np.where(d.days_to_sched.between(0,14),1,0)
    sc['news']=np.where(d.news_hot&(d.ret5>0),1,0)
    sc['tech']=np.where((d.Close>d.ma20)&(d.ma20>d.ma60),1,np.where(d.Close<d.ma60,-1,0))+np.where(d.vol_ratio>2,np.sign(d.ret),0)
    d['score']=sc.sum(axis=1); d[[f'sc_{c}' for c in sc]]=sc.values
    S['S5 Composite 8-factor score']=(d.score>=4,d.score<=0)
    for k in list(S):   # v4: DART warning filing = extra exit for S1/S4/S5
        if k[:2] in ('S1','S4','S5'): S[k]=(S[k][0],S[k][1]|d.dart_neg)
    return S

if __name__=='__main__':
    d=load(); S=strategies(d)
    res,alltr,eqs=[],[],{}
    for k,(en,ex) in S.items():
        st,tr,e=backtest(d,en.fillna(False),ex.fillna(False),name=k); res.append(st); alltr.append(tr); eqs[k]=e
    bh=d.Close/d.Close.iloc[0]; eqs['Buy & hold']=bh
    res.append(dict(strategy='Buy & hold',trades=1,win_rate=np.nan,avg_trade=np.nan,total_ret=bh.iloc[-1]-1,max_dd=(bh/bh.cummax()-1).min(),exposure=1))
    R=pd.DataFrame(res).set_index('strategy'); print(R.round(3).to_string())
    T=pd.concat(alltr); T.to_csv('trades.csv',index=False); print(T.round(3).to_string())
    pd.DataFrame(eqs).to_csv('equity.csv'); R.to_csv('summary.csv')
    d[['Close','score']+[c for c in d if c.startswith('sc_')]].to_csv('daily_scores.csv')
    print('\nLATEST SCORE',d.score.iloc[-1],d[[c for c in d if c.startswith('sc_')]].iloc[-1].to_dict())
