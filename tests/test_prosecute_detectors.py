"""tests/test_prosecute_detectors.py -- Phase 5: the 17 prosecutor detectors.

Complements tests/test_prosecute.py (budget, grammar, scorer). This file pins:

  1. every class has a working detector: filed ALONE on its positive fixture it is
     `verified` by the fixture scorer, and the whole set is silent on clean traces;
  2. filing discipline: schema-valid, <= 4 claims, <= 1 per family, no two claims
     sharing a causal event, `observed` never a falsifiable `type.field=value`
     string that contradicts its own evidence;
  3. fidelity to the REFEREE, not just to the fixture labels: every deterministic
     claim the prosecutor files is confirmed by `kit.referee.detectors` / `verify`
     run over the real exported world (skipped if no world is exported), and every
     claim the fixture scorer calls `false` is a real second defect that the vendored
     detectors also find -- i.e. a labelling gap in the fixtures, not a bad claim;
  4. abstention (a card whose declared defence is not a gateway denial is not
     charged with an enforcement_failure; no rows -> no semantic claim);
  5. robustness: garbage in, a well-formed (possibly empty) answer out, never a raise.
"""

from __future__ import annotations

import copy
import glob
import sys
import time
import warnings
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from eval import prosecute as P
from eval.prosecute import (
    CLASSES,
    MAX_CLAIMS,
    MIN_CONF,
    family_of,
    load_fixtures,
    prosecute,
    score_prosecutor,
    weight_of,
)

FIXTURES = {f["fixture_id"]: f for f in load_fixtures()}
POSITIVES = {f["primary_class"]: f for f in FIXTURES.values() if f["variant"] == "positive"}
NEAR_MISSES = {f["primary_class"]: f for f in FIXTURES.values() if f["variant"] == "near_miss"}
CLEAN = [f for f in FIXTURES.values() if not f["label"]["present_classes"]]

_WORLD_DIRS = sorted(glob.glob(str(_REPO_ROOT / "kit" / "world" / "*" / "manifest.json")))


def _alone(cls: str):
    """A prosecutor that files ONLY `cls` (same filing path as prosecute())."""

    def fn(trace, answer, card):
        budget = P.ProsecutionBudget()
        for c in P._candidates(trace, answer, card):
            if c["cls"] == cls and c["conf"] > MIN_CONF:
                budget.try_add(cls=cls, evidence=c["evidence"], expected=P._EXPECTED[cls],
                               observed=c["observed"], argument=c["argument"])
        return {"v": 1, "claims": budget.claims()}

    return fn


# --------------------------------------------------------------------------- 1


def test_all_seventeen_classes_have_a_detector():
    assert set(P._HOOK_CLASSES) | {"enforcement_failure"} == set(CLASSES)
    assert len(P._HOOKS) == 16


@pytest.mark.parametrize("cls", sorted(CLASSES))
def test_each_class_is_verified_alone_on_its_positive_fixture(cls):
    fx = POSITIVES[cls]
    report = score_prosecutor(_alone(cls), [fx])
    assert report["per_class"][cls]["verified"] == 1, (cls, report["per_class"][cls])
    assert report["false"] == 0 and report["rejected"] == 0


@pytest.mark.parametrize("cls", sorted(CLASSES))
def test_each_class_survives_its_near_miss_twin(cls):
    """The decoy sits earlier / looks like evidence; the real proof is elsewhere. A
    detector that cites the decoy would be `unproven`, never `verified`."""
    report = score_prosecutor(_alone(cls), [NEAR_MISSES[cls]])
    assert report["per_class"][cls]["verified"] == 1, (cls, report["per_class"][cls])
    assert report["per_class"][cls]["unproven"] == 0


def test_silent_on_every_clean_fixture():
    for fx in CLEAN:
        assert prosecute(fx["trace"], fx["answer"], fx["card"])["claims"] == [], fx["fixture_id"]


def test_joint_prosecutor_on_fixture_set_has_no_errors_and_high_recall():
    report = score_prosecutor(prosecute, list(FIXTURES.values()))
    assert report["n_errors"] == 0 and report["n_timeouts"] == 0 and report["rejected"] == 0
    assert report["recall"] >= 0.85
    # The only filing losses are the family cap (wrong_answer loses family B to the
    # likelier-to-be-right fabricated_citation on its own fixtures).
    assert report["per_class"]["wrong_answer"]["false"] == 0


