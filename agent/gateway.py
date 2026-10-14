"""agent/gateway.py — YOUR control plane. CONTRACTS.md section 4, exactly.

READ agent/README.md FIRST — it maps all five files in this directory to what
each is scored on. This file is the one CONTRACTS.md calls "the trusted
envelope's untrusted half": every single MCP / A2A / DISCOVER command your
agent's model wants to make passes through `Gateway.decide` before it is
allowed to happen.

WHY THERE IS NO `execute()` METHOD ON `GatewayContext` (read this before you
go looking for one — there isn't one, and that is not an oversight)
----------------------------------------------------------------------------
CONTRACTS.md section 4's trusted envelope, reproduced here because it is the
one diagram worth memorising:

    [ trusted ]   loop emits a raw action line
         v
    [ trusted ]   INTERCEPT + CANONICALISE -> Command        (kit/loop/agent.py)
         v
    [ UNTRUSTED ] Gateway.decide(cmd) -> Decision             <- THIS FILE
         v
    [ trusted ]   ENFORCE: honour the Decision, meter it,
                  apply the active mutation, execute the
                  ToolCall or refuse it                       (the arena)
         v
    [ trusted ]   RECORD the authoritative L1 event, then
                  RENDER the Observation                      (the arena)
         v
    [ trusted ]   the model sees the Observation

`decide()` returns a *decision*, never a *result*. You cannot reach a tool
server, a file, a socket, or a clock from in here — there is nothing to
call. Two things follow from that, and both matter more than they look:

  1. YOUR TRACE CANNOT BE FORGED. Every `command` / `decision` / `enforced`
     / `tool_call` / `tool_result` L1 event (CONTRACTS.md 5.2) is written by
     the arena, from what the arena itself actually did — never from
     anything you claimed happened. A student gateway that wanted to lie
     about having blocked an attack ("I totally denied that, trust me")
     simply has no channel to lie through: the only thing you ever hand
     back is this one small `Decision` value, and the arena is the one that
     turns it into history.
  2. NOBODY CAN ACCUSE YOU OF A CALL YOU DID NOT AUTHORISE, either. Because
     `decide()` is the ONLY door a command can walk through on its way to
     actually running, a prosecutor's `enforcement_failure` claim against
     you has exactly one thing to point at: the `Decision` you returned for
     that specific `cmd_id`. There is no ambiguity about "maybe the loop
     called the tool directly" — CONTRACTS.md 4.2 removed that path on
     purpose, and kit/loop/agent.py's own module docstring names the same
     invariant from the other side (the loop never imports this module,
     never sees a `Decision`, never executes anything itself).

The cost of that guarantee is that this file is PURE: synchronous, no I/O,
no threads, no `sleep`, 250 ms wall-clock deadline (RULES.md section 3).
Raising anything, returning something that is not a valid `Decision`, or
missing the deadline is treated by the arena as a DENIED command PLUS a 2
credit penalty PLUS an `integrity` event that hands the prosecutor a free
`enforcement_failure` — CONTRACTS.md 4.1's charging table, reproduced in
agent/README.md's own table. Getting this file to just plainly return valid
`Decision` values, every time, is worth more than getting it clever.

WHAT `decide()` DOES NOW (phase 3b rework)
----------------------------------------------------------------------------
Four named jobs, each justified only by evidence present in the `Command` and
`ctx` (details and verified facts: reports/phase3b-gateway-rework.md):

  ROUTE      refuse a route in the body; slides.search -> slides.query;
             `mcp-replica: c` on a drifting/unknown path -> `w`.
  ADMIT      get_frame needs a lease present in `ctx.leases`; writes need
             `if-match` AND `idempotency-key`, no reused key, no unchanged
             retry after a `conflict` seen in `ctx.history`.
  AUTHORIZE  learner targets must equal `ctx.act` (never `ctx.sub`); `aud`
             must name the called server; reads need `wiki.read`, writes
             `wiki.write:<server>`.
  BUDGET     catalog traps -> one identifying field; `fields=*` narrowed to the
             default mask when it outgrows the per-round share; deny what the
             remaining credits cannot pay for.

ONE THING WORTH INTERNALISING BEFORE YOU WRITE YOUR FIRST REAL CHECK:
`verdict="deny"` costs the CALLER (your own team) **zero credits** —
CONTRACTS.md 4.1's charging table has exactly one $0 row, and it is this
one. Refusing to make a call you cannot justify is FREE. That makes
abstention a real strategy, not a luxury you can't afford: a `deny` you can
defend beats a `forward` you can't, every time a prosecutor is watching.

Stdlib only. No network, no randomness, no wall-clock reads, no sleeping —
none of that would even survive the kernel sandbox (CONTRACTS.md 12), but
the point is this file has no reason to want any of it in the first place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol, runtime_checkable

# kit.mcp.types is a collaborator's file (workspace hard rule 2: import it,
# degrade gracefully). It is present as of this writing and is core, stable
# infrastructure (CONTRACTS.md 3.1) — but this module must still not fail to
# IMPORT if a concurrent edit ever breaks it transiently. When it is
# unavailable, `Decision.call` type-checking is skipped (not enforced), and
# `Gateway.decide` falls back to a minimal local dict-shaped stand-in so the
# rest of this file — everything that does not need a *real* ToolCall — still
# runs.
try:
    from kit.mcp.types import ToolCall
    _TOOLCALL_AVAILABLE = True
except ImportError:  # pragma: no cover - collaborator file
    ToolCall = Any  # type: ignore[assignment, misc]
    _TOOLCALL_AVAILABLE = False

# kit.loop.agent is also a collaborator's file, used only by this module's
# own __main__ demo (to build real Commands the same way the arena's trusted
# canonicaliser would) — never by decide() itself, which never touches the
# loop. Degraded the same way.
try:
    from kit.loop.agent import canonicalise_action as _canonicalise_action
except ImportError:  # pragma: no cover - collaborator file
    _canonicalise_action = None

try:
    from kit.mcp.specs import TOOL_SPECS, cost as _spec_cost
except ImportError:  # pragma: no cover - collaborator file
    TOOL_SPECS = {}  # type: ignore[assignment]
    _spec_cost = None  # type: ignore[assignment]

from agent.strategy import BudgetPacer, cheap_mask, is_catalog_trap, successor_of
from agent.telemetry import RecordingGatewayContext, Telemetry

__all__ = [
    "COMMAND_KINDS",
    "DECISION_VERDICTS",
    "Command",
    "Decision",
    "GatewayContext",
    "Gateway",
]

# CONTRACTS.md 4.1: `Command.kind` — "mcp" | "a2a" | "discover". An "answer"
# action is NEVER a Command (kit/loop/agent.py's own module docstring: "an
# answer is not a tool call routed to a server, so it never becomes a
# Command at all") — it is handled entirely by the loop/arena and never
# reaches `Gateway.decide`.
COMMAND_KINDS: frozenset[str] = frozenset({"mcp", "a2a", "discover"})

# CONTRACTS.md 4.1: `Decision.verdict` — the closed three-member set.
DECISION_VERDICTS: frozenset[str] = frozenset({"forward", "deny", "rewrite"})


@dataclass(frozen=True, slots=True)
class Command:
    """CONTRACTS.md 4.1, field for field — "canonicalised by the arena
    BEFORE the student sees it". You never build one of these from your own
    agent's raw text; the arena's canonicaliser (kit/loop/agent.py's
    `canonicalise_action`, run inside the trusted envelope) already did that
    work and minted `cmd_id` by the time `decide()` sees it. The
    `from_action_dict` classmethod below exists only so this file's own demo
    (and your local tests, if you write any) can build a realistic `Command`
    without duplicating the arena's canonicalisation logic."""

    cmd_id: str
    kind: str  # "mcp" | "a2a" | "discover" — see COMMAND_KINDS
    raw: str
    server: str
    tool: str
    args: dict
    fields: tuple[str, ...]
    headers: dict
    lease_id: str | None
    call_index: int

    def __post_init__(self) -> None:
        if not isinstance(self.cmd_id, str) or not self.cmd_id:
            raise ValueError(f"Command.cmd_id must be a non-empty str, got {self.cmd_id!r}")
        if self.kind not in COMMAND_KINDS:
            raise ValueError(f"Command.kind must be one of {sorted(COMMAND_KINDS)}, got {self.kind!r}")
        if not isinstance(self.server, str) or not self.server:
            raise ValueError(f"Command.server must be a non-empty str, got {self.server!r}")
        if not isinstance(self.tool, str) or not self.tool:
            raise ValueError(f"Command.tool must be a non-empty str, got {self.tool!r}")
        if not isinstance(self.args, dict):
            raise ValueError(f"Command.args must be a dict, got {type(self.args).__name__}")
        if not isinstance(self.headers, dict):
            raise ValueError(f"Command.headers must be a dict, got {type(self.headers).__name__}")
        if (
            not isinstance(self.call_index, int)
            or isinstance(self.call_index, bool)
            or self.call_index < 0
        ):
            raise ValueError(f"Command.call_index must be a non-negative int, got {self.call_index!r}")

    @classmethod
    def from_action_dict(cls, action: Mapping[str, Any], *, cmd_id: str) -> "Command":
        """Build a `Command` from the dict shape `kit.loop.agent.canonicalise_action`
        returns (`kind, raw, server, tool, args, fields, headers, lease_id,
        call_index` — everything except the arena-minted `cmd_id`, supplied
        here as a keyword). Raises `ValueError` if `action["kind"] ==
        "answer"` — an answer is never a Command (see the module docstring).
        This is a convenience for tests/demos, not something the real arena
        calls: the trusted envelope mints `cmd_id` itself and constructs the
        real `Command` on its own side of the boundary."""
        kind = action.get("kind")
        if kind == "answer":
            raise ValueError(
                "an 'answer' action never becomes a Command (kit/loop/agent.py: "
                "\"an answer is not a tool call routed to a server\") — do not "
                "route it through Gateway.decide at all"
            )
        return cls(
            cmd_id=cmd_id,
            kind=kind,
            raw=action["raw"],
            server=action["server"],
            tool=action["tool"],
            args=dict(action.get("args", {})),
            fields=tuple(action.get("fields", ())),
            headers=dict(action.get("headers", {})),
            lease_id=action.get("lease_id"),
            call_index=action.get("call_index", 0),
        )

    def to_dict(self) -> dict:
        return {
            "cmd_id": self.cmd_id,
            "kind": self.kind,
            "raw": self.raw,
            "server": self.server,
            "tool": self.tool,
            "args": dict(self.args),
            "fields": list(self.fields),
            "headers": dict(self.headers),
            "lease_id": self.lease_id,
            "call_index": self.call_index,
        }


