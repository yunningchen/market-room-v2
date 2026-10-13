"""Market Room plain-language LINE digest based on saved daily-candle scans."""
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
MODES = {
    'morning': ('🌤️ 早安！台股盤前與美股回顧', [('美股', 'us'), ('台股', 'tw')]),
    'tw_close': ('🌇 台股收盤觀察', [('台股', 'tw')]),
    'us_pre': ('🌙 美股盤前觀察', [('美股', 'us')]),
}


def load_rows(path):
    if not path.exists():
        return []
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def fresh_rows(rows, markets, now):
    cutoff = now - timedelta(days=7)
    output = []
    for row in rows:
        if row.get('market') not in markets:
            continue
        try:
            checked = datetime.fromisoformat(row['last_checked_utc'].replace('Z', '+00:00'))
            if checked.tzinfo is None:
                checked = checked.replace(tzinfo=timezone.utc)
            price_date = datetime.fromisoformat(row['date']).date()
            score = float(row['score'])
            if checked < cutoff or price_date < cutoff.astimezone(TAIPEI).date():
                continue
            if not (0 <= score <= 100):
                continue
        except (KeyError, ValueError, TypeError):
            continue
        output.append(row)
    return sorted(output, key=lambda r: (-float(r['score']), r.get('symbol', '')))


def safe_number(row, key):
    try:
        return float(row[key])
    except (KeyError, ValueError, TypeError):
        return None


def explanation(row):
    rsi = safe_number(row, 'rsi14')
    vol = safe_number(row, 'volume_ratio')
    notes = []
    if vol is not None:
        if vol >= 1.5:
            notes.append(f'成交量約為近期平均的 {vol:.1f} 倍，交易比平常熱絡')
        elif vol >= 1.0:
            notes.append('成交量接近或略高於近期平均')
        else:
            notes.append('成交量比近期平均少，還需要觀察量能是否跟上')
    if rsi is not None:
        if rsi >= 70:
            notes.append(f'RSI {rsi:.0f}，短線偏熱，留意追高風險')
        elif rsi >= 50:
            notes.append(f'RSI {rsi:.0f}，近期動能偏強')
        else:
            notes.append(f'RSI {rsi:.0f}，動能尚未明顯轉強')
    return '；'.join(notes) or '目前缺少足夠指標可解讀'


def display_name(row):
    symbol = row.get('symbol', '?')
    name = (row.get('name') or row.get('company_name') or '').strip()
    if name and name.lower() != symbol.lower():
        return f'{name}（{symbol}）'
    return symbol


def compose(mode, now, rows):
    heading, groups = MODES[mode]
    lines = [f'📈 Market Room｜{heading}', now.astimezone(TAIPEI).strftime('%Y/%m/%d %H:%M（台灣時間）'), '']
    for market_label, market_key in groups:
        selected = fresh_rows(rows, MARKETS[market_key], now)
        lines.append(f'【{market_label}觀察名單】目前有 {len(selected)} 檔近期有效掃描資料')
        if not selected:
            lines.append('目前沒有足夠新的日 K 資料，先不提供個股排行。')
        else:
            for i, row in enumerate(selected[:3], 1):
                score = int(float(row['score']))
                lines.append(f'{i}. {display_name(row)}｜技術評分 {score} 分')
                lines.append('   ' + explanation(row))
                lines.append('   行情日期：' + row.get('date', '未提供'))
            lines.append('提醒：這是已掃描股票中的技術條件排序，不是全市場排名。')
        lines.append('')
    lines.extend([
        '💡 怎麼看？分數越高，代表越符合目前設定的技術篩選條件，不代表上漲機率。',
        '行情為已保存的日 K 資料，請留意行情日期。',
    ])
    # V5: add only newly detected events within 24 hours, avoiding repeated old alerts.
    try:
        payload = json.loads(Path('docs/data/signals.json').read_text(encoding='utf-8'))
        cutoff = now - timedelta(hours={'morning': 11, 'tw_close': 6, 'us_pre': 7}[mode])
        valid_markets = set().union(*(MARKETS[key] for _, key in groups))
        events = []
        for e in payload.get('signals', []):
            t = datetime.fromisoformat(e['detected_utc'].replace('Z', '+00:00'))
            if t >= cutoff and e.get('market') in valid_markets:
                events.append(e)
        if events:
            lines.extend(['', '【近期技術變化】'])
            for e in events[:3]:
                lines.append(f"• {e.get('symbol', '?')}：{e.get('message', '')}（{e.get('date', '')}）")
            lines.append('詳情：https://yunningchen.github.io/market-room-v2/')
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return '\n'.join(lines)[:4900]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=MODES, required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    msg = compose(args.mode, now, load_rows(Path('data/v33/master.csv')))
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
