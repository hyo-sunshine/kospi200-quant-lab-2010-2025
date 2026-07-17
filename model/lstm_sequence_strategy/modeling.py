# -*- coding: utf-8 -*-
"""
모델링 모듈 — Dataset·LSTM 모델·학습/예측·체크포인트 입출력
=============================================================
common.py 의 설정을 사용한다. torch 의존 코드는 전부 이 파일에 모아둔다.
"""
import random

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.metrics import mean_squared_error
from sklearn.preprocessing import StandardScaler
from torch.utils.data import DataLoader, Dataset

from common import (BATCH_SIZE, DROPOUT, EPOCHS, GRAD_CLIP_NORM, HIDDEN_SIZE,
                    HORIZONS, LEARNING_RATE, LOOKBACK, NUM_LAYERS, PATIENCE,
                    TARGET_MODE, WEIGHT_DECAY, log)


def pick_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


DEVICE = pick_device()


def seed_everything(seed: int):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


# ---------- Dataset ----------
class LazySequenceDataset(Dataset):
    """스케일된 2차원 피처 행렬에서 필요한 시퀀스만 즉석 생성하는 메모리 절약형 Dataset."""

    def __init__(self, df, end_indices, scaled_features, target_cols=None):
        self.end_indices = np.asarray(end_indices, dtype=np.int64)
        self.scaled_features = scaled_features
        self.target_cols = target_cols
        self.y_values = (df[target_cols].values.astype(np.float32)
                         if target_cols is not None else None)

    def __len__(self):
        return len(self.end_indices)

    def __getitem__(self, i):
        end_idx = int(self.end_indices[i])
        start_idx = end_idx - LOOKBACK + 1
        x = torch.from_numpy(self.scaled_features[start_idx:end_idx + 1]).float()
        if self.target_cols is None:
            return x
        return x, torch.from_numpy(self.y_values[end_idx]).float()


def filter_valid_end_indices(df, end_indices, target_cols):
    """시퀀스 생성 가능(lookback 확보·ticker 연속·타깃 존재)한 end index만 남긴다."""
    lookback_ok = df["has_full_lookback"].values
    tickers = df["ticker"].values
    target_ok = (df[target_cols].notna().all(axis=1).values
                 if target_cols is not None else None)

    valid = []
    for idx in np.asarray(end_indices, dtype=np.int64):
        start = idx - LOOKBACK + 1
        if start < 0 or not lookback_ok[idx]:
            continue
        if tickers[start] != tickers[idx]:
            continue
        if target_ok is not None and not target_ok[idx]:
            continue
        valid.append(idx)
    return np.asarray(valid, dtype=np.int64)


def fit_scaler_on_train(df, train_idx, feature_cols) -> StandardScaler:
    """누수 방지: train 종료일 이하 데이터로만 scaler를 fit한다."""
    scaler = StandardScaler()
    train_end_date = df.loc[train_idx, "date"].max()
    fit_values = (df.loc[df["date"] <= train_end_date, feature_cols]
                  .replace([np.inf, -np.inf], np.nan).fillna(0)
                  .values.astype(np.float32))
    scaler.fit(fit_values)
    return scaler


def make_scaled_feature_matrix(df, feature_cols, scaler) -> np.ndarray:
    values = (df[feature_cols]
              .replace([np.inf, -np.inf], np.nan).fillna(0)
              .values.astype(np.float32))
    return scaler.transform(values).astype(np.float32)


def make_loader(df, end_indices, scaled_features, target_cols=None,
                shuffle=False, batch_size=BATCH_SIZE) -> DataLoader:
    ds = LazySequenceDataset(df, end_indices, scaled_features, target_cols)
    return DataLoader(ds, batch_size=batch_size, shuffle=shuffle,
                      num_workers=0, pin_memory=torch.cuda.is_available())


# ---------- 모델 ----------
class LSTMReturnModel(nn.Module):
    """최근 N일 시퀀스를 받아 여러 horizon의 초과수익률을 동시에 예측하는 LSTM."""

    def __init__(self, input_size, hidden_size, num_layers, output_size, dropout):
        super().__init__()
        self.lstm = nn.LSTM(input_size=input_size, hidden_size=hidden_size,
                            num_layers=num_layers, batch_first=True,
                            dropout=dropout if num_layers > 1 else 0.0)
        self.head = nn.Sequential(
            nn.LayerNorm(hidden_size),
            nn.Linear(hidden_size, hidden_size),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_size, output_size),
        )

    def forward(self, x):
        out, _ = self.lstm(x)
        return self.head(out[:, -1, :])


def build_model(input_size, output_size=len(HORIZONS),
                hidden_size=HIDDEN_SIZE, num_layers=NUM_LAYERS,
                dropout=DROPOUT) -> nn.Module:
    return LSTMReturnModel(input_size, hidden_size, num_layers, output_size, dropout)


