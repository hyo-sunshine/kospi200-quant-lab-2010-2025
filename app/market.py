# -*- coding: utf-8 -*-
"""시세 데이터 서비스 — UI 차트/종목 목록용 패널 캐시.

master_panel.parquet에서 OHLCV만 읽어 프로세스 메모리에 1회 캐시한다.
(패널은 일 단위 데이터라 서버 구동 중 갱신이 필요하면 /api/reload 사용)
"""
import csv
import threading

import pandas as pd

from config import MAX_CHART_DAYS, PANEL_PATH, ROOT, TICKER_MAP_PATH

UNIVERSE_NAME_FILES = [
    ROOT / "data/raw/meta/kospi200_universe_by_year_2010_2015.csv",
    ROOT / "data/raw/meta/kospi200_universe_by_year.csv",
    ROOT / "data/raw/meta/kospi200_universe_2016_2025.csv",   # 최신 파일이 마지막 (이름 우선)
]

_lock = threading.Lock()
_ohlcv: pd.DataFrame | None = None
_names: dict[str, dict] | None = None

OHLCV_COLS = ["date", "ticker", "open", "high", "low", "close", "volume",
              "is_pit_universe_kospi200"]


def get_name_lookup() -> dict[str, str]:
    """ticker → 종목명."""
    return {t: info["name"] for t, info in _load_ticker_map().items()}


def _load_ticker_map() -> dict[str, dict]:
    global _names
    with _lock:
        if _names is None:
            names: dict[str, dict] = {}
            # 1) 유니버스 파일에서 종목명 수집 (뒤 파일이 최신이라 이름을 덮어씀)
            for path in UNIVERSE_NAME_FILES:
                if not path.exists():
                    continue
                with open(path, encoding="utf-8-sig") as f:
                    for row in csv.DictReader(f):
                        ticker = str(row.get("ticker", "")).zfill(6)
                        name = (row.get("name") or "").strip()
                        if ticker and name:
                            names[ticker] = {"name": name,
                                             "sector": names.get(ticker, {}).get("sector", "")}
            # 2) 섹터 맵으로 섹터 정보(및 없는 이름) 보강
            if TICKER_MAP_PATH.exists():
                with open(TICKER_MAP_PATH, encoding="utf-8-sig") as f:
                    for row in csv.DictReader(f):
                        ticker = str(row["ticker"]).zfill(6)
                        entry = names.get(ticker, {"name": row.get("name", ticker)})
                        entry["sector"] = row.get("sector", "")
                        names[ticker] = entry
            _names = names
        return _names


def _load_ohlcv() -> pd.DataFrame:
    global _ohlcv
    with _lock:
        if _ohlcv is None:
            if not PANEL_PATH.exists():
                raise FileNotFoundError(f"패널 파일이 없습니다: {PANEL_PATH}")
            df = pd.read_parquet(PANEL_PATH, columns=OHLCV_COLS)
            df["date"] = pd.to_datetime(df["date"])
            df["ticker"] = df["ticker"].astype(str).str.zfill(6)
            _ohlcv = df.sort_values(["ticker", "date"]).reset_index(drop=True)
        return _ohlcv


def reload_cache():
    global _ohlcv, _names
    with _lock:
        _ohlcv, _names = None, None


def latest_base_date() -> str:
    df = _load_ohlcv()
    return str(df["date"].max().date())


def list_stocks(query: str = "", limit: int = 250) -> list[dict]:
    """최신일 기준 유니버스 종목 목록 (이름·종가·등락률)."""
    df = _load_ohlcv()
    names = _load_ticker_map()

    last_two = sorted(df["date"].unique())[-2:]
    recent = df[df["date"].isin(last_two)]
    latest = recent[recent["date"] == last_two[-1]]
    latest = latest[latest["is_pit_universe_kospi200"] == 1]
    prev_close = (recent[recent["date"] == last_two[0]]
                  .set_index("ticker")["close"].to_dict())

    stocks = []
    for _, row in latest.iterrows():
        ticker = row["ticker"]
        info = names.get(ticker, {})
        name = info.get("name", ticker)
        if query and query not in name and query not in ticker:
            continue
        prev = prev_close.get(ticker)
        change_pct = ((row["close"] / prev - 1) * 100) if prev else 0.0
        stocks.append({
            "ticker": ticker,
            "name": name,
            "sector": info.get("sector", ""),
            "close": float(row["close"]),
            "change_pct": round(float(change_pct), 2),
        })
    stocks.sort(key=lambda s: s["name"])
    return stocks[:limit]


def get_prices(ticker: str, days: int = 90) -> dict:
    """종목 일봉 시계열 (차트용). days는 MAX_CHART_DAYS로 제한."""
    df = _load_ohlcv()
    ticker = str(ticker).zfill(6)
    days = max(10, min(int(days), MAX_CHART_DAYS))

    sub = df[df["ticker"] == ticker].tail(days)
    if sub.empty:
        raise KeyError(f"종목 데이터가 없습니다: {ticker}")

    info = _load_ticker_map().get(ticker, {})
    return {
        "ticker": ticker,
        "name": info.get("name", ticker),
        "sector": info.get("sector", ""),
        "candles": [
            {"date": str(r["date"].date()), "open": float(r["open"]),
             "high": float(r["high"]), "low": float(r["low"]),
             "close": float(r["close"]), "volume": float(r["volume"])}
            for _, r in sub.iterrows()
        ],
    }
