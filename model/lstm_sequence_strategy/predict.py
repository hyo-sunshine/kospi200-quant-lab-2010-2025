# -*- coding: utf-8 -*-
"""
predict.py — 최신 날짜 기준 horizon별 TopK 예측 (실운용 인퍼런스)
==================================================================
models/final_lstm_model.pt 를 로드해 패널 최신 날짜의 각 종목에 대해
1/7/30거래일 후 시장 대비 초과수익률을 예측하고 상위 종목을 출력한다.

    python predict.py
    python predict.py --top-k 10
"""
import argparse

import pandas as pd

from common import CHECKPOINT_PATH, TOP_K, log, prepare_panel
from modeling import (filter_valid_end_indices, fit_scaler_on_train,
                      load_checkpoint, make_loader,
                      make_scaled_feature_matrix, predict_model, restore_scaler)


def run_prediction(top_k: int = TOP_K, panel_df: pd.DataFrame | None = None) -> dict:
    """최신일 기준 horizon별 TopK 예측을 수행한다.

    Returns:
        {"base_date": str, "horizons": [int], "rankings": DataFrame, "all": DataFrame}
        rankings 컬럼: horizon, rank, date, ticker, close, pred_target, pred_target_pct
    """
    model, ckpt = load_checkpoint(CHECKPOINT_PATH)
    feature_cols = ckpt["feature_cols"]
    horizons = list(ckpt["horizons"])

    df = panel_df if panel_df is not None else prepare_panel(with_targets=False)
    missing = [c for c in feature_cols if c not in df.columns]
    if missing:
        raise ValueError(f"체크포인트 피처가 패널에 없습니다: {missing[:10]}")

    latest_date = df["date"].max()
    latest_idx = df.index[(df["date"] == latest_date) & df["has_full_lookback"]].to_numpy()
    latest_idx = filter_valid_end_indices(df, latest_idx, target_cols=None)
    if len(latest_idx) == 0:
        raise RuntimeError(f"최신일({latest_date.date()})에 예측 가능한 종목이 없습니다.")

    scaler = restore_scaler(ckpt)
    if scaler is None:
        # Colab 구버전 체크포인트에는 scaler가 없다 → 전체 기간으로 재적합 (근사)
        log("경고: 체크포인트에 scaler가 없어 전체 패널로 재적합합니다.")
        scaler = fit_scaler_on_train(df, df.index.to_numpy(), feature_cols)

    scaled = make_scaled_feature_matrix(df, feature_cols, scaler)
    loader = make_loader(df, latest_idx, scaled, target_cols=None, shuffle=False)
    preds = predict_model(model, loader)

    pred_df = df.loc[latest_idx, ["date", "ticker", "close"]].reset_index(drop=True)
    for j, h in enumerate(horizons):
        pred_df[f"pred_target_{h}d"] = preds[:, j]

    ranking_tables = []
    for h in horizons:
        pred_col = f"pred_target_{h}d"
        topk = pred_df.nlargest(top_k, pred_col).copy()
        topk["horizon"] = h
        topk["rank"] = range(1, len(topk) + 1)
        topk["pred_target"] = topk[pred_col]
        topk["pred_target_pct"] = topk[pred_col] * 100
        ranking_tables.append(
            topk[["horizon", "rank", "date", "ticker", "close",
                  "pred_target", "pred_target_pct"]])

    return {
        "base_date": str(latest_date.date()),
        "horizons": horizons,
        "rankings": pd.concat(ranking_tables, ignore_index=True),
        "all": pred_df,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-k", type=int, default=TOP_K)
    args = ap.parse_args()

    result = run_prediction(top_k=args.top_k)
    print(f"\n예측 기준일: {result['base_date']}  (시장 대비 초과수익률 예측)")
    for h in result["horizons"]:
        sub = result["rankings"][result["rankings"]["horizon"] == h]
        print(f"\n[{h}거래일 후] Top{args.top_k}:")
        for _, row in sub.iterrows():
            print(f"  {int(row['rank']):2d}. {row['ticker']}  "
                  f"close={row['close']:,.0f}  예상초과수익={row['pred_target_pct']:+.2f}%")


if __name__ == "__main__":
    main()
