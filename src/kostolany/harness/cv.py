"""Combinatorial Purged Cross-Validation and purged walk-forward splits.

Implements the López de Prado purged/embargoed CV pattern for financial
time series, preventing label/feature leakage across train/test boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Iterator

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CVFold:
    fold_id: int
    train_idx: np.ndarray
    test_idx: np.ndarray
    purged_count: int
    embargo_count: int


def _as_positions(index: pd.Index, idx: np.ndarray) -> np.ndarray:
    return np.asarray(idx, dtype=int)


class CombinatorialPurgedCV:
    """Combinatorial Purged CV (CPCV) with embargo.

    Splits the timeline into ``n_groups`` contiguous groups. Each fold uses
    ``n_test_groups`` groups as test and the rest as train, then:
      1) Purges train samples whose *label horizon* overlaps the test set
      2) Embargoes a fraction of samples immediately after each test block
    """

    def __init__(
        self,
        n_groups: int = 6,
        n_test_groups: int = 2,
        purge_horizon: int = 5,
        embargo_pct: float = 0.01,
    ) -> None:
        if n_test_groups >= n_groups:
            raise ValueError("n_test_groups must be < n_groups")
        if not 0.0 <= embargo_pct < 0.5:
            raise ValueError("embargo_pct must be in [0, 0.5)")
        self.n_groups = n_groups
        self.n_test_groups = n_test_groups
        self.purge_horizon = max(0, int(purge_horizon))
        self.embargo_pct = float(embargo_pct)

    def _group_bounds(self, n: int) -> list[tuple[int, int]]:
        edges = np.linspace(0, n, self.n_groups + 1, dtype=int)
        return [(int(edges[i]), int(edges[i + 1])) for i in range(self.n_groups)]

    def split(self, X: pd.DataFrame | np.ndarray | pd.Index) -> Iterator[CVFold]:
        n = len(X)
        if n < self.n_groups * 3:
            raise ValueError(f"Need more samples for CPCV (got {n})")

        bounds = self._group_bounds(n)
        embargo = max(1, int(n * self.embargo_pct)) if self.embargo_pct > 0 else 0
        fold_id = 0

        for test_groups in combinations(range(self.n_groups), self.n_test_groups):
            test_mask = np.zeros(n, dtype=bool)
            for g in test_groups:
                a, b = bounds[g]
                test_mask[a:b] = True

            train_mask = ~test_mask
            purged = 0
            embargoed = 0

            # Purge: remove train samples within purge_horizon before each test start
            # and whose forward horizon would touch test.
            test_idx = np.flatnonzero(test_mask)
            if len(test_idx):
                # contiguous test blocks
                cuts = np.where(np.diff(test_idx) > 1)[0]
                starts = [test_idx[0], *[test_idx[i + 1] for i in cuts]]
                ends = [*[test_idx[i] for i in cuts], test_idx[-1]]
                for start, end in zip(starts, ends):
                    purge_lo = max(0, start - self.purge_horizon)
                    purge_hi = start
                    block = train_mask[purge_lo:purge_hi].copy()
                    purged += int(block.sum())
                    train_mask[purge_lo:purge_hi] = False

                    # Embargo immediately after test block
                    emb_lo = end + 1
                    emb_hi = min(n, end + 1 + embargo)
                    embargoed += int(train_mask[emb_lo:emb_hi].sum())
                    train_mask[emb_lo:emb_hi] = False

            train_idx = np.flatnonzero(train_mask)
            test_idx = np.flatnonzero(test_mask)
            if len(train_idx) == 0 or len(test_idx) == 0:
                continue

            yield CVFold(
                fold_id=fold_id,
                train_idx=_as_positions(pd.RangeIndex(n), train_idx),
                test_idx=_as_positions(pd.RangeIndex(n), test_idx),
                purged_count=purged,
                embargo_count=embargoed,
            )
            fold_id += 1

    def n_splits(self) -> int:
        from math import comb

        return comb(self.n_groups, self.n_test_groups)


class PurgedWalkForward:
    """Expanding or rolling walk-forward with purge + embargo + optional lag."""

    def __init__(
        self,
        n_splits: int = 5,
        min_train_size: int | None = None,
        test_size: int | None = None,
        purge_horizon: int = 5,
        embargo: int = 5,
        expanding: bool = True,
    ) -> None:
        self.n_splits = n_splits
        self.min_train_size = min_train_size
        self.test_size = test_size
        self.purge_horizon = purge_horizon
        self.embargo = embargo
        self.expanding = expanding

    def split(self, X: pd.DataFrame | np.ndarray) -> Iterator[CVFold]:
        n = len(X)
        test_size = self.test_size or max(20, n // (self.n_splits + 1))
        min_train = self.min_train_size or max(60, test_size)

        # Place fold boundaries from the end backwards so latest data is tested
        fold_id = 0
        cursor = min_train
        while fold_id < self.n_splits and cursor + test_size <= n:
            test_start = cursor
            test_end = min(n, cursor + test_size)

            if self.expanding:
                train_start = 0
            else:
                train_start = max(0, test_start - min_train - self.purge_horizon - self.embargo)

            train_end = max(train_start, test_start - self.purge_horizon)
            # Embargo is applied by shifting test_start conceptually already via purge;
            # additionally drop last `embargo` train points before purge boundary.
            train_end = max(train_start, train_end - self.embargo)

            train_idx = np.arange(train_start, train_end)
            test_idx = np.arange(test_start, test_end)
            if len(train_idx) and len(test_idx):
                yield CVFold(
                    fold_id=fold_id,
                    train_idx=train_idx,
                    test_idx=test_idx,
                    purged_count=self.purge_horizon,
                    embargo_count=self.embargo,
                )
                fold_id += 1
            cursor = test_end

        if fold_id == 0:
            raise ValueError("Not enough data for walk-forward splits")
