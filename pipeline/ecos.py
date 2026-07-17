# -*- coding: utf-8 -*-
"""ECOS(한국은행) 매크로 수집 — 일 주기 지표 3종 (환율·기준금리·국고3년).

API 키는 프로젝트 루트 .env 의 ECOS_API_KEY (sangjunInBus에서 이관, 2026-07-17 유효 확인).
월/분기 지표(CPI·M2·수출·GDP)는 발표 지연이 커서 ffill 유지 — 필요 시 확장.
"""
import os
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
ECOS_URL = "https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/10000/{code}/D/{start}/{end}"

# (통계코드, ITEM_NAME1 필터, 패널 컬럼)
DAILY_SERIES = [
    ("731Y001", "원/미국달러(매매기준율)", "macro_usdkrw"),
    ("722Y001", None, "macro_kor_base_rate"),
    ("817Y002", None, "macro_kor_bond_3y"),
]


def get_ecos_key() -> str | None:
    key = os.environ.get("ECOS_API_KEY")
    if key:
        return key
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("ECOS_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None


def fetch_daily_series(code: str, item_name: str | None,
                       start: str, end: str, key: str) -> pd.Series:
    """일 주기 시계열 조회 → date 인덱스 Series."""
    res = requests.get(ECOS_URL.format(key=key, code=code, start=start, end=end),
                       timeout=20)
    res.raise_for_status()
    body = res.json()
    rows = body.get("StatisticSearch", {}).get("row", [])
    if not rows:
        return pd.Series(dtype="float64")
    df = pd.DataFrame(rows)
    if item_name:
        df = df[df["ITEM_NAME1"] == item_name]
    df["date"] = pd.to_datetime(df["TIME"], format="%Y%m%d")
    df["value"] = pd.to_numeric(df["DATA_VALUE"], errors="coerce")
    s = df.dropna(subset=["value"]).set_index("date")["value"]
    return s.groupby(level=0).last()      # 항목 복수 응답 시 날짜별 1값으로 정리


def collect_macro(start: str, end: str, progress=print) -> pd.DataFrame:
    """일 주기 매크로 3종 수집 → DataFrame(date, macro_usdkrw, ...). 키 없으면 빈 df."""
    key = get_ecos_key()
    if not key:
        progress("[ecos] ECOS_API_KEY 없음 — 매크로는 마지막 값 유지")
        return pd.DataFrame()
    out = {}
    for code, item, col in DAILY_SERIES:
        try:
            s = fetch_daily_series(code, item, start, end, key)
            if not s.empty:
                out[col] = s
        except Exception as e:
            progress(f"[ecos] {code} 수집 실패: {e}")
    if not out:
        return pd.DataFrame()
    df = pd.DataFrame(out).sort_index()
    df.index.name = "date"
    return df.reset_index()
