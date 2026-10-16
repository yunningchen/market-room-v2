"""Market Room V5: batched end-of-day scanner, with real daily candles.

This is a best-effort free-source scanner, NOT guaranteed full-market coverage.
It retains last-known rows with their original date, and publishes freshness stats.
"""
import argparse
import csv
import json
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from scanner_v31 import calc
from scanner_v32 import COLUMNS, classify

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'docs' / 'data'
HISTORY = DATA / 'history'
MASTER = ROOT / 'data' / 'v33' / 'master.csv'
TW = {'TWSE', 'TPEX'}
US = {'NASDAQ', 'NYSE', 'NYSE American'}
TPE = timezone(timedelta(hours=8))
FIELDS = COLUMNS + ['last_checked_utc']


def read_csv(path):
    if not path.exists():
        return []
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def save_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def extract(data, symbol):
    if data is None or data.empty:
        raise ValueError('Empty download')
    if isinstance(data.columns, pd.MultiIndex):
        if symbol in data.columns.get_level_values(0):
            return data[symbol].copy()
        if symbol in data.columns.get_level_values(1):
            return data.xs(symbol, axis=1, level=1).copy()
        raise ValueError('Ticker absent from batch response')
    return data.copy()


def finite(value, digits=4):
    try:
        v = float(value)
        return round(v, digits) if math.isfinite(v) else None
    except (ValueError, TypeError):
        return None