# ---------- 학습/예측 ----------
def train_one_epoch(model, loader, optimizer, criterion):
    model.train()
    total_loss, total_count = 0.0, 0
    for x_batch, y_batch in loader:
        x_batch = x_batch.to(DEVICE, non_blocking=True)
        y_batch = y_batch.to(DEVICE, non_blocking=True)
        optimizer.zero_grad(set_to_none=True)
        loss = criterion(model(x_batch), y_batch)
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), GRAD_CLIP_NORM)
        optimizer.step()
        total_loss += loss.item() * len(x_batch)
        total_count += len(x_batch)
    return total_loss / max(total_count, 1)


@torch.no_grad()
def predict_model(model, loader) -> np.ndarray:
    model.eval()
    preds = []
    for batch in loader:
        x_batch = batch[0] if isinstance(batch, (list, tuple)) else batch
        pred = model(x_batch.to(DEVICE, non_blocking=True))
        preds.append(pred.detach().cpu().float().numpy())
    return np.vstack(preds)


def train_model_from_loaders(input_size, train_loader, valid_loader, y_valid):
    """train으로 학습, valid loss 최소 시점의 가중치를 반환 (early stopping)."""
    model = build_model(input_size).to(DEVICE)
    optimizer = torch.optim.AdamW(model.parameters(), lr=LEARNING_RATE,
                                  weight_decay=WEIGHT_DECAY)
    criterion = nn.MSELoss()

    best_state, best_valid_loss, patience_count = None, float("inf"), 0
    history = []
    for epoch in range(1, EPOCHS + 1):
        train_loss = train_one_epoch(model, train_loader, optimizer, criterion)
        valid_pred = predict_model(model, valid_loader)
        valid_loss = mean_squared_error(y_valid.reshape(-1), valid_pred.reshape(-1))
        history.append({"epoch": epoch, "train_loss": train_loss,
                        "valid_loss": valid_loss})
        log(f"epoch {epoch:02d} | train_loss={train_loss:.6f} | valid_loss={valid_loss:.6f}")

        if valid_loss < best_valid_loss:
            best_valid_loss = valid_loss
            best_state = {k: v.detach().cpu().clone()
                          for k, v in model.state_dict().items()}
            patience_count = 0
        else:
            patience_count += 1
        if patience_count >= PATIENCE:
            log(f"early stopping: {PATIENCE} epochs 동안 valid 개선 없음")
            break

    model.load_state_dict(best_state)
    return model, pd.DataFrame(history)


# ---------- 체크포인트 ----------
def save_checkpoint(model, scaler, feature_cols, path):
    """모델 가중치 + scaler 파라미터 + 설정을 한 파일에 저장 (predict 재현 보장)."""
    torch.save({
        "model_state_dict": model.state_dict(),
        "feature_cols": list(feature_cols),
        "horizons": list(HORIZONS),
        "lookback": LOOKBACK,
        "target_mode": TARGET_MODE,
        "input_size": len(feature_cols),
        "hidden_size": HIDDEN_SIZE,
        "num_layers": NUM_LAYERS,
        "dropout": DROPOUT,
        "scaler_mean": scaler.mean_.tolist() if scaler is not None else None,
        "scaler_scale": scaler.scale_.tolist() if scaler is not None else None,
    }, path)
    log(f"체크포인트 저장: {path}")


def load_checkpoint(path):
    """체크포인트 로드 → (model, checkpoint dict). scaler는 파라미터가 있으면 복원."""
    if not path.exists():
        raise FileNotFoundError(
            f"LSTM 체크포인트가 없습니다: {path}\n"
            "train_production.py 를 실행하거나 Colab에서 학습한 "
            "final_lstm_model.pt 를 models/ 에 복사하세요.")

    ckpt = torch.load(path, map_location="cpu", weights_only=False)
    model = build_model(
        input_size=ckpt["input_size"],
        output_size=len(ckpt["horizons"]),
        hidden_size=ckpt["hidden_size"],
        num_layers=ckpt["num_layers"],
        dropout=ckpt["dropout"],
    )
    model.load_state_dict(ckpt["model_state_dict"])
    model.to(DEVICE).eval()
    return model, ckpt


def restore_scaler(ckpt) -> StandardScaler | None:
    """체크포인트에 저장된 파라미터로 scaler 복원. 없으면 None (호출부에서 재적합)."""
    if ckpt.get("scaler_mean") is None or ckpt.get("scaler_scale") is None:
        return None
    scaler = StandardScaler()
    scaler.mean_ = np.asarray(ckpt["scaler_mean"], dtype=np.float64)
    scaler.scale_ = np.asarray(ckpt["scaler_scale"], dtype=np.float64)
    scaler.var_ = scaler.scale_ ** 2
    scaler.n_features_in_ = len(scaler.mean_)
    return scaler