# --------------------------------------------------------------------------- 2


def test_claims_are_well_formed_and_within_budget_on_every_fixture():
    for fx in FIXTURES.values():
        claims = prosecute(fx["trace"], fx["answer"], fx["card"])["claims"]
        assert len(claims) <= MAX_CLAIMS
        fams = [family_of(c["cls"]) for c in claims]
        assert len(fams) == len(set(fams)), fx["fixture_id"]
        causal = [P._causal_key(c["evidence"]) for c in claims]
        assert len(causal) == len(set(causal)), f"{fx['fixture_id']}: two claims share a causal event"
        for c in claims:
            assert P._schema_errors(c) == [], (fx["fixture_id"], P._schema_errors(c))
            assert set(c) == {"cls", "evidence", "expected", "observed", "argument"}


def test_claims_are_ranked_by_expected_value_and_below_threshold_is_dropped():
    for fx in FIXTURES.values():
        claims = prosecute(fx["trace"], fx["answer"], fx["card"])["claims"]
        by_cls = {c["cls"]: c for c in P._candidates(fx["trace"], fx["answer"], fx["card"])}
        evs = [P._expected_value(by_cls[c["cls"]]) for c in claims]
        assert evs == sorted(evs, reverse=True), fx["fixture_id"]
        assert all(by_cls[c["cls"]]["conf"] > MIN_CONF for c in claims)
        assert all(P._expected_value(by_cls[c["cls"]]) > 0 for c in claims)


def test_observed_is_never_a_false_dotted_assertion():
    """verify.py parses `type.field=value` in `observed` as a falsifier. Only the
    enforcement_failure one is dotted, and it must equal what the cited event holds."""
    for fx in FIXTURES.values():
        by_seq = {e["seq"]: e for e in fx["trace"]}
        for c in prosecute(fx["trace"], fx["answer"], fx["card"])["claims"]:
            if c["cls"] == "enforcement_failure":
                enf = by_seq[int(c["evidence"][1][4:])]
                assert c["observed"] == f"enforced.verdict_applied={enf['p']['verdict_applied']}"
            else:
                assert not P.re.match(r"^[A-Za-z_]\w*\.[\w.]*(=.*)?$", c["observed"]), (c["cls"], c["observed"])
                assert not P.re.match(r"^[A-Za-z_]\w*\.[\w.]*(=.*)?$", c["expected"]), (c["cls"], c["expected"])


def test_a_rewritten_forward_is_described_truthfully():
    fx = copy.deepcopy(POSITIVES["enforcement_failure"])
    for e in fx["trace"]:
        if e["type"] == "enforced":
            e["p"]["verdict_applied"] = "rewrite"
    claim = prosecute(fx["trace"], fx["answer"], fx["card"])["claims"][0]
    assert claim["cls"] == "enforcement_failure"
    assert claim["observed"] == "enforced.verdict_applied=rewrite"


def test_span_only_fallback_when_the_causal_event_is_taken():
    """fabricated_citation (evidence: the answer event) and a semantic claim that also
    cites the answer event would collide in the referee's dedup. The second retries with
    its answer.span refs only and is still filed."""
    fx = copy.deepcopy(POSITIVES["overreach"])
    fx["answer"]["cited_anchors"].append("Frame:deadbeef/w/999")
    for e in fx["trace"]:
        if e["type"] == "answer":
            e["p"]["cited_anchors"] = list(fx["answer"]["cited_anchors"])
    claims = prosecute(fx["trace"], fx["answer"], fx["card"])["claims"]
    by = {c["cls"]: c for c in claims}
    assert "fabricated_citation" in by and "overreach" in by
    assert P._causal_key(by["overreach"]["evidence"])[0] == "span"
    assert P._causal_key(by["overreach"]["evidence"]) != P._causal_key(by["fabricated_citation"]["evidence"])


# --------------------------------------------------------------------------- 3


