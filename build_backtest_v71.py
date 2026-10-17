"""Independent V7 research backtest; never writes live scanner or LINE files.

Uses the exact V6 build_strategy.analyze() on historical prefixes, and executes
signals at the next session open. Research sample is CURRENT ranked constituents:
survivorship and selection bias; not a historical full-market backtest.
"""
import argparse
import csv
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

from build_strategy import analyze
from build_history import calc_rsi

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'backtest_v71.json'
TW = {'TWSE', 'TPEX'}
US = {'NASDAQ', 'NYSE', 'NYSE American'}


def candles_from_frame(frame):
    if isinstance(frame.columns, pd.MultiIndex):
        frame = frame.copy()
        frame.columns = frame.columns.get_level_values(0)
    required = ['Open', 'High', 'Low', 'Close', 'Volume']
    if any(x not in frame for x in required):
        return []
    f = frame[required].dropna(subset=required[:4]).copy()
    f = f[(f['Close'] > 0) & (f['Open'] > 0) & (f['High'] >= f['Low'])]
    if len(f) < 110:
        return []
    c = f['Close']
    f['ma20'] = c.rolling(20).mean()
    f['ma60'] = c.rolling(60).mean()
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    f['hist'] = macd - macd.ewm(span=9, adjust=False).mean()
    f['rsi'] = calc_rsi(c)
    output = []
    for dt, x in f.iterrows():
        def val(k):
            v = x[k]
            return float(v) if pd.notna(v) and math.isfinite(float(v)) else None
        output.append({'date': pd.Timestamp(dt).strftime('%Y-%m-%d'),
                       'open': val('Open'), 'high': val('High'), 'low': val('Low'),
                       'close': val('Close'), 'volume': val('Volume'),
                       'ma20': val('ma20'), 'ma60': val('ma60'),
                       'hist': val('hist'), 'rsi': val('rsi')})
    return output


def trade_return(bars, signal_i, holding, stop, fee_bps):
    # Signal is observed at day i CLOSE. Enter at day i+1 OPEN.
    entry_i = signal_i + 1
    exit_i = entry_i + holding - 1
    if exit_i >= len(bars):
        return None
    entry = bars[entry_i]['open']
    if not entry or entry <= 0 or stop is None:
        return None
    # If gap opens below planned stop, skip trade rather than assume an impossible fill.
    if entry <= stop:
        return None
    exit_price = bars[exit_i]['close']
    reason = 'time'
    for j in range(entry_i, exit_i + 1):
        day = bars[j]
        if day['low'] is not None and day['low'] <= stop:
            # Gap through stop exits at worse open; intraday stop at stop.
            exit_price = min(stop, day['open']) if day['open'] is not None else stop
            exit_i = j
            reason = 'stop'
            break
    if exit_price is None:
        return None
    gross = exit_price / entry - 1
    net = (exit_price / entry) * (1 - fee_bps / 10000) / (1 + fee_bps / 10000) - 1
    return {'entry_date': bars[entry_i]['date'], 'exit_date': bars[exit_i]['date'],
            'entry': round(entry, 4), 'exit': round(exit_price, 4),
            'gross_pct': round(100 * gross, 3), 'return_pct': round(100 * net, 3), 'exit_reason': reason}


def simulate(symbol, market, bars, holding, fee_bps, strategy):
    trades = []
    last_exit_date = ''
    for i in range(85, len(bars) - holding):
        if bars[i]['date'] <= last_exit_date:
            continue
        result = analyze({'symbol': symbol, 'market': market, 'name': ''}, bars[:i + 1])
        if result is None:
            continue
        # Both can qualify; evaluate both independently for research.
        for kind, flag, stop in [('A', 'a_qualified', 'a_stop'), ('B', 'b_qualified', 'b_stop')]:
            if kind != strategy:
                continue
            if not result[flag]:
                continue
            trade = trade_return(bars, i, holding, result[stop], fee_bps)
            if trade:
                trades.append({'symbol': symbol, 'market': market, 'strategy': kind,
                               'signal_date': bars[i]['date'], **trade})
                # No overlapping trades on the same symbol, across A/B.
                last_exit_date = trade['exit_date']
                break
    return trades


def stats(trades):
    if not trades:
        return {'trades': 0, 'win_rate_pct': None, 'mean_return_pct': None,
                'median_return_pct': None, 'profit_factor': None, 'worst_trade_return_pct': None}
    vals = [x['return_pct'] for x in trades]
    gain = sum(x for x in vals if x > 0)
    loss = -sum(x for x in vals if x < 0)
    return {'trades': len(vals), 'win_rate_pct': round(100 * sum(v > 0 for v in vals) / len(vals), 2),
            'mean_return_pct': round(sum(vals) / len(vals), 3),
            'median_return_pct': round(float(pd.Series(vals).median()), 3),
            'profit_factor': round(gain / loss, 3) if loss else None,
            'worst_trade_return_pct': round(min(vals), 3)}


