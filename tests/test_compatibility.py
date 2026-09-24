import pytest

from onebridge.adapters.capabilities import AdapterCapabilities, AdapterHealthReport
from onebridge.compatibility import CompatibilityEntry, CompatibilityMatrix, validate_capabilities


def test_candidate_promotion_keeps_single_active():
    matrix = CompatibilityMatrix([
        CompatibilityEntry("flowise", "1", "active"),
        CompatibilityEntry("flowise", "2", "candidate"),
    ])
    health = AdapterHealthReport.now(adapter_id="flowise", version="2", status="healthy", latency_ms=5)
    promoted = matrix.promote("flowise", "2", health=health)
    assert promoted.state == "active"
    assert matrix.active("flowise").version == "2"
    assert [x.version for x in matrix.list("flowise") if x.state == "active"] == ["2"]


def test_unhealthy_candidate_cannot_promote():
    matrix = CompatibilityMatrix([CompatibilityEntry("hermes", "2", "candidate")])
    health = AdapterHealthReport.now(adapter_id="hermes", version="2", status="degraded", latency_ms=5)
    with pytest.raises(ValueError):
        matrix.promote("hermes", "2", health=health)


def test_capability_contract_validation():
    entry = CompatibilityEntry("hermes", "1", "active")
    caps = AdapterCapabilities(
        adapter_id="hermes",
        display_name="Hermes",
        version="1",
        operations=frozenset({"execute"}),
        output_kinds=frozenset({"code"}),
        network_access=False,
        requires_review=True,
    )
    assert validate_capabilities(entry, caps) == []
