"""Build a candidate stock-symbol universe. Uses public sources; verify exchange coverage before production."""
import csv
import io
import json
import pathlib
import urllib.request
from datetime import datetime, timezone

OUT = pathlib.Path('output')
OUT.mkdir(exist_ok=True)
HEADERS = {'User-Agent': 'MarketRoomV2/0.1 (personal research)'}
SOURCES = {
    'TWSE': 'https://openapi.twse.com.tw/v1/opendata/t187ap03_L',
    'TPEX': 'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O',
    'NASDAQ': 'https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt',
    'OTHER_US': 'https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt',
}

def fetch(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=25) as response:
        return response.read().decode('utf-8-sig')

def taiwan(key, suffix):
    rows = json.loads(fetch(SOURCES[key]))
    if not isinstance(rows, list):
        raise ValueError('Expected JSON array')
    result = []
    for row in rows:
        code = str(row.get('公司代號') or row.get('SecuritiesCompanyCode') or '').strip()
        name = str(row.get('公司名稱') or row.get('公司簡稱') or '').strip()
        if len(code) == 4 and code.isdigit():
            result.append({'symbol': f'{code}.{suffix}', 'name': name, 'market': key})
    if not result:
        raise ValueError('No four-digit stock codes parsed; check schema')
    return result

def us_listed():
    result = []
    for key in ('NASDAQ', 'OTHER_US'):
        lines = fetch(SOURCES[key]).splitlines()
        reader = csv.DictReader((line for line in lines if not line.startswith('File Creation Time:')), delimiter='|')
        for row in reader:
            if key == 'NASDAQ':
                symbol = (row.get('Symbol') or '').strip()
                exchange = 'NASDAQ'
                test = row.get('Test Issue')
            else:
                symbol = (row.get('ACT Symbol') or '').strip()
                exchange = {'N': 'NYSE', 'A': 'NYSE American', 'P': 'NYSE Arca', 'Z': 'Cboe BZX'}.get(row.get('Exchange'), 'OTHER')
                test = row.get('Test Issue')
            if not symbol or test != 'N' or exchange not in ('NASDAQ', 'NYSE', 'NYSE American'):
                continue
            if row.get('ETF') == 'Y':
                continue
            # This is a candidate universe, not a guaranteed common-stock-only list.
            result.append({'symbol': symbol, 'name': (row.get('Security Name') or '').strip(), 'market': exchange})
    if not result:
        raise ValueError('No US symbols parsed')
    return result

all_rows, report = [], {}
for market, loader in [('TWSE', lambda: taiwan('TWSE', 'TW')), ('TPEX', lambda: taiwan('TPEX', 'TWO')), ('US', us_listed)]:
    try:
        records = loader()
        all_rows.extend(records)
        report[market] = {'status': 'ok', 'count': len(records)}
    except Exception as exc:
        report[market] = {'status': 'error', 'message': str(exc)}
unique = {f"{row['market']}:{row['symbol']}": row for row in all_rows}
rows = sorted(unique.values(), key=lambda r: (r['market'], r['symbol']))
with (OUT / 'universe.csv').open('w', encoding='utf-8-sig', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=['symbol', 'name', 'market'])
    writer.writeheader()
    writer.writerows(rows)
report['generated_at_utc'] = datetime.now(timezone.utc).isoformat()
report['unique_symbols'] = len(rows)
report['note'] = 'Candidate listings; ETF filtered in US, but preferred shares, units, warrants and other non-common securities may remain. Further classification required.'
(OUT / 'universe_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))
if any(report[k]['status'] == 'error' for k in ('TWSE', 'TPEX', 'US')):
    raise SystemExit('One or more sources failed; see output/universe_report.json')