def candles_from_frame(frame):
    frame = frame[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
    frame = frame.dropna(subset=['Open', 'High', 'Low', 'Close'])
    frame = frame[frame['Close'] > 0]
    close = frame['Close']
    ma20, ma60 = close.rolling(20).mean(), close.rolling(60).mean()
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    signal = macd.ewm(span=9, adjust=False).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    rsi = rsi.mask((loss == 0) & (gain > 0), 100)
    rsi = rsi.mask((loss == 0) & (gain == 0), 50)
    out = []
    for idx in range(max(0, len(frame)-180), len(frame)):
        row = frame.iloc[idx]
        out.append({
            'date': pd.Timestamp(frame.index[idx]).strftime('%Y-%m-%d'),
            'open': finite(row['Open']), 'high': finite(row['High']),
            'low': finite(row['Low']), 'close': finite(row['Close']),
            'volume': int(row['Volume']) if pd.notna(row['Volume']) else 0,
            'ma20': finite(ma20.iloc[idx]), 'ma60': finite(ma60.iloc[idx]),
            'macd': finite(macd.iloc[idx]), 'signal': finite(signal.iloc[idx]),
            'hist': finite((macd-signal).iloc[idx]), 'rsi': finite(rsi.iloc[idx]),
        })
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--market', choices=['TW', 'US'], required=True)
    parser.add_argument('--input', default='output/cleaned/candidates.csv')
    parser.add_argument('--chunk-size', type=int, default=10)
    parser.add_argument('--history-limit', type=int, default=250)
    parser.add_argument('--time-budget-minutes', type=int, default=95)
    args = parser.parse_args()
    if not 1 <= args.chunk_size <= 50 or not 0 <= args.history_limit <= 10000:
        parser.error('Invalid chunk-size or history-limit')

    DATA.mkdir(parents=True, exist_ok=True)
    HISTORY.mkdir(parents=True, exist_ok=True)
    candidates = read_csv(ROOT / args.input)
    markets = TW if args.market == 'TW' else US
    selected = [r for r in candidates if r.get('market') in markets and r.get('symbol')]
    if not selected:
        raise SystemExit(f'No symbols for {args.market}; previous data preserved')

    all_candidates = {(r['market'], r['symbol']) for r in candidates}
    master = {(r['market'], r['symbol']): r for r in read_csv(MASTER)
              if (r.get('market'), r.get('symbol')) in all_candidates}
    if not master:
        master = {(r['market'], r['symbol']): r for r in read_csv(DATA / 'ranking.csv')
                  if (r.get('market'), r.get('symbol')) in all_candidates}

    # Keep existing histories current first, then add missing ones in successive runs.
    existing = {r['symbol'] for r in selected if (HISTORY / f"{r['symbol']}.json").exists()}
    missing = [r['symbol'] for r in selected if r['symbol'] not in existing]
    # Rotate which missing histories are added, without affecting price scanning.
    progress = ROOT / 'data' / 'v33' / f'history_cursor_{args.market.lower()}.json'
    cursor = 0
    if progress.exists():
        try:
            cursor = int(json.loads(progress.read_text()).get('cursor', 0))
        except (ValueError, TypeError):
            pass
    missing = missing[cursor:] + missing[:cursor] if missing else []
    history_targets = set(existing)
    slots = max(0, args.history_limit - len(existing))
    history_targets.update(missing[:slots])

    started = time.monotonic()
    succeeded = failed = saved_histories = 0
    errors = []
    completed = 0
    now_utc = datetime.now(timezone.utc).isoformat()
    for start in range(0, len(selected), args.chunk_size):
        if time.monotonic() - started > args.time_budget_minutes * 60:
            print('Time budget reached; remaining symbols retain their prior date', flush=True)
            break
        batch = selected[start:start + args.chunk_size]
        names = [r['symbol'] for r in batch]
        try:
            downloaded = yf.download(names, period='9mo', interval='1d', auto_adjust=True,
                                     group_by='ticker', progress=False, threads=False, timeout=25)
        except Exception as exc:
            downloaded = None
            print(f'Batch download error: {type(exc).__name__}: {exc}', flush=True)
        for row in batch:
            symbol = row['symbol']
            try:
                frame = extract(downloaded, symbol)
                result = calc(symbol, frame)
                result.update(name=row.get('name', ''), market=row['market'])
                result['volume_status'], result['swing_group'] = classify(result)
                result['last_checked_utc'] = now_utc
                master[(row['market'], symbol)] = result
                succeeded += 1
                if symbol in history_targets and all(c in frame for c in ('Open','High','Low','Close','Volume')):
                    bars = candles_from_frame(frame)
                    if len(bars) >= 65:
                        (HISTORY / f'{symbol}.json').write_text(json.dumps({
                            'symbol': symbol, 'updated_utc': now_utc,
                            'source': 'Yahoo Finance via yfinance, adjusted daily OHLCV',
                            'adjustment': 'Adjusted prices, same basis as technical scanner',
                            'candles': bars,
                        }, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
                        saved_histories += 1
            except Exception as exc:
                failed += 1
                errors.append({'symbol': symbol, 'error': f'{type(exc).__name__}: {str(exc)[:140]}'})
        completed += len(batch)
        if completed % 100 == 0:
            print(f'{args.market} {completed}/{len(selected)}; success={succeeded}, fail={failed}', flush=True)
        time.sleep(0.6)

    if succeeded == 0:
        raise SystemExit('No stocks updated; existing dashboard data preserved')
    if succeeded / max(completed, 1) < 0.7:
        raise SystemExit('Download success rate below 70%; existing dashboard data preserved')

    # Do not present very old prices as current. 7 calendar days is a retention
    # window, NOT a promise of freshness; the UI must show each stock's date.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).date()
    fresh = []
    for r in master.values():
        try:
            date = datetime.fromisoformat(str(r['date'])).date()
            checked = datetime.fromisoformat(str(r['last_checked_utc']))
            if date >= cutoff and checked.date() >= cutoff:
                fresh.append(r)
        except (ValueError, TypeError, KeyError):
            continue
    fresh.sort(key=lambda r: (-float(r.get('score') or 0), r['market'], r['symbol']))
    save_csv(MASTER, FIELDS, sorted(master.values(), key=lambda r: (r['market'], r['symbol'])))
    save_csv(DATA / 'ranking.csv', FIELDS, fresh)
    progress.parent.mkdir(parents=True, exist_ok=True)
    progress.write_text(json.dumps({'cursor': (cursor + slots) % len(missing) if missing else 0}), encoding='utf-8')
    status = {
        'market': args.market, 'generated_utc': now_utc,
        'candidate_count': len(selected), 'attempted': completed,
        'successful': succeeded, 'failed': failed,
        'unattempted': len(selected)-completed,
        'success_rate': round(succeeded / max(completed, 1), 4),
        'history_written': saved_histories,
        'stored_symbols': len(master), 'fresh_symbols': len(fresh),
        'elapsed_seconds': round(time.monotonic()-started, 1),
        'note': '7-day retention is not a real-time freshness guarantee. Check each stock date.',
    }
    (DATA / 'status.json').write_text(json.dumps(status, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    out = ROOT / 'output' / 'bulk'
    out.mkdir(parents=True, exist_ok=True)
    (out / f'{args.market.lower()}_report.json').write_text(json.dumps({'status': status, 'errors': errors}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(status, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
