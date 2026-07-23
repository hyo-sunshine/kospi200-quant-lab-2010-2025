# -*- coding: utf-8 -*-
"""REST API 라우터 — 모델·예측·시세·설정·브로커."""
import logging
import threading

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import db
import market
import registry
import scheduler
import trading
from broker import kis

logger = logging.getLogger("quantdesk.api")
router = APIRouter(prefix="/api")


# ---------- 상태 ----------
@router.get("/health")
def health():
    try:
        base_date = market.latest_base_date()
    except Exception as e:
        base_date = None
        logger.warning("패널 로드 실패: %s", e)
    return {
        "status": "ok",
        "panel_base_date": base_date,
        "scheduler": scheduler.scheduler_status(),
        "broker": kis.get_status(),
    }


# ---------- 모델 ----------
@router.get("/models")
def get_models():
    return {"models": registry.list_models()}


class PredictRequest(BaseModel):
    model_id: str


@router.post("/predict")
def run_predict(req: PredictRequest):
    """모델 예측을 백그라운드 스레드로 실행하고 run_id를 반환.

    예측은 패널 로드+피처 생성 때문에 수 분이 걸릴 수 있어
    UI는 run_id로 /api/runs/{run_id}를 폴링한다.
    """
    if req.model_id not in registry.get_adapters():
        raise HTTPException(404, f"등록되지 않은 모델: {req.model_id}")

    from datetime import date
    run_id = db.start_run(req.model_id, date.today().isoformat(), "manual")

    thread = threading.Thread(
        target=registry.run_model,
        kwargs={"model_id": req.model_id, "trigger": "manual", "run_id": run_id},
        daemon=True)
    thread.start()
    return {"status": "running", "run_id": run_id}


@router.get("/runs/{run_id}")
def get_run(run_id: int):
    run = db.get_run(run_id)
    if run is None:
        raise HTTPException(404, "실행 이력이 없습니다.")
    return run


@router.get("/runs")
def get_runs(limit: int = Query(20, le=100)):
    return {"runs": db.recent_runs(limit)}


# ---------- 예측 결과 ----------
@router.get("/predictions")
def get_predictions(model_id: str | None = None, run_date: str | None = None,
                    limit: int = Query(500, le=5000)):
    return {"predictions": db.query_predictions(model_id, run_date, limit)}


@router.get("/predictions/latest")
def get_latest_predictions():
    return {"predictions": db.latest_predictions()}


@router.get("/predictions/dates")
def get_prediction_dates():
    return {"dates": db.prediction_dates()}


# ---------- 시세 ----------
@router.get("/stocks")
def get_stocks(q: str = "", limit: int = Query(250, le=500)):
    try:
        return {"base_date": market.latest_base_date(),
                "stocks": market.list_stocks(q, limit)}
    except FileNotFoundError as e:
        raise HTTPException(503, str(e))


@router.get("/prices/{ticker}")
def get_prices(ticker: str, days: int = Query(90, le=500)):
    try:
        return market.get_prices(ticker, days)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except FileNotFoundError as e:
        raise HTTPException(503, str(e))


@router.post("/reload")
def reload_market_cache():
    market.reload_cache()
    return {"status": "ok", "base_date": market.latest_base_date()}


@router.post("/update-data")
def update_data_now():
    """패널 증분 갱신을 즉시 실행 (수 분 소요 — 백그라운드 스레드)."""
    def _work():
        try:
            result = scheduler.update_data()
            logger.info("수동 데이터 갱신: %s", result)
        except Exception:
            logger.exception("수동 데이터 갱신 실패")

    threading.Thread(target=_work, daemon=True).start()
    return {"status": "running",
            "message": "데이터 갱신 시작 — 완료 후 /api/health 의 panel_base_date 확인"}


# ---------- 설정 ----------
@router.get("/settings")
def get_settings():
    return {"settings": db.get_settings()}


class SettingsUpdate(BaseModel):
    auto_trade: bool | None = None
    account_mode: str | None = None
    conf_threshold: int | None = None
    take_profit_pct: float | None = None
    stop_loss_pct: float | None = None
    max_position_krw: int | None = None
    max_holdings: int | None = None
    enabled_models: list[str] | None = None
    trade_times: list[str] | None = None


@router.put("/settings")
def put_settings(update: SettingsUpdate):
    changes = {k: v for k, v in update.model_dump().items() if v is not None}
    settings = db.save_settings(changes)
    if "trade_times" in changes:
        scheduler.reschedule_trade_jobs()   # 자동매매 시각 변경 즉시 반영
    return {"settings": settings}


# ---------- 브로커 (한국투자증권 KIS OpenAPI) ----------
@router.get("/broker/status")
def broker_status():
    return kis.get_status()


@router.get("/broker/balance")
def broker_balance():
    return kis.get_balance()


@router.get("/broker/trades")
def broker_trades(limit: int = Query(20, le=100)):
    """KIS 계좌 체결 내역. 미연동이면 앱에서 시도한 주문 로그(로컬)라도 반환."""
    trades = kis.get_trade_log(limit)
    source = "kis"
    if not trades:
        trades = db.list_trades(limit)
        source = "local"
    return {"trades": trades, "source": source}


@router.get("/broker/plan")
def broker_plan():
    """모델 신호 + 계좌 상태 기반 매매 플랜 (주문은 내지 않음)."""
    return trading.suggest_orders()


class OrderRequest(BaseModel):
    ticker: str
    side: str                       # BUY / SELL
    qty: int
    price: int | None = None        # None = 시장가


@router.post("/broker/order")
def broker_order(req: OrderRequest):
    """수동 주문 — UI에서 사용자 확인을 거친 뒤 호출된다."""
    if not kis.get_status()["connected"]:
        raise HTTPException(503, "KIS 미연동 — .env에 KIS_APP_KEY 등을 설정하세요.")
    try:
        return trading.place_manual_order(req.ticker, req.side, req.qty, req.price)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


class ExecutePlanRequest(BaseModel):
    confirm: bool = False


@router.post("/broker/execute-plan")
def broker_execute_plan(req: ExecutePlanRequest):
    """매매 플랜 일괄 실행 (모의투자 전용 — 실전은 서버에서 차단)."""
    if not req.confirm:
        raise HTTPException(400, "confirm=true 필요 — UI 확인 대화상자를 거쳐야 합니다.")
    return trading.execute_plan(trigger="manual")


@router.get("/broker/price/{ticker}")
def broker_price(ticker: str):
    price = kis.get_current_price(ticker)
    if price is None:
        raise HTTPException(503, "현재가 조회 실패 (미연동 또는 통신 오류)")
    return {"ticker": ticker, "price": price}
