"""Machine learning models for detection.

Chunks: random_forest, isolation_forest, cri
Responsibility: Classifiers and anomaly detectors to identify
collusion, favoritism and anomalous pricing.

Dependencies: scikit-learn
"""

from __future__ import annotations

import logging
from typing import Any, cast

import numpy as np
import pandas as pd
from sklearn.ensemble import (  # pyright: ignore[reportMissingTypeStubs]
    IsolationForest,
    RandomForestClassifier,
)

logger = logging.getLogger(__name__)


def train_rf(
    X: pd.DataFrame,  # noqa: N803 — sklearn convention
    y: pd.Series,
    **kwargs: Any,
) -> RandomForestClassifier:
    """Trains a Random Forest classifier.

    Best predictive cost-benefit for detecting collusion
    and favoritism. High explainability via
    feature_importances_.

    Args:
        X: Input features.
        y: Binary target (0 = normal, 1 = fraudulent).
        **kwargs: Additional parameters for RandomForestClassifier.

    Returns:
        Trained model.
    """
    default_params = {
        "n_estimators": 200,
        "max_depth": 10,
        "min_samples_split": 5,
        "class_weight": "balanced",
        "random_state": 42,
        "n_jobs": -1,
    }
    default_params.update(kwargs)

    logger.info("Training Random Forest: %s", default_params)
    model = RandomForestClassifier(**default_params)
    model.fit(X, y)

    return model


def train_if(
    X: pd.DataFrame,  # noqa: N803 — sklearn convention
    contamination: float = 0.1,
    **kwargs: Any,
) -> IsolationForest:
    """Trains an Isolation Forest detector.

    Unsupervised anomaly detection. Ideal for identifying
    suspicious processes without fraud labels.

    In Paraguay: >90% of processes with complaints
    identified at the bidding stage.

    Args:
        X: Input features.
        contamination: Expected proportion of anomalies.
        **kwargs: Additional parameters.

    Returns:
        Trained model.
    """
    default_params = {
        "n_estimators": 200,
        "contamination": contamination,
        "random_state": 42,
        "n_jobs": -1,
    }
    default_params.update(kwargs)

    logger.info("Training Isolation Forest: contamination=%.2f", contamination)
    model = IsolationForest(**default_params)
    model.fit(X)

    return model


def compute_cri(
    contract: pd.Series,
    models: dict[str, Any],
) -> float:
    """Computes the Composite Risk Index (CRI).

    Combines five signals into a single score:
    1. Single bid
    2. Short submission window
    3. Irregular decision timeline
    4. Non-competitive procedure type
    5. High buyer-supplier concentration

    Reference balanced accuracy: 0.931 (Distributed Random Forest).

    Args:
        contract: Series with the contract features.
        models: Dict with trained models.

    Returns:
        CRI score (0-1). Values > 0.7 indicate high risk.
    """
    # Extract features
    features = cast(
        "np.ndarray[Any, Any]",
        contract[
            [
                "single_bid",
                "short_submission_window",
                "irregular_timeline",
                "non_competitive",
                "high_concentration",
            ]
        ].values,
    ).reshape(1, -1)

    # Prediction with RF (main model)
    rf_model = models.get("random_forest")
    if rf_model is None:
        raise ValueError("Random Forest model not found")

    proba: float = rf_model.predict_proba(features)[0][1]

    logger.info("CRI: %.4f", proba)
    return round(proba, 4)
