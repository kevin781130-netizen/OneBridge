from onebridge.evidence import BLOCKED, PARTIAL, SUCCESS, classify_evidence


def test_evidence_classification():
    assert classify_evidence(verification={"status": "passed"}) == SUCCESS
    assert classify_evidence(verification={"status": "failed"}) == BLOCKED
    assert classify_evidence(artifacts=[{"status": "awaiting_review"}]) == PARTIAL
