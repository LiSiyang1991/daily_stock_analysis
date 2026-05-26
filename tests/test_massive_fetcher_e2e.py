# -*- coding: utf-8 -*-
"""E2E tests for MassiveFetcher — requires MASSIVE_API_KEY and network access.

NOTE: Basic plan has 5 req/min rate limit. These tests are consolidated
into a single network call to stay within limits.
"""

import os
import time

import pandas as pd
import pytest
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

from data_provider.massive_fetcher import MassiveFetcher
from src.config import Config

# Ensure .env is loaded so MASSIVE_API_KEY is available for the skipif check
load_dotenv()

MASSIVE_KEY = (os.getenv("MASSIVE_API_KEY") or "").strip()
pytestmark = pytest.mark.skipif(
    not MASSIVE_KEY,
    reason="未设置 MASSIVE_API_KEY 环境变量，跳过 E2E 测试",
)


@pytest.fixture(autouse=True)
def _ensure_config(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", MASSIVE_KEY)
    Config._instance = None
    yield
    Config._instance = None


@pytest.fixture
def fetcher():
    return MassiveFetcher(timeout=30)


class TestMassiveFetcherE2E:
    """End-to-end tests hitting the real Massive (Polygon.io) REST API.

    Only two tests to stay within Basic plan 5 req/min limit:
    - test_fetch_aapl_data: validates shape, types, values (1 API call)
    - test_fetcher_availability: no API call, just checks availability flags
    """

    @pytest.mark.network
    def test_fetch_aapl_data(self, fetcher):
        """Fetch AAPL recent 7 days — validates shape, columns, types, and values."""
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=7)
        start_str = start.isoformat()
        end_str = end.isoformat()

        raw = fetcher._fetch_raw_data("AAPL", start_str, end_str)
        assert isinstance(raw, pd.DataFrame)

        df = fetcher._normalize_data(raw, "AAPL")
        assert isinstance(df, pd.DataFrame)
        expected_cols = {"date", "open", "high", "low", "close", "volume", "amount", "pct_chg"}
        assert set(df.columns) == expected_cols

        if not df.empty:
            # Verify data types
            assert pd.api.types.is_datetime64_any_dtype(df["date"])
            assert pd.api.types.is_numeric_dtype(df["close"])
            assert pd.api.types.is_numeric_dtype(df["volume"])
            # All closes should be positive
            assert (df["close"] > 0).all(), f"Got non-positive close: {df['close'].describe()}"
            # Volume should be non-negative
            assert (df["volume"] >= 0).all()
            # pct_chg for first row should be 0.0 (convention)
            assert df.loc[df.index[0], "pct_chg"] == 0.0

    @pytest.mark.network
    def test_fetch_tsla_and_unknown_ticker(self, fetcher):
        """Fetch TSLA (should work) + unknown ticker (should not crash).

        Combined into one test to reduce API calls within rate limit.
        """
        # Wait for rate limit cooldown from previous test
        time.sleep(2)

        # Test 1: Fetch TSLA recent 5 days
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=5)
        raw = fetcher._fetch_raw_data("TSLA", start.isoformat(), end.isoformat())
        df = fetcher._normalize_data(raw, "TSLA")

        expected_cols = {"date", "open", "high", "low", "close", "volume", "amount", "pct_chg"}
        assert set(df.columns) == expected_cols
        if not df.empty:
            assert (df["close"] > 0).all()

        # Test 2: Unknown ticker — should not crash (may return empty, 403, or rate limit)
        time.sleep(1)
        from data_provider.base import DataFetchError

        try:
            raw2 = fetcher._fetch_raw_data("ZZZZUNKNOWN", "2026-05-01", "2026-05-25")
            df2 = fetcher._normalize_data(raw2, "ZZZZUNKNOWN")
            assert all(col in df2.columns for col in expected_cols)
        except DataFetchError as exc:
            err_msg = str(exc)
            # Acceptable: 403, NOT_AUTHORIZED, rate limit, or generic error
            assert any(
                keyword in err_msg.lower() or keyword in err_msg
                for keyword in ["403", "not_authorized", "rate limit", "not found"]
            ), f"Unexpected error: {err_msg}"

    @pytest.mark.network
    def test_fetcher_availability(self, fetcher):
        """Fetcher should report available when key is present."""
        assert fetcher.is_available() is True
        assert fetcher.is_available_for_request("daily_data") is True
        assert fetcher.is_available_for_request("realtime") is False
