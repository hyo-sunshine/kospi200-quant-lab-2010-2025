# -*- coding: utf-8 -*-
"""모델 레지스트리 — 두 전략 모델을 등록하고 예측 실행/DB 적재를 담당."""
import threading
import traceback
from datetime import date

import db
from adapters.lstm_sequence import LstmSequenceAdapter
from adapters.rank_ensemble import RankEnsembleAdapter
from config import MODEL_META
from market import get_name_lookup

_lock = threading.Lock()
_adapters: dict | None = None


def get_adapters() -> dict:
    global _adapters
    with _lock:
        if _adapters is None:
            names = get_name_lookup()
            _adapters = {
                a.model_id: a
                for a in [RankEnsembleAdapter(names), LstmSequenceAdapter(names)]
            }
        return _adapters


def list_models() -> list[dict]:
    """UI 모델 카드용 메타데이터 + 준비 상태."""
    models = []
    for model_id, adapter in get_adapters().items():
        meta = MODEL_META.get(model_id, {})
        try:
            ready = adapter.is_ready()
        except Exception as e:
            ready = {"ready": False, "detail": f"상태 확인 실패: {e}"}
        models.append({"id": model_id, **meta, **ready})
    return models


def run_model(model_id: str, trigger: str = "manual",
              run_id: int | None = None) -> dict:
    """단일 모델 예측 실행 → daily_predictions 적재. 실행 이력도 기록한다.

    run_id를 넘기면 이미 생성된 실행 이력(running)을 이어서 사용한다.
    """
    adapters = get_adapters()
    if model_id not in adapters:
        raise KeyError(f"등록되지 않은 모델: {model_id}")

    run_date = date.today().isoformat()
    if run_id is None:
        run_id = db.start_run(model_id, run_date, trigger)
    try:
        result = adapters[model_id].predict()
        rows = [{**row, "run_date": run_date} for row in result["rows"]]
        db.delete_predictions(run_date, model_id)   # 그날 분 전체 교체
        db.insert_predictions(rows)
        db.finish_run(run_id, "success",
                      f"{len(rows)}건 적재", base_date=result["base_date"])
        return {"run_id": run_id, "status": "success",
                "base_date": result["base_date"], "count": len(rows)}
    except Exception as e:
        db.finish_run(run_id, "error", f"{e}\n{traceback.format_exc()}")
        return {"run_id": run_id, "status": "error", "message": str(e)}


def run_all(trigger: str = "schedule") -> list[dict]:
    """설정에서 활성화된 모든 모델 순차 실행 (스케줄러 진입점)."""
    enabled = db.get_settings().get("enabled_models", [])
    results = []
    for model_id in get_adapters():
        if enabled and model_id not in enabled:
            continue
        results.append({"model_id": model_id, **run_model(model_id, trigger)})
    return results
