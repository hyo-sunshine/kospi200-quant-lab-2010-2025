# lstm_sequence_strategy — KOSPI200 LSTM 시퀀스 전략 (모델 2)

> **전략 한 줄**: 종목별 최근 20거래일 피처 시퀀스를 LSTM에 넣어
> **1/7/30거래일 후 시장 대비 초과수익률**을 예측, horizon별 Top5 종목을 선정.
>
> 원본: `notebook/kospi200_lstm_sequence_colab_v3_2_memory_safe.ipynb` (Colab v3.2)

## 폴더 구성

```
lstm_sequence_strategy/
├── README.md            ← 이 문서
├── common.py            ← 설정·데이터 로드·피처/타깃 생성
├── modeling.py          ← Dataset·LSTM 모델·학습/예측·체크포인트 입출력
├── train_production.py  ← 실운용 최종 모델 학습 → models/final_lstm_model.pt
├── predict.py           ← 최신일 기준 horizon별 TopK 예측
├── models/              ← 체크포인트 (final_lstm_model.pt)
└── notebook/            ← 원본 Colab 노트북 (walk-forward 검증 포함)
```

## 빠른 시작

```bash
# 의존성: torch, pandas, numpy, pyarrow, scikit-learn  (저장소 루트 .venv 사용)
../../.venv/bin/python predict.py            # 최신일 Top5 (체크포인트 필요)
../../.venv/bin/python train_production.py   # 로컬 학습 (CPU/MPS는 오래 걸림)
```

**체크포인트 준비 (둘 중 하나)**

1. Colab에서 노트북 실행 → `kospi200_lstm_sequence_outputs/final_lstm_model.pt` 를
   `models/` 에 복사 (구버전 체크포인트는 scaler가 없어 predict 시 재적합 경고가 뜸)
2. 로컬에서 `train_production.py` 실행 (scaler까지 함께 저장됨)

## 모델 명세

| 항목 | 값 |
|---|---|
| 입력 | (20거래일, 피처 ≤60개) 시퀀스 — 수익률·MA괴리·변동성·거래량·시장공통 등 |
| 출력 | 3개 값 = 1/7/30거래일 후 **market_excess** 수익률 |
| 구조 | LSTM(hidden 32, 1층) + LayerNorm/MLP head |
| 학습 | MSE, AdamW, early stopping(patience 5), 타깃 ±50% 클리핑 |
| 누수 방지 | scaler는 train 구간만 fit, final 모델은 최신일 예측 전용 |

## rank_ensemble_strategy(모델 1)와의 관계

| | 모델 1 (rank_ensemble) | 모델 2 (lstm_sequence) |
|---|---|---|
| 알고리즘 | LightGBM 18개 앙상블 | LSTM 단일 |
| 입력 | 1행 tabular (재무+수급+모멘텀) | 20일 시퀀스 (가격/거래량 패턴) |
| 출력 | 횡단면 랭크 신호 → 상위 8종목 | 초과수익률 예측 → horizon별 Top5 |
| 검증 | walk-forward 2013~2025 (README 참고) | walk-forward는 노트북에서 수행 |

두 모델 모두 `app/`(QuantDesk 콘솔)의 모델 레지스트리에 등록되어
매일 오전 8시 자동 예측 → DB 적재된다.
