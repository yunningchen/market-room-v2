"""Read-only batch download benchmark for Market Room. No dashboard writes."""
import argparse
import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import yfinance as yf

MARKETS = {'TW': {'TWSE', 'TPEX'}, 'US': {'NASDAQ', 'NYSE', 'NYSE American'}}

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--input', default='output/cleaned/candidates.csv')
    p.add_argument('--per-market', type=int, default=30)
    p.add_argument('--chunk-size', type=int, default=10)
    args = p.parse_args()
    if not 1 <= args.per_market <= 100 or not 1 <= args.chunk_size <= 50:
        p.error('per-market must be 1..100 and chunk-size 1..50')
    with open(args.input, encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    report = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'markets': {}}
    for label, markets in MARKETS.items():
        symbols = list(dict.fromkeys(r['symbol'] for r in rows if r.get('market') in markets and r.get('symbol')))
        symbols = symbols[:args.per_market]
        results = []
        started = time.monotonic()
        for i in range(0, len(symbols), args.chunk_size):
            batch = symbols[i:i+args.chunk_size]
            try:
                data = yf.download(batch, period='9mo', interval='1d', auto_adjust=True,
                                   progress=False, threads=False, timeout=20, group_by='ticker')
                for symbol in batch:
                    try:
                        if isinstance(data.columns, pd.MultiIndex):
                            if symbol in data.columns.get_level_values(0):
                                frame = data[symbol]
                            elif symbol in data.columns.get_level_values(1):
                                frame = data.xs(symbol, axis=1, level=1)
                            else:
                                raise ValueError('Ticker missing in response')
                        else:
                            frame = data
                        close = pd.to_numeric(frame['Close'], errors='coerce').dropna()
                        if len(close) < 90:
                            raise ValueError(f'Only {len(close)} valid bars')
                        results.append({'symbol': symbol, 'ok': True,
                                        'last_price_date': str(pd.Timestamp(close.index[-1]).date()),
                                        'bars': len(close)})
                    except Exception as exc:
                        results.append({'symbol': symbol, 'ok': False, 'error': str(exc)[:160]})
            except Exception as exc:
                for symbol in batch:
                    results.append({'symbol': symbol, 'ok': False, 'error': str(exc)[:160]})
            time.sleep(1.5)
        good = sum(r['ok'] for r in results)
        report['markets'][label] = {'attempted': len(symbols), 'successful': good,
                                    'success_rate': round(good / len(symbols), 3) if symbols else 0,
                                    'elapsed_seconds': round(time.monotonic()-started, 1),
                                    'results': results}
        print(f'{label}: {good}/{len(symbols)} in {report["markets"][label]["elapsed_seconds"]}s', flush=True)
    Path('output/benchmark').mkdir(parents=True, exist_ok=True)
    Path('output/benchmark/report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

if __name__ == '__main__':
    main()
