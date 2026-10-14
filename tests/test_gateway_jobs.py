"""tests/test_gateway_jobs.py - the four jobs of agent/gateway.py's Gateway.decide
(ROUTE, ADMIT, AUTHORIZE, BUDGET), each against the real kit shapes: lower-case
header keys, `ctx.act == "learner:sv-0417"`, wire anchors `Learner:sv-0417`,
`ctx.leases` a tuple of live lease ids, scopes `wiki.read` / `wiki.write:<server>`.
"""

from __future__ import annotations

import time

import pytest

from agent.gateway import DRIFTING_PATH_IDS, Command, Gateway
from agent.telemetry import RecordingGatewayContext

ACT = "learner:sv-0417"


def make_ctx(**kw):
    base = dict(act=ACT, sub="agent:vlearn-tutor", scopes=frozenset({"wiki.read"}),
                credits=100, round=1, call_index=0, leases=(), history=())
    base.update(kw)
    return RecordingGatewayContext(**base)


def cmd(server, tool, *, args=None, fields=(), headers=None, lease_id=None, i=0, cid=None):
    return Command(cmd_id=cid or f"cmd:{i:04d}", kind="mcp", raw=f"{server}.{tool}", server=server,
                   tool=tool, args=args or {}, fields=tuple(fields), headers=headers or {},
                   lease_id=lease_id, call_index=i)


def verdict_and_call(gw, c):
    d = gw.decide(c)
    return d, (d.call.to_dict() if d.call is not None else None)


# ---------------------------------------------------------------- JOB 1 ROUTE
def test_drifting_canonical_request_is_routed_to_working_replica():
    pid = sorted(DRIFTING_PATH_IDS)[0]
    d, call = verdict_and_call(
        Gateway(make_ctx()),
        cmd("registry", "provenance", args={"anchor": f"Frame:{pid}/c/001"}, fields=("etag",)))
    assert d.verdict == "rewrite" and call["headers"]["mcp-replica"] == "w"


def test_header_c_on_drifting_path_is_rewritten_even_with_mixed_case_key():
    pid = sorted(DRIFTING_PATH_IDS)[0]
    d, call = verdict_and_call(
        Gateway(make_ctx()),
        cmd("registry", "provenance", args={"anchor": f"Frame:{pid}/w/001"}, headers={"Mcp-Replica": "c"}))
    assert d.verdict == "rewrite"
    assert call["headers"] == {"mcp-replica": "w"}


def test_non_drifting_path_is_left_alone():
    d, call = verdict_and_call(
        Gateway(make_ctx()),
        cmd("registry", "provenance", args={"anchor": "Frame:04032b0c/c/001"}, fields=("etag",)))
    assert d.verdict == "forward" and "mcp-replica" not in call["headers"]


@pytest.mark.parametrize("key", ["route", "_route", "replica"])
def test_route_in_body_is_denied(key):
    assert Gateway(make_ctx()).decide(cmd("slides", "query", args={"q": "x", key: "c"})).verdict == "deny"


def test_deprecated_search_is_rerouted_to_query():
    d, call = verdict_and_call(Gateway(make_ctx()), cmd("slides", "search", args={"q": "mcp"}, fields=("snippet", "title")))
    assert d.verdict == "rewrite" and (call["server"], call["tool"]) == ("slides", "query")
    assert call["fields"] == ["title"]  # snippet does not exist on query


# ---------------------------------------------------------------- JOB 2 ADMIT
def test_get_frame_without_or_with_dead_lease_is_denied():
    gw = Gateway(make_ctx(leases=("lse_aaaaaaaa",)))
    a = {"anchor": "Frame:04032b0c/w/001"}
    assert gw.decide(cmd("slides", "get_frame", args=a)).verdict == "deny"
    assert gw.decide(cmd("slides", "get_frame", args=a, lease_id="lse_dead0000")).verdict == "deny"
    assert gw.decide(cmd("slides", "get_frame", args=a, lease_id="lse_aaaaaaaa")).verdict == "forward"


def write_cmd(headers, i=0):
    return cmd("progress", "record_mastery", args={"anchor": "Learner:sv-0417", "concept": "Concept:x"},
               headers=headers, i=i)


WRITE_CTX = dict(scopes=frozenset({"wiki.read", "wiki.write:progress"}))


@pytest.mark.parametrize("headers", [{}, {"if-match": "sha256:aa"}, {"idempotency-key": "k1"}])
def test_write_missing_a_required_header_is_denied(headers):
    assert Gateway(make_ctx(**WRITE_CTX)).decide(write_cmd(headers)).verdict == "deny"


def test_write_with_both_headers_forwards_then_key_reuse_is_denied():
    gw = Gateway(make_ctx(**WRITE_CTX))
    h = {"if-match": "sha256:aa", "idempotency-key": "k1"}
    assert gw.decide(write_cmd(h)).verdict == "forward"
    assert gw.decide(write_cmd(h, i=1)).verdict == "deny"
    assert gw.decide(write_cmd({"if-match": "sha256:aa", "idempotency-key": "k2"}, i=2)).verdict == "forward"


def test_unchanged_retry_after_conflict_in_history_is_denied():
    h = {"if-match": "sha256:stale", "idempotency-key": "k9"}
    prior = write_cmd(h).to_dict()
    hist = ({"command": prior, "decision": {"verdict": "forward"},
             "outcome": {"ok": False, "error": {"code": "conflict"}, "cost": 0}},)
    gw = Gateway(make_ctx(history=hist, **WRITE_CTX))
    assert gw.decide(write_cmd({"if-match": "sha256:stale", "idempotency-key": "k10"}, i=1)).verdict == "deny"
    assert gw.decide(write_cmd({"if-match": "sha256:fresh", "idempotency-key": "k11"}, i=2)).verdict == "forward"


