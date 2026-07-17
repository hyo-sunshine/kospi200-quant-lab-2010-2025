# -*- coding: utf-8 -*-
"""
공용 모듈 — 설정·데이터 로드·피처/타깃 생성
=============================================
전략: KOSPI200 LSTM 시퀀스 (최근 20거래일 피처 배열 → 1/7/30일 후 초과수익률)
노트북 kospi200_lstm_sequence_colab_v3_2_memory_safe.ipynb 를 프로덕션 코드로 옮긴 것.
저장소 구조 기준으로 경로를 자동 해석하므로 수정 없이 바로 실행된다.
"""
import time
from pathlib import Path

import numpy as np
import pandas as pd

# ---------- 경로 (저장소 기준 자동) ----------
PKG = Path(__file__).resolve().parent          # model/lstm_sequence_strategy
ROOT = PKG.parents[1]                          # 저장소 루트
PANEL = ROOT / "data/processed/master_panel.parquet"
MODELS_DIR = PKG / "models"
CHECKPOINT_PATH = MODELS_DIR / "final_lstm_model.pt"

# ---------- 시퀀스/타깃 설정 (노트북 CFG와 동일) ----------
LOOKBACK = 20                    # 입력 시퀀스 길이 (거래일)
HORIZONS = (1, 7, 30)            # 예측 지평 (거래일)
TARGET_MODE = "market_excess"    # raw / market_excess / sector_excess
TARGET_CLIP = (-0.50, 0.50)      # 타깃 이상치 클리핑
MAX_FEATURES = 60                # 사용 피처 수 상한
MISSING_THRESHOLD = 0.40         # 결측률이 이보다 높은 컬럼 제외

# ---------- 학습 설정 ----------
HIDDEN_SIZE = 32
NUM_LAYERS = 1
DROPOUT = 0.20
BATCH_SIZE = 128
EPOCHS = 20
LEARNING_RATE = 1e-3
WEIGHT_DECAY = 1e-5
PATIENCE = 5
GRAD_CLIP_NORM = 1.0
RANDOM_STATE = 42
FINAL_TRAIN_SPLIT = 0.90         # 최종 모델: 마지막 10% 날짜를 내부 valid로 사용

# ---------- 평가 설정 ----------
TOP_K = 5

TARGET_COLS = [f"target_{h}d_clipped" for h in HORIZONS]


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def normalize_ticker(x):
    """종목코드를 항상 6자리 문자열로 맞춘다. 예: 5930 -> '005930'"""
    return str(x).replace(".0", "").zfill(6)


