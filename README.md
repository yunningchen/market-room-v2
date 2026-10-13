# Market Room V2 — Starter

This is a **six-stock proof of concept**, not a whole-market scanner. It does not send LINE notifications, place orders, or modify the existing Market Room dashboard.

## Manual test

1. Open **Actions** on GitHub.
2. Select **Market Room V2 - Test Scan**.
3. Click **Run workflow**, choose `ALL`, then confirm.
4. Open the finished workflow run, inspect logs, and download `market-room-test-result` under **Artifacts**.

The JSON output contains scores, indicator values, and any data-download errors. Scores are experimental, not investment advice. Yahoo Finance may throttle or block automated requests; its availability and terms must be evaluated before expanding coverage.

## Scope

- TW: 2330.TW, 2317.TW, 2454.TW
- US: AAPL, MSFT, NVDA
- Indicators: MA20, MA60, MACD(12,26,9), RSI(14), 20-day volume ratio, 20-day breakout.
- Workflow: manual only until validation is complete.

**Security:** Never commit API keys, LINE tokens, channel secrets, or passwords. Keep the repository free of credentials and personal information.
