"""Isolation Forest anomaly scoring — ported field-for-field from
SentinelMesh's `packages/ml-py/src/sm_ml/models/isolation_forest.py`.
Same scoring formula, same normalization, same "never fabricate a score"
rule (a missing/corrupt artifact raises, it doesn't silently return 0.0).

Artifact used: `ml/artifacts/isolation_forest_network_flow/v1/` — copied
directly from SentinelMesh's own local checkout (`model.joblib` is
git-ignored there too, same reason: a 2MB binary that's a build output,
not source). Its `metadata.json` records what it actually is: trained on
125,973 real NSL-KDD rows, `nsl-kdd-v1`. `requirements.txt` pins
`scikit-learn==1.9.1` exactly (not just `>=`) because that's the version
this specific artifact was trained under — loading it under a different
version works but scikit-learn's own `InconsistentVersionWarning` is
correctly suspicious of silent scoring drift across versions; pinning
removes the question entirely rather than hoping the warning is harmless.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import sklearn

__all__ = ["AnomalyScore", "ModelUnavailable", "IsolationForestModel"]


class ModelUnavailable(RuntimeError):
    """Raised when the artifact is missing or fails to load. The caller
    must treat this as "no score available", never as "score is 0.0" —
    same rule SentinelMesh's own `ml-inference` states."""


@dataclass(frozen=True)
class AnomalyScore:
    method: str
    score: float
    normalized_score: float
    threshold: float
    is_anomaly: bool
    model_version: str


class IsolationForestModel:
    def __init__(
        self,
        estimator: Any,
        *,
        feature_names: tuple[str, ...],
        model_version: str,
        score_min: float,
        score_max: float,
        threshold: float,
        artifact_checksum: str = "",
        sklearn_version: str = "",
    ) -> None:
        self._estimator = estimator
        self.feature_names = feature_names
        self.model_version = model_version
        self._lo = score_min
        self._hi = score_max
        self._threshold = threshold
        #: sha256 of the on-disk model.joblib at load time -- lets a health
        #: check confirm which exact artifact bytes are actually loaded,
        #: not just which directory was configured.
        self.artifact_checksum = artifact_checksum
        #: scikit-learn version this process loaded the artifact under.
        #: Compared against metadata.json's own training version indirectly
        #: via requirements.txt's exact pin -- this field is what a health
        #: check surfaces, not a re-derivation of the pin.
        self.sklearn_version = sklearn_version

    @classmethod
    def load(cls, model_dir: Path) -> IsolationForestModel:
        artifact = model_dir / "model.joblib"
        meta_path = model_dir / "metadata.json"
        if not artifact.exists() or not meta_path.exists():
            raise ModelUnavailable(f"no isolation-forest artifact under {model_dir}")
        meta: dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8"))
        artifact_bytes = artifact.read_bytes()
        try:
            estimator = joblib.load(artifact)
        except Exception as exc:
            raise ModelUnavailable(f"failed to load {artifact}: {exc}") from exc
        return cls(
            estimator,
            feature_names=tuple(meta["feature_names"]),
            model_version=str(meta["model_version"]),
            score_min=float(meta["score_min"]),
            score_max=float(meta["score_max"]),
            threshold=float(meta["threshold"]),
            artifact_checksum=hashlib.sha256(artifact_bytes).hexdigest(),
            sklearn_version=sklearn.__version__,
        )

    def score(self, features: list[float]) -> AnomalyScore:
        if len(features) != len(self.feature_names):
            raise ValueError(f"expected {len(self.feature_names)} features, got {len(features)}")
        raw = float(-self._estimator.score_samples(np.asarray([features], dtype=float))[0])
        span = self._hi - self._lo
        normalized = (raw - self._lo) / span if span > 1e-9 else 0.0
        normalized = min(1.0, max(0.0, normalized))
        return AnomalyScore(
            method="isolation_forest",
            score=raw,
            normalized_score=normalized,
            threshold=self._threshold,
            is_anomaly=raw >= self._threshold,
            model_version=self.model_version,
        )
