"""Market Room V5 strategy candidates: B trend pullback, A early breakout.
Only use published historical candles, never synthesize missing bars.
"""
import csv, json, math
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
DATA=ROOT/'docs'/'data'

def number(x):
    try:
        v=float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):return None

def analyze(row,bars):
    bars=[b for b in bars if all(number(b.get(k)) is not None for k in ('close','high','low','ma20','ma60'))]
    if len(bars)<65:return None
    x=bars[-1]; prev=bars[-2]; p=number(x['close']); ma=number(x['ma20']); ma60=number(x['ma60']); rsi=number(x.get('rsi')); hist=number(x.get('hist')); ph=number(prev.get('hist'))
    if None in (p,ma,ma60,rsi,hist,ph) or p<=0:return None
    # Prior 20-day extremes exclude today's candle; no look-ahead.
    high20=max(float(b['high']) for b in bars[-21:-1]); low20=min(float(b['low']) for b in bars[-21:-1]);
    true_ranges=[]
    for i in range(len(bars)-14,len(bars)):
        b=bars[i]; pc=float(bars[i-1]['close']); true_ranges.append(max(float(b['high'])-float(b['low']),abs(float(b['high'])-pc),abs(float(b['low'])-pc)))
    atr=sum(true_ranges)/len(true_ranges)
    if atr<=0:return None
    vols=[number(b.get('volume')) or 0 for b in bars[-21:-1]]
    avgvol=sum(vols)/len(vols); vr=(number(x.get('volume')) or 0)/avgvol if avgvol>0 else 0
    uptrend=p>ma>ma60 and ma>float(bars[-6]['ma20'])
    # B: rising trend, pullback near MA20 (within 1 ATR) and held above support.
    near_ma=abs(p-ma)<=atr
    rebound=p>float(prev['close']) and p>=float(x['open']) if number(x.get('open')) is not None else p>float(prev['close'])
    stop_b=min(ma,low20)-0.5*atr
    risk_b=p-stop_b; target_b=high20 if high20>p else p+2.5*atr
    rr_b=(target_b-p)/risk_b if risk_b>0 else 0
    trend_b=(15 if p>ma else 0)+(10 if ma>ma60 else 0)
    entry_b=(20 if near_ma else 0)+(10 if rebound else 0)
    momentum_b=(10 if hist>0 else 0)+(5 if 45<=rsi<=65 else 0)
    volume_b=10 if vr>=1.2 else (5 if vr>=0.9 else 0)
    risk_b_score=(10 if risk_b>0 and rr_b>=2 else 0)+(10 if risk_b>0 and risk_b/p<=0.08 else 0)
    score_b=trend_b+entry_b+momentum_b+volume_b+risk_b_score
    qualified_b=uptrend and near_ma and risk_b>0 and rr_b>=1.8 and risk_b/p<=0.1 and 40<=rsi<=70
    # A: fresh close above previous 20d high, with volume and momentum confirmation.
    recent_break=p>high20 and float(prev['close'])<=max(float(b['high']) for b in bars[-22:-2])
    stop_a=high20-0.5*atr; risk_a=p-stop_a; target_a=p+2.5*atr
    rr_a=(target_a-p)/risk_a if risk_a>0 else 0
    score_a=(10 if ma>ma60 else 0)+(10 if p>ma else 0)+(30 if recent_break else 0)+(10 if hist>0 else 0)+(10 if hist>ph else 0)+(15 if vr>=1.5 else (7 if vr>=1.1 else 0))+(10 if risk_a>0 and rr_a>=2 else 0)+(5 if 45<=rsi<=70 else 0)
    qualified_a=recent_break and hist>0 and vr>=1.3 and risk_a>0 and rr_a>=1.8 and risk_a/p<=0.1 and 45<=rsi<=75
    kind='B' if qualified_b else ('A' if qualified_a else '觀察')
    why_b='上升趨勢，靠近MA20且風險報酬符合門檻' if qualified_b else '尚未同時符合趨勢、回檔位置及風險報酬門檻'
    why_a='突破前20日高點，動能與成交量確認' if qualified_a else '尚未同時符合突破、量能及風險門檻'
    return dict(symbol=row['symbol'],name=row.get('name',''),market=row.get('market',''),date=x['date'],price=round(p,3),original_score=number(row.get('score')),strategy=kind,b_score=score_b,a_score=score_a,b_qualified=qualified_b,a_qualified=qualified_a,b_reason=why_b,a_reason=why_a,ma20=round(ma,3),ma60=round(ma60,3),rsi=round(rsi,2),volume_ratio=round(vr,2),atr14=round(atr,3),prior_high20=round(high20,3),b_watch_price=round(ma,3),b_stop=round(stop_b,3),b_target=round(target_b,3),b_rr=round(rr_b,2),a_breakout=round(high20,3),a_stop=round(stop_a,3),a_target=round(target_a,3),a_rr=round(rr_a,2))

def main():
    rows=list(csv.DictReader((DATA/'ranking.csv').open(encoding='utf-8-sig',newline='')))
    output=[]; skipped=0
    for row in rows:
        symbol=row.get('symbol','')
        if not symbol or '/' in symbol or '\\' in symbol or '..' in symbol:continue
        path=DATA/'history'/f'{symbol}.json'
        if not path.exists():skipped+=1;continue
        try:
            bars=json.loads(path.read_text(encoding='utf-8'))['candles']
            if not bars or bars[-1]['date']!=row.get('date'):skipped+=1;continue
            item=analyze(row,bars)
            if item:output.append(item)
            else:skipped+=1
        except (ValueError,KeyError,TypeError,IndexError,ZeroDivisionError):skipped+=1
    output.sort(key=lambda r:(r['strategy'] not in ('B','A'),-max(r['b_score'],r['a_score'])))
    payload={'generated_utc':datetime.now(timezone.utc).isoformat(),'total_ranking':len(rows),'analyzed':len(output),'missing_or_stale_history':skipped,'b_count':sum(x['b_qualified'] for x in output),'a_count':sum(x['a_qualified'] for x in output),'note':'Experimental heuristic, not backtested; only stocks with matching daily history are evaluated.','stocks':output}
    (DATA/'strategy.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f"Strategy B={payload['b_count']}, A={payload['a_count']}, analyzed={len(output)}/{len(rows)}")
if __name__=='__main__':main()
