# -*- coding: utf-8 -*-
"""
train_production.py — 실운용 모델 가중치 학습
===============================================
walk-forward(검증용)와 달리 전 기간 데이터로 학습해 models/ 에 저장한다.
predict.py 가 이 가중치를 사용한다. 연 1회 패널 갱신 후 재실행 권장 (약 10분).

    python train_production.py
"""
import json

import lightgbm as lgb

from common import (FEATURES, TARGET_COL, LGB_PARAMS, SEEDS, MODELS_DIR,
                    load_feature_panel, log)


def main():
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    log("패널 로드 & 피처 생성")
    df = load_feature_panel(with_labels=True)
    pool = df.loc[(df["is_pit_universe_kospi200"] == 1)
                  & (df["is_volume_zero"] == 0)
                  & df["mom_12_1"].notna()]
    for tag in ["flow60", "flow20"]:
        features, target_col = FEATURES[tag], TARGET_COL[tag]
        train = pool.loc[pool[target_col].notna()].copy()
        train["target"] = train.groupby("date")[target_col].rank(pct=True)
        log(f"{tag}: 학습 {len(train):,}행, 피처 {len(features)}개")
        for seed in SEEDS:
            out = MODELS_DIR / f"prod_{tag}_seed{seed}.txt"
            m = lgb.LGBMRegressor(**LGB_PARAMS, seed=seed)
            m.fit(train[features], train["target"])
            m.booster_.save_model(str(out))
            log(f"  저장: {out.name}")
    (MODELS_DIR / "features.json").write_text(
        json.dumps(FEATURES, ensure_ascii=False, indent=2))
    log("완료")


if __name__ == "__main__":
    main()
