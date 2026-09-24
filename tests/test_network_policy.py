from onebridge.network_policy import EgressPolicy, EgressRule


def test_allowed_host_path_and_method():
    policy = EgressPolicy([
        EgressRule(
            rule_id="flowise",
            host="flowise.example.com",
            path_prefix="/api/v1",
            methods=("GET", "POST"),
        )
    ])
    decision = policy.decide(
        "https://flowise.example.com/api/v1/prediction",
        "POST",
    )
    assert decision.allowed
    assert decision.rule_id == "flowise"


def test_denies_unlisted_host():
    policy = EgressPolicy([
        EgressRule(rule_id="flowise", host="flowise.example.com")
    ])
    assert not policy.decide("https://other.example.com/", "GET").allowed


def test_denies_sensitive_query_and_non_https():
    policy = EgressPolicy([
        EgressRule(rule_id="flowise", host="flowise.example.com")
    ])
    assert not policy.decide(
        "https://flowise.example.com/?api_key=secret",
        "GET",
    ).allowed
    assert not policy.decide("http://flowise.example.com/", "GET").allowed
