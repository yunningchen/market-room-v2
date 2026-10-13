"""Offline, conservative classification of Market Room candidate universe.
Usage: python clean_universe.py --input output/universe.csv
Does not assert that remaining records are verified common shares.
"""
import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

EXCLUDE = re.compile(r'\b(warrants?|rights?|units?|depositary units?|subscription rights?|notes?|debentures?|bonds?|etfs?|exchange.traded funds?|etns?|exchange.traded notes?)\b', re.I)
REVIEW = re.compile(r'\b(preferred|preference|depositary shares?|american depositary|adr|ads|trust|fund|closed.end|beneficial interest|acquisition corp|spac|limited partnership|lp\b|reit|receipts?)\b', re.I)

def classify(row):
    market = row['market'].strip()
    name = row['name'].strip()
    symbol = row['symbol'].strip()
    if market in ('TWSE', 'TPEX'):
        if not re.fullmatch(r'\d{4}\.(TW|TWO)', symbol):
            return 'review', 'invalid_tw_symbol'
        if not name:
            return 'review', 'missing_name'
        return 'candidate', 'tw_four_digit_stock_candidate'
    if market not in ('NASDAQ', 'NYSE', 'NYSE American'):
        return 'review', 'unknown_market'
    if EXCLUDE.search(name):
        return 'excluded', 'non_common_security_keyword'
    if REVIEW.search(name):
        return 'review', 'security_type_requires_verification'
    if re.search(r'(?i)(?:[./-]WS|[./-]WT|[./-]U|[./-]R)$', symbol):
        return 'review', 'suspicious_symbol_suffix'
    return 'candidate', 'name_screen_only_unverified'

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', default='output/universe.csv')
    parser.add_argument('--out', default='output/cleaned')
    args = parser.parse_args()
    source = Path(args.input)
    if not source.exists():
        raise SystemExit(f'Missing input: {source}. Upload/extract the universe artifact first.')
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with source.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        required = {'symbol', 'name', 'market'}
        if not required.issubset(reader.fieldnames or []):
            raise SystemExit(f'Expected columns: {sorted(required)}')
        rows = list(reader)
    for row in rows:
        row['classification'], row['reason'] = classify(row)
    columns = ['symbol', 'name', 'market', 'classification', 'reason']
    for category, filename in [('candidate','candidates.csv'), ('review','needs_review.csv'), ('excluded','excluded.csv')]:
        with (out / filename).open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(row for row in rows if row['classification'] == category)
    report = {
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'input_records': len(rows),
        'by_classification': dict(Counter(r['classification'] for r in rows)),
        'by_market': dict(Counter(r['market'] for r in rows)),
        'review_reasons': dict(Counter(r['reason'] for r in rows if r['classification']=='review')),
        'note': 'Heuristic name/symbol filtering only. Candidates are NOT verified ordinary shares. Original universe preserved.'
    }
    (out / 'cleaning_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
