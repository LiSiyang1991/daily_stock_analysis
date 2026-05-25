# feat(data_provider): Add MassiveFetcher (US daily aggregates) and prefer Massive before YF (US stocks)

## What changed
- Added `data_provider/massive_fetcher.py` implementing Massive (Polygon.io rebranded) REST integration for US daily OHLCV aggregates.
- Registered `MassiveFetcher` in `data_provider/__init__.py`.
- Data source routing (`data_provider/base.py`):
  - Declared support map: `"MassiveFetcher": {"us"}`
  - US routing order updated per request:
    - If Longbridge credentials are configured (and non-index): `LongbridgeFetcher → MassiveFetcher → YfinanceFetcher`
    - Otherwise: `MassiveFetcher → YfinanceFetcher → LongbridgeFetcher`
  - Default instantiation only when `MASSIVE_API_KEY` is present.
- Config (`src/config.py`):
  - Added `massive_api_key` field and `MASSIVE_API_KEY` loading in `_load_from_env()`.
- Env template (`.env.example`):
  - Documented `MASSIVE_API_KEY` and linked Massive pricing.
- Tests (`tests/test_massive_fetcher.py`):
  - Unit tests (mocked HTTP, no network): availability probe, normalize Polygon/Massive-style payload, empty results path.

## Why
- Provide a more stable, licensing-friendly US market data source with clear upgrade paths (delayed/real-time, minutes/seconds, WebSocket), and keep the project’s multi-source failover design.
- By request, prioritize Massive ahead of YF for US stocks (note: current key is Basic/EOD; order can be reverted or made configurable later if needed).

## How to configure
- Put your key in project-local `.env` (do not commit):
  ```
  MASSIVE_API_KEY=<your-key>
  ```
- No changes required for CN/HK sources. US-only activation is based on key presence.

## Tests & results
- `tests/test_massive_fetcher.py`: 3 tests passing locally (Python 3.11)
  - `test_is_available_when_key_present`: availability probes pass when key set via env
  - `test_normalize_polygon_style_payload`: normalizes to standard columns and checks pct_chg
  - `test_empty_results`: handles empty results gracefully
- A detailed test report is included under `docs/tests/massive_integration_report.md` (summary: all unit tests pass; network tests intentionally mocked for CI stability).

## Risks & boundaries
- US-only in this PR (indices continue to use YF fallback). Extending to indices/WebSocket/minute/second endpoints can follow in a separate PR.
- With Basic plan (EOD, 5 req/min), placing Massive first is acceptable for low-frequency/EOD runs. For intraday or higher frequency, consider upgrading plan or reverting priority.

## Rollback
- Remove `data_provider/massive_fetcher.py` and its imports.
- Restore US routing to previous order in `data_provider/base.py`.
- Remove `massive_api_key` field loading from `src/config.py` and `.env.example` docs.

## Checklist
- [x] Minimal invasive integration; only active when `MASSIVE_API_KEY` is set
- [x] Unit tests
- [x] Env template updated
- [x] Docs: report attached

---
Notes: This PR is targeted to the fork repository as requested (not upstream). If you want a toggle for routing preference (e.g., `PREFER_MASSIVE_US=true|false`), I can add it in a follow-up.
