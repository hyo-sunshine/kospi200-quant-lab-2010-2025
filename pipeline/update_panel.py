# -*- coding: utf-8 -*-
"""패널 증분 갱신 — 마지막 거래일 이후 데이터를 수집해 master_panel에 append.

    python -m pipeline.update_panel            # 오늘까지 갱신
    python -m pipeline.update_panel --end 20260716

동작:
  1. 패널 마지막 날짜 이후 ~ end 까지 유니버스 종목 시세 수집 (KRX 또는 네이버)
  2. 검증된 수식으로 파생 피처 재계산 (EWM 계열은 종목별 전체 이력 사용)
  3. 재계산 불가 컬럼(DART 재무 z·매크로 원값·공매도 등)은 종목별 마지막 값 유지(ffill)
  4. 라벨(label_*)은 NaN (미래 정보 — 예측에는 불필요)
  5. 원자적 저장 (임시 파일 → 교체), 기존 이력 행은 절대 수정하지 않음

주의: 유니버스는 패널 마지막 날의 KOSPI200 구성으로 고정된다.
      신규 편입 종목 반영은 KRX 계정 확보 후 구성종목 API로 확장할 것.
"""
import argparse
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline import ecos, features, sources

ROOT = Path(__file__).resolve().parents[1]
PANEL_PATH = ROOT / "data/processed/master_panel.parquet"

# 신규 행에서 종목별 ffill 로 유지하는 컬럼 (분기/저빈도 or 수집원 없음)
FFILL_PREFIXES = ("macro_", "revenue", "operating_inc", "net_income", "total_",
                  "opm_", "npm_", "roe_", "roa_", "debt_ratio", "earning_surprise",
                  "short_")
FFILL_EXACT = {"listed_shares", "is_pit_universe_kospi200", "is_short_ban",
               "days_since_listing", "days_to_delisting", "is_pre_delisting_30d",
               "split_ratio_today", "days_since_split", "is_post_split_30d",
               "price_limit_pct", "sector"}
LABEL_COLS = ("label_fwd_5d", "label_fwd_20d", "label_tb_20d")


def _load_panel() -> pd.DataFrame:
    df = pd.read_parquet(PANEL_PATH)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values(["ticker", "date"]).reset_index(drop=True)


def _build_new_rows(panel: pd.DataFrame, raw: dict) -> pd.DataFrame:
    """수집 원시 데이터 → 패널 스키마의 신규 행 생성."""
    ohlcv = raw["ohlcv"].copy()
    last_date = panel["date"].max()
    ohlcv = ohlcv[ohlcv["date"] > last_date]
    if ohlcv.empty:
        return pd.DataFrame()

    # 시총/수급/공매도 병합 (KRX 모드에서만 존재)
    for key in ["cap", "flows", "short"]:
        extra = raw.get(key)
        if extra is not None and not extra.empty:
            extra = extra[extra["date"] > last_date]
            ohlcv = ohlcv.merge(extra, on=["date", "ticker"], how="left")

    # 지수 병합
    idx = raw.get("index")
    if idx is not None and not idx.empty:
        ohlcv = ohlcv.merge(idx[idx["date"] > last_date], on="date", how="left")

    # 이력(피처 계산용 베이스 컬럼) + 신규 행 결합
    base_cols = ["date", "ticker", "open", "high", "low", "close", "volume",
                 "market_cap", "listed_shares", "trading_value", "kospi_close",
                 "foreign_net", "inst_net", "indiv_net", "corp_net", "total_net"]
    hist = panel[[c for c in base_cols if c in panel.columns]].copy()
    new = ohlcv.reindex(columns=hist.columns.union(ohlcv.columns, sort=False))
    combo = pd.concat([hist, new], ignore_index=True)
    combo = combo.sort_values(["ticker", "date"]).reset_index(drop=True)
    g = combo.groupby("ticker", sort=False)

    # 시총 폴백: listed_shares ffill × close (네이버 모드)
    combo["listed_shares"] = g["listed_shares"].ffill()
    combo["market_cap"] = combo["market_cap"].fillna(
        combo["close"] * combo["listed_shares"])
    # 거래대금 폴백: close × volume (근사)
    combo["trading_value"] = combo["trading_value"].fillna(
        combo["close"] * combo["volume"])
    # 지수 폴백: 마지막 값 유지
    combo["kospi_close"] = combo.sort_values("date")["kospi_close"].ffill()

    # 파생 재계산 (전체 이력 기준 → EWM 연속성 보장)
    combo = features.recompute_price_features(combo)
    combo = features.recompute_market_features(combo)

    new_rows = combo[combo["date"] > last_date].copy()

    # 패널 전체 스키마로 확장
    for col in panel.columns:
        if col not in new_rows.columns:
            new_rows[col] = np.nan
    new_rows = new_rows[panel.columns]

    # 라벨은 NaN 유지
    for col in LABEL_COLS:
        if col in new_rows.columns:
            new_rows[col] = np.nan

    # ffill 대상: 종목별 마지막 관측값 이어붙이기
    last_by_ticker = panel.groupby("ticker").tail(1).set_index("ticker")
    ffill_cols = [c for c in panel.columns
                  if c.startswith(FFILL_PREFIXES) or c in FFILL_EXACT]
    for col in ffill_cols:
        if col in last_by_ticker.columns:
            fill = new_rows["ticker"].map(last_by_ticker[col])
            new_rows[col] = new_rows[col].fillna(fill)

    # ECOS 일 주기 매크로 (환율·기준금리·국고3년) — ffill 값을 실제값으로 덮어씀
    start = (last_date + pd.Timedelta(days=1)).strftime("%Y%m%d")
    end = new_rows["date"].max().strftime("%Y%m%d")
    macro = ecos.collect_macro(start, end)
    if not macro.empty:
        macro = (macro.set_index("date")
                 .reindex(pd.date_range(macro["date"].min(), new_rows["date"].max()))
                 .ffill().rename_axis("date").reset_index())
        for col in [c for c in macro.columns if c != "date"]:
            mapping = macro.set_index("date")[col]
            fetched = new_rows["date"].map(mapping)
            new_rows[col] = fetched.fillna(new_rows[col])

    # 매크로 변화율 재계산 — 신규 행의 ffill 잔재를 지우고 실제 시계열로 다시 계산
    new_rows["macro_usdkrw_chg_5d"] = np.nan
    new_rows["macro_usdkrw_chg_20d"] = np.nan
    full = pd.concat([panel, new_rows], ignore_index=True)
    full = features.recompute_macro_changes(full)
    new_rows = full[full["date"] > last_date].copy()

    # 잔여 기본값
    for col in ["is_lower_limit_hit", "is_upper_limit_hit",
                "is_boundary_2014_residual", "is_lower_limit_loose",
                "is_high_corrected", "is_listed_shares_event",
                "is_macro_release_day_monthly"]:
        if col in new_rows.columns:
            new_rows[col] = new_rows[col].fillna(0)
    if "high_adj" in new_rows.columns:
        new_rows["high_adj"] = new_rows["high_adj"].fillna(new_rows["high"])
    return new_rows


