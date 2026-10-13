"""Incrementally publish real daily OHLCV + indicators for Market Room V4.2.
Run after docs/data/ranking.csv is refreshed. No API key required.
"""
import argparse
import csv
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'docs' / 'data'
HISTORY = DATA / 'history'


def safe_name(symbol):
    if not re.fullmatch(r'[A-Za-z0-9.\-^=]+', symbol):
        raise ValueError('Unsupported symbol')
    return symbol


def calc_rsi(series, window=14):
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/window, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/window, adjust=False).mean()
    rs = gain / loss.replace(0, float('nan'))
    return 100 - 100 / (1 + rs)


def maybe_num(value, digits=4):
    return round(float(value), digits) if pd.notna(value) else None


def build(symbol):
    raw = yf.download(symbol, period='9mo', interval='1d', auto_adjust=False,
                      progress=False, threads=False, timeout=18)
    if raw is None or raw.empty:
        raise ValueError('No price history returned')
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    required = ['Open', 'High', 'Low', 'Close', 'Volume']
    if any(col not in raw.columns for col in required):
        raise ValueError('OHLCV columns missing')
    frame = raw[required].copy().dropna(subset=['Open', 'High', 'Low', 'Close'])
    frame = frame[frame['Close'] > 0]
    if len(frame) < 65:
        raise ValueError('Less than 65 valid trading days')
    close = frame['Close']
    frame['ma20'] = close.rolling(20).mean()
    frame['ma60'] = close.rolling(60).mean()
    fast = close.ewm(span=12, adjust=False).mean()
    slow = close.ewm(span=26, adjust=False).mean()
    frame['macd'] = fast - slow
    frame['signal'] = frame['macd'].ewm(span=9, adjust=False).mean()
    frame['hist'] = frame['macd'] - frame['signal']
    frame['rsi'] = calc_rsi(close)
    candles = []
    for date, row in frame.tail(180).iterrows():
        candles.append({
            'date': pd.Timestamp(date).strftime('%Y-%m-%d'),
            'open': maybe_num(row['Open']), 'high': maybe_num(row['High']),
            'low': maybe_num(row['Low']), 'close': maybe_num(row['Close']),
            'volume': int(row['Volume']) if pd.notna(row['Volume']) else 0,
            'ma20': maybe_num(row['ma20']), 'ma60': maybe_num(row['ma60']),
            'macd': maybe_num(row['macd']), 'signal': maybe_num(row['signal']),
            'hist': maybe_num(row['hist']), 'rsi': maybe_num(row['rsi'])
        })
    return {'symbol': symbol, 'updated_utc': datetime.now(timezone.utc).isoformat(),
            'source': 'Yahoo Finance via yfinance, daily historical prices',
            'adjustment': 'Unadjusted OHLC; splits/dividends may affect charts',
            'candles': candles}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=12)
    parser.add_argument('--refresh-hours', type=float, default=24)
    args = parser.parse_args()
    HISTORY.mkdir(parents=True, exist_ok=True)
    with (DATA / 'ranking.csv').open(encoding='utf-8-sig', newline='') as f:
        stocks = list(csv.DictReader(f))
    stocks.sort(key=lambda r: float(r.get('score') or 0), reverse=True)
    now = time.time()
    candidates = []
    for r in stocks:
        symbol = (r.get('symbol') or '').strip()
        try:
            safe_name(symbol)
        except ValueError:
            continue
        target = HISTORY / f'{symbol}.json'
        if target.exists() and now - target.stat().st_mtime < args.refresh_hours * 3600:
            continue
        candidates.append(symbol)
    # Missing symbols first, then refresh existing histories.
    candidates.sort(key=lambda s: (HISTORY / f'{s}.json').exists())
    success = 0
    for symbol in candidates[:max(0, args.limit)]:
        try:
            result = build(symbol)
            dest = HISTORY / f'{symbol}.json'
            dest.write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
            success += 1
            print(f'OK {symbol}: {len(result["candles"])} sessions')
        except Exception as exc:
            print(f'SKIP {symbol}: {type(exc).__name__}: {exc}')
        time.sleep(0.4)
    print(f'History updated: {success}; attempted: {min(len(candidates), max(0, args.limit))}')


if __name__ == '__main__':
    main()
