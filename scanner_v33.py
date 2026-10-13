"""Market Room V3.3: persist one V3.2 batch and merge results (research only)."""
import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from scanner_v32 import COLUMNS, ordered_universe

STATE_DIR = Path('data/v33')
OUT_DIR = Path('output/v33')
STATE_FILE = STATE_DIR / 'state.json'
MASTER_FILE = STATE_DIR / 'master.csv'


def read_csv(path):
    with Path(path).open(newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--input', default='output/cleaned/candidates.csv')
    p.add_argument('--limit', type=int, default=100)
    p.add_argument('--min-success-rate', type=float, default=0.70)
    args = p.parse_args()
    if not 1 <= args.limit <= 100 or not 0 <= args.min_success_rate <= 1:
        p.error('limit must be 1..100 and min-success-rate must be 0..1')

    universe = read_csv(args.input)
    ordered = ordered_universe(universe)
    if not ordered:
        raise SystemExit('Empty candidate universe')
    batches = (len(ordered) + args.limit - 1) // args.limit
    state = json.loads(STATE_FILE.read_text(encoding='utf-8')) if STATE_FILE.exists() else {}
    batch = int(state.get('next_batch', 0)) % batches
    print(f'V3.3 batch {batch + 1}/{batches}, {len(ordered)} candidates', flush=True)

    cmd = [sys.executable, 'scanner_v32.py', '--input', args.input,
           '--limit', str(args.limit), '--batch-index', str(batch)]
    subprocess.run(cmd, check=True)
    report = json.loads(Path('output/v32/report.json').read_text(encoding='utf-8'))
    good = read_csv('output/v32/ranking.csv')
    bad = read_csv('output/v32/failures.csv')
    selected = int(report['selected'])
    success_rate = len(good) / selected if selected else 0
    if success_rate < args.min_success_rate:
        raise SystemExit(f'Batch success rate {success_rate:.1%} below threshold; state NOT advanced')

    now = datetime.now(timezone.utc)
    checked_at = now.isoformat()
    keys = {(r['market'], r['symbol']) for r in universe}
    master = {}
    if MASTER_FILE.exists():
        for r in read_csv(MASTER_FILE):
            k = (r['market'], r['symbol'])
            if k in keys:
                master[k] = r
    for r in good:
        row = dict(r)
        row['last_checked_utc'] = checked_at
        master[(r['market'], r['symbol'])] = row
    master_fields = COLUMNS + ['last_checked_utc']
    master_rows = sorted(master.values(), key=lambda r: (r['market'], r['symbol']))
    # A 7-day freshness window is deliberately shorter than a 70-batch rotation.
    cutoff = now - timedelta(days=7)
    fresh = []
    for r in master_rows:
        try:
            checked = datetime.fromisoformat(r['last_checked_utc'])
            if checked >= cutoff and datetime.fromisoformat(r['date']).date() >= cutoff.date():
                fresh.append(r)
        except (KeyError, ValueError, TypeError):
            continue
    fresh.sort(key=lambda r: (-int(float(r['score'])), r['market'], r['symbol']))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(OUT_DIR / 'fresh_ranking.csv', master_fields, fresh)
    write_csv(OUT_DIR / 'batch_failures.csv', ['symbol', 'market', 'error'], bad)
    write_csv(MASTER_FILE, master_fields, master_rows)
    next_batch = (batch + 1) % batches
    state_out = {'next_batch': next_batch, 'last_batch': batch,
                 'total_batches': batches, 'candidate_count': len(ordered),
                 'last_run_utc': checked_at, 'last_batch_success': len(good),
                 'last_batch_failed': len(bad)}
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state_out, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    summary = dict(state_out, stored_symbols=len(master_rows), fresh_symbols=len(fresh),
                   last_batch_selected=selected, last_batch_success_rate=round(success_rate, 3),
                   note='Fresh means checked and price date within 7 calendar days. Full-universe freshness is NOT guaranteed.')
    (OUT_DIR / 'report.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
