# -*- coding: utf-8 -*-
"""앱 전역 설정 — 경로·스케줄·모델 메타데이터."""
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent

PANEL_PATH = ROOT / "data/processed/master_panel.parquet"
TICKER_MAP_PATH = ROOT / "data/raw/meta/ticker_sector_map.csv"
DB_PATH = ROOT / "data/db/quant.db"
STATIC_DIR = APP_DIR / "static"

RANK_ENSEMBLE_DIR = ROOT / "model/rank_ensemble_strategy"
LSTM_SEQUENCE_DIR = ROOT / "model/lstm_sequence_strategy"

# 매일 오전 8시에 데이터 갱신 → 전체 모델 예측 → DB 적재
SCHEDULE_HOUR = 8
SCHEDULE_MINUTE = 0
TIMEZONE = "Asia/Seoul"
DATA_UPDATE_ENABLED = True     # 08:00 배치에서 패널 증분 갱신 수행 여부

# 자동매매 배치 — 개장 직후 변동성 구간(09:00~09:05)을 피해 실행 (평일만)
# auto_trade ON + KIS 모의투자(paper) 연동 시에만 실제 주문이 나간다.
TRADE_HOUR = 9
TRADE_MINUTE = 5

# UI 차트에 기본으로 내려줄 일봉 개수
DEFAULT_CHART_DAYS = 90
MAX_CHART_DAYS = 500

MODEL_META = {
    "rank_ensemble": {
        "name": "Rank Ensemble",
        "type": "LightGBM",
        "file": "prod_flow{20,60}_seed*.txt (18개)",
        "description": "2지평×9시드 랭크앙상블 → 상위 8종목 동일가중 (검증 CAGR 16.8%)",
        "output": "횡단면 랭크 신호 (0~1)",
    },
    "lstm_sequence": {
        "name": "LSTM Sequence",
        "type": "LSTM",
        "file": "final_lstm_model.pt",
        "description": "최근 20거래일 시퀀스 → 1/7/30일 후 시장 대비 초과수익률 예측",
        "output": "horizon별 예상 초과수익률 (%)",
    },
}
