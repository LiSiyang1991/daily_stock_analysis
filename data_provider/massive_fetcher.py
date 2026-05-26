# -*- coding: utf-8 -*-
"""
===================================
MassiveFetcher - US stocks data via Massive REST API
===================================

Data source: https://massive.com (formerly Polygon.io)
- Supports US stocks only (trades/quotes/aggregates)
- This implementation uses plain HTTP requests (no SDK) to avoid
  introducing a hard runtime dependency. If the official SDK is preferred,
  we can switch to `massive.RESTClient` later.

Auth:
- MASSIVE_API_KEY from environment (.env) via src.config.Config
- API key is passed via query parameter `apiKey=...` (per REST Quickstart)

Endpoint (aggregates):
- GET https://api.massive.com/v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}?limit=50000&adjusted=true
- Response follows Polygon/Massive style with results list, commonly using
  keys like 'o','h','l','c','v','t'. We normalize defensively.

Limitations:
- US only. For indices, DataFetcherManager still prefers Yfinance.
- 'amount' is approximated as `close * volume` in USD when not provided.
- 'pct_chg' computed from previous close.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log

from .base import (
    BaseFetcher,
    DataFetchError,
    STANDARD_COLUMNS,
    normalize_stock_code,
)
from src.config import get_config


logger = logging.getLogger(__name__)

_API_BASE = "https://api.massive.com"


class MassiveFetcher(BaseFetcher):
    name = "MassiveFetcher"
    # Keep priority low by default so it doesn't disrupt existing CN/HK flows.
    # DataFetcherManager will route US stocks explicitly and include this fetcher
    # when MASSIVE_API_KEY is configured.
    priority = 2

    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout
        cfg = get_config()
        self._api_key = (getattr(cfg, "massive_api_key", None) or "").strip()
        if not self._api_key:
            logger.warning("[MassiveFetcher] MASSIVE_API_KEY not configured; fetcher unavailable")
        else:
            logger.info("[MassiveFetcher] API key detected; US aggregates enabled")

    # Optional availability probes picked up by DataFetcherManager
    def is_available(self) -> bool:
        return bool(self._api_key)

    def is_available_for_request(self, capability: str = "") -> bool:
        # Only provide daily_data capability; other capabilities are not implemented here.
        if not self._api_key:
            return False
        if capability in {"", "daily_data"}:
            return True
        return False

    @retry(
        retry=retry_if_exception_type((requests.RequestException,)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _http_get(self, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        resp = requests.get(url, params=params, timeout=self.timeout)
        if resp.status_code == 401:
            raise DataFetchError("Massive API unauthorized – check MASSIVE_API_KEY or plan limits")
        if resp.status_code == 429:
            raise DataFetchError("Massive API rate limited – please slow down or upgrade plan")
        if resp.status_code >= 400:
            raise DataFetchError(f"Massive API HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except Exception as exc:
            raise DataFetchError(f"Massive API invalid JSON: {exc}") from exc

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        # Massive expects US tickers like AAPL/TSLA; normalize but do not strip US tickers.
        ticker = normalize_stock_code(stock_code).upper()
        # Build v2 aggregates (1 day)
        url = f"{_API_BASE}/v2/aggs/ticker/{ticker}/range/1/day/{start_date}/{end_date}"
        params = {
            "limit": 50000,
            "adjusted": "true",
            "apiKey": self._api_key,  # per REST Quickstart
        }
        data = self._http_get(url, params)
        results = (data or {}).get("results") or []
        results_count = data.get("resultsCount") if isinstance(data, dict) else len(results)
        if not results:
            logger.info(
                "[MassiveFetcher] 无数据: ticker=%s range=%s→%s resultsCount=%s", ticker, start_date, end_date, results_count
            )
            return pd.DataFrame(columns=STANDARD_COLUMNS)

        # Normalize each item into a dict with canonical keys
        rows: List[Dict[str, Any]] = []
        for item in results:
            # Common Massive/Polygon fields
            # t: timestamp (ms or s);
            # o/h/l/c: open/high/low/close; v: volume; n: transactions; vw: vwap
            t_raw = item.get("t") or item.get("timestamp")
            # Handle both seconds and milliseconds
            if isinstance(t_raw, (int, float)):
                ts = int(t_raw)
                if ts > 1_000_000_000_000:  # likely ms
                    dt = datetime.utcfromtimestamp(ts / 1000.0)
                else:
                    dt = datetime.utcfromtimestamp(ts)
            else:
                # Fallback: ignore malformed rows
                continue

            o = item.get("o") if "o" in item else item.get("open")
            h = item.get("h") if "h" in item else item.get("high")
            l = item.get("l") if "l" in item else item.get("low")
            c = item.get("c") if "c" in item else item.get("close")
            v = item.get("v") if "v" in item else item.get("volume")

            rows.append(
                {
                    "date": dt,  # pandas will parse to datetime64
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": v,
                    # Massive does not return RMB amount; approximate to USD notional if desired.
                    # Keep None here; we'll fill later with close*volume if both present.
                    "amount": None,
                }
            )

        df = pd.DataFrame(rows)
        logger.info(
            "[MassiveFetcher] 数据获取: ticker=%s range=%s→%s rows=%d resultsCount=%s",
            ticker, start_date, end_date, len(df), results_count,
        )
        return df

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame(columns=STANDARD_COLUMNS)

        out = df.copy()
        # Ensure correct dtypes
        out["date"] = pd.to_datetime(out["date"], errors="coerce")
        for col in ["open", "high", "low", "close", "volume"]:
            if col in out.columns:
                out[col] = pd.to_numeric(out[col], errors="coerce")

        # Compute amount if missing and feasible (USD notional)
        if "amount" not in out.columns or out["amount"].isna().all():
            if "close" in out.columns and "volume" in out.columns:
                mask = out["close"].notna() & out["volume"].notna()
                out["amount"] = None
                out.loc[mask, "amount"] = out.loc[mask, "close"] * out.loc[mask, "volume"]
            else:
                out["amount"] = None

        # Sort and compute pct_chg from prior close
        out = out.sort_values("date", ascending=True).reset_index(drop=True)
        if "close" in out.columns:
            prev_close = out["close"].shift(1)
            out["pct_chg"] = ((out["close"] - prev_close) / prev_close * 100.0).round(4)
            # For first row with no prev_close, set 0.0
            out.loc[out.index[0], "pct_chg"] = 0.0
        else:
            out["pct_chg"] = None

        # Log data summary for workflow observability
        if not out.empty:
            first = out.iloc[0]
            last = out.iloc[-1]
            logger.info(
                "[MassiveFetcher] %s: %d条数据, %s→%s, 首行 close=%.2f vol=%s, 末行 close=%.2f vol=%s",
                stock_code,
                len(out),
                str(first.get("date", "?")),
                str(last.get("date", "?")),
                float(first.get("close", 0) or 0),
                str(first.get("volume", "?")),
                float(last.get("close", 0) or 0),
                str(last.get("volume", "?")),
            )

        # Reindex to standard columns
        for col in STANDARD_COLUMNS:
            if col not in out.columns:
                out[col] = None

        out = out[STANDARD_COLUMNS]
        return out
