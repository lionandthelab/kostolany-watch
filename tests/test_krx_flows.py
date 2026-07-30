"""KRX signed investor-flow extraction: the sign must survive to the features."""

import numpy as np
import pandas as pd

from kostolany.connectors.krx import investor_signed_flows, investor_to_participation
from kostolany.features import FEATURE_SPECS, build_features, model_matrix


def _fake_pykrx_flow(n: int = 260) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-02", periods=n)
    rng = np.random.default_rng(7)
    # pykrx-style Korean column names, signed KRW values
    return pd.DataFrame(
        {
            "기관합계": rng.normal(0, 1e11, n),
            "기타법인": rng.normal(0, 1e10, n),
            "개인": rng.normal(0, 1e11, n),
            "외국인합계": rng.normal(0, 1e11, n),
            "전체": rng.normal(0, 1e11, n),
        },
        index=idx,
    )


def test_fetch_krx_investor_branch_survives_dataframe_return(monkeypatch):
    """`a or b` on a DataFrame raises ValueError — regression for krx.py:148."""
    import kostolany.connectors.krx as krx

    flow = _fake_pykrx_flow(80)
    monkeypatch.setattr(krx, "_investor_flow_pykrx", lambda start: flow)
    monkeypatch.setattr(
        krx,
        "_investor_flow_fdr",
        lambda start: (_ for _ in ()).throw(AssertionError("fallback must not run")),
    )
    idx = flow.index
    close = pd.Series(np.linspace(100, 110, len(idx)), index=idx)
    ohlcv = pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close, "volume": 1000.0},
        index=idx,
    )
    from kostolany.data import MarketData

    monkeypatch.setattr(krx, "_from_fdr", lambda start: MarketData(symbol="KS11", ohlcv=ohlcv))
    monkeypatch.setattr(krx, "write_cache", lambda key, df: None)
    market = krx.fetch_krx(start="2024-01-02", use_cache=False, with_investor=True)
    assert market.extras is not None
    assert {"krx_foreign_net", "krx_inst_net", "krx_retail_net"} <= set(market.extras.columns)


def test_signed_flows_preserve_sign_and_map_columns():
    flow = _fake_pykrx_flow()
    signed = investor_signed_flows(flow)
    assert signed is not None
    assert list(signed.columns) == ["foreign_net", "inst_net", "retail_net"]
    # Sign is preserved exactly — this is the regression the .abs() bug caused.
    pd.testing.assert_series_equal(
        signed["foreign_net"], flow["외국인합계"], check_names=False
    )
    pd.testing.assert_series_equal(signed["inst_net"], flow["기관합계"], check_names=False)
    pd.testing.assert_series_equal(signed["retail_net"], flow["개인"], check_names=False)
    assert (signed["foreign_net"] < 0).any() and (signed["foreign_net"] > 0).any()


def test_participation_proxy_still_magnitude_only():
    flow = _fake_pykrx_flow()
    part = investor_to_participation(flow)
    assert (part.dropna() >= 0).all()


def test_signed_features_are_optional_and_never_in_model_matrix():
    idx = pd.bdate_range("2020-01-01", periods=400)
    rng = np.random.default_rng(3)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(idx)))), index=idx)
    ohlcv = pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": rng.integers(1_000, 5_000, len(idx)).astype(float),
        },
        index=idx,
    )
    extras = pd.DataFrame(
        {
            "krx_foreign_net": rng.normal(0, 1e11, len(idx)),
            "krx_inst_net": rng.normal(0, 1e11, len(idx)),
            "krx_retail_net": rng.normal(0, 1e11, len(idx)),
        },
        index=idx,
    )

    feats = build_features(ohlcv, extras)
    for col in ("foreign_net_z", "inst_net_z", "retail_net_z"):
        assert col in feats.columns
        assert feats[col].abs().max() <= 8.0 + 1e-9

    # Serving models must not see them until the gated experiment promotes them.
    spec_names = {f.name for f in FEATURE_SPECS}
    assert not spec_names & {"foreign_net_z", "inst_net_z", "retail_net_z"}
    X = model_matrix(feats)
    assert not set(X.columns) & {"foreign_net_z", "inst_net_z", "retail_net_z"}

    # Without extras the columns simply do not exist — no NaN poisoning.
    feats_plain = build_features(ohlcv, None)
    assert "foreign_net_z" not in feats_plain.columns
