# -*- coding: utf-8 -*-
"""QuantDesk 서버 진입점 — FastAPI + 정적 UI + 일일 스케줄러.

    .venv/bin/python -m uvicorn main:app --app-dir app --port 8500
    또는
    .venv/bin/python app/main.py
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

import db
import scheduler
from api import router
from config import STATIC_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("quantdesk")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    scheduler.start_scheduler()
    logger.info("QuantDesk 시작")
    yield
    scheduler.stop_scheduler()
    logger.info("QuantDesk 종료")


app = FastAPI(title="QuantDesk — KOSPI200 Quant Console", lifespan=lifespan)
app.include_router(router)
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8500)