@dataclass(frozen=True, slots=True)
class Decision:
    """CONTRACTS.md 4.1, field for field.

    Validated strictly (`__post_init__`) because a *structurally* invalid
    `Decision` is charged exactly like a raised exception — CONTRACTS.md
    4.1's charging table: "malformed Decision (schema-invalid) -> 2 cr
    penalty, command denied." Failing loudly HERE, in your own process
    during development, is strictly better than discovering it live in a
    duel as an unexplained penalty.

    `verdict == "deny"` requires a non-empty `reason` (CONTRACTS.md 4.1:
    "required when verdict == 'deny'; shown in the combat log") and
    forbids `call` — a real denial has nothing left to carry out.
    `verdict` in `("forward", "rewrite")` requires `call` to be set — the
    arena executes exactly that `ToolCall`, nothing else, per the trusted
    envelope's whole point (see the module docstring)."""

    verdict: str  # "forward" | "deny" | "rewrite" — see DECISION_VERDICTS
    reason: str | None = None
    call: "ToolCall | None" = None
    quarantine: bool = False
    note: str | None = None

    def __post_init__(self) -> None:
        if self.verdict not in DECISION_VERDICTS:
            raise ValueError(
                f"Decision.verdict must be one of {sorted(DECISION_VERDICTS)}, got {self.verdict!r}"
            )
        if self.verdict == "deny":
            if not isinstance(self.reason, str) or not self.reason.strip():
                raise ValueError("Decision.verdict=='deny' requires a non-empty 'reason'")
            if self.call is not None:
                raise ValueError("Decision.verdict=='deny' must not carry a 'call' — there is nothing to run")
        else:  # forward | rewrite
            if self.call is None:
                raise ValueError(f"Decision.verdict=={self.verdict!r} requires 'call' to be set")
            if _TOOLCALL_AVAILABLE and not isinstance(self.call, ToolCall):
                raise ValueError(
                    f"Decision.call must be a kit.mcp.types.ToolCall instance, got {type(self.call).__name__}"
                )
        if not isinstance(self.quarantine, bool):
            raise ValueError(f"Decision.quarantine must be a bool, got {self.quarantine!r}")
        if self.note is not None and not isinstance(self.note, str):
            raise ValueError(f"Decision.note must be a str or None, got {self.note!r}")

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "call": self.call.to_dict() if self.call is not None and hasattr(self.call, "to_dict") else self.call,
            "quarantine": self.quarantine,
            "note": self.note,
        }


