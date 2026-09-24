from onebridge.approval import decide_approval


def test_approval_fails_closed():
    decision = decide_approval()
    assert not decision.approved
    assert decision.reason_code == "approval_missing"


def test_conflicting_flags_fail_closed():
    decision = decide_approval(approve=True, reject=True)
    assert not decision.approved
