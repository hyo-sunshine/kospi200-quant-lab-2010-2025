# -*- coding: utf-8 -*-
"""SQLite 저장소 — 일별 예측 결과·실행 이력·앱 설정.

표준 라이브러리 sqlite3만 사용한다. 커넥션은 호출마다 열고 닫는다
(스케줄러 스레드와 API 스레드가 섞여 들어오므로 공유 커넥션을 피한다).
"""
import json
import sqlite3
from contextlib import contextmanager

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_predictions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date   TEXT NOT NULL,              -- 예측 실행일 YYYY-MM-DD
    base_date  TEXT NOT NULL,              -- 데이터 기준일 YYYY-MM-DD
    model_id   TEXT NOT NULL,
    ticker     TEXT NOT NULL,
    name       TEXT,
    rank       INTEGER,
    score      REAL,                       -- 모델 원신호 (랭크신호 or 예상초과수익률)
    horizon    INTEGER,                    -- LSTM: 1/7/30, rank_ensemble: NULL
    close      REAL,
    signal     TEXT,                       -- BUY / WATCH
    created_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(run_date, model_id, ticker, horizon)
);
CREATE INDEX IF NOT EXISTS idx_pred_run ON daily_predictions(run_date, model_id);

CREATE TABLE IF NOT EXISTS prediction_runs (
    run_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id   TEXT NOT NULL,
    run_date   TEXT NOT NULL,
    base_date  TEXT,
    status     TEXT NOT NULL,              -- running / success / error
    message    TEXT,
    trigger    TEXT,                       -- schedule / manual
    started_at TEXT DEFAULT (datetime('now','localtime')),
    ended_at   TEXT
);

CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trade_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT DEFAULT (datetime('now','localtime')),
    env       TEXT,                        -- paper / real
    side      TEXT NOT NULL,               -- BUY / SELL
    ticker    TEXT NOT NULL,
    name      TEXT,
    qty       INTEGER,
    price     REAL,
    ord_dvsn  TEXT,                        -- 00 지정가 / 01 시장가
    status    TEXT,                        -- submitted / failed
    order_no  TEXT,
    reason    TEXT,                        -- 매매 사유 (모델 신호·익절·손절 등)
    message   TEXT,
    trigger   TEXT                         -- manual / schedule
);
"""

DEFAULT_SETTINGS = {
    "auto_trade": False,
    "account_mode": "virtual",
    "conf_threshold": 70,
    "take_profit_pct": 8,
    "stop_loss_pct": 5,
    "max_position_krw": 2000000,
    "max_holdings": 5,
    "enabled_models": ["rank_ensemble", "lstm_sequence"],
    "trade_times": ["09:05"],      # 자동매매 실행 시각 (평일 장중, HH:MM)
}


@contextmanager
def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


# ---------- 예측 결과 ----------
def delete_predictions(run_date: str, model_id: str):
    """같은 날 재실행 시 이전 실행의 잔존 종목 제거 (전체 교체 방식)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM daily_predictions WHERE run_date=? AND model_id=?",
                     (run_date, model_id))


def insert_predictions(rows: list[dict]):
    """예측 결과 upsert — 같은 (run_date, model, ticker, horizon)이면 갱신."""
    if not rows:
        return
    with get_conn() as conn:
        conn.executemany(
            """INSERT INTO daily_predictions
               (run_date, base_date, model_id, ticker, name, rank, score,
                horizon, close, signal)
               VALUES (:run_date, :base_date, :model_id, :ticker, :name, :rank,
                       :score, :horizon, :close, :signal)
               ON CONFLICT(run_date, model_id, ticker, horizon) DO UPDATE SET
                 base_date=excluded.base_date, name=excluded.name,
                 rank=excluded.rank, score=excluded.score,
                 close=excluded.close, signal=excluded.signal""",
            rows)


def query_predictions(model_id: str | None = None, run_date: str | None = None,
                      limit: int = 500) -> list[dict]:
    sql = "SELECT * FROM daily_predictions WHERE 1=1"
    params: list = []
    if model_id:
        sql += " AND model_id = ?"
        params.append(model_id)
    if run_date:
        sql += " AND run_date = ?"
        params.append(run_date)
    sql += " ORDER BY run_date DESC, model_id, horizon, rank LIMIT ?"
    params.append(max(1, min(limit, 5000)))
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def latest_predictions() -> list[dict]:
    """모델별 가장 최근 run_date의 예측 목록."""
    sql = """SELECT p.* FROM daily_predictions p
             JOIN (SELECT model_id, MAX(run_date) AS md
                   FROM daily_predictions GROUP BY model_id) m
               ON p.model_id = m.model_id AND p.run_date = m.md
             ORDER BY p.model_id, p.horizon, p.rank"""
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql).fetchall()]


def prediction_dates(limit: int = 60) -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT run_date FROM daily_predictions "
            "ORDER BY run_date DESC LIMIT ?", (limit,)).fetchall()
    return [r["run_date"] for r in rows]


# ---------- 실행 이력 ----------
def start_run(model_id: str, run_date: str, trigger: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO prediction_runs (model_id, run_date, status, trigger) "
            "VALUES (?, ?, 'running', ?)", (model_id, run_date, trigger))
        return int(cur.lastrowid)


def finish_run(run_id: int, status: str, message: str = "",
               base_date: str | None = None):
    with get_conn() as conn:
        conn.execute(
            "UPDATE prediction_runs SET status=?, message=?, base_date=?, "
            "ended_at=datetime('now','localtime') WHERE run_id=?",
            (status, message[:2000], base_date, run_id))


def get_run(run_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM prediction_runs WHERE run_id=?",
                           (run_id,)).fetchone()
    return dict(row) if row else None


def recent_runs(limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM prediction_runs ORDER BY run_id DESC LIMIT ?",
            (limit,)).fetchall()
    return [dict(r) for r in rows]


# ---------- 매매 로그 ----------
def add_trade(row: dict):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO trade_log
               (env, side, ticker, name, qty, price, ord_dvsn, status,
                order_no, reason, message, trigger)
               VALUES (:env, :side, :ticker, :name, :qty, :price, :ord_dvsn,
                       :status, :order_no, :reason, :message, :trigger)""", row)


def list_trades(limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM trade_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ---------- 설정 ----------
def get_settings() -> dict:
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    saved = {r["key"]: json.loads(r["value"]) for r in rows}
    return {**DEFAULT_SETTINGS, **saved}


def save_settings(updates: dict) -> dict:
    valid = {k: v for k, v in updates.items() if k in DEFAULT_SETTINGS}
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            [(k, json.dumps(v, ensure_ascii=False)) for k, v in valid.items()])
    return get_settings()
