# -*- coding: utf-8 -*-
"""매매 엔진 — 모델 신호를 주문 플랜으로 변환하고 실행한다.

매매 타이밍
- 08:00  데이터 갱신 + 모델 예측 (기존 스케줄러 그대로)
- 09:05  자동매매 배치 — 개장 직후 5분 동시호가·변동성 구간을 피해 리밸런스
- 익절/손절은 플랜 생성 시 보유 종목 평가손익률로 판정

플랜 규칙 (전부 settings 기반, UI [자동매매 설정]에서 조정)
- 목표 포트폴리오: rank_ensemble BUY 상위 max_holdings 종목
- SELL: ① 익절(take_profit_pct 이상) ② 손절(stop_loss_pct 이하)
        ③ 목표 이탈(모델 신호에서 빠진 보유 종목)
- BUY: 목표 중 미보유 종목, 종목당 max_position_krw 한도 내 수량

안전 가드
- 자동 실행은 모의투자(paper) 환경에서만. 실전(real)은 UI 수동 주문만 허용.
- auto_trade OFF(기본) 또는 미연동이면 자동 배치는 아무 것도 하지 않는다.
"""
import logging

import db
from broker import kis

logger = logging.getLogger("quantdesk.trading")


def _latest_signals() -> tuple[list[dict], dict, str | None]:
    """최신 예측에서 (rank_ensemble BUY 목록, LSTM 7d 점수맵, 신호 기준일)."""
    latest = db.latest_predictions()
    rank = sorted((p for p in latest if p["model_id"] == "rank_ensemble"),
                  key=lambda p: p["rank"] or 999)
    lstm7 = {p["ticker"]: p["score"] for p in latest
             if p["model_id"] == "lstm_sequence" and p["horizon"] == 7}
    buys = [p for p in rank if p["signal"] == "BUY"]
    signal_date = buys[0]["run_date"] if buys else None
    return buys, lstm7, signal_date


