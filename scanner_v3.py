"""Market Room V3: limited-batch swing screening; research only."""
import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf


def calc(symbol, frame):
    if frame is None or len(frame) < 90:
        raise ValueError('fewer than 90 daily bars')
    close = pd.to_numeric(frame['Close'], errors='coerce').dropna()
    volume = pd.to_numeric(frame['Volume'], errors='coerce').reindex(close.index).fillna(0)
    if len(close) < 90 or close.iloc[-1] <= 0:
        raise ValueError('invalid or insufficient close data')
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = (100 - 100 / (1 + rs)).fillna(100)
    vol20 = volume.shift(1).rolling(20).mean()
    vr = float(volume.iloc[-1] / vol20.iloc[-1]) if vol20.iloc[-1] > 0 else 0.0
    price = float(close.iloc[-1]); m20 = float(ma20.iloc[-1]); m60 = float(ma60.iloc[-1]); r = float(rsi.iloc[-1])
    hist = float(macd.iloc[-1] - signal.iloc[-1]); prior_hist = float(macd.iloc[-2] - signal.iloc[-2])
    # 100-point observation score; not a predicted return or probability.
    score = 0
    score += 20 if price > m20 else 0
    score += 20 if m20 > m60 else 0
    score += 15 if m20 > float(ma20.iloc[-6]) else 0
    score += 15 if hist > 0 else 0
    score += 10 if hist > prior_hist else 0
    score += 10 if 45 <= r <= 70 else (5 if 35 <= r < 45 else 0)
    score += 10 if vr >= 1.5 else (5 if vr >= 1.1 else 0)
    return {'symbol': symbol, 'date': str(close.index[-1].date()), 'close': round(price, 3),
            'ma20': round(m20, 3), 'ma60': round(m60, 3), 'macd_hist': round(hist, 4),
            'rsi14': round(r, 2), 'volume_ratio': round(vr, 2), 'score': score}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--input', default='cleaned/candidates.csv')
    p.add_argument('--limit', type=int, default=50)
    p.add_argument('--offset', type=int, default=0)
    args = p.parse_args()
    if not 1 <= args.limit <= 100 or args.offset < 0:
        p.error('limit must be 1..100 and offset >= 0')
    with open(args.input, newline='', encoding='utf-8-sig') as f:
        universe = list(csv.DictReader(f))
    # Interleave Taiwan and US for representative initial test.
    tw = [r for r in universe if r['market'] in ('TWSE', 'TPEX')]
    us = [r for r in universe if r['market'] not in ('TWSE', 'TPEX')]
    ordered = []
    for i in range(max(len(tw), len(us))):
        if i < len(tw): ordered.append(tw[i])
        if i < len(us): ordered.append(us[i])
    selected = ordered[args.offset:args.offset + args.limit]
    if not selected: raise SystemExit('No symbols selected')
    out = Path('output/v3'); out.mkdir(parents=True, exist_ok=True)
    good, bad = [], []
    for item in selected:
        symbol = item['symbol']
        try:
            frame = yf.download(symbol, period='9mo', interval='1d', auto_adjust=True,
                                progress=False, threads=False, timeout=20)
            if isinstance(frame.columns, pd.MultiIndex):
                frame.columns = frame.columns.get_level_values(0)
            result = calc(symbol, frame)
            result.update(name=item['name'], market=item['market'])
            good.append(result)
            print(f'OK {symbol}: {result["score"]}', flush=True)
        except Exception as e:
            bad.append({'symbol': symbol, 'error': str(e)[:250]})
            print(f'FAIL {symbol}: {e}', flush=True)
    good.sort(key=lambda r: (-r['score'], r['symbol']))
    cols = ['symbol', 'name', 'market', 'date', 'close', 'ma20', 'ma60',
            'macd_hist', 'rsi14', 'volume_ratio', 'score']
    with (out/'ranking.csv').open('w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(good)
    with (out/'failures.csv').open('w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=['symbol', 'error']); w.writeheader(); w.writerows(bad)
    report = {'generated_at_utc': datetime.now(timezone.utc).isoformat(),
              'selected': len(selected), 'scanned': len(good), 'failed': len(bad),
              'offset': args.offset, 'limit': args.limit,
              'note': 'Candidate names are unverified; technical scores are not investment advice.'}
    (out/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)
    if not good: raise SystemExit('No successful downloads; inspect failures.csv')


if __name__ == '__main__':
    main()
