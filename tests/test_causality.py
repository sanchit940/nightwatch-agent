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


def test_quote_matching_tolerates_json_escapes_and_line_breaks():
    from nightwatch.causality import check_chain
    stored = '{\n "op": "http",\n "ok": false,\n "status": 503,\n "body": "{\\"error\\": \\"backend unavailable\\"}"\n}'
    chain = [{"cause": "nw-api", "effect": "nw-web returns 503", "evidence": '"status": 503, "body": "{"error": "backend unavailable"}"'}]
    assert check_chain(chain, [stored]) == []


def test_downgrade_keeps_proven_links_only():
    from nightwatch.causality import split_chain
    good, bad = split_chain([
        {"cause": "nw-redis stopped", "effect": "nw-api cannot reach Redis", "evidence": "ERROR redis dns lookup failed host=redis"},
        {"cause": "shop-worker restart on nw-vm", "effect": "nw-redis was stopped", "evidence": "2026-10-11T10:20:44Z\tcontainer\trestart\tnw-vm"}],
        OUTPUTS)
    assert len(good) == 1 and len(bad) == 1 and "shop-worker" in bad[0]["cause"]


def test_paraphrase_with_real_values_passes_but_invented_values_fail():
    from nightwatch.causality import check_chain
    out = '{\n "op": "http",\n "ok": true,\n "status": 200,\n "ms": 38.0,\n "body": "{\\"status\\": \\"ok\\"}"\n}'
    ok = [{"cause": "nw-redis", "effect": "nw-api", "evidence": "GET http://api:8000/healthz returned status 200 in 38.0 ms, body status ok"}]
    bad = [{"cause": "nw-redis", "effect": "nw-api", "evidence": "GET http://api:8000/healthz returned status 500 in 912 ms"}]
    tagged = '@@ net_http_check {"source": "nw-toolbox", "url": "http://api:8000/healthz"}\n' + out
    assert check_chain(ok, [tagged]) == []
    assert check_chain(bad, [tagged])


def test_combined_sources_and_ellipsis_pass_time_correlation_still_fails():
    from nightwatch.causality import check_chain
    logs = "@@ docker_logs {\"container\": \"nw-api\"}\n2026-10-11T12:57:13Z ERROR redis dns lookup failed host=redis port=6379 error=\"Error -2 connecting to redis:6379. Name or service not known.\""
    events = "@@ docker_events {}\n2026-10-11T12:54:42Z\tcontainer\tkill\tnw-redis\n2026-10-11T12:54:42Z\tcontainer\trestart\tnw-vm"
    ok = [{"cause": "nw-redis stopped", "effect": "nw-api cannot resolve redis",
           "evidence": "docker_logs (nw-api): 'ERROR redis dns lookup failed host=redis port=6379... Name or service not known'; docker_inspect: ExitCode=0"}]
    assert check_chain(ok, [logs, events]) == []
    bad = [{"cause": "shop-worker restart on nw-vm", "effect": "nw-redis was stopped",
            "evidence": "docker_events: 'kill nw-redis' at 12:54:42Z; 'restart nw-vm' at 12:54:42Z"}]
    assert check_chain(bad, [logs, events])