def suggest_orders() -> dict:
    """모델 신호 + 계좌 상태 → 매매 플랜. 미연동이어도 신호 기반 플랜은 보여준다."""
    settings = db.get_settings()
    status = kis.get_status()
    buys, lstm7, signal_date = _latest_signals()

    max_holdings = int(settings.get("max_holdings", 5))
    budget = int(settings.get("max_position_krw", 2_000_000))
    tp = float(settings.get("take_profit_pct", 8))
    sl = float(settings.get("stop_loss_pct", 5))
    targets = buys[:max_holdings]
    target_ticks = {p["ticker"] for p in targets}

    holdings, cash = [], None
    if status["connected"]:
        bal = kis.get_balance()
        if bal["connected"]:
            holdings, cash = bal["holdings"], bal["cash"]

    orders, notes = [], []
    if not targets:
        notes.append("저장된 매수 신호가 없습니다 — [모델 예측]을 먼저 실행하세요.")
    if not status["connected"]:
        notes.append("KIS 미연동 — 플랜은 신호 기준으로만 생성되며 주문은 불가합니다.")

    held = {h["ticker"] for h in holdings}
    for h in holdings:                                   # 1) 청산 대상
        pnl = h.get("pnl_pct") or 0.0
        if pnl >= tp:
            reason = f"익절 — 수익률 +{pnl:.1f}% ≥ 목표 +{tp:.0f}%"
        elif pnl <= -sl:
            reason = f"손절 — 수익률 {pnl:.1f}% ≤ 한도 -{sl:.0f}%"
        elif h["ticker"] not in target_ticks:
            reason = "리밸런스 — 모델 목표 포트폴리오에서 이탈"
        else:
            continue
        orders.append({"action": "SELL", "ticker": h["ticker"], "name": h["name"],
                       "qty": h["qty"], "price": h["cur_price"], "reason": reason})

    for p in targets:                                    # 2) 신규 편입
        if p["ticker"] in held:
            continue
        price = (kis.get_current_price(p["ticker"]) if status["connected"] else None) \
            or p["close"]
        qty = int(budget // price) if price else 0
        if qty <= 0:
            notes.append(f"{p['name'] or p['ticker']}: 종목당 한도(₩{budget:,})로 1주 미만 — 제외")
            continue
        reason = f"rank_ensemble {p['rank']}위 신호"
        if p["ticker"] in lstm7:
            reason += f" · LSTM 7일 {lstm7[p['ticker']] * 100:+.1f}%"
        orders.append({"action": "BUY", "ticker": p["ticker"], "name": p["name"],
                       "qty": qty, "price": price, "reason": reason})

    return {
        "connected": status["connected"], "env": status.get("env"),
        "account_no": status.get("account_no"),
        "auto_trade": bool(settings.get("auto_trade")),
        "signal_date": signal_date,
        "cash": cash,
        "orders": orders, "notes": notes,
    }


def _log_trade(result_or_err, order: dict, env: str | None, trigger: str):
    ok = isinstance(result_or_err, dict)
    db.add_trade({
        "env": env or "-", "side": order["action"], "ticker": order["ticker"],
        "name": order.get("name"), "qty": order["qty"],
        "price": order.get("price"),
        "ord_dvsn": "01",
        "status": "submitted" if ok else "failed",
        "order_no": result_or_err.get("order_no", "") if ok else "",
        "reason": order.get("reason", ""),
        "message": result_or_err.get("message", "") if ok else str(result_or_err),
        "trigger": trigger,
    })


def execute_plan(trigger: str = "manual") -> dict:
    """suggest_orders() 플랜을 시장가로 일괄 실행. 실전(real)에서는 거부."""
    plan = suggest_orders()
    if not plan["connected"]:
        return {"status": "skipped", "message": "KIS 미연동", "results": []}
    if plan["env"] != "paper":
        return {"status": "blocked",
                "message": "실전 계좌 일괄 실행은 차단되어 있습니다 — 종목별 수동 주문만 가능합니다.",
                "results": []}
    results = []
    for order in plan["orders"]:
        try:
            r = kis.place_order(order["ticker"], order["action"], order["qty"], price=None)
            _log_trade(r, order, plan["env"], trigger)
            results.append({**order, "status": "submitted", "order_no": r["order_no"]})
        except Exception as e:
            logger.warning("플랜 주문 실패 %s %s: %s", order["action"], order["ticker"], e)
            _log_trade(e, order, plan["env"], trigger)
            results.append({**order, "status": "failed", "message": str(e)})
    done = sum(1 for r in results if r["status"] == "submitted")
    return {"status": "done", "message": f"{done}/{len(results)}건 접수",
            "results": results}


def place_manual_order(ticker: str, side: str, qty: int,
                       price: int | None = None) -> dict:
    """UI 수동 주문 — 실전/모의 모두 허용 (사용자 확인을 거친 명시적 주문)."""
    status = kis.get_status()
    name = None
    try:
        import market
        name = market.get_name_lookup().get(str(ticker).zfill(6))
    except Exception:
        pass
    order = {"action": side.upper(), "ticker": str(ticker).zfill(6),
             "name": name, "qty": qty, "price": price, "reason": "수동 주문"}
    try:
        r = kis.place_order(ticker, side, qty, price)
    except Exception as e:
        _log_trade(e, order, status.get("env"), "manual")
        raise
    _log_trade(r, order, status.get("env"), "manual")
    return r


def run_auto_trade() -> dict:
    """09:05 자동매매 배치 진입점 (scheduler)."""
    settings = db.get_settings()
    if not settings.get("auto_trade"):
        return {"status": "skipped", "message": "자동매매 OFF"}
    status = kis.get_status()
    if not status["connected"]:
        return {"status": "skipped", "message": "KIS 미연동"}
    if status.get("env") != "paper":
        logger.warning("자동매매는 모의투자(paper)에서만 실행됩니다 — 실전 차단")
        return {"status": "blocked", "message": "실전 자동매매 차단 (모의투자 전용)"}
    result = execute_plan(trigger="schedule")
    logger.info("자동매매 배치: %s", result["message"])
    return result
