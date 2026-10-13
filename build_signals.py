"""Compare consecutive daily scan snapshots; publish non-trading technical change events.
No secrets or paid APIs. Baseline run emits no signals.
"""
import csv
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent / 'docs' / 'data'
STATE = BASE / 'signals_state.json'
OUT = BASE / 'signals.json'


def numeric(row, key):
    try:
        return float(row.get(key, ''))
    except (ValueError, TypeError):
        return None


def conditions(r):
    close, ma20, ma60 = (numeric(r, k) for k in ('close', 'ma20', 'ma60'))
    rsi = numeric(r, 'rsi14')
    vol = numeric(r, 'volume_ratio')
    hist = numeric(r, 'macd_hist')
    return {
        'above_ma20': close > ma20 if close is not None and ma20 is not None else None,
        'bull_stack': ma20 > ma60 if ma20 is not None and ma60 is not None else None,
        'macd_positive': hist > 0 if hist is not None else None,
        'rsi_50': rsi >= 50 if rsi is not None else None,
        'high_volume': vol >= 1.5 if vol is not None else None,
    }


MESSAGES = {
    'above_ma20': ('收盤站上 MA20', '收盤跌回 MA20 下方'),
    'bull_stack': ('MA20 高於 MA60', 'MA20 低於 MA60'),
    'macd_positive': ('MACD 柱由負轉正', 'MACD 柱由正轉負'),
    'rsi_50': ('RSI 回到 50 以上', 'RSI 跌破 50'),
    'high_volume': ('相對成交量達 1.5 倍', '相對成交量低於 1.5 倍'),
}


def main():
    BASE.mkdir(parents=True, exist_ok=True)
    with (BASE / 'ranking.csv').open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    try:
        old = json.loads(STATE.read_text(encoding='utf-8'))
        if not isinstance(old, dict):
            old = {}
    except (OSError, ValueError):
        old = {}
    now = datetime.now(timezone.utc)
    previous_events = []
    try:
        prior = json.loads(OUT.read_text(encoding='utf-8'))
        previous_events = prior.get('signals', [])
    except (OSError, ValueError, AttributeError):
        pass
    current = dict(old)
    events = []
    for r in rows:
        symbol = r.get('symbol', '').strip()
        if not symbol or not r.get('date') or not r.get('last_checked_utc'):
            continue
        try:
            checked = datetime.fromisoformat(r['last_checked_utc'].replace('Z', '+00:00'))
            if checked.tzinfo is None:
                checked = checked.replace(tzinfo=timezone.utc)
            if now - checked > timedelta(days=7):
                continue
        except ValueError:
            continue
        current_flags = conditions(r)
        previous = old.get(symbol)
        # Compare only different daily price dates, not repeated scans of same candle.
        if previous and previous.get('date') != r['date']:
            for key, (up, down) in MESSAGES.items():
                a, b = previous.get('flags', {}).get(key), current_flags[key]
                if a is None or b is None or a == b:
                    continue
                events.append({'symbol': symbol, 'market': r.get('market', ''),
                               'date': r['date'], 'score': numeric(r, 'score'),
                               'message': up if b else down,
                               'direction': 'positive' if b else 'negative',
                               'detected_utc': now.isoformat()})
        if not previous or r['date'] >= previous.get('date', ''):
            current[symbol] = {'date': r['date'], 'flags': current_flags}
    # Keep a rolling 7-day history; prevent duplicate events across repeated scans.
    cutoff = now - timedelta(days=7)
    merged = {}
    for e in previous_events + events:
        try:
            t = datetime.fromisoformat(e['detected_utc'].replace('Z', '+00:00'))
            if t < cutoff:
                continue
            k = (e['symbol'], e['date'], e['message'])
            merged[k] = e
        except (KeyError, ValueError, TypeError):
            continue
    ordered = sorted(merged.values(), key=lambda e: e['detected_utc'], reverse=True)
    STATE.write_text(json.dumps(current, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    OUT.write_text(json.dumps({'updated_utc': now.isoformat(), 'signals': ordered[:500]}, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print(f'V5 signal events: new={len(events)}, retained={len(ordered[:500])}, symbols={len(current)}')


if __name__ == '__main__':
    main()
