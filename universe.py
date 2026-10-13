
"""Market Room V2 — Full Market Universe Builder."""

import csv
import json
import pathlib
import random
import time
import urllib.request
from datetime import datetime, timezone


OUT = pathlib.Path("output")
OUT.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json,text/plain,*/*",
}

SOURCES = {
    "TWSE": "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
    "TPEX": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
    "NASDAQ": "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
    "OTHER_US": "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
}


def fetch(url, attempts=4):
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            print(
                f"Downloading {url} "
                f"(attempt {attempt}/{attempts})",
                flush=True,
            )

            request = urllib.request.Request(
                url,
                headers=HEADERS,
            )

            with urllib.request.urlopen(
                request,
                timeout=45,
            ) as response:
                raw = response.read()

            if not raw:
                raise ValueError("Empty response")

            return raw.decode("utf-8-sig")

        except Exception as exc:
            last_error = exc
            print(f"Download failed: {exc}", flush=True)

            if attempt < attempts:
                delay = min(
                    2 ** attempt + random.uniform(0, 1),
                    15,
                )
                time.sleep(delay)

    raise RuntimeError(
        f"Download failed after retries: {last_error}"
    )


def taiwan(key, suffix):
    data = json.loads(fetch(SOURCES[key]))

    if not isinstance(data, list):
        raise ValueError("Expected JSON array")

    # Print the actual TPEx field names for diagnosis.
    if key == "TPEX" and data:
        print(
            "TPEX FIELD NAMES:",
            list(data[0].keys()),
            flush=True,
        )
        print(
            "TPEX FIRST RECORD:",
            json.dumps(
                data[0],
                ensure_ascii=False,
            ),
            flush=True,
        )

    records = []

    for row in data:
        code = str(
            row.get("公司代號")
            or row.get("SecuritiesCompanyCode")
            or ""
        ).strip()

        name = str(
            row.get("公司名稱")
            or row.get("公司簡稱")
            or row.get("CompanyName")
            or row.get("CompanyAbbreviation")
            or row.get("CompanyShortName")
            or ""
        ).strip()

        if len(code) == 4 and code.isdigit():
            records.append({
                "symbol": f"{code}.{suffix}",
                "name": name,
                "market": key,
            })

    if not records:
        raise ValueError(
            f"No valid symbols parsed from {key}"
        )

    return records


def us_listed():
    records = []

    for key in ("NASDAQ", "OTHER_US"):
        content = fetch(SOURCES[key])

        lines = [
            line
            for line in content.splitlines()
            if not line.startswith(
                "File Creation Time:"
            )
        ]

        reader = csv.DictReader(
            lines,
            delimiter="|",
        )

        for row in reader:
            if key == "NASDAQ":
                symbol = (
                    row.get("Symbol") or ""
                ).strip()
                exchange = "NASDAQ"

            else:
                symbol = (
                    row.get("ACT Symbol") or ""
                ).strip()

                exchange = {
                    "N": "NYSE",
                    "A": "NYSE American",
                }.get(
                    row.get("Exchange"),
                    "OTHER",
                )

            if not symbol:
                continue

            if row.get("Test Issue") != "N":
                continue

            if row.get("ETF") == "Y":
                continue

            if exchange not in (
                "NASDAQ",
                "NYSE",
                "NYSE American",
            ):
                continue

            records.append({
                "symbol": symbol,
                "name": (
                    row.get("Security Name") or ""
                ).strip(),
                "market": exchange,
            })

    if not records:
        raise ValueError(
            "No US symbols parsed"
        )

    return records


def main():
    all_rows = []
    report = {}

    markets = [
        (
            "TWSE",
            lambda: taiwan("TWSE", "TW"),
        ),
        (
            "TPEX",
            lambda: taiwan("TPEX", "TWO"),
        ),
        (
            "US",
            us_listed,
        ),
    ]

    for market, loader in markets:
        try:
            records = loader()
            all_rows.extend(records)

            report[market] = {
                "status": "ok",
                "count": len(records),
            }

            print(
                f"{market}: {len(records)} symbols",
                flush=True,
            )

        except Exception as exc:
            report[market] = {
                "status": "error",
                "message": str(exc),
            }

            print(
                f"{market}: ERROR - {exc}",
                flush=True,
            )

    unique = {
        f"{row['market']}:{row['symbol']}": row
        for row in all_rows
    }

    rows = sorted(
        unique.values(),
        key=lambda row: (
            row["market"],
            row["symbol"],
        ),
    )

    csv_path = OUT / "universe.csv"

    with csv_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "symbol",
                "name",
                "market",
            ],
        )

        writer.writeheader()
        writer.writerows(rows)

    report["generated_at_utc"] = (
        datetime.now(timezone.utc).isoformat()
    )

    report["unique_symbols"] = len(rows)

    report["complete"] = all(
        report[key]["status"] == "ok"
        for key in (
            "TWSE",
            "TPEX",
            "US",
        )
    )

    report["note"] = (
        "Candidate listings only. "
        "Further security-type classification "
        "and source validation are required."
    )

    report_path = (
        OUT / "universe_report.json"
    )

    report_path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )

    if not report["complete"]:
        raise SystemExit(
            "Incomplete universe: "
            "check universe_report.json"
        )


if __name__ == "__main__":
    main()
