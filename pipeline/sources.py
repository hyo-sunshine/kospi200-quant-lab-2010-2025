# -*- coding: utf-8 -*-
"""데이터 수집 소스 — KRX(pykrx, 계정 필요) 와 네이버 금융(키 불필요 폴백).

KRX 정보데이터시스템은 2025-12-27부터 로그인 필수로 전환됨.
pykrx 1.2.8+ 는 환경변수 KRX_ID / KRX_PW 로 자동 로그인한다 (회원가입 무료).
계정이 없으면 네이버 폴백으로 시세(OHLCV)만 수집한다 — 이 경우
수급(외국인/기관/개인)·공매도 컬럼은 갱신되지 않는다 (마지막 값 유지).
"""
import ast
import os
import re
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
NAVER_SISE_URL = "https://api.finance.naver.com/siseJson.naver"
NAVER_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
REQUEST_DELAY_SEC = 0.25         # 서버 부하 방지 (원 수집과 동일 관례)


def _load_dotenv_into_environ():
    """프로젝트 루트 .env 의 값을 os.environ 에 주입 (이미 있으면 유지).

    pykrx 의 KRXSession 이 os.environ["KRX_ID"/"KRX_PW"] 를 읽으므로
    .env 에 적어두기만 하면 별도 export 없이 동작하게 한다.
    """
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def has_krx_credentials() -> bool:
    _load_dotenv_into_environ()
    return bool(os.environ.get("KRX_ID") and os.environ.get("KRX_PW"))


# ---------- 네이버 (폴백: OHLCV만) ----------
def _naver_sise(symbol: str, start: str, end: str) -> pd.DataFrame:
    """siseJson.naver 호출 → DataFrame(date, open, high, low, close, volume)."""
    params = {"symbol": symbol, "requestType": 1, "startTime": start,
              "endTime": end, "timeframe": "day"}
    res = requests.get(NAVER_SISE_URL, params=params,
                       headers=NAVER_HEADERS, timeout=15)
    res.raise_for_status()
    text = re.sub(r"\s+", " ", res.text).strip()
    rows = ast.literal_eval(text)
    if len(rows) < 2:
        return pd.DataFrame()
    df = pd.DataFrame(rows[1:], columns=rows[0])
    df = df.rename(columns={"날짜": "date", "시가": "open", "고가": "high",
                            "저가": "low", "종가": "close", "거래량": "volume"})
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    return df[["date", "open", "high", "low", "close", "volume"]]


def collect_naver(tickers: list[str], start: str, end: str,
                  progress=None) -> dict:
    """네이버에서 종목 OHLCV + KOSPI200 지수 수집.

    Returns: {"ohlcv": long-df(date,ticker,ohlcv), "index": df(date,kospi_close)}
    """
    frames = []
    failed = []
    for i, ticker in enumerate(tickers):
        try:
            df = _naver_sise(ticker, start, end)
            if not df.empty:
                df["ticker"] = ticker
                frames.append(df)
        except Exception as e:
            failed.append((ticker, str(e)))
        if progress and (i + 1) % 25 == 0:
            progress(f"네이버 시세 {i + 1}/{len(tickers)}")
        time.sleep(REQUEST_DELAY_SEC)

    # 패널의 kospi_close 는 KOSPI 종합지수 (컬럼 사전의 'KOSPI200 종가' 표기는 오기 —
    # 2026-07-17 실측: 패널 4129.68 == 네이버 KOSPI, KPI200(590.08) 아님)
    idx = _naver_sise("KOSPI", start, end)
    index_df = (idx.rename(columns={"close": "kospi_close"})[["date", "kospi_close"]]
                if not idx.empty else pd.DataFrame())
    if failed:
        print(f"[sources] 네이버 수집 실패 {len(failed)}종목 (예: {failed[:3]})")
    ohlcv = (pd.concat(frames, ignore_index=True)
             if frames else pd.DataFrame())
    return {"mode": "naver", "ohlcv": ohlcv, "index": index_df,
            "flows": pd.DataFrame(), "cap": pd.DataFrame(),
            "short": pd.DataFrame()}


