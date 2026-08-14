"""Low-complexity statistical operators.

Chunks: benford, single_bids, hhi, anomalous_duration
Responsibility: Statistical signals that do not require trained
models, implementable as streaming pipelines.

Dependencies: scipy, numpy, pandas
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy import stats

logger = logging.getLogger(__name__)


def benford_score(values: pd.Series) -> float:
    """Computes conformance with Benford's Law.

    Benford's Law predicts the frequency of leading digits
    in sets of natural numbers. Significant deviations
    indicate possible manipulation.

    Args:
        values: Series of numeric values (positive).

    Returns:
        Conformance score (0-1). 1 = conformant, 0 = total deviation.
        Returns NaN if values <= 0 or empty series.

    Dead ends:
    - Manual distribution implementation: slow, imprecise.
    - KS test instead of chi-squared: less sensitive to small deviations.
    """
    # Filter valid values
    valid = values[values > 0].dropna()
    if len(valid) == 0:
        return float("nan")

    # Extract leading digit
    first_digits = valid.astype(str).str[0].astype(int)

    # Observed counts
    obs_counts = first_digits.value_counts().sort_index()

    # Expected counts (Benford's Law scaled by the sample size —
    # chisquare requires absolute counts, not proportions)
    digits = np.arange(1, 10)
    exp_counts = np.log10(1 + 1 / digits) * len(valid)

    # Align indexes
    obs_counts = obs_counts.reindex(digits, fill_value=0)

    # Chi-squared test
    chi2, p_value = stats.chisquare(
        f_obs=np.asarray(obs_counts.values, dtype=float),
        f_exp=np.asarray(exp_counts, dtype=float),
    )

    # Score: normalized p-value (higher = more conformant)
    score: float = min(p_value, 1.0)

    logger.info("Benford score: %.4f (chi2=%.4f)", score, chi2)
    return round(score, 4)


def single_bid_rate(bids: pd.DataFrame) -> float:
    """Computes the rate of bids with a single participant.

    Favoritism proxy: proportion of processes with only
    one participating bidder.

    Args:
        bids: DataFrame with column 'num_participants'.

    Returns:
        Single-bid rate (0-1).
    """
    if bids.empty:
        return 0.0

    singles = (bids["num_participants"] == 1).sum()
    total = len(bids)
    rate = singles / total

    logger.info("Single bid rate: %.2f%% (%d/%d)", rate * 100, singles, total)
    return round(rate, 4)


def hhi_index(
    buyer_id: str,
    contracts: pd.DataFrame,
) -> float:
    """Computes the Herfindahl-Hirschman concentration index.

    Measures a specific buyer's dependence on its suppliers.
    Values close to 1 indicate extreme concentration
    (possible favoritism).

    Args:
        buyer_id: Identifier of the buying agency.
        contracts: DataFrame with columns 'buyer_id',
            'supplier_id', 'amount'.

    Returns:
        HHI index (0-1).
    """
    subset = contracts[contracts["buyer_id"] == buyer_id]
    if subset.empty:
        return 0.0

    # Market share per supplier
    shares = subset.groupby("supplier_id")["amount"].sum()
    shares = shares / shares.sum()

    # HHI = sum of squared market shares
    hhi: float = (shares**2).sum()

    logger.info("HHI for %s: %.4f", buyer_id, hhi)
    return round(hhi, 4)


def duration_outlier(
    processes: pd.DataFrame,
    method: str = "iqr",
) -> pd.Series:
    """Detects anomalous process durations.

    Args:
        processes: DataFrame with column 'duration_days'.
        method: 'iqr' (Interquartile Range) or 'zscore'.

    Returns:
        Boolean Series indicating outliers, indexed by the original index.
    """
    durations = processes["duration_days"]
    valid = durations.dropna()

    if method == "iqr":
        q1 = valid.quantile(0.25)
        q3 = valid.quantile(0.75)
        iqr = q3 - q1
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        valid_outliers = (valid < lower) | (valid > upper)

    elif method == "zscore":
        z_values = np.asarray(stats.zscore(np.asarray(valid, dtype=float)), dtype=float)
        z_scores = np.abs(z_values)
        valid_outliers = pd.Series(z_scores > 3, index=valid.index)

    else:
        raise ValueError(f"Unknown method: {method}")

    # Reindex to the original index, filling nulls with False
    outliers = valid_outliers.reindex(durations.index, fill_value=False)

    logger.info("Duration outliers: %d/%d", int(outliers.sum()), len(valid))
    return outliers
