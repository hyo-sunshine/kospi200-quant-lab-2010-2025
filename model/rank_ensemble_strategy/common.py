# -*- coding: utf-8 -*-
"""
공용 모듈 — 경로·피처 정의·데이터 로드·백테스트 엔진
======================================================
전략: KOSPI200 압축 랭크앙상블 (상세는 README.md / report/)
이 저장소 구조를 기준으로 경로를 자동 해석하므로 수정 없이 바로 실행된다.
"""
import time
from pathlib import Path

import numpy as np
import pandas as pd

# ---------- 경로 (저장소 기준 자동) ----------
PKG = Path(__file__).resolve().parent          # model/rank_ensemble_strategy
ROOT = PKG.parents[1]                          # 저장소 루트
PANEL = ROOT / "data/processed/master_panel.parquet"
SCORES_DIR = PKG / "model_scores"
MODELS_DIR = PKG / "models"

# ---------- 전략 파라미터 (최종 확정값) ----------
SEEDS = [42, 7, 2024, 1, 2, 3, 4, 5, 6]   # 9시드 앙상블
SMOOTH = 20                                 # 스코어 평활 (거래일)
W60 = 0.3                                   # 신호 = 0.3×flow60 + 0.7×flow20
N_HOLD = 8                                  # 보유 종목 수 (동일가중)
BUF_MULT = 4                                # 버퍼: 랭크 N_HOLD×4 밖 이탈 시만 교체
H_REBAL = 10                                # 리밸런싱 주기 (거래일)
BASE_COST = 0.001                           # 편도 거래비용 10bp
EVAL_START = pd.Timestamp("2013-01-01")     # OOS 평가 시작
L, SKIP, FWD_H = 250, 20, 60                # 모멘텀 룩백 / 스킵 / 느린모델 지평

# ---------- 피처 정의 ----------
TECH_FEATS = ["log_return", "rsi_z63", "macd_z252", "foreign_net_pct",
              "volume_norm_30", "atr_pct_z252", "bb_pct", "volatility_20",
              "short_ratio_pct"]
FUND_FEATS = ["revenue_z", "operating_inc_z", "net_income_z",
              "revenue_yoy_growth_z", "operating_inc_yoy_growth_z",
              "net_income_yoy_growth_z", "opm_ttm_z", "npm_ttm_z",
              "roe_ttm_z", "roa_ttm_z", "debt_ratio_z", "earning_surprise_z"]
MKT_FEATS = ["kospi_volatility_20", "macro_usdkrw_chg_20d", "is_market_stress",
             "macro_usdkrw_chg_5d"]
MOM_FEATS = ["mom_5", "mom_20", "mom_60", "mom_12_1", "foreign_flow_20d"]
FLOW_FAST = ["foreign_flow_5d", "inst_flow_5d", "indiv_flow_5d",
             "inst_flow_20d", "indiv_flow_20d"]
FLOW_SLOW = ["foreign_flow_60d", "inst_flow_60d", "indiv_flow_60d"]

FEATURES = {
    # 빠른 모델: 20일 지평, 전체 피처
    "flow20": TECH_FEATS + FUND_FEATS + MKT_FEATS + MOM_FEATS + FLOW_FAST + FLOW_SLOW,
    # 느린 모델: 60일 지평, 천천히 변하는 피처만 (회전율 통제)
    "flow60": (FUND_FEATS + MKT_FEATS
               + ["mom_60", "mom_12_1", "foreign_flow_20d", "volatility_20",
                  "atr_pct_z252"] + FLOW_SLOW + ["inst_flow_20d", "indiv_flow_20d"]),
}
TARGET_COL = {"flow20": "label_fwd_20d", "flow60": "fwd60"}
PURGE_DAYS = {"flow20": 45, "flow60": 100}

LGB_PARAMS = {"objective": "regression", "num_leaves": 63, "learning_rate": 0.05,
              "n_estimators": 300, "min_child_samples": 500, "subsample": 0.8,
              "subsample_freq": 1, "colsample_bytree": 0.8, "reg_lambda": 1.0,
              "n_jobs": -1, "verbosity": -1}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------- 데이터 로드 ----------
def load_feature_panel(with_labels=True):
    """패널 로드 + 파생 피처(모멘텀·수급 누적) 생성. with_labels=True면 fwd60 라벨 추가."""
    cols = (["date", "ticker", "close", "label_fwd_20d", "is_pit_universe_kospi200",
             "is_volume_zero", "kospi_return", "inst_net_pct", "indiv_net_pct"]
            + TECH_FEATS + FUND_FEATS + MKT_FEATS)
    df = pd.read_parquet(PANEL, columns=list(dict.fromkeys(cols)))
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["ticker", "date"]).reset_index(drop=True)
    g = df.groupby("ticker", sort=False)
    close = df["close"].astype("float64")
    df["mom_5"] = close / g["close"].shift(5) - 1.0
    df["mom_20"] = close / g["close"].shift(20) - 1.0
    df["mom_60"] = close / g["close"].shift(60) - 1.0
    df["mom_12_1"] = g["close"].shift(SKIP) / g["close"].shift(L) - 1.0

    def roll(col, win, minp):
        return (df.groupby("ticker", sort=False)[col]
                .rolling(win, min_periods=minp).sum().reset_index(level=0, drop=True))

    for actor, col in [("foreign", "foreign_net_pct"), ("inst", "inst_net_pct"),
                       ("indiv", "indiv_net_pct")]:
        df[f"{actor}_flow_5d"] = roll(col, 5, 3)
        df[f"{actor}_flow_20d"] = roll(col, 20, 10)
        df[f"{actor}_flow_60d"] = roll(col, 60, 30)

    if with_labels:
        lr = df.pivot_table(index="date", columns="ticker",
                            values="log_return").sort_index()
        fwd = lr.fillna(0.0).rolling(FWD_H).sum().shift(-FWD_H)
        fwd_long = fwd.stack().rename("fwd60").reset_index()
        fwd_long.columns = ["date", "ticker", "fwd60"]
        df = df.merge(fwd_long, on=["date", "ticker"], how="left")
    return df