# ---------- KRX (정식: 전체 항목) ----------
def collect_krx(tickers: list[str], start: str, end: str, progress=None) -> dict:
    """pykrx로 OHLCV·시총·투자자별 수급·공매도·지수 수집 (KRX_ID/PW 필요)."""
    if not has_krx_credentials():
        raise RuntimeError(
            "KRX_ID / KRX_PW 환경변수가 없습니다. data.krx.co.kr 회원가입(무료) 후 "
            "설정하세요. 임시로는 네이버 폴백(collect_naver)이 사용됩니다.")
    from pykrx import stock

    ohlcv_frames, cap_frames, flow_frames, short_frames = [], [], [], []
    for i, ticker in enumerate(tickers):
        o = stock.get_market_ohlcv(start, end, ticker)
        if not o.empty:
            o = o.rename(columns={"시가": "open", "고가": "high", "저가": "low",
                                  "종가": "close", "거래량": "volume"})
            o = o.reset_index().rename(columns={"날짜": "date"})
            o["ticker"] = ticker
            ohlcv_frames.append(o[["date", "ticker", "open", "high", "low",
                                   "close", "volume"]])
        time.sleep(REQUEST_DELAY_SEC)

        c = stock.get_market_cap(start, end, ticker)
        if not c.empty:
            c = c.rename(columns={"시가총액": "market_cap", "거래대금": "trading_value",
                                  "상장주식수": "listed_shares"})
            c = c.reset_index().rename(columns={"날짜": "date"})
            c["ticker"] = ticker
            cap_frames.append(c[["date", "ticker", "market_cap",
                                 "trading_value", "listed_shares"]])
        time.sleep(REQUEST_DELAY_SEC)

        # detail=False → 기관합계/기타법인/개인/외국인합계 집계 컬럼 (패널 스키마와 일치)
        f = stock.get_market_trading_value_by_date(start, end, ticker, detail=False)
        if not f.empty:
            rename = {"기관합계": "inst_net", "기타법인": "corp_net",
                      "개인": "indiv_net", "외국인합계": "foreign_net",
                      "전체": "total_net"}
            f = f.rename(columns=rename).reset_index().rename(columns={"날짜": "date"})
            keep = ["date"] + [v for v in rename.values() if v in f.columns]
            f["ticker"] = ticker
            flow_frames.append(f[keep + ["ticker"]])
        time.sleep(REQUEST_DELAY_SEC)

        try:
            s = stock.get_shorting_volume_by_date(start, end, ticker)
            if not s.empty:
                s = s.rename(columns={"공매도": "short_volume", "매수": "buy_volume",
                                      "비중": "short_ratio_pct"})
                s = s.reset_index().rename(columns={"날짜": "date"})
                s["ticker"] = ticker
                short_frames.append(
                    s[["date", "ticker", "short_volume", "short_ratio_pct"]])
        except Exception:
            pass                       # 공매도는 결측 허용 (2016 이전·금지기간 등)
        time.sleep(REQUEST_DELAY_SEC)

        if progress and (i + 1) % 20 == 0:
            progress(f"KRX 수집 {i + 1}/{len(tickers)}")

    idx = stock.get_index_ohlcv(start, end, "1028")
    index_df = (idx.rename(columns={"종가": "kospi_close"})
                .reset_index().rename(columns={"날짜": "date"})[["date", "kospi_close"]]
                if not idx.empty else pd.DataFrame())

    def cat(frames):
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    return {"mode": "krx", "ohlcv": cat(ohlcv_frames), "cap": cat(cap_frames),
            "flows": cat(flow_frames), "short": cat(short_frames),
            "index": index_df}


def collect(tickers: list[str], start: str, end: str, progress=None) -> dict:
    """KRX 계정이 있으면 KRX, 없으면 네이버 폴백."""
    if has_krx_credentials():
        try:
            return collect_krx(tickers, start, end, progress)
        except Exception as e:
            print(f"[sources] KRX 수집 실패 → 네이버 폴백: {e}")
    return collect_naver(tickers, start, end, progress)
