"""Uses the same real feature values already established for the real
NSL-KDD rows 30 (apache2) and 61 (warezmaster) elsewhere in this suite
(test_detection_consumer.py) -- proves the mapper produces two *different*
technique labels for two different real detections, without ever reading
either row's hidden attack label.
"""

from __future__ import annotations

from app.mitre.catalog import CATALOG, map_to_technique


def test_not_anomalous_maps_to_none():
    result = map_to_technique(
        is_anomaly=False, protocol="tcp", app_protocol="private", verdict="REJ", bytes_sent=0, bytes_received=0
    )
    assert result.technique is None
    assert "not anomalous" in result.reason


def test_real_warezmaster_row_maps_to_dos_via_features_only():
    # row 61: tcp, ftp_data, SF, bytes_sent=283618, bytes_received=0
    result = map_to_technique(
        is_anomaly=True, protocol="tcp", app_protocol="ftp_data", verdict="SF", bytes_sent=283618, bytes_received=0
    )
    assert result.technique is CATALOG["T1498"]


def test_real_apache2_row_maps_to_brute_force_via_features_only():
    # row 30: tcp, http, RSTR, bytes_sent=76944, bytes_received=1
    result = map_to_technique(
        is_anomaly=True, protocol="tcp", app_protocol="http", verdict="RSTR", bytes_sent=76944, bytes_received=1
    )
    assert result.technique is CATALOG["T1110"]


def test_two_real_detections_get_different_techniques():
    warezmaster = map_to_technique(
        is_anomaly=True, protocol="tcp", app_protocol="ftp_data", verdict="SF", bytes_sent=283618, bytes_received=0
    )
    apache2 = map_to_technique(
        is_anomaly=True, protocol="tcp", app_protocol="http", verdict="RSTR", bytes_sent=76944, bytes_received=1
    )
    assert warezmaster.technique is not None
    assert apache2.technique is not None
    assert warezmaster.technique.id != apache2.technique.id


def test_probe_shaped_rejected_connection():
    result = map_to_technique(
        is_anomaly=True, protocol="tcp", app_protocol="private", verdict="REJ", bytes_sent=0, bytes_received=0
    )
    assert result.technique is CATALOG["T1046"]


def test_no_rule_matches_returns_none_not_a_guess():
    result = map_to_technique(
        is_anomaly=True, protocol="udp", app_protocol="domain", verdict="SF", bytes_sent=50, bytes_received=50
    )
    assert result.technique is None
    assert "no rule matched" in result.reason


def test_catalog_has_no_u2r_entry():
    names = {t.name.lower() for t in CATALOG.values()}
    assert not any("privilege" in n or "u2r" in n for n in names)
