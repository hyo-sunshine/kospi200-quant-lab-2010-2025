# -*- coding: utf-8 -*-
"""모델 2 어댑터 — lstm_sequence_strategy (LSTM 시퀀스 모델).

전략 패키지의 predict.run_prediction()을 그대로 사용한다.
lstm 패키지 내부의 `from common import ...` / `from modeling import ...`이
동작하도록 sys.path에 패키지 디렉터리를 추가한 뒤 로드한다.
"""
import sys

from config import LSTM_SEQUENCE_DIR
from adapters.base import ModelAdapter, load_module_from_path

TOP_K_STORE = 5           # horizon별 상위 5종목 저장


class LstmSequenceAdapter(ModelAdapter):
    model_id = "lstm_sequence"

    def __init__(self, name_lookup=None):
        self._predict_mod = None
        self._name_lookup = name_lookup or {}

    @property
    def predict_mod(self):
        if self._predict_mod is None:
            # lstm 패키지 모듈 간 flat import를 위해 검색 경로에 추가.
            # rank_ensemble의 common.py와 충돌하지 않도록 rank 쪽은
            # load_module_from_path("rank_ensemble_common")으로만 로드한다.
            pkg_dir = str(LSTM_SEQUENCE_DIR)
            if pkg_dir not in sys.path:
                sys.path.insert(0, pkg_dir)
            load_module_from_path("common", LSTM_SEQUENCE_DIR / "common.py")
            load_module_from_path("modeling", LSTM_SEQUENCE_DIR / "modeling.py")
            self._predict_mod = load_module_from_path(
                "lstm_sequence_predict", LSTM_SEQUENCE_DIR / "predict.py")
        return self._predict_mod

    def is_ready(self) -> dict:
        ckpt = LSTM_SEQUENCE_DIR / "models/final_lstm_model.pt"
        if not ckpt.exists():
            return {"ready": False,
                    "detail": "final_lstm_model.pt 없음 — train_production.py 실행 "
                              "또는 Colab 학습 결과를 models/에 복사"}
        size_mb = ckpt.stat().st_size / 1e6
        return {"ready": True, "detail": f"체크포인트 {size_mb:.1f}MB"}

    def predict(self) -> dict:
        result = self.predict_mod.run_prediction(top_k=TOP_K_STORE)
        base_date = result["base_date"]

        rows = []
        for _, row in result["rankings"].iterrows():
            ticker = str(row["ticker"])
            rows.append({
                "base_date": base_date,
                "model_id": self.model_id,
                "ticker": ticker,
                "name": self._name_lookup.get(ticker),
                "rank": int(row["rank"]),
                "score": round(float(row["pred_target"]), 6),
                "horizon": int(row["horizon"]),
                "close": float(row["close"]),
                "signal": "BUY" if row["rank"] <= 3 else "WATCH",
            })
        return {"base_date": base_date, "rows": rows}