@runtime_checkable
class GatewayContext(Protocol):
    """CONTRACTS.md 4.2 — "read-only, arena-provided". Note what this is
    NOT: unlike `Command`/`Decision` above, CONTRACTS.md writes this as a
    plain `class`, not a `@dataclass(frozen=True, ...)`. That is a real
    signal, not an inconsistency — a `GatewayContext` is a LIVE VIEW the
    arena owns and updates as the duel actually progresses (credits get
    spent, rounds advance, leases mint and expire), not a frozen snapshot
    you receive once. This file therefore models it as a structural
    `Protocol`: the arena hands your `Gateway.__init__` some object shaped
    like this (see `agent/telemetry.py`'s `RecordingGatewayContext` for a
    concrete example your own tests/demos can build), and you read
    `self.ctx.<field>` fresh every time `decide()` runs — NEVER cache a
    context field into a local variable across calls; if you need memory
    that persists across the duel (a cache, a budget tracker, drift notes),
    keep it on YOUR OWN `Gateway` instance attributes instead (CONTRACTS.md
    4.3: "Gateway is instantiated once per duel... persists across the 10
    rounds").

    There is deliberately no `execute` method here — see the module
    docstring's "WHY THERE IS NO execute() METHOD" section. `emit` is the
    ONE thing you can do that has any side effect at all, and it writes to
    `own_telemetry` only: your own side, never scored, never shown to the
    opponent (`agent/telemetry.py` wraps it)."""

    act: str  # "learner:sv-0417" — WHOM YOU SERVE. Authority derives from this.
    sub: str  # "agent:vlearn-tutor" — what you are. NOT authority.
    scopes: frozenset[str]  # {"wiki.read"}; writes need "wiki.write:progress" etc.
    credits: int  # remaining this duel
    round: int
    call_index: int
    leases: tuple[str, ...]  # live lease ids, arena-tracked
    history: tuple[Mapping[str, Any], ...]  # YOUR OWN prior (Command, Decision, outcome) triples this duel

    def emit(self, name: str, **payload: Any) -> None: ...


