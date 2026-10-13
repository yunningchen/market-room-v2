
"""Read-only batch download benchmark for Market Room. No dashboard writes."""

import argparse
import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

MARKETS = {
    "TW": {"TWSE", "TPEX"},
    "US": {"NASDAQ", "NYSE", "NYSE American"},
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="output/cleaned/candidates.csv")
    parser.add_argument("--per-market", type=int, default=30)
    parser.add_argument("--chunk-size", type=int, default=10)
    args = parser.parse_args()

    if not 1 <= args.per_market <= 100:
        parser.error("per-market must be 1..100")
    if not 1 <= args.chunk_size <= 50:
        parser.error("chunk-size must be 1..50")

    with open(args.input, encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "markets": {},
    }

    for label, markets in MARKETS.items():
        symbols = list(dict.fromkeys(
            row["symbol"]
            for row in rows
            if row.get("market") in markets and row.get("symbol")
        ))
        symbols = symbols[:args.per_market]

        results = []
        started = time.monotonic()

        for i in range(0, len(symbols), args.chunk_size):
            batch = symbols[i:i + args.chunk_size]

            try:
                data = yf.download(
                    batch,
                    period="9mo",
                    interval="1d",
                    auto_adjust=True,
                    progress=False,
                    threads=False,
                    timeout=20,
                    group_by="ticker",
                )

                for symbol in batch:
                    try:
                        if isinstance(data.columns, pd.MultiIndex):
                            if symbol in data.columns.get_level_values(0):
                                frame = data[symbol]
                            elif symbol in data.columns.get_level_values(1):
                                frame = data.xs(
                                    symbol, axis=1, level=1
                                )
                            else:
                                raise ValueError(
                                    "Ticker missing in response"
                                )
                        else:
                            frame = data

                        close = pd.to_numeric(
                            frame["Close"], errors="coerce"
                        ).dropna()

                        if len(close) < 90:
                            raise ValueError(
                                f"Only {len(close)} valid bars"
                            )

                        results.append({
                            "symbol": symbol,
                            "ok": True,
                            "last_price_date": str(
                                pd.Timestamp(close.index[-1]).date()
                            ),
                            "bars": len(close),
                        })

                    except Exception as exc:
                        results.append({
                            "symbol": symbol,
                            "ok": False,
                            "error": str(exc)[:160],
                        })

            except Exception as exc:
                for symbol in batch:
                    results.append({
                        "symbol": symbol,
                        "ok": False,
                        "error": str(exc)[:160],
                    })

            time.sleep(1.5)

        successful = sum(result["ok"] for result in results)

        report["markets"][label] = {
            "attempted": len(symbols),
            "successful": successful,
            "success_rate": (
                round(successful / len(symbols), 3)
                if symbols else 0
            ),
            "elapsed_seconds": round(
                time.monotonic() - started, 1
            ),
            "results": results,
        }

        print(
            f"{label}: {successful}/{len(symbols)} "
            f"in {report['markets'][label]['elapsed_seconds']}s",
            flush=True,
        )

    output = Path("output/benchmark")
    output.mkdir(parents=True, exist_ok=True)

    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("Benchmark report saved successfully.", flush=True)


if __name__ == "__main__":
    main()
