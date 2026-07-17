# -*- coding: utf-8 -*-
"""
train.py — Walk-forward 학습 → OOS 예측 스코어 생성
=====================================================
매년(2013~2025) "그 이전 데이터로만" 학습한 모델로 그 해를 예측한다.
결과는 data/processed/model_scores/ 에 저장되며, backtest.py 가 이를 사용한다.
이미 존재하는 시드는 건너뛰므로 재실행 안전. (2모델 × 9시드, 약 30분)

    python train.py
"""
import pandas as pd
import lightgbm as lgb

from common import (FEATURES, TARGET_COL, PURGE_DAYS, LGB_PARAMS, SEEDS,
                    SCORES_DIR, load_feature_panel, log)

TEST_YEARS = list(range(2013, 2026))


def train_walk_forward(df, tag, seed):
    cache = SCORES_DIR / f"model_scores_{tag}_seed{seed}.parquet"
    if cache.exists():
        log(f"  건너뜀 (존재): {cache.name}")
        return
    features, target_col = FEATURES[tag], TARGET_COL[tag]
    pool = df.loc[(df["is_pit_universe_kospi200"] == 1)
                  & (df["is_volume_zero"] == 0)
                  & df["mom_12_1"].notna()]
    train_pool = pool.loc[pool[target_col].notna()].copy()
    # 타깃: 날짜별 미래 수익률의 횡단면 순위 (0~1)
    train_pool["target"] = train_pool.groupby("date")[target_col].rank(pct=True)
    outs = []
    for year in TEST_YEARS:
        ts, te = pd.Timestamp(f"{year}-01-01"), pd.Timestamp(f"{year}-12-31")
        tr = train_pool.loc[train_pool["date"]
                            < ts - pd.Timedelta(days=PURGE_DAYS[tag])]
        pr = pool.loc[(pool["date"] >= ts) & (pool["date"] <= te)]
        if tr.empty or pr.empty:
            continue
        m = lgb.LGBMRegressor(**LGB_PARAMS, seed=seed)
        m.fit(tr[features], tr["target"])
        outs.append(pd.DataFrame({"date": pr["date"].values,
                                  "ticker": pr["ticker"].values,
                                  "score": m.predict(pr[features])}))
    SCORES_DIR.mkdir(parents=True, exist_ok=True)
    pd.concat(outs, ignore_index=True).to_parquet(cache)
    log(f"  저장: {cache.name}")


def main():
    log("패널 로드 & 피처 생성")
    df = load_feature_panel(with_labels=True)
    for seed in SEEDS:
        for tag in ["flow60", "flow20"]:
            log(f"seed={seed} {tag}")
            train_walk_forward(df, tag, seed)
    log("완료")


if __name__ == "__main__":
    main()