# ---------- 데이터 로드 ----------
def load_panel_data(path: Path = PANEL) -> pd.DataFrame:
    """패널 parquet을 읽고 기본 형식 정리 + KOSPI200 유니버스 필터링."""
    if not path.exists():
        raise FileNotFoundError(f"입력 parquet 파일이 없습니다: {path}")

    df = pd.read_parquet(path)
    df.columns = [str(c).strip() for c in df.columns]

    required = ["date", "ticker", "open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"필수 컬럼이 없습니다: {missing}")

    df["date"] = pd.to_datetime(df["date"])
    df["ticker"] = df["ticker"].map(normalize_ticker)
    for col in ["open", "high", "low", "close", "volume", "market_cap", "trading_value"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.drop_duplicates(["date", "ticker"], keep="last")
    df = df.sort_values(["ticker", "date"]).reset_index(drop=True)

    if "is_pit_universe_kospi200" in df.columns:
        before = len(df)
        df = df[df["is_pit_universe_kospi200"] == 1].copy().reset_index(drop=True)
        log(f"KOSPI200 유니버스 필터링: {before:,}행 -> {len(df):,}행")

    log(f"패널 로드 완료 shape={df.shape} 기간={df['date'].min().date()}~{df['date'].max().date()}")
    return df


def _find_sector_col(df: pd.DataFrame):
    for c in ["sector", "industry"]:
        if c in df.columns and df[c].notna().sum() > 0:
            return c
    return None


# ---------- 피처 생성 ----------
def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """LSTM 입력용 시계열 피처 생성 (수익률·이동평균 괴리·변동성·거래량 등)."""
    df = df.copy().sort_values(["ticker", "date"]).reset_index(drop=True)
    g = df.groupby("ticker", sort=False)

    # 1) 전일 종가 대비 OHLC 위치 — 절대가격 대신 비율
    prev_close = g["close"].shift(1)
    for col in ["open", "high", "low", "close"]:
        df[f"{col}_to_prev_close"] = df[col] / prev_close - 1

    # 2) 과거 수익률
    for w in [1, 2, 5, 10, 20, 60]:
        df[f"ret_{w}d"] = g["close"].pct_change(w, fill_method=None)

    # 3) 이동평균 괴리율
    for w in [5, 10, 20, 60]:
        ma = g["close"].transform(lambda x: x.rolling(w, min_periods=w).mean())
        df[f"ma_gap_{w}d"] = df["close"] / ma - 1

    # 4) 변동성
    for w in [5, 20, 60]:
        df[f"volatility_{w}d"] = g["ret_1d"].transform(
            lambda x: x.rolling(w, min_periods=w).std())

    # 5) 거래량/거래대금
    df["log_volume"] = np.log1p(df["volume"].clip(lower=0))
    volume_ma20 = g["volume"].transform(lambda x: x.rolling(20, min_periods=20).mean())
    df["volume_ratio_20d"] = df["volume"] / volume_ma20 - 1
    if "trading_value" in df.columns:
        df["log_trading_value"] = np.log1p(df["trading_value"].clip(lower=0))
    else:
        df["log_trading_value"] = np.log1p((df["close"] * df["volume"]).clip(lower=0))
    if "market_cap" in df.columns:
        df["log_market_cap"] = np.log1p(df["market_cap"].clip(lower=0))

    # 6) 시장 공통 피처 (당일 장 종료 후 알 수 있는 값)
    market_daily = (df.groupby("date", as_index=False)
                    .agg(market_ret_1d=("ret_1d", "mean"),
                         market_ret_5d=("ret_5d", "mean"),
                         market_volatility_20d=("volatility_20d", "mean")))
    df = df.merge(market_daily, on="date", how="left")

    # 7) 업종 피처 (sector/industry 컬럼이 있을 때만)
    sector_col = _find_sector_col(df)
    if sector_col is not None:
        sector_daily = (df.groupby(["date", sector_col], dropna=False, as_index=False)
                        .agg(sector_ret_1d=("ret_1d", "mean"),
                             sector_ret_5d=("ret_5d", "mean"),
                             sector_volatility_20d=("volatility_20d", "mean")))
        df = df.merge(sector_daily, on=["date", sector_col], how="left")

    # 8) 날짜 피처
    df["month"] = df["date"].dt.month
    df["dayofweek"] = df["date"].dt.dayofweek

    return df.sort_values(["ticker", "date"]).reset_index(drop=True)


# ---------- 타깃 생성 ----------
def add_targets(df: pd.DataFrame) -> pd.DataFrame:
    """미래 수익률 타깃(raw/초과수익률) 생성. 학습 시에만 필요."""
    df = df.copy().sort_values(["ticker", "date"]).reset_index(drop=True)
    g = df.groupby("ticker", sort=False)
    sector_col = _find_sector_col(df)

    for h in HORIZONS:
        raw_col = f"raw_return_{h}d"
        future_close = g["close"].shift(-h)
        df[raw_col] = future_close / df["close"] - 1

        market_avg = df.groupby("date")[raw_col].transform("mean")
        df[f"market_excess_return_{h}d"] = df[raw_col] - market_avg

        if sector_col is not None:
            sector_avg = df.groupby(["date", sector_col], dropna=False)[raw_col].transform("mean")
            df[f"sector_excess_return_{h}d"] = df[raw_col] - sector_avg
        else:
            df[f"sector_excess_return_{h}d"] = np.nan

        if TARGET_MODE == "raw":
            df[f"target_{h}d"] = df[raw_col]
        elif TARGET_MODE == "market_excess":
            df[f"target_{h}d"] = df[f"market_excess_return_{h}d"]
        elif TARGET_MODE == "sector_excess":
            if sector_col is None:
                raise ValueError("sector_excess 모드는 sector/industry 컬럼이 필요합니다.")
            df[f"target_{h}d"] = df[f"sector_excess_return_{h}d"]
        else:
            raise ValueError("TARGET_MODE는 raw/market_excess/sector_excess 중 하나여야 합니다.")

        lo, hi = TARGET_CLIP
        df[f"target_{h}d_clipped"] = df[f"target_{h}d"].clip(lo, hi)

    return df


# ---------- 피처 선택 ----------
def select_feature_columns(df: pd.DataFrame,
                           missing_threshold: float = MISSING_THRESHOLD) -> list:
    """모델 입력 숫자 피처 선택 — 타깃류·식별자·고결측 컬럼 제외, 상한 MAX_FEATURES."""
    exclude_exact = {"date", "ticker", "name", "sector", "industry",
                     "is_pit_universe_kospi200"}
    feature_cols = []
    for col in df.columns:
        is_target_like = (col.startswith("raw_return_")
                          or col.startswith("market_excess_return_")
                          or col.startswith("sector_excess_return_")
                          or col.startswith("target_"))
        if col in exclude_exact or is_target_like:
            continue
        if not pd.api.types.is_numeric_dtype(df[col]):
            continue
        if df[col].isna().mean() > missing_threshold:
            continue
        feature_cols.append(col)

    if MAX_FEATURES is not None and len(feature_cols) > MAX_FEATURES:
        feature_cols = feature_cols[:MAX_FEATURES]

    if len(feature_cols) < 5:
        raise ValueError("사용 가능한 피처가 너무 적습니다. 데이터/피처 생성 과정을 확인하세요.")
    log(f"사용 피처 수: {len(feature_cols)}")
    return feature_cols


# ---------- 시퀀스 가능 여부 ----------
def mark_sequence_availability(df: pd.DataFrame) -> pd.DataFrame:
    """각 행이 LOOKBACK 길이의 과거 시퀀스를 만들 수 있는지 표시."""
    df = df.copy().sort_values(["ticker", "date"]).reset_index(drop=True)
    df["row_in_ticker"] = df.groupby("ticker").cumcount()
    df["has_full_lookback"] = df["row_in_ticker"] >= (LOOKBACK - 1)
    return df


def prepare_panel(with_targets: bool) -> pd.DataFrame:
    """패널 로드 → 피처 (→ 타깃) → 시퀀스 가능 여부까지 한 번에 수행."""
    df = load_panel_data()
    log("피처 생성")
    df = add_features(df)
    if with_targets:
        log("타깃 생성")
        df = add_targets(df)
    return mark_sequence_availability(df)
