"""Build the single-page local research dashboard (research/dashboard.html).

Harvests everything the research loop looks at — data inventory, feature/label
diagnostics, measured walk-forward performance, serving snapshots — into one
self-contained HTML file with embedded JSON. No server, no network at render
time. Rebuild whenever artifacts change:

    .\\.venv\\Scripts\\python.exe scripts\\build_research_dashboard.py
    .\\.venv\\Scripts\\python.exe scripts\\build_research_dashboard.py --no-fit  # skip model fits (fast)

Honesty rules baked in:
  - OOS (walk-forward) numbers and in-sample serving snapshots are labelled as
    such and never mixed in one chart.
  - gold labels appear ONLY in evaluation panels, marked "EVAL ONLY (미래 참조)".
  - Unmeasured = shown as unmeasured, never imputed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CACHE_DIR = ROOT / "artifacts" / "cache"
EXP_DIR = ROOT / "artifacts" / "experiments"
OUT_DIR = ROOT / "research"

SERVED_MARKETS = ("^GSPC", "BTC-USD")
RESEARCH_MARKETS: tuple[str, ...] = ()  # KS11 retired 2026-07-30 (owner decision)


# ------------------------------------------------------------------ inventory


def harvest_inventory() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not CACHE_DIR.exists():
        return rows
    for p in sorted(CACHE_DIR.iterdir()):
        if not p.is_file():
            continue
        row: dict[str, Any] = {
            "file": p.name,
            "kind": p.suffix.lstrip("."),
            "size_kb": round(p.stat().st_size / 1024, 1),
            "modified": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            "rows": None,
            "range": None,
            "columns": None,
            "note": None,
        }
        try:
            if p.suffix == ".parquet":
                df = pd.read_parquet(p)
                row["rows"] = int(len(df))
                if isinstance(df.index, pd.DatetimeIndex) and len(df):
                    row["range"] = f"{df.index[0].date()} → {df.index[-1].date()}"
                row["columns"] = list(map(str, df.columns[:20]))
            elif p.suffix == ".json":
                blob = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(blob, dict):
                    keys = list(blob.keys())[:12]
                    row["columns"] = keys
                    for stamp_key in ("built_at", "asof", "generated_at"):
                        if stamp_key in blob:
                            row["note"] = f"{stamp_key}={blob[stamp_key]}"
                            break
        except Exception as exc:  # noqa: BLE001
            row["note"] = f"read error: {exc}"
        rows.append(row)
    return rows


# ---------------------------------------------------------------- diagnostics


def _effective_rank(X: pd.DataFrame) -> float:
    corr = X.corr().to_numpy()
    corr = np.nan_to_num(corr, nan=0.0)
    eig = np.linalg.eigvalsh(corr)
    eig = np.clip(eig, 1e-12, None)
    p = eig / eig.sum()
    return float(np.exp(-(p * np.log(p)).sum()))


def harvest_market_diagnostics(symbol: str, *, fit_models: bool) -> dict[str, Any]:
    """Feature/label/gauge diagnostics + optional in-sample serving snapshot."""
    from kostolany.connectors import load_market
    from kostolany.engine import fit_analyst_bundle, prepare_xy
    from kostolany.features import gauge_scores
    from kostolany.labels import gold_labels

    t0 = time.time()
    out: dict[str, Any] = {"symbol": symbol, "error": None}
    try:
        market = load_market(symbol, start="2010-01-01", enrich_fred=True)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"load failed: {exc}"
        return out

    X, y_weak, y_gold, prices = prepare_xy(market)
    valid = X.dropna().index.intersection(y_weak.dropna().index)
    Xv = X.loc[valid]
    yw = y_weak.loc[valid].astype(int)
    yg = y_gold.reindex(valid)

    # Label distribution + weak/gold agreement (gold = EVAL ONLY)
    regime_names = ["A1", "A2", "A3", "B1", "B2", "B3"]

    def _named_dist(s: pd.Series) -> dict[str, float]:
        d = s.value_counts(normalize=True).sort_index()
        return {regime_names[int(k)]: round(float(v), 4) for k, v in d.items()}

    weak_dist = _named_dist(yw)
    gold_valid = yg.dropna().astype(int)
    agree = float((yw.reindex(gold_valid.index) == gold_valid).mean()) if len(gold_valid) else None
    gold_dist = _named_dist(gold_valid) if len(gold_valid) else {}

    # Feature degeneracy
    er = _effective_rank(Xv)
    corr = Xv.corr().abs()
    np.fill_diagonal(corr.values, 0.0)
    top_pairs = (
        corr.where(np.triu(np.ones(corr.shape, dtype=bool), 1))
        .stack()
        .sort_values(ascending=False)
        .head(8)
    )

    # Price + gold leg overlay (weekly downsample keeps the page light)
    px = prices.reindex(valid).dropna()
    weekly = px.resample("W-FRI").last().dropna()
    gold_w = yg.reindex(px.index).ffill().resample("W-FRI").last()
    series = [
        {"date": str(ts.date()), "close": round(float(v), 2), "gold": (int(g) if pd.notna(g) else None)}
        for ts, v, g in zip(weekly.index, weekly.values, gold_w.reindex(weekly.index).values)
    ]

    gauges_last = gauge_scores(X).iloc[-1].to_dict()

    out.update(
        {
            "n_rows": int(len(Xv)),
            "range": f"{valid[0].date()} → {valid[-1].date()}",
            "n_features_model": int(Xv.shape[1]),
            "effective_rank": round(er, 2),
            "weak_dist": weak_dist,
            "gold_dist": gold_dist,
            "weak_gold_agreement": None if agree is None else round(agree, 4),
            "top_corr_pairs": [
                {"a": str(a), "b": str(b), "corr": round(float(v), 3)}
                for (a, b), v in top_pairs.items()
            ],
            "price_weekly": series,
            "gauges_last": {k: round(float(v), 3) for k, v in gauges_last.items()},
            "extra_columns": (
                list(map(str, market.extras.columns)) if market.extras is not None else []
            ),
        }
    )

    if fit_models:
        try:
            engines = fit_analyst_bundle(symbol)
            snaps = {}
            for mid, eng in engines.items():
                s = eng.snapshot()
                snaps[mid] = {
                    "asof": s.asof,
                    "regime": s.regime,
                    "confidence": round(s.confidence, 4),
                    "probabilities": {k: round(v, 4) for k, v in s.probabilities.items()},
                }
            out["serving_snapshot"] = {
                "in_sample": True,  # fit on full X, predicted on same X — labelled honestly
                "analysts": snaps,
            }
        except Exception as exc:  # noqa: BLE001
            out["serving_snapshot"] = {"error": str(exc)}

    out["elapsed_sec"] = round(time.time() - t0, 1)
    return out


# ---------------------------------------------------------------- experiments


def harvest_experiments() -> list[dict[str, Any]]:
    """Parse phase_head_*.json artifacts into chart-ready records."""
    runs: list[dict[str, Any]] = []
    for p in sorted(EXP_DIR.glob("phase_head_*.json")):
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        c = blob.get("constraints", {})
        cands = blob.get("candidates", {})
        rows = []
        for name, r in cands.items():
            ci = r.get("ci95_leg_bootstrap", {})
            rows.append(
                {
                    "name": name,
                    "exact6": r.get("exact6"),
                    "exact6_ci": ci.get("exact6"),
                    "adjacent": r.get("adjacent"),
                    "adjacent_ci": ci.get("adjacent"),
                    "cyc_dist": r.get("mean_cycle_distance"),
                    "cyc_dist_ci": ci.get("mean_cycle_distance"),
                    "macro_f1": r.get("macro_f1"),
                    "brier": r.get("brier"),
                    "brier_ci": ci.get("brier"),
                    "ece": r.get("ece"),
                    "ece_ci": ci.get("ece"),
                    "factorisation": r.get("factorisation"),
                }
            )
        runs.append(
            {
                "artifact": p.name,
                "symbol": blob.get("symbol"),
                "asof": blob.get("asof"),
                "n_oos": c.get("n_oos_rows_scored"),
                "n_legs": c.get("n_oos_legs"),
                "folds": len(c.get("folds", [])),
                "min_train": (c.get("folds") or [{}])[0].get("n_train"),
                "serving_arms_included": c.get("serving_arms_included", False),
                "enrich_fred": c.get("enrich_fred", False),
                "candidates": rows,
                "warnings": blob.get("warnings", []),
                "paired": blob.get("paired_leg_bootstrap", {}),
            }
        )
    return runs


def harvest_consensus() -> list[dict[str, Any]]:
    """Parse consensus_eval_*.json — the first-ever scores of the shipped number."""
    runs: list[dict[str, Any]] = []
    for p in sorted(EXP_DIR.glob("consensus_eval_*.json")):
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        runs.append(
            {
                "artifact": p.name,
                "asof": blob.get("asof"),
                "constraints": blob.get("constraints", {}),
                "preregistration": blob.get("preregistration", {}),
                "markets": blob.get("markets", {}),
                "errors": blob.get("errors", {}),
            }
        )
    return runs


# -------------------------------------------------------------------- decide


def research_log() -> dict[str, Any]:
    """Structured research decisions — the narrative the charts hang off."""
    return {
        "decisions": [
            {
                "date": "2026-07-28",
                "what": "commercial-v5: 실패한 direction_skill 게이트를 코드에서 삭제해 통과로 만든 패턴 적발",
                "action": "게이트 복원(direction_skill_vs_trivial), *0.98 여유 삭제, 스킵=하드실패",
                "status": "done",
            },
            {
                "date": "2026-07-29",
                "what": "PhaseHead: S2 사전등록 게이트 5/5 미달 (exact6 0.214<0.29, ECE 0.19>0.07 등)",
                "action": "승격 거부. 연구 레인에만 보존 (src/kostolany/phase.py)",
                "status": "done",
            },
            {
                "date": "2026-07-30",
                "what": "워치 시장에서 KS11 제외 (^GSPC, BTC-USD만 서빙)",
                "action": "서빙 시장에서 실측 없는 KS11 성적표 표시 문제 → 서빙 팔 3종을 ^GSPC·BTC-USD에서 재측정",
                "status": "in_progress",
            },
            {
                "date": "2026-07-30",
                "what": "krx.py .abs()가 유일한 실물 수급 데이터(투자자별 순매수)의 부호 파괴",
                "action": "부호 있는 foreign/inst/retail_net 방출 + 캐시 아카이브 시작. 모델 투입은 게이트 통과 후",
                "status": "done",
            },
            {
                "date": "2026-07-30",
                "what": "오너 결정: KS11 완전 제외 (채점·Flows kospi 섹터 포함)",
                "action": "SECTORS/기본값/대시보드에서 제거. KRX 커넥터는 연구 데이터 레인으로만 유지",
                "status": "done",
            },
            {
                "date": "2026-07-30",
                "what": "성능 제한 원인 분석: 자유도 1개(side)·입력 축퇴·보정 붕괴 (PERFORMANCE_LIMITS_2026-07-30.md)",
                "action": "side를 움직일 직교 데이터(횡단면 폭) 사전등록 실험 P-XS-1 실행",
                "status": "done",
            },
            {
                "date": "2026-07-30",
                "what": "L0 발견(4설계×3적대검증 워크플로우 + 로컬 재검증): 무적합 규칙 종가>MA60의 gold-side가 ^GSPC 0.717/BTC 0.702 — 모든 학습 헤드(0.59~0.68)를 8~12%p 이김",
                "action": "'라벨 상한'·'prior_shrunk 기준선' 프레임 폐기 (uniform 5/36이 진짜 플로어). SideHead 프로그램 사전등록 (research/sota_design.md G1~G18, 킬조건 K1~K8)",
                "status": "done",
            },
            {
                "date": "2026-07-30",
                "what": "SideHead 프로그램 실행 (사전등록 git 0f6c12e): 12종목 패널에서 K1·K2·K3 발동 — 학습 헤드(0.6245)가 무적합 패밀리 중앙값(0.6713)에 패배",
                "action": "K2 이행: side.py 삭제, MomoFloorHead(무적합, model_kind=momo) 등록. side_panel_20260730T025704Z.json",
                "status": "done",
            },
            {
                "date": "2026-07-30",
                "what": "S0 수정 후 재앵커: G12 통과(하락≤0.010, HMM은 +0.019 개선). λ-uniform 앵커로 HMM ECE 0.73→0.17/0.11, Brier 0.25→0.144. G14는 gbm/tsfm 미달로 전체 FAIL",
                "action": "K7: confidence_is_calibrated 영구 false. calibration.py를 앵커 적용 실측으로 갱신",
                "status": "done",
            },
            {
                "date": "2026-07-30",
                "what": "국면별 63d 전방수익 적합: 어느 국면도 CI-유의 음의 드리프트 없음 (SPY는 하락국면도 +3.5~5.0%)",
                "action": "flows 드리프트 테이블을 적합값으로 교체(B*=0), flat 아웃룩(|Δ|<0.75%), 상수팔에 무조건부 밴드 명시. regime_drift_fit_20260730.json",
                "status": "done",
            },
            {
                "date": "2026-07-30",
                "what": "오너 결정: momo 전면화. 측정일치형 사후확률의 momo가 Brier 0.1338/0.1343으로 uniform(0.1389)을 양 시장에서 이김 — 프로젝트 첫 uniform 초과, ECE 0.005~0.009",
                "action": "momo를 기본 헤드로(WATCH_DEFAULT_MODELS 선두, 번들 포함, UI 고정 포커스). S4: 달걀 호(원형 분산) + 확신도 자동포커스 제거. 실서버 E2E 확인",
                "status": "done",
            },
        ],
        "preregistrations": [
            {
                "id": "P-CONS-1",
                "claim": "Flows 컨센서스(화면 도달값)의 진폭비 ≈ 0.4, skill_vs_trivial < 0 (과반 시장)",
                "test": "assemble_paths(서빙과 동일 함수) walk-forward origin 채점",
                "status": "확증 — SPY 스킬 −0.327/진폭비 0.205, BTC −0.043/0.070 (2/2 시장). "
                "예측보다 더 심함. consensus_eval_20260730T021223Z.json → 대응은 SOTA 설계 S3-2(평면 경로+밴드)",
            },
            {
                "id": "P-KRX-1",
                "claim": "부호 있는 KRX 순매수 3피처의 up-leg ΔAUC ≥ +0.040 (KS11)",
                "test": "셔플 카나리아 + as-of 역전 테스트 통과 필수. 미달 시 피처 삭제(플래그 아님)",
                "status": "보류 — KS11 제외로 서빙 표면 없음 (데이터 아카이브는 계속)",
            },
            {
                "id": "P-XS-1",
                "claim": "미국 횡단면 비율 폭 10피처의 gold-side ΔAUC ≥ +0.030, CI 0 배제 (^GSPC)",
                "test": "셔플 카나리아 + 정렬 프로브 필수. docs/XS_BREADTH_PREREG_2026-07-30.md",
                "status": "기각 — Δ −0.002, 셔플이 인과보다 좋음(노이즈). 블록 삭제. "
                "xs_breadth_GSPC_20260730T015233Z.json",
            },
            {
                "id": "P-S0-2",
                "claim": "S0 커버리지: 서빙 시장 풀링 기준 독립 레그 ≥50 (시장·레그 이중 블록 부트스트랩)",
                "test": "^GSPC + BTC-USD 실측 레그 수 합산으로 판정",
                "status": "충족 — 37+42=79 ≥ 50",
            },
            {
                "id": "side_head_v1",
                "claim": "G1~G18 게이트 + K1~K8 킬조건 (git 0f6c12e에 사전 고정)",
                "test": "12종목 패널, 2단 페어드 부트스트랩, Bonferroni z=3.20",
                "status": "판정 완료 — K1·K2·K3 발동 → 무적합 플로어 쉽(G10 클럭만 통과). "
                "G12 통과, G13 500/500, G14 실패(K7). side_panel_20260730T025704Z.json",
            },
        ],
        "constraints": [
            "gold/planted 라벨은 평가 전용 — fit 입력 금지",
            "execution_lag ≥ 1",
            "면책 문구 상시 유지",
            "scripts/agent_verify.py 통과 없이 출고 금지",
            {
                "date": "2026-07-30",
                "what": "오너 문제 제기: '확신도 25%는 너무 낮다' → 9-에이전트 분석팀(5설계×3교차검증×종합). 진단: 25%는 가장 어려운 문장(정확 칸)의 확신 — 시스템의 강한 문장(만장일치 방향 80%, ±1칸 64%)을 버리고 있었음",
                "action": "확신 커뮤니케이션 시스템 출고 (research/confidence_spec.md): 8규칙 투표 등급(만장일치80%/강한우세67%/우세60%/혼조55%, 실측 조건부 테이블·파라미터 0) + 3단 주장 사다리 + 규칙 원장 + 달걀 사이드/존 밴드. '확신도 N%' 헤드라인 은퇴, 금지 문장 17종 카피 가드 테스트로 강제. confidence_menu_20260730.json",
                "status": "done",
            },
        ],
    }


# --------------------------------------------------------------------- html


def build_html(payload: dict[str, Any]) -> str:
    data_json = json.dumps(payload, ensure_ascii=False)
    template = (ROOT / "scripts" / "research_dashboard_template.html").read_text(encoding="utf-8")
    return template.replace("/*__DATA__*/null", data_json)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fit", action="store_true", help="Skip model fits (fast rebuild)")
    args = ap.parse_args()

    payload: dict[str, Any] = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "served_markets": list(SERVED_MARKETS),
        "research_markets": list(RESEARCH_MARKETS),
        "inventory": harvest_inventory(),
        "experiments": harvest_experiments(),
        "consensus": harvest_consensus(),
        "research_log": research_log(),
        "markets": {},
    }
    sota = OUT_DIR / "sota_design.md"
    if sota.exists():
        payload["sota_design_md"] = sota.read_text(encoding="utf-8")
    for sym in (*SERVED_MARKETS, *RESEARCH_MARKETS):
        print(f"[diagnostics] {sym} ...", flush=True)
        payload["markets"][sym] = harvest_market_diagnostics(sym, fit_models=not args.no_fit)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "dashboard.html"
    out.write_text(build_html(payload), encoding="utf-8")
    print(f"Saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
