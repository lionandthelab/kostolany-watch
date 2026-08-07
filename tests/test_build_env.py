"""What CI ships must not depend on an untracked file.

`web/.env` is gitignored and CI publishes nightly (`daily-publish.yml` builds and
deploys Hosting on a schedule). Any `VITE_*` value that lives only there is
absent from the bundle CI produces — and the failure is silent, because every
consumer treats a missing value as "feature off" rather than as an error.

That is not hypothetical. On 2026-08-07 the live bundle contained neither the
GA measurement ID nor the AdSense client: `analytics.ts` had been reading an
empty string since the switch to CI publishing, so `trackPageView` and
`trackEvent` were no-ops and GA4 recorded only the default hit from the inline
tag in index.html.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
ENV_PRODUCTION = WEB / ".env.production"

#: Consumed at build time and required for the shipped bundle to behave like the
#: developer's local one. Add to this list whenever a new `VITE_*` gates a
#: user-visible feature.
REQUIRED_KEYS = (
    "VITE_GA_MEASUREMENT_ID",
    "VITE_ADSENSE_CLIENT",
    "VITE_ADSENSE_SLOT",
    "VITE_ADSENSE_APPROVED",
)


def _parse_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def test_env_production_is_tracked_so_ci_builds_match_local_ones() -> None:
    assert ENV_PRODUCTION.exists(), (
        "web/.env.production is missing — CI builds would ship with GA and "
        "AdSense silently disabled"
    )
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
    assert ".env.production" not in gitignore and "web/.env.production" not in gitignore, (
        "web/.env.production is gitignored — CI cannot see it, which is the "
        "whole reason the file exists"
    )


@pytest.mark.parametrize("key", REQUIRED_KEYS)
def test_build_time_key_is_committed(key: str) -> None:
    env = _parse_env(ENV_PRODUCTION)
    assert env.get(key), f"{key} is absent from web/.env.production"


def test_adsense_values_satisfy_the_gate_in_adslot() -> None:
    """AdSlot.tsx collapses the unit unless all three hold — assert them here.

    A typo in any one of these renders nothing at all and reports no error, so
    the shape is worth pinning even though the values themselves may change.
    """
    env = _parse_env(ENV_PRODUCTION)
    assert env["VITE_ADSENSE_APPROVED"].lower() in {"1", "true", "yes"}
    assert env["VITE_ADSENSE_CLIENT"].startswith("ca-pub-")
    assert re.fullmatch(r"\d+", env["VITE_ADSENSE_SLOT"]), "slot must be digits only"


def test_no_secret_shaped_key_leaked_into_the_committed_env() -> None:
    """Only public `VITE_*` values belong here; Vite inlines them into the bundle."""
    env = _parse_env(ENV_PRODUCTION)
    for key in env:
        assert key.startswith("VITE_"), (
            f"{key} is not a VITE_ build constant — it does not belong in a "
            "committed env file"
        )
        assert not re.search(r"SECRET|PRIVATE|PASSWORD|TOKEN", key, re.I), (
            f"{key} looks like a credential; keep it in .env or Cloud Run env"
        )


def test_index_html_and_env_agree_on_the_public_ids() -> None:
    """index.html hardcodes the same two IDs. Two sources must not drift apart."""
    env = _parse_env(ENV_PRODUCTION)
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert env["VITE_ADSENSE_CLIENT"] in html, (
        "the AdSense client in .env.production is not the one index.html declares "
        "in its google-adsense-account meta"
    )
    assert env["VITE_GA_MEASUREMENT_ID"] in html, (
        "the GA id in .env.production is not the one index.html's gtag tag loads"
    )