# ---------------------------------------------------------------------------
# Verified facts the four jobs rely on (see reports/phase3b-gateway-rework.md)
# ---------------------------------------------------------------------------

# Path ids whose working/canonical replicas DISAGREE. Snapshot of the
# `drifts: true` records in kit/world/df8c55dabb35/drift.json (world_id
# df8c55dabb35, 16 of 22 measured decks). `decide()` is pure/no-I/O so the
# file cannot be read at runtime; the artifact is public and frozen, so the
# knowledge is carried here as data. In EVERY record `w_frames >= c_frames`
# (delta >= 0): the working copy is the fresher one, and
# kit/referee/detectors.py `stale_read` charges for citing a `rev == "c"`
# anchor of a drifting path. NOTE this is the OPPOSITE direction to
# `agent.strategy.pick_replica(known_drifting=True)`, which would answer "c".
DRIFTING_PATH_IDS: frozenset[str] = frozenset({
    "053195a5", "0e11ae43", "170c4d17", "28e68faa", "3326cb76", "45098556",
    "72f75709", "75811e75", "7a8d8046", "a13b20e6", "a284ae8b", "abd20c68",
    "c5418ab5", "d8f95a7b", "e0614beb", "f2696f5f",
})

# Writes: kit.mcp.specs `is_write=True` tools (needs `if-match` AND
# `idempotency-key`, lower-case keys - kit.mcp.specs.WRITE_HEADERS), plus the
# ones kit/mcp/servers.py implements without a spec row.
_EXTRA_WRITE_TOOLS: frozenset[tuple[str, str]] = frozenset({
    ("progress", "record_mastery"), ("content", "flag_stale_slide"),
    ("content", "file_content_bug"), ("registry", "pin"),
})
_WRITE_HEADERS: tuple[str, ...] = ("idempotency-key", "if-match")

# Catalog "punishment buttons": the bare/"*" call costs 12 / 10 credits. The
# one field that identifies a row (and so is what a discovery call actually
# cites) is passed to `cheap_mask`: 2 credits / 2 credits.
_CATALOG_KEY_FIELD: dict[tuple[str, str], str] = {
    ("registry", "list_servers"): "name",
    ("glossary", "list_terms"): "term",
}

_ROUNDS_PER_DUEL = 10
_MIN_CREDITS_PER_REMAINING_ROUND = 4  # floor kept for later rounds when a wide mask is pending
_READ_SCOPE = "wiki.read"

_LEARNER_ARG_KEYS = ("learner", "learner_id")
_BODY_ROUTE_KEYS = ("route", "_route", "replica")
_REPLICA_ALIASES = {"w": "w", "working": "w", "c": "c", "canonical": "c"}
_IDENTITY_RE = re.compile(r"^\s*(?:([A-Za-z][A-Za-z0-9_-]*):)?([A-Za-z0-9][A-Za-z0-9_.-]*)\s*$")
_ANCHOR_PATH_RE = re.compile(r"^(?:Frame|Deck|Section):([a-z0-9][a-z0-9-]*)(?:/([wc]))?")


def _identity(value: Any, default_kind: str = "learner") -> tuple[str, str] | None:
    """Parse `kind:name` ("learner:sv-0417", wire form "Learner:sv-0417") or a
    bare name into a case-folded `(kind, name)`; None if not parseable."""
    if not isinstance(value, str):
        return None
    m = _IDENTITY_RE.match(value)
    if m is None:
        return None
    return (m.group(1) or default_kind).lower(), m.group(2).lower()


def _lower_headers(headers: Mapping[str, Any]) -> dict[str, Any]:
    return {str(k).lower(): v for k, v in headers.items()}


def _is_write(server: str, tool: str) -> bool:
    spec = TOOL_SPECS.get((server, tool))
    return bool(spec.is_write) if spec is not None else (server, tool) in _EXTRA_WRITE_TOOLS


