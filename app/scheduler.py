# -*- coding: utf-8 -*-
"""스케줄러 — 매일 오전 8시: 데이터 갱신 → 캐시 리로드 → 전체 모델 예측 → DB 적재."""
import logging
import sys

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import market
import registry
from config import DATA_UPDATE_ENABLED, ROOT, SCHEDULE_HOUR, SCHEDULE_MINUTE, TIMEZONE

logger = logging.getLogger("quantdesk.scheduler")

_scheduler: BackgroundScheduler | None = None
JOB_ID = "daily_prediction"


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
    return {"running": True, "next_run": next_run,
            "cron": f"매일 {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d} {TIMEZONE}"}
