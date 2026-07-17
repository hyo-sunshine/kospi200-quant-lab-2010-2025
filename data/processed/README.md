# 학습용 가공 데이터

## master_panel.parquet — 마스터 패널 (최종 학습 데이터)
- 1,237,580행 × 114컬럼, 2010-01-04 ~ 2025-12-30, 369종목 (PIT KOSPI200)
- 계보: KRX 원본(가격·수급·공매도) + ECOS 거시 + DART 분기재무(공시일 기준 PIT 결합)
  → 기술지표 계산(RSI/MACD/ATR/볼린저) + 정규화 + 라벨 생성
- 컬럼 전체 사전: `master_panel_columns.txt`

## dart_quarterly_2010_2025.parquet
DART 분기 재무 가공본 (368종목 × 분기, 매출/영업이익/순이익/자산/부채 + 성장률/비율).

※ 모델 예측 스코어(model_scores)는 `model/rank_ensemble_strategy/model_scores/` 에 있다.