def benchmark_returns():
    result = {}
    for market, symbol in [('TW', '^TWII'), ('US', '^GSPC')]:
        try:
            f = yf.download(symbol, period='5y', interval='1d', auto_adjust=True, progress=False, threads=False, timeout=20)
            if isinstance(f.columns, pd.MultiIndex):
                f.columns = f.columns.get_level_values(0)
            closes = f['Close'].dropna()
            annual = {}
            for year, group in closes.groupby(closes.index.year):
                if len(group) >= 2:
                    annual[str(year)] = round(100 * (float(group.iloc[-1]) / float(group.iloc[0]) - 1), 3)
            result[market] = {'symbol': symbol, 'annual_return_pct': annual, 'method': 'first-to-last observed trading close within each calendar year; partial years included'}
        except Exception as e:
            result[market] = {'symbol': symbol, 'error': str(e)[:120]}
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--per-market', type=int, default=30)
    ap.add_argument('--years', type=int, choices=[5], default=5)
    ap.add_argument('--fee-bps', type=float, default=20, help='One-way illustrative cost in basis points; NOT tax-specific')
    ap.add_argument('--delay', type=float, default=0.35)
    ap.add_argument('--sample-mode', choices=['ranking'], default='ranking')
    args = ap.parse_args()
    if not 1 <= args.per_market <= 100 or not 0 <= args.fee_bps <= 500:
        ap.error('per-market must be 1..100 and fee-bps 0..500')
    ranking = ROOT / 'docs' / 'data' / 'ranking.csv'
    if not ranking.is_file():
        sys.exit('Missing docs/data/ranking.csv')
    with ranking.open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    selected = {'TW': [], 'US': []}
    seen = set()
    for row in sorted(rows, key=lambda r: -(float(r.get('score') or 0))):
        market = 'TW' if row.get('market') in TW else ('US' if row.get('market') in US else None)
        sym = row.get('symbol', '')
        if market and sym and sym not in seen and len(selected[market]) < args.per_market:
            selected[market].append((sym, row.get('market', '')))
            seen.add(sym)
    all_trades = []
    holdings = (5, 10, 20, 40)
    failures = []
    coverage = {'TW': 0, 'US': 0}
    for group in ('TW', 'US'):
        for sym, market in selected[group]:
            try:
                frame = yf.download(sym, period='5y', interval='1d', auto_adjust=False,
                                    progress=False, threads=False, timeout=20)
                bars = candles_from_frame(frame)
                if not bars:
                    raise ValueError('Insufficient valid history')
                coverage[group] += 1
                for holding in holdings:
                    
                    for strategy in ('A', 'B'):
                        all_trades.extend([{**t, 'holding_sessions': holding} for t in simulate(sym, market, bars, holding, args.fee_bps, strategy)])
            except Exception as e:
                failures.append({'symbol': sym, 'error': f'{type(e).__name__}: {str(e)[:120]}'})
            print(f'{group} {sym}: processed; successful symbols={coverage[group]}', flush=True)
            time.sleep(args.delay)
    summary = {m: {s: {str(h): stats([t for t in all_trades if (t['market'] in (TW if m == 'TW' else US)) and t['strategy'] == s and t['holding_sessions'] == h])
                   for h in holdings} for s in ('A', 'B')} for m in ('TW', 'US')}
    payload = {'version': 'V7.1-research-2', 'generated_utc': datetime.now(timezone.utc).isoformat(),
               'years_requested': args.years, 'holding_sessions': list(holdings),
               'one_way_fee_bps': args.fee_bps,
               'selected_symbols': {m: len(selected[m]) for m in selected},
               'selection': 'Top-ranked symbols from current docs/data/ranking.csv, NOT historical full universe',
               'downloaded_symbols': coverage, 'failed': failures,
               'summary': summary, 'trades': all_trades,
               'annual': {m: {st: {str(h): {str(y): stats([t for t in all_trades if t['market'] in (TW if m == 'TW' else US) and t['strategy'] == st and t['holding_sessions'] == h and t['entry_date'].startswith(str(y))]) for y in range(2021, 2027)} for h in holdings} for st in ('A', 'B')} for m in ('TW', 'US')},
               'benchmarks': benchmark_returns(),
               'limitations': [
                   'Research sample uses current ranking, not historical constituents: severe survivorship and selection bias.',
                   'A/B conditions are reused from build_strategy.analyze; candle indicators are calculated from historical daily OHLCV.',
                   'Signals use closing prices; entries occur next session at open, stops are modeled from daily OHLC (intraday ordering unknown).',
                   'Cost is illustrative and not Taiwan-specific transaction tax; dividends and corporate actions may be unadjusted.',
                   'A/B are evaluated independently per symbol and holding period; signals within each strategy are non-overlapping. This is NOT a capital-weighted portfolio.',
                   'No historical delisted symbols; results cannot establish a reliable live strategy win rate.',
                   'Benchmarks are buy-and-hold calendar-year returns, not matched-date excess returns; not directly comparable with mean trade returns.'
               ]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Wrote {OUT}; trades={len(all_trades)}')


if __name__ == '__main__':
    main()
