"""A small, real subset of the public MITRE ATT&CK (Enterprise) matrix,
plus a rule-based mapper from real detection features to a technique.

Real technique IDs/names/tactics only, no invented ones:
  - T1498 Network Denial of Service (Impact)
  - T1046 Network Service Discovery (Discovery)
  - T1110 Brute Force (Credential Access)

Why only three, not all four NSL-KDD categories (DoS/Probe/R2L/U2R):
U2R (User-to-Root -- e.g. buffer overflow, rootkit) is a *host-level*
privilege-escalation category. This pipeline only ever sees
`network_flow` features (`app/detection/features.py`) -- there is no
signal in bytes/protocol/verdict that could honestly distinguish a U2R
event from anything else. Mapping U2R here would be fabricating a
classification the data can't support, exactly what this project's
non-fabrication rule exists to prevent. Left out on purpose, not an
oversight.

Critical honesty constraint, stated once and load-bearing everywhere this
is used: the mapper below **never reads NSL-KDD's own attack label**
(`row[41]`, e.g. "apache2"/"warezmaster") -- that label is deliberately
never sent into the pipeline at all (`replay/main.py`'s own stated rule).
The mapper only sees what the real detection pipeline sees: protocol,
app_protocol, verdict, bytes_sent, bytes_received, is_anomaly. Its
mappings can and sometimes will disagree with the dataset's own hidden
label -- that's the honest, expected behavior of a feature-based heuristic
with no label access, not a bug. It is explicitly a heuristic, not a
certified classification, and every surface that shows it says so.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Technique", "CATALOG", "TechniqueMapping", "map_to_technique"]


@dataclass(frozen=True)
class Technique:
    id: str
    name: str
    tactic: str
    description: str


CATALOG: dict[str, Technique] = {
    "T1498": Technique(
        id="T1498",
        name="Network Denial of Service",
        tactic="Impact",
        description=(
            "Adversaries degrade or block availability of a targeted resource by "
            "flooding it with traffic, often one-directional and high-volume."
        ),
    ),
    "T1046": Technique(
        id="T1046",
        name="Network Service Discovery",
        tactic="Discovery",
        description=(
            "Adversaries probe a network to find services worth targeting, "
            "typically producing many low-payload, rejected or reset connections."
        ),
    ),
    "T1110": Technique(
        id="T1110",
        name="Brute Force",
        tactic="Credential Access",
        description=(
            "Adversaries attempt repeated access to a remote-facing service, "
            "often visible as repeated connections carrying real payload."
        ),
    ),
}


@dataclass(frozen=True)
class TechniqueMapping:
    technique: Technique | None
    reason: str


# Thresholds below are stated plainly so they can be argued with, not
# hidden inside opaque conditionals. They are tuned against this artifact's
# real feature ranges (see app/detection/features.py's known-limitation
# note: dst_port/packets/direction are constant zero for NSL-KDD-replayed
# data, so only protocol, verdict, and the two byte-count features carry
# any real signal here).
_DOS_MIN_BYTES_SENT = 200_000
_PROBE_MAX_BYTES_SENT = 5_000
_PROBE_VERDICTS = {"REJ", "RSTR", "RSTO", "S0"}
_BRUTE_FORCE_MIN_BYTES_SENT = 10_000
_BRUTE_FORCE_MAX_BYTES_SENT = 200_000
_BRUTE_FORCE_APP_PROTOCOLS = {"ftp_data", "ftp", "telnet", "http"}
_BRUTE_FORCE_VERDICTS = {"SF", "RSTR"}


def map_to_technique(
    *,
    is_anomaly: bool,
    protocol: str | None,
    app_protocol: str | None,
    verdict: str | None,
    bytes_sent: int | None,
    bytes_received: int | None,
) -> TechniqueMapping:
    """Rule-based, feature-only mapping -- see module docstring for what
    it deliberately cannot know (NSL-KDD's own label) and cannot cover
    (U2R). Returns `technique=None` rather than forcing a guess when no
    rule matches; that is a correct, honest result, not a failure."""
    if not is_anomaly:
        return TechniqueMapping(None, "not anomalous -- no technique to map")

    sent = bytes_sent or 0
    received = bytes_received or 0
    proto = (protocol or "").lower()
    app = (app_protocol or "").lower()
    verdict_u = (verdict or "").upper()

    if proto == "tcp" and sent >= _DOS_MIN_BYTES_SENT and received == 0:
        return TechniqueMapping(
            CATALOG["T1498"],
            f"one-directional flow, bytes_sent={sent} >= {_DOS_MIN_BYTES_SENT} with zero bytes_received "
            "-- flood/DoS-shaped pattern",
        )

    if verdict_u in _PROBE_VERDICTS and sent < _PROBE_MAX_BYTES_SENT:
        return TechniqueMapping(
            CATALOG["T1046"],
            f"verdict={verdict_u} (rejected/reset) with minimal payload (bytes_sent={sent}) "
            "-- scanning/probing-shaped pattern",
        )

    if (
        app in _BRUTE_FORCE_APP_PROTOCOLS
        and verdict_u in _BRUTE_FORCE_VERDICTS
        and _BRUTE_FORCE_MIN_BYTES_SENT <= sent < _BRUTE_FORCE_MAX_BYTES_SENT
    ):
        return TechniqueMapping(
            CATALOG["T1110"],
            f"repeated-access-shaped flow to {app} (verdict={verdict_u}, bytes_sent={sent}) "
            "-- resembles credentialed access attempts",
        )

    return TechniqueMapping(None, "anomalous, but no rule matched this feature combination")
