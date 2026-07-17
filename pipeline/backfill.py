# -*- coding: utf-8 -*-
"""수급·공매도·시총 백필 — 네이버 폴백 모드로 채워진 구간을 KRX 정식 데이터로 보정.

    python -m pipeline.backfill --since 20251231

기존 행의 다음 컬럼만 갱신한다 (가격/기술지표는 건드리지 않음):
  market_cap, trading_value, listed_shares (근사 → 정확값)
  inst_net, corp_net, indiv_net, foreign_net, total_net + *_net_pct
  short_volume, short_ratio_pct, short_volume_pct_volume
"""
import argparse
import time
from pathlib import Path

import pandas as pd

from pipeline.sources import REQUEST_DELAY_SEC, has_krx_credentials

ROOT = Path(__file__).resolve().parents[1]
PANEL_PATH = ROOT / "data/processed/master_panel.parquet"

FLOW_RENAME = {"기관합계": "inst_net", "기타법인": "corp_net",
               "개인": "indiv_net", "외국인합계": "foreign_net", "전체": "total_net"}
CAP_RENAME = {"시가총액": "market_cap", "거래대금": "trading_value",
              "상장주식수": "listed_shares"}


def backfill(since: str, progress=print) -> dict:
    if not has_krx_credentials():
        raise RuntimeError("KRX_ID/KRX_PW 필요 — .env 확인")
    from pykrx import stock

    panel = pd.read_parquet(PANEL_PATH)
    panel["date"] = pd.to_datetime(panel["date"])
    cutoff = pd.Timestamp(since)
    mask = panel["date"] > cutoff
    tickers = sorted(panel.loc[mask, "ticker"].unique())
    start = (cutoff + pd.Timedelta(days=1)).strftime("%Y%m%d")
    end = panel["date"].max().strftime("%Y%m%d")
    progress(f"[backfill] {start}~{end} · {len(tickers)}종목 · 대상 {mask.sum():,}행")

    frames = []
    for i, ticker in enumerate(tickers):
        merged = None
        c = stock.get_market_cap(start, end, ticker)
        if not c.empty:
            merged = (c.rename(columns=CAP_RENAME).reset_index()
                      .rename(columns={"날짜": "date"})
                      [["date", "market_cap", "trading_value", "listed_shares"]])
        time.sleep(REQUEST_DELAY_SEC)

        f = stock.get_market_trading_value_by_date(start, end, ticker, detail=False)
        if not f.empty:
            f = (f.rename(columns=FLOW_RENAME).reset_index()
                 .rename(columns={"날짜": "date"}))
            keep = ["date"] + [v for v in FLOW_RENAME.values() if v in f.columns]
            merged = f[keep] if merged is None else merged.merge(f[keep], on="date", how="outer")
        time.sleep(REQUEST_DELAY_SEC)

        try:
            s = stock.get_shorting_volume_by_date(start, end, ticker)
            if not s.empty:
                s = (s.rename(columns={"공매도": "short_volume", "비중": "short_ratio_pct"})
                     .reset_index().rename(columns={"날짜": "date"}))
                merged = (s[["date", "short_volume", "short_ratio_pct"]] if merged is None
                          else merged.merge(s[["date", "short_volume", "short_ratio_pct"]],
                                            on="date", how="outer"))
        except Exception:
            pass
        time.sleep(REQUEST_DELAY_SEC)

        if merged is not None:
            merged["ticker"] = ticker
            frames.append(merged)
        if (i + 1) % 20 == 0:
            progress(f"[backfill] {i + 1}/{len(tickers)}")

    if not frames:
        return {"status": "no_data"}
    fetched = pd.concat(frames, ignore_index=True)
    fetched["date"] = pd.to_datetime(fetched["date"])

    # 대상 행에 병합 — 갱신 컬럼만 덮어씀
    idx_cols = ["date", "ticker"]
    update_cols = [c for c in fetched.columns if c not in idx_cols]
    target = panel.loc[mask, idx_cols].merge(fetched, on=idx_cols, how="left")
    target.index = panel.index[mask]
    updated_counts = {}
    for col in update_cols:
        if col in panel.columns:
            valid = target[col].notna()
            panel.loc[target.index[valid], col] = target.loc[valid, col]
            updated_counts[col] = int(valid.sum())

    # 파생 재계산: *_net_pct = net/market_cap, 공매도/거래량 비율
    sub = panel.loc[mask]
    for actor in ["foreign", "inst", "indiv", "corp"]:
        panel.loc[mask, f"{actor}_net_pct"] = sub[f"{actor}_net"] / sub["market_cap"]
    panel.loc[mask, "short_volume_pct_volume"] = sub["short_volume"] / sub["volume"]

    tmp = PANEL_PATH.with_suffix(".parquet.tmp")
    panel.to_parquet(tmp, index=False)
    tmp.replace(PANEL_PATH)
    progress(f"[backfill] 완료 — 컬럼별 갱신 행수: {updated_counts}")
    return {"status": "done", "updated": updated_counts}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="20251230", help="이 날짜 이후 행을 보정")
    args = ap.parse_args()
    print(backfill(args.since))
