import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

SYMBOLS = {"TW": ["2330.TW", "2317.TW", "2454.TW"], "US": ["AAPL", "MSFT", "NVDA"]}


def calculate(df):
    close = df["Close"].astype(float)
    volume = df["Volume"].astype(float)
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    signal = dif.ewm(span=9, adjust=False).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi = 100 - 100 / (1 + rs)
    if loss.iloc[-1] == 0:
        rsi.iloc[-1] = 100 if gain.iloc[-1] > 0 else 50
    vol20 = volume.rolling(20).mean()
    breakout = close.iloc[-1] > close.shift(1).rolling(20).max().iloc[-1]
    conditions = {
        "above_ma20": bool(close.iloc[-1] > ma20.iloc[-1]),
        "above_ma60": bool(close.iloc[-1] > ma60.iloc[-1]),
        "macd_positive": bool(dif.iloc[-1] > signal.iloc[-1]),
        "rsi_45_to_68": bool(45 <= rsi.iloc[-1] <= 68),
        "volume_above_average": bool(volume.iloc[-1] > vol20.iloc[-1]),
        "breakout_20d": bool(breakout),
    }
    weights = {"above_ma20": 15, "above_ma60": 15, "macd_positive": 25, "rsi_45_to_68": 15, "volume_above_average": 20, "breakout_20d": 10}
    return {
        "date": str(df.index[-1].date()),
        "close": round(float(close.iloc[-1]), 4),
        "ma20": round(float(ma20.iloc[-1]), 4),
        "ma60": round(float(ma60.iloc[-1]), 4),
        "macd_dif": round(float(dif.iloc[-1]), 4),
        "macd_signal": round(float(signal.iloc[-1]), 4),
        "rsi14": round(float(rsi.iloc[-1]), 2),
        "volume_ratio": round(float(volume.iloc[-1] / vol20.iloc[-1]), 2) if vol20.iloc[-1] else None,
        "score": sum(weights[k] for k, v in conditions.items() if v),
        "conditions": conditions,
    }


def main():
    market = os.getenv("MARKET", "ALL").upper()
    if market not in ("ALL", "TW", "US"):
        raise ValueError("MARKET must be ALL, TW, or US")
    chosen = [s for m, symbols in SYMBOLS.items() if market in ("ALL", m) for s in symbols]
    results, errors = [], []
    for symbol in chosen:
        try:
            data = yf.download(symbol, period="9mo", interval="1d", auto_adjust=True, progress=False, threads=False, timeout=20)
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.get_level_values(0)
            data = data.dropna(subset=["Close", "Volume"])
            if len(data) < 65:
                raise ValueError(f"Insufficient trading days: {len(data)}")
            results.append({"symbol": symbol, "market": "TW" if symbol.endswith(".TW") else "US", **calculate(data)})
            print(f"OK {symbol}")
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)[:250]})
            print(f"ERROR {symbol}: {exc}")
    results.sort(key=lambda x: x["score"], reverse=True)
    output = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "market": market, "requested": len(chosen), "success": len(results), "errors": errors, "stocks": results}
    Path("output").mkdir(exist_ok=True)
    Path("output/latest.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Finished: {len(results)}/{len(chosen)} successful; output/latest.json")
    if not results:
        raise SystemExit("No market data fetched; see errors in output/latest.json")


if __name__ == "__main__":
    main()
