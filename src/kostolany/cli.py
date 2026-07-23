"""CLI for Kostolany Watch demos and serving."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from kostolany.data import make_synthetic
from kostolany.engine import KostolanyEngine, prepare_xy
from kostolany.harness.runner import ExperimentConfig, ExperimentRunner
from kostolany.models import EnsembleEngine, KostolanyHMM
from kostolany.regimes import REGIME_META, Regime

app = typer.Typer(help="Kostolany Watch - regime engine + SOTA harness")
console = Console(force_terminal=False, legacy_windows=False)


@app.command()
def demo(
    n: int = typer.Option(2000, help="Synthetic bars"),
    model: str = typer.Option("gbm", help="hmm|gbm|ensemble|tsfm|ensemble_v3"),
    cv: str = typer.Option("walkforward", help="walkforward|cpcv"),
) -> None:
    """Run synthetic end-to-end demo with harness evaluation."""
    console.print("[bold]Kostolany Watch - synthetic demo[/bold]")
    market, planted = make_synthetic(n=n, seed=7)
    X, _y_weak, _y_gold, prices = prepare_xy(market)

    def fit_predict(Xtr, ytr, Xte):
        if model in {"tsfm", "ensemble_v3"}:
            from kostolany.tsfm import TSFMEnsemble

            m = TSFMEnsemble()
        elif model == "ensemble":
            m = EnsembleEngine()
        elif model == "gbm":
            from kostolany.models import KostolanyGBM

            m = KostolanyGBM()
        else:
            m = KostolanyHMM()
        return m.fit_predict(Xtr, ytr, Xte)

    runner = ExperimentRunner(
        ExperimentConfig(
            name=f"synth_{model}_{cv}",
            cv=cv,
            n_splits=4,
            purge_horizon=5,
            embargo=5,
            execution_lag=1,
            output_dir="artifacts/experiments",
        )
    )

    # Synthetic weak labels = noisy observation of planted DGP state (causal, no lookahead).
    # Real markets use rule-based weak_labels(); planted/gold remain eval-only.
    rng = __import__("numpy").random.default_rng(7)
    y_train = planted.reindex(X.index).copy()
    flip = rng.random(len(y_train)) < 0.22
    y_train.iloc[flip] = rng.integers(0, 6, size=int(flip.sum()))
    y_train = y_train.astype(int)

    result = runner.run(
        X,
        y_train,
        prices,
        fit_predict,
        y_gold=planted.reindex(X.index),
        gold_used_in_training=False,
    )

    table = Table(title="Harness OOS Summary")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("Leakage gate", "PASS" if result.passed_leakage else "FAIL")
    table.add_row("Accuracy", f"{result.metrics['accuracy']:.3f}")
    table.add_row("Macro F1", f"{result.metrics['macro_f1']:.3f}")
    table.add_row("ECE", f"{result.metrics['calibration']['ece']:.3f}")
    table.add_row("Transition hit", f"{result.metrics['transition']['hit_within_window']:.3f}")
    table.add_row("Sharpe", f"{result.backtest['sharpe']:.3f}")
    table.add_row("PSR", f"{result.backtest.get('probabilistic_sharpe', float('nan')):.3f}")
    table.add_row("DSR", f"{result.backtest.get('deflated_sharpe', float('nan')):.3f}")
    table.add_row("MDD", f"{result.backtest['max_drawdown']:.3f}")
    table.add_row("Excess vs B&H", f"{result.backtest['excess_return']:.3f}")
    console.print(table)

    eng = KostolanyEngine(model_kind=model)  # type: ignore[arg-type]
    eng.fit_market(market)
    snap = eng.snapshot()
    meta = REGIME_META[Regime[snap.regime]]
    console.print(
        f"\nNow: [bold {meta.color}]{snap.regime} {snap.regime_name_ko}[/] "
        f"(conf={snap.confidence:.0%}) → {snap.action_ko}"
    )
    console.print(f"[dim]{snap.disclaimer}[/dim]")
    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/last_snapshot.json").write_text(
        json.dumps(snap.__dict__, indent=2, ensure_ascii=False), encoding="utf-8"
    )


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Start FastAPI inference server."""
    import uvicorn

    uvicorn.run("kostolany.api:app", host=host, port=port, reload=reload)


@app.command()
def snapshot(symbol: str = "SYNTH", model: str = "ensemble") -> None:
    """Print current regime snapshot for a symbol."""
    eng = KostolanyEngine(model_kind=model)  # type: ignore[arg-type]
    if symbol.upper() in {"SYNTH", "SYNTHETIC"}:
        eng.fit_synthetic()
    else:
        eng.fit_symbol(symbol)
    snap = eng.snapshot()
    console.print_json(json.dumps(snap.__dict__, ensure_ascii=False))


@app.command("fetch-data")
def fetch_data(
    symbol: str = typer.Option("KS11", help="KS11 | ^GSPC | ticker"),
    start: str = typer.Option("2015-01-01"),
) -> None:
    """Fetch & cache KRX/Yahoo + FRED extras."""
    from kostolany.connectors import load_market

    market = load_market(symbol, start=start, enrich_fred=True)
    n = len(market.ohlcv)
    extra_cols = list(market.extras.columns) if market.extras is not None else []
    console.print(f"Loaded {market.symbol}: {n} bars, extras={extra_cols}")


@app.command()
def replay(
    symbol: str = "SYNTH",
    model: str = "hmm",
    limit: int = 20,
) -> None:
    """Print last N egg replay frames."""
    eng = KostolanyEngine(model_kind=model)  # type: ignore[arg-type]
    if symbol.upper() in {"SYNTH", "SYNTHETIC"}:
        eng.fit_synthetic(n=800)
    else:
        eng.fit_symbol(symbol)
    payload = eng.replay_dict(limit=limit, stride=max(1, limit // 20))
    console.print_json(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    app()