def run_update(end: str | None = None, progress=print) -> dict:
    """패널 증분 갱신 실행. Returns 요약 dict."""
    panel = _load_panel()
    last_date = panel["date"].max()
    end = end or date.today().strftime("%Y%m%d")
    start = (last_date + pd.Timedelta(days=1)).strftime("%Y%m%d")
    if start > end:
        return {"status": "up_to_date", "base_date": str(last_date.date()),
                "new_dates": 0, "new_rows": 0, "mode": None}

    universe = sorted(panel.loc[
        (panel["date"] == last_date)
        & (panel["is_pit_universe_kospi200"] == 1), "ticker"].unique())
    progress(f"[pipeline] {start}~{end} 수집 시작 — 유니버스 {len(universe)}종목")

    raw = sources.collect(universe, start, end, progress=progress)
    if raw["ohlcv"].empty:
        return {"status": "no_data", "base_date": str(last_date.date()),
                "new_dates": 0, "new_rows": 0, "mode": raw["mode"]}

    new_rows = _build_new_rows(panel, raw)
    if new_rows.empty:
        return {"status": "up_to_date", "base_date": str(last_date.date()),
                "new_dates": 0, "new_rows": 0, "mode": raw["mode"]}

    updated = pd.concat([panel, new_rows], ignore_index=True)
    updated = updated.sort_values(["ticker", "date"]).reset_index(drop=True)

    tmp = PANEL_PATH.with_suffix(".parquet.tmp")
    updated.to_parquet(tmp, index=False)
    tmp.replace(PANEL_PATH)

    new_dates = sorted(new_rows["date"].dt.date.unique())
    from pipeline.ecos import get_ecos_key
    macro_state = "일일 매크로 갱신(ECOS)" if get_ecos_key() else "매크로 ffill(ECOS 키 없음)"
    stale = (f"수급·공매도 stale (KRX 계정 필요) · {macro_state} · 재무 ffill(분기)"
             if raw["mode"] == "naver" else f"{macro_state} · 재무 ffill(분기)")
    result = {"status": "updated", "mode": raw["mode"],
              "base_date": str(new_dates[-1]), "new_dates": len(new_dates),
              "new_rows": len(new_rows), "stale_columns": stale}
    progress(f"[pipeline] 완료 — {len(new_dates)}거래일 {len(new_rows):,}행 추가, "
             f"기준일 {new_dates[-1]} (mode={raw['mode']})")
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", default=None, help="YYYYMMDD (기본: 오늘)")
    args = ap.parse_args()
    print(run_update(end=args.end))