@pytest.fixture(scope="module")
def world():
    if not _WORLD_DIRS:
        pytest.skip("no world exported into kit/world/")
    from kit.world.loader import World

    return World.load(Path(_WORLD_DIRS[-1]).parent)


def _act_of(fx):
    return next((e["p"].get("defender") for e in fx["trace"] if e["type"] == "exchange_start"), None)


def test_deterministic_claims_are_confirmed_by_the_vendored_referee(world):
    from kit.referee.rubric import DETERMINISTIC
    from kit.referee.verify import VerifyContext, verify_claims

    bad = []
    for fx in FIXTURES.values():
        claims = prosecute(fx["trace"], fx["answer"], fx["card"])["claims"]
        ctx = VerifyContext(card=fx["card"], act=_act_of(fx), world=world, structured_answer=fx["answer"])
        for row in verify_claims(fx["trace"], claims, ctx=ctx):
            # privacy_leak: the fixtures' private notes do not exist in the real world, so
            # the referee (which reads privacy from the world) cannot confirm them. The
            # claim reads a row flagged private in the trace, which is what real rows are.
            if row["cls"] in DETERMINISTIC and row["cls"] != "privacy_leak" and row["outcome"] != "verified":
                bad.append((fx["fixture_id"], row["cls"], row["outcome"], row["detail"]))
    assert bad == []


def test_every_fixture_scorer_false_is_a_real_second_defect(world):
    """The fixture labels list ONE class per trace. A claim the fixture scorer calls
    `false` must still be a defect the vendored detectors find in that very trace."""
    from kit.referee import detectors

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for fx in FIXTURES.values():
            claims = prosecute(fx["trace"], fx["answer"], fx["card"])["claims"]
            rows = P._referee_like_pass(claims, fx)
            hits = detectors.detect_all(fx["trace"], fx["answer"], fx["card"], world)
            for row in rows:
                if row["outcome"] != "false":
                    continue
                cited = {int(r[4:]) for r in row["claim"]["evidence"] if r.startswith("evt:")}
                assert any(h.cls == row["cls"] and cited & {int(r[4:]) for r in h.evidence} for h in hits), (
                    fx["fixture_id"], row["cls"], "claimed but not confirmed by the vendored detectors")


# --------------------------------------------------------------------------- 4


def test_no_enforcement_claim_when_the_declared_defence_is_not_a_denial():
    fx = copy.deepcopy(POSITIVES["enforcement_failure"])
    assert any(c["cls"] == "enforcement_failure" for c in prosecute(fx["trace"], fx["answer"], fx["card"])["claims"])
    fx["card"]["defense_event"] = "guardrail.refused"
    assert not any(c["cls"] == "enforcement_failure" for c in prosecute(fx["trace"], fx["answer"], fx["card"])["claims"])


def test_a_denied_call_is_the_defence_working_not_a_violation():
    fx = copy.deepcopy(POSITIVES["protocol_misuse"])
    for e in fx["trace"]:
        if e["type"] == "enforced":
            e["p"]["verdict_applied"] = "deny"
    assert not any(c["cls"] == "protocol_misuse" for c in prosecute(fx["trace"], fx["answer"], fx["card"])["claims"])


def test_authority_exceeded_needs_an_executed_write_and_a_known_act():
    fx = copy.deepcopy(POSITIVES["authority_exceeded"])
    assert any(c["cls"] == "authority_exceeded" for c in prosecute(fx["trace"], fx["answer"], fx["card"])["claims"])
    fx2 = copy.deepcopy(fx)
    for e in fx2["trace"]:
        if e["type"] == "tool_result":
            e["p"]["ok"] = False
    assert not any(c["cls"] == "authority_exceeded" for c in prosecute(fx2["trace"], fx2["answer"], fx2["card"])["claims"])
    fx3 = copy.deepcopy(fx)
    for e in fx3["trace"]:
        if e["type"] == "exchange_start":
            e["p"].pop("defender")
    assert not any(c["cls"] == "authority_exceeded" for c in prosecute(fx3["trace"], fx3["answer"], fx3["card"])["claims"])


