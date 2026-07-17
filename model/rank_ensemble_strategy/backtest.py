# -*- coding: utf-8 -*-
"""
backtest.py — 최종 전략 백테스트 + 강건성 검증
================================================
저장된 OOS 스코어로 전략 성과를 재현한다 (재학습 불필요, 약 1분).
  1) 최종 구성 성과 (2013~2025)
  2) 거래비용 민감도 (10/20/50bp)
  3) 하위기간 3구간
  4) 개별 시드 분해 (앙상블이 요행이 아닌지)
결과: backtest_result.json + 콘솔 출력

    python backtest.py
"""
import json

import numpy as np
import pandas as pd

from common import (PKG, SEEDS, BASE_COST, EVAL_START, load_market_matrices,
                    build_signal, backtest, metrics, log)

SUBPERIODS = [("2013-01-01", "2016-12-31"), ("2017-01-01", "2020-12-31"),
              ("2021-01-01", "2025-12-31")]


def main():
    log("데이터 로드")
    mats = load_market_matrices()
    dates, columns = mats[0].index, mats[0].columns
    kospi = mats[4]

    log("신호 생성 (9시드 랭크앙상블)")
    signal = build_signal(dates, columns)

    result = {}

    log("=== 1) 최종 구성 성과 ===")
    nav = backtest(mats, signal)
    result["strategy"] = metrics(nav, "전략 (10bp)")
    knav = np.exp(kospi[dates >= EVAL_START].cumsum())
    knav = pd.Series(knav.values, index=dates[dates >= EVAL_START])
    result["kospi200"] = metrics(knav, "KOSPI200")
    for k in ["strategy", "kospi200"]:
        m = result[k]
        log(f"  {m['label']:12s}: CAGR {m['cagr_pct']:5.2f}% 누적 {m['cum_pct']:+7.1f}% "
            f"5y중앙 {m.get('median_5y_pct', 0):+6.1f}% 최악5y {m.get('min_5y_pct', 0):+6.1f}% "
            f"MDD {m['mdd_pct']:+.1f}%")

    log("=== 2) 비용 민감도 ===")
    result["costs"] = {}
    for bp in [10, 20, 50]:
        m = metrics(backtest(mats, signal, cost=bp / 10000.0), f"{bp}bp")
        result["costs"][f"{bp}bp"] = m
        log(f"  {bp}bp: CAGR {m['cagr_pct']:5.2f}% 5y중앙 {m.get('median_5y_pct', 0):+6.1f}%")

    log("=== 3) 하위기간 ===")
    result["subperiods"] = {}
    for s, e in SUBPERIODS:
        nav_s = backtest(mats, signal, start=pd.Timestamp(s), end=pd.Timestamp(e))
        span = (dates >= s) & (dates <= e)
        kospi_cum = float(np.exp(kospi[span].sum()) - 1) * 100
        m = metrics(nav_s, f"{s[:4]}-{e[:4]}")
        m["excess_vs_kospi_pt"] = round(m["cum_pct"] - kospi_cum, 1)
        result["subperiods"][m["label"]] = m
        log(f"  {m['label']}: 누적 {m['cum_pct']:+7.1f}% (지수 대비 {m['excess_vs_kospi_pt']:+.1f}pt)")

    log("=== 4) 개별 시드 분해 ===")
    result["per_seed"] = []
    meds = []
    for s in SEEDS:
        m = metrics(backtest(mats, build_signal(dates, columns, seeds=[s])),
                    f"seed={s}")
        result["per_seed"].append(m)
        meds.append(m.get("median_5y_pct", 0))
        log(f"  seed={s}: 5y중앙 {m.get('median_5y_pct', 0):+7.1f}% CAGR {m['cagr_pct']:5.2f}%")
    result["per_seed_summary"] = {"min": min(meds), "mean": round(float(np.mean(meds)), 1),
                                  "max": max(meds)}
    log(f"  (앙상블이 개별시드 최고치보다 낮고 평균보다 약간 높으면 정상)")

    out = PKG / "backtest_result.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    log(f"저장 → {out}")


if __name__ == "__main__":
    main()
