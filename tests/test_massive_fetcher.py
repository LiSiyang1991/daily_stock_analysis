# -*- coding: utf-8 -*-
import json
from datetime import datetime, timezone
import types

import pandas as pd
import pytest

from data_provider.massive_fetcher import MassiveFetcher
from src.config import get_config, Config


class DummyResp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def ensure_config_env(monkeypatch):
    # Ensure MASSIVE_API_KEY is considered configured for availability probes
    monkeypatch.setenv("MASSIVE_API_KEY", "test_key")
    # Reload config singleton
    Config._instance = None
    yield
    Config._instance = None


def test_is_available_when_key_present(monkeypatch):
    f = MassiveFetcher()
    assert f.is_available() is True
    assert f.is_available_for_request("daily_data") is True


def test_normalize_polygon_style_payload(monkeypatch):
    # Build a fake Polygon/Massive results payload with ms timestamps
    ts = int(datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    payload = {
        "results": [
            {"t": ts, "o": 100.0, "h": 110.0, "l": 90.0, "c": 105.0, "v": 100000},
            {"t": ts + 24*3600*1000, "o": 105.0, "h": 115.0, "l": 95.0, "c": 110.0, "v": 120000},
        ]
    }

    # Monkeypatch HTTP layer to return our payload
    def fake_get(url, params, timeout):
        return DummyResp(200, payload)

    import requests
    monkeypatch.setattr(requests, "get", fake_get)

    f = MassiveFetcher()
    raw = f._fetch_raw_data("AAPL", start_date="2024-01-01", end_date="2024-01-31")
    assert not raw.empty
    df = f._normalize_data(raw, "AAPL")
    assert list(df.columns) == [
        "date", "open", "high", "low", "close", "volume", "amount", "pct_chg"
    ]
    assert len(df) == 2
    assert isinstance(df.loc[0, "date"], pd.Timestamp)
    # pct_chg of first row is 0.0 by convention
    assert df.loc[0, "pct_chg"] == 0.0
    # second row close=110 vs prev close=105 => ~4.7619%
    assert abs(df.loc[1, "pct_chg"] - ((110.0-105.0)/105.0*100.0)) < 1e-4


def test_empty_results(monkeypatch):
    def fake_get(url, params, timeout):
        return DummyResp(200, {"results": []})

    import requests
    monkeypatch.setattr(requests, "get", fake_get)

    f = MassiveFetcher()
    raw = f._fetch_raw_data("AAPL", start_date="2024-01-01", end_date="2024-01-31")
    assert raw.empty
    df = f._normalize_data(raw, "AAPL")
    assert df.empty or set(df.columns) == {"date","open","high","low","close","volume","amount","pct_chg"}
