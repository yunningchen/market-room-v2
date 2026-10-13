"""Market Room V3.3 LINE digest. Uses existing data/v33/master.csv; no trading orders."""
import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo('Asia/Taipei')
MARKETS = {'tw': {'TWSE', 'TPEX'}, 'us': {'NASDAQ', 'NYSE', 'NYSE American'}}
LABELS = {'morning': '08:00 台股盤前／美股收盤回顧', 'tw_close': '14:00 台股收盤回顧', 'us_pre': '21:00 美股盤前觀察'}


def load_rows(path):
    if not path.exists():
        return []
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def fresh_rows(rows, markets, now):
    cutoff = now - timedelta(days=7)
    out = []
    for row in rows:
        if row.get('market') not in markets:
            continue
        try:
            checked = datetime.fromisoformat(row['last_checked_utc'].replace('Z', '+00:00'))
            price_date = datetime.fromisoformat(row['date']).date()
            if checked.tzinfo is None:
                checked = checked.replace(tzinfo=timezone.utc)
            if checked < cutoff or price_date < cutoff.astimezone(TAIPEI).date():
                continue
            float(row['score'])
        except (KeyError, ValueError, TypeError):
            continue
        out.append(row)
    return sorted(out, key=lambda r: -float(r['score']))


def fmt(row):
    try:
        score = int(float(row['score']))
        rsi = f"{float(row['rsi14']):.1f}"
        vol = f"{float(row['volume_ratio']):.2f}"
    except (ValueError, KeyError, TypeError):
        score, rsi, vol = '?', '?', '?'
    return f"{row.get('symbol', '?')}｜{score}分 RSI {rsi} 量比 {vol}｜{row.get('swing_group', '')}"


def compose(mode, now, rows):
    tw = fresh_rows(rows, MARKETS['tw'], now)
    us = fresh_rows(rows, MARKETS['us'], now)
    selected = [('美股最近有效掃描', us), ('台股最近有效掃描', tw)] if mode == 'morning' else ([('台股最近有效掃描', tw)] if mode == 'tw_close' else [('美股最近有效掃描', us)])
    lines = [f"📈 Market Room｜{LABELS[mode]}", now.astimezone(TAIPEI).strftime('%Y-%m-%d %H:%M 台灣時間'), '']
    for title, group in selected:
        lines.append(f"【{title}】有效樣本 {len(group)} 檔")
        if group:
            lines.extend('• ' + fmt(r) for r in group[:5])
            newest = max(r.get('date', '') for r in group)
            lines.append(f"樣本最新日K：{newest}")
        else:
            lines.append('尚無7日內有效資料')
        lines.append('')
    lines.extend(['⚠️ 這是歷史日K掃描排行榜，不是即時盤前報價或即時新聞。', '未掃描股票不代表不符合條件；請留意行情日期與市場休市。', '僅供研究，不構成投資建議。'])
    return '\n'.join(lines)[:4900]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--mode', choices=LABELS, required=True)
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    now = datetime.now(timezone.utc)
    rows = load_rows(Path('data/v33/master.csv'))
    msg = compose(args.mode, now, rows)
    print(msg)
    if args.dry_run:
        return
    token = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '').strip()
    target = os.environ.get('LINE_TO_USER_ID', '').strip()
    if not token or not target:
        sys.exit('Missing LINE_CHANNEL_ACCESS_TOKEN or LINE_TO_USER_ID GitHub secret')
    body = json.dumps({'to': target, 'messages': [{'type': 'text', 'text': msg}]}, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request('https://api.line.me/v2/bot/message/push', data=body, headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            print('LINE API status:', response.status)
    except urllib.error.HTTPError as e:
        print('LINE API error status:', e.code, 'response:', e.read().decode('utf-8', 'replace')[:500], file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
