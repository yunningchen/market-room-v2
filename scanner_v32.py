"""Market Room V3.2: five-market rotating swing scanner (research only)."""
import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf
from scanner_v31 import calc

MARKETS = ('TWSE', 'TPEX', 'NASDAQ', 'NYSE', 'NYSE American')
COLUMNS = ['symbol', 'name', 'market', 'date', 'close', 'ma20', 'ma60',
           'macd_hist', 'rsi14', 'volume_ratio', 'score', 'category',
           'risk_warning', 'volume_status', 'swing_group']


def ordered_universe(rows):
    """Round-robin across five markets, preserving order within each market."""
    groups = {market: [] for market in MARKETS}
    for row in rows:
        if row['market'] in groups:
            groups[row['market']].append(row)
    result = []
    for index in range(max((len(v) for v in groups.values()), default=0)):
        for market in MARKETS:
            if index < len(groups[market]):
                result.append(groups[market][index])
    return result


def classify(result):
    ratio = result['volume_ratio']
    if ratio >= 1.5:
        status = '放量確認'
    elif ratio >= 1.1:
        status = '量能增加'
    elif ratio >= 0.8:
        status = '量能普通'
    else:
        status = '量能不足'
    if result['category'] == '過熱觀察':
        group = '過熱觀察'
    elif result['category'] == '趨勢強勢' and ratio >= 1.1:
        group = '強勢且有量'
    elif result['category'] == '趨勢強勢':
        group = '技術強勢但量能不足'
    elif result['category'] == '剛轉強':
        group = '剛轉強'
    else:
        group = '其他觀察'
    return status, group


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--input', default='output/cleaned/candidates.csv')
    p.add_argument('--limit', type=int, default=100)
    p.add_argument('--batch-index', type=int, default=0)
    args = p.parse_args()
    if not 1 <= args.limit <= 100 or args.batch_index < 0:
        p.error('limit must be 1..100 and batch-index >= 0')

    with open(args.input, newline='', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    if not rows or not {'symbol', 'name', 'market'}.issubset(rows[0]):
        raise SystemExit('Input needs symbol,name,market and nonempty rows')
    ordered = ordered_universe(rows)
    if not ordered:
        raise SystemExit('No supported market symbols')
    # Explicitly wrap the batch index; this does not imply the entire universe was scanned.
    total_batches = (len(ordered) + args.limit - 1) // args.limit
    effective_batch = args.batch_index % total_batches
    start = effective_batch * args.limit
    selected = ordered[start:start + args.limit]

    output = Path('output/v32')
    output.mkdir(parents=True, exist_ok=True)
    good, bad = [], []
    for item in selected:
        symbol = item['symbol']
        try:
            frame = yf.download(symbol, period='9mo', interval='1d',
                                auto_adjust=True, progress=False,
                                threads=False, timeout=20)
            if isinstance(frame.columns, pd.MultiIndex):
                frame.columns = frame.columns.get_level_values(0)
            result = calc(symbol, frame)
            result.update(name=item['name'], market=item['market'])
            result['volume_status'], result['swing_group'] = classify(result)
            good.append(result)
            print(f'OK {symbol}: {result["score"]} {result["swing_group"]}', flush=True)
        except Exception as exc:
            bad.append({'symbol': symbol, 'market': item['market'], 'error': str(exc)[:250]})
            print(f'FAIL {symbol}: {exc}', flush=True)

    good.sort(key=lambda r: (-r['score'], r['symbol']))
    with (output / 'ranking.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(good)
    with (output / 'failures.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['symbol', 'market', 'error'])
        writer.writeheader()
        writer.writerows(bad)
    report = {
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'candidate_count': len(ordered), 'batch_index_requested': args.batch_index,
        'batch_index_used': effective_batch, 'total_batches': total_batches,
        'batch_start_offset': start, 'selected': len(selected),
        'scanned': len(good), 'failed': len(bad),
        'success_rate_pct': round(100 * len(good) / len(selected), 1),
        'selected_by_market': dict(Counter(x['market'] for x in selected)),
        'successful_by_market': dict(Counter(x['market'] for x in good)),
        'groups': dict(Counter(x['swing_group'] for x in good)),
        'note': 'Only this batch was scanned. Scores are rule-based, not investment advice.'
    }
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if not good:
        raise SystemExit('No successful downloads; inspect failures.csv')


if __name__ == '__main__':
    main()
