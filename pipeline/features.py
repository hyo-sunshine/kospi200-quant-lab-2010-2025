# -*- coding: utf-8 -*-
"""파생 피처 재계산 — 원본 패널 수식을 역검증(2026-07-16)해 확정한 정의만 사용.

검증 결과 (기존 패널 저장값과 100% 일치 확인):
  log_return  = ln(close / prev_close)
  excess_return = log_return - kospi_return
  rsi_14      = Wilder RSI (ewm alpha=1/14)
  ema_12/26   = ewm(span), macd = ema12-ema26, signal = ewm9(macd), hist = macd-signal
  bb_middle   = sma20, upper/lower = ±2×std20, bb_pct = (c-lo)/(up-lo)
  atr_14      = TR의 ewm(alpha=1/14), atr_pct = atr/close
  volatility_20 = log_return 20일 std
  volume_norm_30 = volume / 30일 평균
  *_z63/z252  = 해당 원값의 rolling 63/252일 z-score
  *_net_pct   = 순매수(원) / market_cap
  kospi_return/vola20 = 지수에 동일 수식

근사 (원식 미복원 — 오차 중앙값 ~3e-3, 문서화):
  macro_usdkrw_chg_5d/20d = 거래일 기준 pct_change  ← 원본과 정확히 일치하지 않음
  is_market_stress = (vola20 > 0.015 & 20일수익 < -5%) 또는 20일수익 < -10%
"""
import numpy as np
import pandas as pd

MARKET_STRESS_VOLA = 0.015
MARKET_STRESS_RET20 = -0.05
MARKET_STRESS_CRASH20 = -0.10


def _by_ticker(df):
    return df.groupby("ticker", sort=False)