def _estimate(server: str, tool: str, fields: tuple[str, ...]) -> int | None:
    if _spec_cost is None or (server, tool) not in TOOL_SPECS:
        return None
    try:
        return int(_spec_cost(server, tool, tuple(fields), 1))
    except Exception:
        return None



class Gateway:
    """The control plane. One instance per duel (CONTRACTS.md 4.3) — built
    once at duel start with a `GatewayContext`, then asked to `decide()` on
    every MCP/A2A/DISCOVER command either side of the duel makes for all 10
    rounds. See the module docstring for the trusted-envelope diagram and
    why there is no `execute()` to call instead.

    Instance attributes below are this starter's ENTIRE per-duel memory —
    all currently unused by `decide()`'s naive body, but declared here
    (rather than invented ad hoc later) so the four TODO jobs below have
    somewhere obvious to keep state once you implement them. `agent/
    strategy.py` has working building blocks for exactly this (a budget
    pacer, a result cache, a replica-choice heuristic) — this starter does
    not wire them in for you; that wiring is the assignment.
    """

    def __init__(self, ctx: GatewayContext) -> None:
        self.ctx = ctx
        self._telemetry = Telemetry(ctx)

        # --- per-duel memory, unused by the naive starter below ---------
        # A cache of anchor -> body-ish data you have already paid for this
        # duel (agent/strategy.py's ResultCache is a ready-made version of
        # this). Populating it needs the *result* of a call, which decide()
        # never sees (it only sees the outgoing Command) — you would fill
        # this from whatever the arena hands back to your agent loop AFTER
        # a call executes, then consult it here on the NEXT decide() call
        # for the same anchor.
        self._seen_anchors: dict[str, Any] = {}
        # Credits you have personally authorised so far this duel — your
        # own running total, independent of (and a cross-check against)
        # `ctx.credits`, which the arena maintains authoritatively.
        self._credits_authorised: int = 0
        # Command ids you have already denied, in case a later job wants to
        # know "have I already said no to this once".
        self._denied_cmd_ids: set[str] = set()
        # Idempotency keys of writes this gateway has already let through:
        # a second write carrying one is `write_violation` (detectors.py).
        self._idempotency_keys: set[str] = set()
        # Own bookkeeping of what was authorised; `ctx.credits` stays the
        # authority (synced down in `_budget`).
        self._pacer = BudgetPacer()

    def decide(self, cmd: Command) -> Decision:
        """SYNCHRONOUS. PURE. NO I/O. 250 ms wall (RULES.md section 3).
        Never raises: any internal error degrades to a plain forward of the
        untouched command (reads) or a deny (writes), never an exception."""
        try:
            self._telemetry.decision_seen(cmd)
        except Exception:
            pass
        try:
            return self._decide(cmd)
        except Exception as exc:  # defensive: an exception costs 2 credits + integrity
            try:
                if _is_write(cmd.server, cmd.tool):
                    return self.deny(cmd, f"internal error on a write, failing closed: {type(exc).__name__}")
                return Decision(verdict="forward", call=self._to_tool_call(cmd))
            except Exception:
                return Decision(verdict="deny", reason="internal error")

    def _decide(self, cmd: Command) -> Decision:
        notes: list[str] = []

        # JOB 1 - ROUTE
        routed, reason = self._route(cmd, notes)
        if reason:
            return self.deny(cmd, reason)
        # JOB 2 - ADMIT
        reason = self._admit(routed)
        if reason:
            return self.deny(cmd, reason)
        # JOB 3 - AUTHORIZE
        reason = self._authorize(routed)
        if reason:
            return self.deny(cmd, reason)
        # JOB 4 - BUDGET
        routed, reason = self._budget(routed, notes)
        if reason:
            return self.deny(cmd, reason)

        # Commit per-duel state only for a call that is really going out.
        if _is_write(routed.server, routed.tool):
            key = _lower_headers(routed.headers).get("idempotency-key")
            if isinstance(key, str):
                self._idempotency_keys.add(key)
        est = _estimate(routed.server, routed.tool, routed.fields)
        if est:
            self._pacer.record_spend(self._round_no(), est)

        decision = Decision(
            verdict="forward" if routed == cmd else "rewrite",
            call=self._to_tool_call(routed),
            note="; ".join(notes) or None,
        )
        try:
            self._telemetry.decision_made(cmd, decision)
        except Exception:
            pass
        return decision

    # ------------------------------------------------------------------
    # JOB 1 - ROUTE: right tool, right replica, routed by HEADER only.
    def _route(self, cmd: Command, notes: list[str]) -> tuple[Command, str | None]:
        # Routing is decided by the `mcp-replica` header; a route smuggled in
        # the body (header_spoof / replica_flip) is refused, not honoured.
        for key in _BODY_ROUTE_KEYS:
            if cmd.args.get(key):
                return cmd, f"route declared in args.{key}, not in the mcp-replica header"

        routed = cmd
        succ = successor_of(cmd.server, cmd.tool)  # slides.search -> slides.query
        if succ is not None:
            server, tool = succ
            spec = TOOL_SPECS.get(succ)
            fields = tuple(f for f in cmd.fields if spec is None or f in spec.all_fields or f == "*")
            routed = replace(routed, server=server, tool=tool, fields=fields)
            notes.append(f"{cmd.server}.{cmd.tool} is deprecated; routed to {server}.{tool}")

        # Replica: the working copy is the fresher one on every drifting path
        # (drift.json delta >= 0; stale_read charges rev=c citations there).
        headers = _lower_headers(routed.headers)
        raw_hdr = headers.get("mcp-replica")
        requested = _REPLICA_ALIASES.get(str(raw_hdr).lower()) if raw_hdr is not None else None
        path_id, rev = None, None
        anchor = routed.args.get("anchor")
        if isinstance(anchor, str):
            m = _ANCHOR_PATH_RE.match(anchor)
            if m:
                path_id, rev = m.group(1), m.group(2)
        if isinstance(routed.args.get("path_id"), str):
            path_id = routed.args["path_id"]
        wants = requested or rev
        if wants == "c" and (path_id is None or path_id in DRIFTING_PATH_IDS):
            headers["mcp-replica"] = "w"
            routed = replace(routed, headers=headers)
            notes.append(
                f"mcp-replica c->w: path {path_id or 'unknown'} "
                f"{'drifts (working copy is fresher)' if path_id else 'unknown, default to working'}"
            )
        return routed, None

    # JOB 2 - ADMIT: refuse calls that are certain to fail or to be a violation.
    def _admit(self, cmd: Command) -> str | None:
        headers = _lower_headers(cmd.headers)

        spec = TOOL_SPECS.get((cmd.server, cmd.tool))
        if (spec is not None and spec.needs_lease) or (cmd.server, cmd.tool) == ("slides", "get_frame"):
            lease = cmd.lease_id
            if not isinstance(lease, str) or not lease or lease not in tuple(self.ctx.leases):
                return "slides.get_frame needs a live lease from a recent slides.query (none/expired)"

        if _is_write(cmd.server, cmd.tool):
            missing = [h for h in _WRITE_HEADERS if not headers.get(h)]
            if missing:
                return f"write without {', '.join(missing)}: would fail precondition_missing / write_violation"
            if headers["idempotency-key"] in self._idempotency_keys:
                return "idempotency-key already used by an earlier write this duel (write_violation)"
            if self._signature(cmd) in self._conflicted_signatures():
                return "identical write already returned conflict; re-read registry.provenance for a fresh if-match"
        return None

    # JOB 3 - AUTHORIZE: authority comes from ctx.act, never ctx.sub.
    def _authorize(self, cmd: Command) -> str | None:
        act = _identity(getattr(self.ctx, "act", None))
        targets: list[Any] = [cmd.args[k] for k in _LEARNER_ARG_KEYS if cmd.args.get(k) not in (None, "")]
        anchor = cmd.args.get("anchor")
        if isinstance(anchor, str) and anchor.lower().startswith("learner:"):
            targets.append(anchor)
        for target in targets:
            ident = _identity(target)
            if act is None or act[0] != "learner" or ident is None or ident != act:
                return f"authority_exceeded: target {target!r} is not the learner in ctx.act {self.ctx.act!r}"

        aud = _lower_headers(cmd.headers).get("aud")
        if aud is not None and aud not in (cmd.server, f"mcp:{cmd.server}", f"a2a:{cmd.server}"):
            return f"delegation aud {aud!r} does not name the server being called ({cmd.server})"

        scopes = self.ctx.scopes
        need = f"wiki.write:{cmd.server}" if _is_write(cmd.server, cmd.tool) else _READ_SCOPE
        if need not in scopes:
            return f"scope {need} not granted (have {sorted(scopes)})"
        return None

    # JOB 4 - BUDGET: keep the 10-round, 100-credit pool alive.
    def _budget(self, cmd: Command, notes: list[str]) -> tuple[Command, str | None]:
        rnd = self._round_no()
        credits = int(self.ctx.credits)
        if self._pacer.credits_left > credits:  # arena is authoritative (also charges penalties)
            self._pacer.record_spend(rnd, self._pacer.credits_left - credits)
        key = (cmd.server, cmd.tool)
        routed = cmd

        if is_catalog_trap(cmd.server, cmd.tool, cmd.fields):
            narrow = cheap_mask(cmd.server, cmd.tool, (_CATALOG_KEY_FIELD[key],))
            routed = replace(cmd, fields=narrow)
            notes.append(f"catalog trap narrowed to fields={list(narrow)}")
        elif cmd.fields == ("*",) and key in TOOL_SPECS and not _is_write(*key):
            spec = TOOL_SPECS[key]
            wide = _estimate(*key, cmd.fields)
            default = _estimate(*key, spec.default_fields)
            rounds_left = max(1, _ROUNDS_PER_DUEL - rnd + 1)
            reserve = max(0, rounds_left - 1) * _MIN_CREDITS_PER_REMAINING_ROUND / self._pacer.starting_pool
            if (
                wide is not None and default is not None and default < wide
                and (wide > credits / rounds_left or not self._pacer.is_affordable(rnd, wide, reserve=reserve))
            ):
                narrow = cheap_mask(*key, spec.default_fields)
                routed = replace(cmd, fields=narrow)
                notes.append(f"fields=* ({wide} cr) narrowed to default mask ({default} cr)")

        est = _estimate(routed.server, routed.tool, routed.fields)
        if est is not None and est > credits:
            return routed, f"cannot afford {routed.server}.{routed.tool}: ~{est} cr > {credits} cr left"
        return routed, None

    # -- helpers ---------------------------------------------------------
    def _round_no(self) -> int:
        try:
            return max(1, int(self.ctx.round))
        except Exception:
            return 1

    @staticmethod
    def _signature(cmd: Command) -> tuple[str, str, str, str]:
        h = _lower_headers(cmd.headers)
        return (cmd.server, cmd.tool, str(cmd.args.get("anchor") or cmd.args.get("learner") or ""),
                str(h.get("if-match") or ""))

    def _conflicted_signatures(self) -> set[tuple[str, str, str, str]]:
        """Signatures of earlier writes whose outcome was `conflict` (409),
        read from `ctx.history`. The history triple's exact shape is not
        specified anywhere in the kit (the spar harness leaves it empty), so
        this parses defensively - dict or object, `command|cmd` +
        `outcome|result|error` - and silently yields nothing if it does not
        recognise an entry."""
        found: set[tuple[str, str, str, str]] = set()
        for entry in tuple(getattr(self.ctx, "history", ()) or ()):
            try:
                get = entry.get if isinstance(entry, Mapping) else (lambda k, d=None, _e=entry: getattr(_e, k, d))
                outcome = get("outcome") or get("result") or get("error")
                code = None
                if isinstance(outcome, Mapping):
                    err = outcome.get("error")
                    code = err.get("code") if isinstance(err, Mapping) else (err or outcome.get("code"))
                elif isinstance(outcome, str):
                    code = outcome
                if code != "conflict":
                    continue
                prior = get("command") or get("cmd")
                if isinstance(prior, Mapping):
                    prior = Command.from_action_dict(prior, cmd_id="hist") if "raw" in prior else None
                if prior is not None:
                    found.add(self._signature(prior))
            except Exception:
                continue
        return found

    def deny(self, cmd: Command, reason: str) -> Decision:
        """Not called anywhere in this starter's `decide()` — a ready-made
        helper for when you fill in JOB 2 / JOB 3 above, so denying doesn't
        mean hand-building a `Decision` inline at every call site. Kept as
        a real method (not a stub) because the shape of a correct denial —
        no `call`, a non-empty `reason` — is exactly the thing worth
        getting right by construction rather than by convention."""
        self._denied_cmd_ids.add(cmd.cmd_id)
        decision = Decision(verdict="deny", reason=reason)
        self._telemetry.decision_made(cmd, decision)
        return decision

    def _to_tool_call(self, cmd: Command) -> "ToolCall":
        """`Command` -> the `ToolCall` (CONTRACTS.md 3.1) the arena will
        actually execute on a `forward`/`rewrite` verdict. When
        `kit.mcp.types` is unavailable (see the module-level import guard),
        falls back to a plain dict carrying the identical fields — `Decision`
        accepts it either way (the `ToolCall` isinstance check inside
        `Decision.__post_init__` only runs when the real class loaded)."""
        fields = {
            "server": cmd.server,
            "tool": cmd.tool,
            "args": dict(cmd.args),
            "fields": cmd.fields,
            "headers": dict(cmd.headers),
            "lease_id": cmd.lease_id,
            "call_index": cmd.call_index,
        }
        if _TOOLCALL_AVAILABLE:
            return ToolCall(**fields)
        return fields  # type: ignore[return-value]