def load_market_matrices():
    """백테스트용 행렬 (일별 수익률·유니버스·거래량·시총·지수)."""
    cols = ["date", "ticker", "log_return", "is_pit_universe_kospi200",
            "volume", "market_cap", "kospi_return"]
    df = pd.read_parquet(PANEL, columns=cols)
    df["date"] = pd.to_datetime(df["date"])
    lr = df.pivot_table(index="date", columns="ticker", values="log_return").sort_index()
    dates = lr.index
    lr_f = lr.fillna(0.0)
    inuni = (df.pivot_table(index="date", columns="ticker",
                            values="is_pit_universe_kospi200")
             .reindex(index=dates, columns=lr.columns).fillna(0).astype(bool))
    vol = (df.pivot_table(index="date", columns="ticker", values="volume")
           .reindex(index=dates, columns=lr.columns).fillna(0))
    mcap = (df.pivot_table(index="date", columns="ticker", values="market_cap")
            .reindex(index=dates, columns=lr.columns).ffill().fillna(0.0))
    kospi = df.groupby("date")["kospi_return"].first().reindex(dates).fillna(0.0)
    return lr_f, inuni, vol, mcap, kospi


# ---------- 신호 ----------
def seed_rank(tag, seed, dates, columns):
    """저장된 OOS 스코어 → 20일 평활 → 날짜별 rank pct."""
    sc = pd.read_parquet(SCORES_DIR / f"model_scores_{tag}_seed{seed}.parquet")
    m = (sc.pivot_table(index="date", columns="ticker", values="score")
         .reindex(index=dates, columns=columns).ffill(limit=5)
         .rolling(SMOOTH, min_periods=5).mean())
    return m.rank(axis=1, pct=True)


def build_signal(dates, columns, seeds=SEEDS, w60=W60):
    """최종 신호 = w60 × rank(flow60) + (1-w60) × rank(flow20), 시드 랭크 평균."""
    r60 = sum(seed_rank("flow60", s, dates, columns) for s in seeds) / len(seeds)
    r20 = sum(seed_rank("flow20", s, dates, columns) for s in seeds) / len(seeds)
    return w60 * r60 + (1 - w60) * r20


# ---------- 백테스트 엔진 ----------
def backtest(mats, signal, n_hold=N_HOLD, buf_mult=BUF_MULT, cost=BASE_COST,
             h=H_REBAL, start=EVAL_START, end=None):
    """상위 N 동일가중 + 버퍼 교체 + 정확 복리. 일별 NAV 시리즈 반환."""
    lr_f, inuni, vol, mcap, kospi = mats
    dates = lr_f.index
    n = len(dates)
    end = end or dates.max()
    span = (dates >= start) & (dates <= end)
    nav, prev_w, held = 1.0, {}, []
    daily_nav, daily_dates = [], []
    i = int(np.where(span)[0][0])
    last = int(np.where(span)[0][-1])
    cols = lr_f.columns
    while i < min(last, n - 1):
        elig = inuni.iloc[i].values & (vol.iloc[i].values > 0) & (mcap.iloc[i].values > 0)
        sc = signal.iloc[i].values.copy()
        sc[~elig] = -np.inf
        valid = [k for k in np.argsort(-sc) if np.isfinite(sc[k])]
        if len(valid) >= n_hold:
            rank_of = {k: r for r, k in enumerate(valid)}
            keep = [k for k in held if rank_of.get(k, 10 ** 9) < buf_mult * n_hold]
            held = keep + [k for k in valid if k not in keep][:n_hold - len(keep)]
            w = {cols[k]: 1.0 / n_hold for k in held}
        else:
            w = prev_w
        turn = sum(abs(w.get(t, 0) - prev_w.get(t, 0)) for t in set(prev_w) | set(w))
        nav *= (1.0 - cost * turn)
        seg0, end_i = nav, min(i + 1 + h, last + 1, n)
        if end_i > i + 1 and w:
            ci = [cols.get_loc(c) for c in w]
            wv = np.array(list(w.values()))
            cash = 1.0 - float(wv.sum())
            seg = lr_f.iloc[i + 1:end_i].values[:, ci]
            path = cash + np.exp(np.cumsum(seg, axis=0)) @ wv
            daily_nav.extend((seg0 * path).tolist())
            daily_dates.extend(dates[i + 1:end_i].tolist())
            nav = seg0 * float(path[-1])
        prev_w = w
        i += h
    return pd.Series(daily_nav, index=pd.DatetimeIndex(daily_dates))


def metrics(nav_s, label=""):
    """CAGR·MDD·롤링5년 요약."""
    r5 = (nav_s / nav_s.shift(1260) - 1.0).dropna()
    yrs = (nav_s.index[-1] - nav_s.index[0]).days / 365.25
    cagr = float((nav_s.iloc[-1] / nav_s.iloc[0]) ** (1 / yrs) - 1)
    dd = float((nav_s / nav_s.cummax() - 1).min())
    m = {"label": label, "cagr_pct": round(cagr * 100, 2),
         "cum_pct": round(float(nav_s.iloc[-1] / nav_s.iloc[0] - 1) * 100, 1),
         "mdd_pct": round(dd * 100, 1)}
    if len(r5):
        m["median_5y_pct"] = round(float(r5.median()) * 100, 1)
        m["min_5y_pct"] = round(float(r5.min()) * 100, 1)
    return m