def recompute_price_features(df: pd.DataFrame) -> pd.DataFrame:
    """가격/거래량 기반 파생 컬럼을 전 구간 재계산해 채운다.

    df: ticker·date 정렬된 패널 (open/high/low/close/volume 필수,
        kospi_close·net 수급·market_cap 은 있으면 사용).
    EWM 계열(RSI/EMA/ATR)은 이력 전체로 계산해야 저장값과 이어지므로
    호출부는 종목별 '전체 이력 + 신규일'을 넘겨야 한다.
    """
    df = df.sort_values(["ticker", "date"]).reset_index(drop=True)
    g = _by_ticker(df)
    close = df["close"].astype("float64")

    df["log_return"] = np.log(close / g["close"].shift(1))

    # RSI(14) — Wilder
    delta = g["close"].diff()
    up = delta.clip(lower=0)
    dn = -delta.clip(upper=0)
    df["_up"] = up
    df["_dn"] = dn
    roll_up = _by_ticker(df)["_up"].transform(
        lambda x: x.ewm(alpha=1 / 14, adjust=False).mean())
    roll_dn = _by_ticker(df)["_dn"].transform(
        lambda x: x.ewm(alpha=1 / 14, adjust=False).mean())
    df["rsi_14"] = 100 - 100 / (1 + roll_up / roll_dn)

    # EMA / MACD
    df["ema_12"] = g["close"].transform(lambda x: x.ewm(span=12, adjust=False).mean())
    df["ema_26"] = g["close"].transform(lambda x: x.ewm(span=26, adjust=False).mean())
    df["macd"] = df["ema_12"] - df["ema_26"]
    df["macd_signal"] = _by_ticker(df)["macd"].transform(
        lambda x: x.ewm(span=9, adjust=False).mean())
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    # 볼린저
    df["sma_20"] = g["close"].transform(lambda x: x.rolling(20).mean())
    df["sma_60"] = g["close"].transform(lambda x: x.rolling(60).mean())
    std20 = g["close"].transform(lambda x: x.rolling(20).std())
    df["bb_middle"] = df["sma_20"]
    df["bb_upper"] = df["sma_20"] + 2 * std20
    df["bb_lower"] = df["sma_20"] - 2 * std20
    df["bb_pct"] = (close - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"])

    # ATR
    prev_close = g["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - prev_close).abs(),
                    (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    df["_tr"] = tr
    df["atr_14"] = _by_ticker(df)["_tr"].transform(
        lambda x: x.ewm(alpha=1 / 14, adjust=False).mean())
    df["atr_pct"] = df["atr_14"] / close

    # 변동성·거래량 정규화
    df["volatility_20"] = _by_ticker(df)["log_return"].transform(
        lambda x: x.rolling(20).std())
    vol_ma30 = g["volume"].transform(lambda x: x.rolling(30).mean())
    df["volume_norm_30"] = df["volume"] / vol_ma30

    # z-score 계열
    for src, win, out in [("rsi_14", 63, "rsi_z63"), ("macd", 252, "macd_z252"),
                          ("atr_pct", 252, "atr_pct_z252")]:
        mu = _by_ticker(df)[src].transform(lambda x: x.rolling(win).mean())
        sd = _by_ticker(df)[src].transform(lambda x: x.rolling(win).std())
        df[out] = (df[src] - mu) / sd

    # 수급 pct (net 값이 있는 행만 — 없으면 NaN 유지 후 상위에서 ffill)
    if "market_cap" in df.columns:
        for actor in ["foreign", "inst", "indiv", "corp"]:
            net = f"{actor}_net"
            if net in df.columns:
                df[f"{actor}_net_pct"] = df[net] / df["market_cap"]

    # 이벤트 플래그
    df["is_volume_zero"] = (df["volume"] <= 0).astype("int64")
    prev_zero = _by_ticker(df)["is_volume_zero"].shift(1)
    df["is_halt_resume"] = ((prev_zero == 1) & (df["is_volume_zero"] == 0)).astype("int64")

    return df.drop(columns=["_up", "_dn", "_tr"])


def recompute_market_features(df: pd.DataFrame) -> pd.DataFrame:
    """지수·시장 공통 컬럼 (kospi_return / vola20 / stress / excess_return)."""
    mkt = (df[["date", "kospi_close"]].dropna().drop_duplicates("date")
           .sort_values("date").reset_index(drop=True))
    mkt["kospi_return"] = np.log(mkt["kospi_close"] / mkt["kospi_close"].shift(1))
    mkt["kospi_volatility_20"] = mkt["kospi_return"].rolling(20).std()
    ret20 = mkt["kospi_close"].pct_change(20)
    stress = (((mkt["kospi_volatility_20"] > MARKET_STRESS_VOLA)
               & (ret20 < MARKET_STRESS_RET20))
              | (ret20 < MARKET_STRESS_CRASH20))
    mkt["is_market_stress"] = stress.fillna(False).astype("float64")

    df = df.drop(columns=["kospi_return", "kospi_volatility_20", "is_market_stress"],
                 errors="ignore")
    df = df.merge(mkt[["date", "kospi_return", "kospi_volatility_20",
                       "is_market_stress"]], on="date", how="left")
    df["excess_return"] = df["log_return"] - df["kospi_return"]
    return df


def recompute_macro_changes(df: pd.DataFrame) -> pd.DataFrame:
    """환율 변화율 재계산 (거래일 기준 근사 — 신규 행에만 적용할 것)."""
    if "macro_usdkrw" not in df.columns:
        return df
    fx = (df[["date", "macro_usdkrw"]].dropna().drop_duplicates("date")
          .sort_values("date").reset_index(drop=True))
    fx["_chg5"] = fx["macro_usdkrw"].pct_change(5)
    fx["_chg20"] = fx["macro_usdkrw"].pct_change(20)
    df = df.merge(fx[["date", "_chg5", "_chg20"]], on="date", how="left")
    df["macro_usdkrw_chg_5d"] = df["macro_usdkrw_chg_5d"].fillna(df["_chg5"])
    df["macro_usdkrw_chg_20d"] = df["macro_usdkrw_chg_20d"].fillna(df["_chg20"])
    return df.drop(columns=["_chg5", "_chg20"])
