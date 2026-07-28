# SOTA Evaluation Harness (ML)

> 이름 주의: Cursor **agent** harness는 `docs/AGENT_HARNESS.md`다.  
> 이 문서는 **모델 평가**용 `src/kostolany/harness/` 코드다.

Kostolany Watch의 핵심 차별점은 모델 자체보다 **정직한 평가 하네스**다.

3개월 경로·국면 하이퍼파라미터 고도화 실험 결과(2026-07-27): [`FORECAST_TUNE_EXPERIMENT_2026-07-27.md`](./FORECAST_TUNE_EXPERIMENT_2026-07-27.md).  
상용화 레인 commercial-v5 (2026-07-28): [`COMMERCIAL_MODEL_V5_2026-07-28.md`](./COMMERCIAL_MODEL_V5_2026-07-28.md).

직접 다중지평·다시장 commercial-v4 R&D 결과(2026-07-28):
[`COMMERCIAL_V4_RND_2026-07-28.md`](./COMMERCIAL_V4_RND_2026-07-28.md).

## 설계 원칙

1. **Gold / planted labels는 평가 전용** — `gold_used_in_training=True`면 leakage auditor가 즉시 FAIL.
2. **Purged + Embargo** — 학습 샘플의 라벨 horizon이 테스트 구간과 겹치면 purge, 테스트 직후는 embargo.
3. **Execution lag ≥ 1** — 신호 봉과 동일 봉 체결을 금지.
4. **3층 메트릭**
   - 국면 분류: accuracy / macro-F1 / confusion
   - 전환 감지: lead/lag, window-within-window
   - 경제성: lag·비용 반영 Sharpe / MDD / excess vs buy&hold
5. **Calibration** — Brier, log-loss, ECE를 항상 리포트.

## 모듈 맵

| 파일 | 내용 |
|---|---|
| `harness/cv.py` | `CombinatorialPurgedCV`, `PurgedWalkForward` |
| `harness/leakage.py` | `LeakageAuditor` (critical findings → gate fail) |
| `harness/metrics.py` | 분류·전환·calibration + egg 좌표 |
| `harness/backtest.py` | regime→position 맵 + lag + cost_bps |
| `harness/runner.py` | walk-forward/CPCV 실험 러너, artifact JSON |
| `harness/commercial_eval.py` | 3시장 direct-v4 승격 gate |
| `harness/pooled_forecast.py` | cross-asset 63d head (equity Flows 승격 / `pooled_serve`) |

## 최소 사용 예

```python
from kostolany.harness import ExperimentConfig, ExperimentRunner
from kostolany.models import KostolanyGBM

runner = ExperimentRunner(ExperimentConfig(cv="walkforward", execution_lag=1))

def fit_predict(Xtr, ytr, Xte):
    return KostolanyGBM().fit_predict(Xtr, ytr, Xte)

result = runner.run(X, y_weak, prices, fit_predict, y_gold=y_gold)
assert result.passed_leakage
print(result.metrics["macro_f1"], result.backtest["sharpe"])
```

## 왜 이 순서인가

제안서 §5–§7을 코드로 고정한 것이다.

```
(A) HMM/비지도 구조 → (B) weak label 학습 → (C) gold는 평가만
```

백테스트가 예뻐 보이도록 평가를 느슨하게 두지 않는다. leakage gate를 통과하지 못한 실험은 프로모션하지 말 것.