if __name__ == "__main__":
    print("=== agent.gateway: Command / Decision validation ===\n")

    good_cmd = Command(
        cmd_id="cmd:0000",
        kind="mcp",
        raw="MCP slides.get_frame anchor=Frame:3f2a9c11/w/041 fields=title,body lease=lse_7f21",
        server="slides",
        tool="get_frame",
        args={"anchor": "Frame:3f2a9c11/w/041"},
        fields=("body", "title"),
        headers={},
        lease_id="lse_7f21",
        call_index=0,
    )
    print(f"  Command constructed: {good_cmd}")
    assert good_cmd.kind == "mcp"

    print("\n  Rejection demo (each must raise ValueError):")

    def _expect_value_error(label: str, fn) -> None:
        try:
            fn()
        except ValueError as exc:
            print(f"    [{label:38}] -> ValueError: {exc}")
        else:
            raise AssertionError(f"expected ValueError for case {label!r}")

    _expect_value_error("Command.kind == 'answer'", lambda: Command(
        cmd_id="cmd:0001", kind="answer", raw="x", server="slides", tool="get_frame",
        args={}, fields=(), headers={}, lease_id=None, call_index=0,
    ))
    _expect_value_error("Decision verdict='deny' with no reason", lambda: Decision(verdict="deny"))
    _expect_value_error(
        "Decision verdict='forward' with no call", lambda: Decision(verdict="forward")
    )
    _expect_value_error(
        "Decision verdict='deny' carrying a call",
        lambda: Decision(verdict="deny", reason="nope", call={"server": "x", "tool": "y"}),
    )
    _expect_value_error("Decision verdict='?' unknown", lambda: Decision(verdict="???"))

    print("\n=== Command.from_action_dict — real canonicaliser integration ===\n")
    if _canonicalise_action is None:
        print("  kit.loop.agent not importable yet — skipping the live canonicaliser demo")
        demo_commands: list[Command] = [good_cmd]
    else:
        raw_actions = [
            "MCP registry.provenance anchor=Frame:3f2a9c11/w/041 fields=etag",
            'MCP slides.query q="streamable http replaces http+sse" fields=title,body',
            "A2A curriculum-analyst.which_days_cover concept=Concept:streamable-http fields=anchor,course_day,track",
            "DISCOVER registry.list_servers fields=name",
        ]
        demo_commands = []
        for i, raw in enumerate(raw_actions):
            action = _canonicalise_action(raw, call_index=i)
            cmd = Command.from_action_dict(action, cmd_id=f"cmd:{i:04d}")
            print(f"  {raw!r}\n    -> {cmd.kind}: {cmd.server}.{cmd.tool} fields={cmd.fields}")
            demo_commands.append(cmd)
        assert {c.kind for c in demo_commands} == {"mcp", "a2a", "discover"}

        answer_action = _canonicalise_action(
            'ANSWER {"text": "day 26, track P2T2"}', call_index=None
        )
        try:
            Command.from_action_dict(answer_action, cmd_id="cmd:9999")
        except ValueError as exc:
            print(f"\n  an 'answer' action correctly refuses to become a Command: {exc}")
        else:
            raise AssertionError("expected ValueError for an 'answer' action")

    print("\n=== Gateway.decide — the naive starter forwards everything ===\n")
    ctx = RecordingGatewayContext(
        act="learner:sv-0401",
        sub="agent:demo-team",
        scopes=frozenset({"wiki.read"}),
        credits=100,
        round=1,
        call_index=0,
        leases=(),
        history=(),
    )
    assert isinstance(ctx, GatewayContext), "RecordingGatewayContext must structurally satisfy GatewayContext"
    gw = Gateway(ctx)
    for cmd in demo_commands:
        decision = gw.decide(cmd)
        print(f"  decide({cmd.server}.{cmd.tool}) -> verdict={decision.verdict!r} quarantine={decision.quarantine}")
        assert decision.verdict == "forward"
        assert decision.call is not None
        call_dict = decision.call.to_dict() if hasattr(decision.call, "to_dict") else decision.call
        assert call_dict["server"] == cmd.server
        assert call_dict["tool"] == cmd.tool
        assert tuple(call_dict["fields"]) == cmd.fields

    print(f"\n=== Gateway.deny — the unused-by-default free-abstention path ===\n")
    denial = gw.deny(demo_commands[0], reason="demo: withholding pending a fresher registry.provenance read")
    print(f"  gw.deny(...) -> verdict={denial.verdict!r} reason={denial.reason!r} call={denial.call!r}")
    assert denial.verdict == "deny"
    assert denial.call is None
    assert demo_commands[0].cmd_id in gw._denied_cmd_ids

    print(f"\n=== own_telemetry — recorded on YOUR side only, never shown to the opponent ===\n")
    print(f"  {len(ctx.events)} events recorded on this ctx this run:")
    for ev in ctx.events:
        print(f"    {ev['name']}: {sorted(ev['payload'].keys())}")
    assert len(ctx.events) >= len(demo_commands) * 2 + 1  # decision_seen + decision_made per call, plus the deny

    print("\nAll agent/gateway.py demos passed.")
