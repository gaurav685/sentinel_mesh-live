"""Redis Streams keys actually consumed or produced by something in this
project. Deliberately not a full topic registry like SentinelMesh's
`sm_contracts.topics` — that one lists every topic across 19 services;
this project has exactly one real producer (`replay/main.py`) and one
real consumer (`app/detection/consumer.py`) so far. Add a name here only
once something reads it — a name nothing consumes is dead weight, not
documentation.
"""

from __future__ import annotations

__all__ = ["TELEMETRY_RAW"]

#: Raw network-flow events. Produced by `replay/main.py` (real NSL-KDD
#: rows), consumed by `app/detection/consumer.py` (isolation-forest
#: classification). Same name SentinelMesh's own `telemetry.raw` Kafka
#: topic used, kept for continuity even though the transport changed.
TELEMETRY_RAW = "telemetry.raw"