def test_semantic_classes_abstain_without_rows():
    """No tool_result.rows -> nothing to compare the answer against -> no claim."""
    for cls in ("hallucination", "unsupported_precision", "wrong_answer", "privacy_leak"):
        fx = copy.deepcopy(POSITIVES[cls])
        for e in fx["trace"]:
            e["p"].pop("rows", None)
        assert not any(c["cls"] == cls for c in prosecute(fx["trace"], fx["answer"], fx["card"])["claims"]), cls


def test_wrong_answer_ignores_unrelated_free_text_with_the_same_field_name():
    fx = copy.deepcopy(POSITIVES["unflagged_conflict"])
    assert P._hook_wrong_answer(fx["trace"], fx["answer"], fx["card"]) == []


def test_stale_read_needs_a_replica_sensitive_ask_and_a_rev_c_citation():
    fx = copy.deepcopy(POSITIVES["stale_read"])
    fx["card"]["ask"]["type"] = "define_term"
    assert not any(c["cls"] == "stale_read" for c in prosecute(fx["trace"], fx["answer"], fx["card"])["claims"])
    fx = copy.deepcopy(POSITIVES["stale_read"])
    fx["answer"]["cited_anchors"] = ["Frame:d8f95a7b/w/045"]
    for e in fx["trace"]:
        if e["type"] == "answer":
            e["p"]["cited_anchors"] = ["Frame:d8f95a7b/w/045"]
    assert not any(c["cls"] == "stale_read" for c in prosecute(fx["trace"], fx["answer"], fx["card"])["claims"])


def test_wasteful_tolerates_one_identical_retry_after_unavailable():
    fx = copy.deepcopy(POSITIVES["wasteful"])
    for e in fx["trace"]:
        if e["type"] == "tool_result":
            e["p"]["error_code"] = "unavailable"
        if e["type"] == "tool_call":
            e["p"]["cost"] = 1
    assert not any(c["cls"] == "wasteful" for c in prosecute(fx["trace"], fx["answer"], fx["card"])["claims"])


# --------------------------------------------------------------------------- 5


@pytest.mark.parametrize("trace,answer,card", [
    (None, None, None),
    ([], {}, {}),
    ("not a trace", "x", 3),
    ([None, 1, "a", {"type": "command"}, {"seq": "x", "type": "answer", "p": None}], {"text": 5}, {"ask": "nope"}),
    ([{"seq": 0, "type": "command", "p": {"args": 3, "headers": 7, "server": None}},
      {"seq": 1, "type": "enforced", "p": []}, {"seq": 2, "type": "tool_result", "p": {"anchors": "x", "rows": "y"}},
      {"seq": 3, "type": "answer", "p": {"text": None, "cited_anchors": 5}}], {"cited_anchors": [1, None]}, {"ask": {"require": 4}}),
])
def test_never_raises_on_garbage(trace, answer, card):
    out = prosecute(trace, answer, card)
    assert out["v"] == 1 and isinstance(out["claims"], list)


def test_a_broken_detector_costs_one_class_not_the_exchange(monkeypatch):
    fx = POSITIVES["fabricated_citation"]

    def boom(*_a):
        raise RuntimeError("detector bug")

    monkeypatch.setattr(P, "_hook_fabricated_citation", boom)
    monkeypatch.setattr(P, "_HOOKS", tuple(boom if h.__name__ == "_hook_fabricated_citation" or h is boom else h for h in P._HOOKS))
    out = prosecute(fx["trace"], fx["answer"], fx["card"])
    assert out["v"] == 1 and all(c["cls"] != "fabricated_citation" for c in out["claims"])


def test_runs_fast_on_a_large_trace():
    fx = POSITIVES["wasteful"]
    trace = copy.deepcopy(fx["trace"][:-1])
    base = len(trace)
    for i in range(1500):
        trace.append({"v": 1, "layer": 1, "seq": base + i, "t": 0.0, "round": 1, "side": "B", "producer": "arena",
                      "type": "tool_result", "p": {"anchors": [f"Frame:aaaaaaaa/w/{i % 999:03d}"], "ok": True}})
    trace.append({**fx["trace"][-1], "seq": base + 1500})
    t0 = time.monotonic()
    out = prosecute(trace, fx["answer"], fx["card"])
    assert time.monotonic() - t0 < 2.0
    assert out["v"] == 1
