"""Causal-claim guard: time correlation is not causation."""

from nightwatch.causality import check_chain, unsupported_pairs

OUTPUTS = [
    "2026-10-11T10:20:44Z\tcontainer\tdie\tnw-redis exitCode=0\n2026-10-11T10:20:44Z\tcontainer\trestart\tnw-vm",
    "2026-10-11T10:20:50Z ERROR redis dns lookup failed host=redis port=6379 error=Name or service not known",
    'GET http://localhost:8080/ -> {"status": 503, "body": "{\\"error\\": \\"backend unavailable\\"}"}',
]


def test_true_chain_passes():
    chain = [{"cause": "nw-redis stopped", "effect": "nw-api cannot reach Redis", "evidence": "ERROR redis dns lookup failed host=redis"},
             {"cause": "nw-api", "effect": "nw-web returns 503", "evidence": "backend unavailable"}]
    assert check_chain(chain, OUTPUTS) == []


def test_time_correlation_is_rejected():
    # The real mistake from the live Slack run: two events at the same second, no dependency.
    chain = [{"cause": "shop-worker restart on nw-vm", "effect": "nw-redis was stopped",
              "evidence": "2026-10-11T10:20:44Z\tcontainer\trestart\tnw-vm"}]
    errs = check_chain(chain, OUTPUTS)
    assert errs and "Time correlation is not causation" in errs[0]


def test_invented_quote_is_rejected():
    chain = [{"cause": "nw-redis", "effect": "nw-api", "evidence": "redis was killed by the OOM killer at 10:20"}]
    assert "not in any tool result" in check_chain(chain, OUTPUTS)[0]


def test_unsupported_pairs_in_plan_reason():
    assert unsupported_pairs("Redis is exited because shop-worker service restart triggered container cleanup") == [("host:nw-vm", "nw-redis")]
    assert unsupported_pairs("nw-redis stopped, so nw-api fails and nw-web returns 503") == []
