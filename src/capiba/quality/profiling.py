"""Data profiling — statistical quality analysis.

Chunk: profiling
Responsibility: Generate statistical profile of datasets
to detect quality anomalies (null values, duplicates,
distributions, outliers).

Dependencies: pandas, numpy
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ColumnProfile:
    """Quality profile of a column."""

    name: str
    type: str
    total_records: int
    nulls: int
    nulls_pct: float
    unique_count: int
    unique_pct: float
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    median: float | None = None
    std_dev: float | None = None
    q1: float | None = None
    q3: float | None = None
    outliers_iqr: int = 0
    dominant_pattern: str | None = None
    dominant_pattern_freq: int = 0


@dataclass
class DatasetProfile:
    """Quality profile of a complete dataset."""

    name: str
    total_records: int
    total_columns: int
    columns: list[ColumnProfile]
    quality_score: float  # 0-1
    alerts: list[str]


def profile_column(series: pd.Series, name: str) -> ColumnProfile:
    """Generates a quality profile for a column.

    Args:
        series: Pandas series.
        name: Column name.

    Returns:
        ColumnProfile with quality metrics.
    """
    total = len(series)
    nulls = series.isna().sum()
    nulls_pct = nulls / total if total > 0 else 0.0
    unique_count = series.nunique(dropna=True)
    unique_pct = unique_count / total if total > 0 else 0.0

    profile = ColumnProfile(
        name=name,
        type=str(series.dtype),
        total_records=total,
        nulls=nulls,
        nulls_pct=round(nulls_pct, 4),
        unique_count=unique_count,
        unique_pct=round(unique_pct, 4),
    )

    # Statistics for numeric columns
    if pd.api.types.is_numeric_dtype(series):
        valid = series.dropna()
        if len(valid) > 0:
            profile.min = float(valid.min())
            profile.max = float(valid.max())
            profile.mean = float(valid.mean())
            profile.median = float(valid.median())
            profile.std_dev = float(valid.std())
            profile.q1 = float(valid.quantile(0.25))
            profile.q3 = float(valid.quantile(0.75))

            # IQR outliers
            iqr = profile.q3 - profile.q1
            lower = profile.q1 - 1.5 * iqr
            upper = profile.q3 + 1.5 * iqr
            profile.outliers_iqr = int(((valid < lower) | (valid > upper)).sum())

    # Dominant pattern for categorical columns
    if pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series):
        mode = series.mode()
        if len(mode) > 0:
            profile.dominant_pattern = str(mode.iloc[0])
            profile.dominant_pattern_freq = int(series.value_counts().iloc[0])

    return profile


def profile_dataset(df: pd.DataFrame, name: str) -> DatasetProfile:
    """Generates a complete quality profile of a DataFrame.

    Args:
        df: DataFrame to be analyzed.
        name: Dataset identifier name.

    Returns:
        DatasetProfile with metrics and alerts.
    """
    columns = [profile_column(df[col], col) for col in df.columns]

    # Compute quality score
    scores = []
    alerts = []

    for col in columns:
        # Penalty for nulls
        if col.nulls_pct > 0.5:
            scores.append(0.0)
            alerts.append(f"Column '{col.name}': {col.nulls_pct:.1%} nulls")
        elif col.nulls_pct > 0.1:
            scores.append(0.5)
            alerts.append(f"Column '{col.name}': {col.nulls_pct:.1%} nulls")
        else:
            scores.append(1.0)

        # Penalty for anomalous cardinality
        if col.unique_pct == 1.0 and col.total_records > 100:
            alerts.append(
                f"Column '{col.name}': 100% cardinality (possible undeclared unique key)"
            )

        # Penalty for extreme outliers
        if col.outliers_iqr > col.total_records * 0.1:
            alerts.append(
                f"Column '{col.name}': {col.outliers_iqr} IQR outliers ({col.outliers_iqr / col.total_records:.1%})"
            )

    quality_score = round(sum(scores) / len(scores), 4) if scores else 1.0

    return DatasetProfile(
        name=name,
        total_records=len(df),
        total_columns=len(df.columns),
        columns=columns,
        quality_score=quality_score,
        alerts=alerts,
    )