# ----------------------------------------------------------- JOB 3 AUTHORIZE
@pytest.mark.parametrize("arg", ["learner:sv-0392", "Learner:sv-0392", "sv-0392"])
def test_cross_learner_target_is_denied_in_any_format(arg):
    gw = Gateway(make_ctx())
    assert gw.decide(cmd("curriculum-analyst", "which_days_cover", args={"learner": arg})).verdict == "deny"
    assert gw.decide(cmd("roster", "lookup_learner", args={"learner_id": arg})).verdict == "deny"


@pytest.mark.parametrize("arg", ["learner:sv-0417", "Learner:sv-0417", "sv-0417", " LEARNER:SV-0417 "])
def test_own_learner_in_any_format_is_allowed(arg):
    assert Gateway(make_ctx()).decide(cmd("roster", "lookup_learner", args={"learner": arg})).verdict == "forward"


def test_cross_learner_write_via_anchor_is_denied_even_with_write_scope():
    gw = Gateway(make_ctx(**WRITE_CTX))
    c = cmd("progress", "record_mastery", args={"anchor": "Learner:sv-0392", "concept": "Concept:x"},
            headers={"if-match": "sha256:aa", "idempotency-key": "k1"})
    assert gw.decide(c).verdict == "deny"


def test_authority_is_act_not_sub():
    gw = Gateway(make_ctx(sub="agent:tutor-with-every-power"))
    assert gw.decide(cmd("roster", "lookup_learner", args={"learner": "learner:sv-0392"})).verdict == "deny"


def test_write_needs_its_own_server_scope():
    h = {"if-match": "sha256:aa", "idempotency-key": "k1"}
    assert Gateway(make_ctx()).decide(write_cmd(h)).verdict == "deny"  # wiki.read only
    other = Gateway(make_ctx(scopes=frozenset({"wiki.read", "wiki.write:content"})))
    assert other.decide(write_cmd(h)).verdict == "deny"  # wrong server's write scope
    assert Gateway(make_ctx(**WRITE_CTX)).decide(write_cmd(h)).verdict == "forward"


def test_read_without_read_scope_is_denied_and_audience_must_match_server():
    assert Gateway(make_ctx(scopes=frozenset())).decide(cmd("slides", "query", args={"q": "x"})).verdict == "deny"
    gw = Gateway(make_ctx())
    assert gw.decide(cmd("glossary", "define", args={"term": "x"}, headers={"aud": "mcp:tickets"})).verdict == "deny"
    assert gw.decide(cmd("glossary", "define", args={"term": "x"}, headers={"aud": "mcp:glossary"})).verdict == "forward"


# ------------------------------------------------------------- JOB 4 BUDGET
@pytest.mark.parametrize("server,tool,fields,key", [
    ("registry", "list_servers", (), "name"),
    ("registry", "list_servers", ("*",), "name"),
    ("glossary", "list_terms", (), "term"),
])
def test_catalog_trap_is_narrowed_to_the_identifying_field(server, tool, fields, key):
    d, call = verdict_and_call(Gateway(make_ctx()), cmd(server, tool, fields=fields))
    assert d.verdict == "rewrite" and call["fields"] == [key]


def test_explicit_narrow_catalog_call_is_untouched():
    d, call = verdict_and_call(Gateway(make_ctx()), cmd("registry", "list_servers", fields=("name", "scopes")))
    assert d.verdict == "forward" and call["fields"] == ["name", "scopes"]


def test_wide_mask_narrowed_only_when_expensive_for_the_remaining_budget():
    a = {"anchor": "Frame:04032b0c/w/001"}
    rich = Gateway(make_ctx(credits=100, leases=("lse_1",)))
    assert rich.decide(cmd("slides", "get_frame", args=a, fields=("*",), lease_id="lse_1")).call.fields == ("*",)
    poor = Gateway(make_ctx(credits=20, round=5, leases=("lse_1",)))
    d = poor.decide(cmd("slides", "get_frame", args=a, fields=("*",), lease_id="lse_1"))
    assert d.verdict == "rewrite" and d.call.fields == ("body", "title")


def test_unaffordable_call_is_denied_and_broke_gateway_denies():
    c = cmd("slides", "query", args={"q": "x"}, fields=("body", "title"))
    assert Gateway(make_ctx(credits=2)).decide(c).verdict == "deny"
    assert Gateway(make_ctx(credits=0)).decide(cmd("registry", "provenance", args={"anchor": "Frame:x/w/001"})).verdict == "deny"


# ------------------------------------------------------------- invariants
def test_never_raises_and_is_fast_on_hostile_input():
    gw = Gateway(make_ctx(history=("garbage", 5, None, {"outcome": 3})))
    weird = [
        cmd("slides", "query", args={"anchor": 5, "learner": 7, "path_id": None}),
        cmd("progress", "record_mastery", args={"learner": ["a"]}, headers={"if-match": None}),
        cmd("nosuch", "tool", args={"route": 0}),
    ]
    t0 = time.perf_counter()
    for c in weird:
        assert gw.decide(c).verdict in ("forward", "deny", "rewrite")
    assert time.perf_counter() - t0 < 0.25
