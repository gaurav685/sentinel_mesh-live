"""Network-flow feature extraction — ported field-for-field from
SentinelMesh's `packages/ml-py/src/sm_ml/features/{schema,extract}.py`.

Trimmed, not a straight copy: SentinelMesh's `FEATURE_SCHEMAS` covers five
canonical event kinds (auth, network_flow, dns, process_exec,
file_access); this project only ever produces `network_flow` events
(`replay/main.py`'s NSL-KDD replay), so only that one schema is ported.
The eight feature names, their order, their clamp bounds, and the
formulas below are copied exactly — this is the same feature vector the
trained artifact under `ml/artifacts/isolation_forest_network_flow/v1/`
expects (its `metadata.json` lists the identical eight names in the
identical order).

Honesty carried over from the artifact's own `metadata.json` (`known_
limitations`): NSL-KDD provides no `dst_port`, packet counts, or
direction — the last five of these eight features are constant zero for
every event `replay/main.py` produces. Only `proto_tcp` and the two
byte-count features carry real signal for this dataset. Not silently
worked around; stated here as it was there.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

__all__ = ["FEATURE_NAMES", "extract_network_flow_features"]

FEATURE_NAMES: tuple[str, ...] = (
    "bytes_sent_log",
    "bytes_received_log",
    "packets_total_log",
    "dst_port",
    "dst_port_is_system",
    "dst_port_is_ephemeral",
    "proto_tcp",
    "direction_outbound",
)


@dataclass(frozen=True)
class _Bound:
    lo: float
    hi: float

    def clamp(self, value: float) -> float:
        return max(self.lo, min(self.hi, value))


_BOUNDS: dict[str, _Bound] = {
    "bytes_sent_log": _Bound(0.0, 30.0),
    "bytes_received_log": _Bound(0.0, 30.0),
    "packets_total_log": _Bound(0.0, 25.0),
    "dst_port": _Bound(0.0, 65535.0),
    "dst_port_is_system": _Bound(0.0, 1.0),
    "dst_port_is_ephemeral": _Bound(0.0, 1.0),
    "proto_tcp": _Bound(0.0, 1.0),
    "direction_outbound": _Bound(0.0, 1.0),
}


def _ln1p(x: float) -> float:
    return math.log1p(max(0.0, x))


def _num(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return default


def extract_network_flow_features(attrs: dict[str, Any]) -> list[float]:
    """`attrs` is the event dict as produced by `replay/main.py` /
    consumed off `telemetry.raw` — same field names
    (`bytes_sent`/`bytes_received`/`protocol`), so no remapping needed
    beyond what SentinelMesh's own extractor already does. Returns the
    feature vector in `FEATURE_NAMES` order, clamped to each feature's
    declared range exactly as SentinelMesh's schema does (a hostile or
    malformed event can't push a value to infinity)."""
    dst_port = _num(attrs.get("dst_port"))
    raw = {
        "bytes_sent_log": _ln1p(_num(attrs.get("bytes_sent"))),
        "bytes_received_log": _ln1p(_num(attrs.get("bytes_received"))),
        "packets_total_log": _ln1p(_num(attrs.get("packets_sent")) + _num(attrs.get("packets_received"))),
        "dst_port": dst_port,
        "dst_port_is_system": 1.0 if 0 < dst_port < 1024 else 0.0,
        "dst_port_is_ephemeral": 1.0 if dst_port >= 49152 else 0.0,
        "proto_tcp": 1.0 if str(attrs.get("protocol", "")).lower() == "tcp" else 0.0,
        "direction_outbound": 1.0 if str(attrs.get("direction", "")).lower() == "outbound" else 0.0,
    }
    return [_BOUNDS[name].clamp(raw[name]) for name in FEATURE_NAMES]
