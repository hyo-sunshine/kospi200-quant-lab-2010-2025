# -*- coding: utf-8 -*-
"""
predict.py — 최신 날짜의 상위 8종목 출력 (실운용 인퍼런스)
============================================================
models/ 의 실운용 가중치 18개를 로드해 패널 최신 날짜의 매수 종목을 출력한다.

    python predict.py                          # 신규 구성
    python predict.py --held 005930,000660,... # 보유 8종목 기준 유지/매도/신규 지시
"""
import argparse
import json

import numpy as np
import pandas as pd
import lightgbm as lgb

from common import (MODELS_DIR, PANEL, SEEDS, SMOOTH, W60, N_HOLD, BUF_MULT,
                    load_feature_panel, log)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default=str(PANEL))
    ap.add_argument("--held", default="", help="현재 보유 종목코드 콤마 구분")
    args = ap.parse_args()

    feats = json.loads((MODELS_DIR / "features.json").read_text())
    log("패널 로드 & 피처 생성")
    df = load_feature_panel(with_labels=False)

    # 평활 20일 + 여유를 위해 최근 300거래일만 스코어링
    recent = sorted(df["date"].unique())[-300:]
    pool = df.loc[df["date"].isin(recent)
                  & (df["is_pit_universe_kospi200"] == 1)
                  & (df["is_volume_zero"] == 0)
                  & df["mom_12_1"].notna()].copy()

    sig = {}
    for tag in ["flow60", "flow20"]:
        ranks = []
        for s in SEEDS:
            booster = lgb.Booster(model_file=str(MODELS_DIR / f"prod_{tag}_seed{s}.txt"))
            pool["score"] = booster.predict(pool[feats[tag]])
            m = (pool.pivot_table(index="date", columns="ticker", values="score")
                 .rolling(SMOOTH, min_periods=5).mean())
            ranks.append(m.rank(axis=1, pct=True))
        sig[tag] = sum(ranks) / len(ranks)

    signal = W60 * sig["flow60"] + (1 - W60) * sig["flow20"]
    last = signal.index[-1]
    s = signal.loc[last].dropna().sort_values(ascending=False)
    print(f"\n기준일: {last.date()}  (유니버스 {len(s)}종목)")
    print("\n상위 12종목 (신호 순):")
    for i, (t, v) in enumerate(s.head(12).items(), 1):
        print(f"  {i:2d}. {t}  score={v:.4f}")

    held = [t.strip() for t in args.held.split(",") if t.strip()]
    if held:
        rank_of = {t: r for r, t in enumerate(s.index, 1)}
        keep = [t for t in held if rank_of.get(t, 10 ** 9) <= BUF_MULT * N_HOLD]
        sell = [t for t in held if t not in keep]
        new = [t for t in s.index if t not in keep][:N_HOLD - len(keep)]
        print(f"\n버퍼 규칙 적용 (보유 {len(held)}종목 기준):")
        print(f"  유지: {keep}")
        print(f"  매도: {sell}  (랭크 {BUF_MULT * N_HOLD}위 밖)")
        print(f"  신규: {new}")
    else:
        print(f"\n신규 구성 시 매수: {list(s.head(N_HOLD).index)}  (각 12.5%)")


if __name__ == "__main__":
    main()
