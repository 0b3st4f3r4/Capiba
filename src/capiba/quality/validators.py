"""Data quality rule validators.

Chunk: quality_validators
Responsibility: Apply business and integrity rules
over datasets, generating compliance reports.

Dependencies: pandas, pydantic
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ValidationRule:
    """Quality validation rule."""

    name: str
    description: str
    column: str
    condition: Callable[..., pd.Series]
    severity: str  # error, warning, info
    reference_column: str | None = None  # reference column for comparative rules


@dataclass
class ValidationResult:
    """Result of applying a rule."""

    rule: str
    severity: str
    total_records: int
    violations: int
    violations_pct: float
    violation_sample: list[Any]


class QualityValidator:
    """Quality rule validation engine.

    Applies declarative rules over DataFrames and generates
    compliance reports for auditing.
    """

    def __init__(self) -> None:
        self.rules: list[ValidationRule] = []

    def add_rule(self, rule: ValidationRule) -> None:
        """Adds a rule to the validator."""
        self.rules.append(rule)
        logger.info("Rule added: %s", rule.name)

    def validate(self, df: pd.DataFrame) -> list[ValidationResult]:
        """Applies all rules over a DataFrame.

        Args:
            df: DataFrame to be validated.

        Returns:
            List of results per rule.
        """
        results = []

        for rule in self.rules:
            if rule.column not in df.columns:
                logger.warning(
                    "Column '%s' not found for rule '%s'",
                    rule.column,
                    rule.name,
                )
                continue
            if rule.reference_column and rule.reference_column not in df.columns:
                logger.warning(
                    "Column '%s' not found for rule '%s'",
                    rule.reference_column,
                    rule.name,
                )
                continue

            if rule.reference_column:
                mask = rule.condition(df[rule.column], df[rule.reference_column])
            else:
                mask = rule.condition(df[rule.column])
            violations = (~mask).sum()

            sample: list[Any] = []
            if violations > 0:
                raw_sample = df[~mask][rule.column].head(5).tolist()
                sample = [
                    None if (isinstance(v, float) and pd.isna(v)) else v
                    for v in raw_sample
                ]

            results.append(
                ValidationResult(
                    rule=rule.name,
                    severity=rule.severity,
                    total_records=len(df),
                    violations=int(violations),
                    violations_pct=round(violations / len(df), 4)
                    if len(df) > 0
                    else 0.0,
                    violation_sample=sample,
                )
            )

        return results


# Pre-defined rules for public procurement data

CONTRACT_RULES = [
    ValidationRule(
        name="positive_value",
        description="Contract amount must be positive",
        column="amount",
        condition=lambda s: s > 0,
        severity="error",
    ),
    ValidationRule(
        name="valid_cnpj",
        description="Supplier CNPJ must have 14 digits",
        column="supplier_cnpj",
        condition=lambda s: s.astype(str).str.match(r"^\d{14}$"),
        severity="error",
    ),
    ValidationRule(
        name="signature_date_present",
        description="Signature date cannot be null",
        column="signature_date",
        condition=lambda s: s.notna(),
        severity="error",
    ),
    ValidationRule(
        name="coherent_validity",
        description="End date must be later than or equal to the start date",
        column="validity_end",
        condition=cast(
            "Callable[..., pd.Series]",
            lambda end, start: pd.to_datetime(end) >= pd.to_datetime(start),
        ),
        severity="error",
        reference_column="validity_start",
    ),
    ValidationRule(
        name="amount_not_extreme",
        description="Amount must be non-negative and non-NaN",
        column="amount",
        condition=lambda s: s.notna() & (s >= 0),
        severity="warning",
    ),
    ValidationRule(
        name="subject_not_empty",
        description="Subject description cannot be empty",
        column="subject",
        condition=lambda s: s.astype(str).str.len() > 10,
        severity="error",
    ),
]

# Pre-defined rules for official gazette records (Querido Diário) —
# applied over the raw gazette metadata of the documents_collect formula.

GAZETTE_RULES = [
    ValidationRule(
        name="valid_territory",
        description="Territory must be a 7-digit IBGE id",
        column="territory_id",
        condition=lambda s: s.astype(str).str.match(r"^\d{7}$"),
        severity="error",
    ),
    ValidationRule(
        name="date_present",
        description="Gazette publication date cannot be null",
        column="date",
        condition=lambda s: s.notna(),
        severity="error",
    ),
    ValidationRule(
        name="file_url_present",
        description="Gazette file URL cannot be empty",
        column="url",
        condition=lambda s: s.notna() & (s.astype(str).str.len() > 0),
        severity="error",
    ),
    ValidationRule(
        name="text_url_present",
        description="Extracted text URL should exist",
        column="txt_url",
        condition=lambda s: s.notna() & (s.astype(str).str.len() > 0),
        severity="warning",
    ),
]
