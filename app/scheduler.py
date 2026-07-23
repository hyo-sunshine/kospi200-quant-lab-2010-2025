# -*- coding: utf-8 -*-
"""스케줄러 — 매일 오전 8시: 데이터 갱신 → 캐시 리로드 → 전체 모델 예측 → DB 적재."""
import logging
import sys

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import market
import registry
from config import (DATA_UPDATE_ENABLED, ROOT, SCHEDULE_HOUR, SCHEDULE_MINUTE,
                    TIMEZONE, TRADE_HOUR, TRADE_MINUTE)

logger = logging.getLogger("quantdesk.scheduler")

_scheduler: BackgroundScheduler | None = None
JOB_ID = "daily_prediction"
TRADE_JOB_ID = "auto_trade"


def update_data() -> dict:
    """패널 증분 갱신 (pipeline 패키지). 실패해도 예측은 기존 데이터로 진행."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.update_panel import run_update
    result = run_update(progress=logger.info)
    if result["status"] == "updated":
        market.reload_cache()
    return result


def _daily_job():
    logger.info("일일 배치 시작")
    if DATA_UPDATE_ENABLED:
        try:
            result = update_data()
            logger.info("데이터 갱신: %s", result)
        except Exception:
            logger.exception("데이터 갱신 실패 — 기존 패널로 예측 진행")
    try:
        results = registry.run_all(trigger="schedule")
        for r in results:
            logger.info("모델 %s → %s", r["model_id"], r["status"])
    except Exception:
        logger.exception("일일 예측 배치 실패")


def _trade_job():
    """자동매매 배치 — trading 모듈이 auto_trade OFF·미연동·실전을 알아서 거른다."""
    try:
        import trading
        result = trading.run_auto_trade()
        logger.info("자동매매 배치: %s", result)
    except Exception:
        logger.exception("자동매매 배치 실패")


def _valid_trade_times(times) -> list[str]:
    """설정의 자동매매 시각 목록 검증 — 평일 장중(09:00~15:20)만, 없으면 기본값."""
    out = set()
    for t in times or []:
        try:
            h, m = map(int, str(t).strip().split(":"))
        except (ValueError, AttributeError):
            continue
        if (9, 0) <= (h, m) <= (15, 20):
            out.add(f"{h:02d}:{m:02d}")
    return sorted(out) or [f"{TRADE_HOUR:02d}:{TRADE_MINUTE:02d}"]


def trade_times() -> list[str]:
    import db
    return _valid_trade_times(db.get_settings().get("trade_times"))


def reschedule_trade_jobs():
    """settings.trade_times 기준으로 자동매매 잡을 다시 등록 (설정 저장 시 호출)."""
    if _scheduler is None:
        return
    for job in _scheduler.get_jobs():
        if job.id.startswith(TRADE_JOB_ID):
            job.remove()
    times = trade_times()
    for t in times:
        h, m = map(int, t.split(":"))
        _scheduler.add_job(
            _trade_job, CronTrigger(day_of_week="mon-fri", hour=h, minute=m),
            id=f"{TRADE_JOB_ID}_{t.replace(':', '')}", replace_existing=True,
            misfire_grace_time=600,   # 10분 이상 지난 회차는 건너뜀
            coalesce=True)
    logger.info("자동매매 스케줄 갱신: 평일 %s", ", ".join(times))


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    scheduler = BackgroundScheduler(timezone=TIMEZONE)
    scheduler.add_job(
        _daily_job, CronTrigger(hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE),
        id=JOB_ID, replace_existing=True,
        misfire_grace_time=3600,   # 서버가 8시에 꺼져 있었으면 1시간 내 기동 시 보충 실행
        coalesce=True)
    scheduler.start()
    _scheduler = scheduler
    reschedule_trade_jobs()
    logger.info("스케줄러 시작 — 매일 %02d:%02d (%s)",
                SCHEDULE_HOUR, SCHEDULE_MINUTE, TIMEZONE)
    return scheduler


def stop_scheduler():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def scheduler_status() -> dict:
    if _scheduler is None:
        return {"running": False, "next_run": None}
    job = _scheduler.get_job(JOB_ID)
    next_run = job.next_run_time.isoformat() if job and job.next_run_time else None
    trade_nexts = [j.next_run_time for j in _scheduler.get_jobs()
                   if j.id.startswith(TRADE_JOB_ID) and j.next_run_time]
    times = trade_times()
    return {"running": True, "next_run": next_run,
            "cron": f"매일 {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d} {TIMEZONE}",
            "trade_next_run": min(trade_nexts).isoformat() if trade_nexts else None,
            "trade_times": times,
            "trade_cron": f"평일 {', '.join(times)} {TIMEZONE}"}
