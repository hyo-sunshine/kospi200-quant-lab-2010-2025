# -*- coding: utf-8 -*-
"""
train_production.py — 실운용 LSTM 최종 모델 학습
==================================================
전체 라벨 데이터로 학습하고 마지막 10% 날짜를 내부 valid로 early stopping.
결과는 models/final_lstm_model.pt 에 저장된다 (가중치 + scaler + 피처 목록).

    python train_production.py

주의: CPU/MPS에서는 수 시간이 걸릴 수 있다. Colab GPU 학습 후
final_lstm_model.pt 만 models/ 에 복사해도 동일하게 동작한다.
"""
import numpy as np

from common import (CHECKPOINT_PATH, FINAL_TRAIN_SPLIT, MODELS_DIR, RANDOM_STATE,
                    TARGET_COLS, log, prepare_panel, select_feature_columns)
from modeling import (DEVICE, filter_valid_end_indices, fit_scaler_on_train,
                      make_loader, make_scaled_feature_matrix,
                      save_checkpoint, seed_everything, train_model_from_loaders)


def main():
    seed_everything(RANDOM_STATE)
    log(f"device: {DEVICE}")

    df = prepare_panel(with_targets=True)
    feature_cols = select_feature_columns(df)

    usable = df[df["has_full_lookback"] & df[TARGET_COLS].notna().all(axis=1)]
    unique_dates = np.array(sorted(usable["date"].unique()))
    split_date = unique_dates[int(len(unique_dates) * FINAL_TRAIN_SPLIT)]

    train_idx = filter_valid_end_indices(
        df, usable.index[usable["date"] <= split_date].to_numpy(), TARGET_COLS)
    valid_idx = filter_valid_end_indices(
        df, usable.index[usable["date"] > split_date].to_numpy(), TARGET_COLS)
    log(f"train {len(train_idx):,} / valid {len(valid_idx):,} samples "
        f"(split={split_date.date()})")

    scaler = fit_scaler_on_train(df, train_idx, feature_cols)
    scaled = make_scaled_feature_matrix(df, feature_cols, scaler)

    train_loader = make_loader(df, train_idx, scaled, TARGET_COLS, shuffle=True)
    valid_loader = make_loader(df, valid_idx, scaled, TARGET_COLS, shuffle=False)
    y_valid = df.loc[valid_idx, TARGET_COLS].values.astype(np.float32)

    model, history = train_model_from_loaders(
        input_size=len(feature_cols),
        train_loader=train_loader, valid_loader=valid_loader, y_valid=y_valid)

    MODELS_DIR.mkdir(exist_ok=True)
    save_checkpoint(model, scaler, feature_cols, CHECKPOINT_PATH)
    history.to_csv(MODELS_DIR / "train_history.csv", index=False)
    log("학습 완료")


if __name__ == "__main__":
    main()
