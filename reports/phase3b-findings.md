# Báo cáo Phase 3B: Kiểm chứng API Gateway và Phân tích Spar

**Ngày:** 2026-10-14  
**Tác giả:** Claude Haiku 4.5  
**Mục tiêu:** Xác lập ground truth về APIs mà `Gateway.decide()` phải sử dụng cho bốn JOB (ROUTE, ADMIT, AUTHORIZE, BUDGET).

---

> **ĐÍNH CHÍNH (2026-10-14, đã kiểm chứng với code thật):** file này do Haiku viết, nhiều chỗ SAI. Bản đã kiểm chứng: `reports/phase3b-gateway-rework.md` mục 3. Tóm tắt các chỗ sai:
> - Mục 1 `pick_replica`/Mục "JOB 1": drift -> canonical là SAI HƯỚNG. Ở mọi record drift `w_frames >= c_frames` và `detectors.stale_read` phạt trích `rev=c`; phải ưu tiên `w`. Không thể "load drift.json lúc init" (cấm I/O).
> - Mục 1/7 `cheap_mask(..., ())`: `()` là sentinel "mask mặc định" (vẫn 12 cr), không phải "không trích gì". `BudgetPacer.credits_left` là property; `is_affordable` mặc định reserve 0.5 sẽ over-deny; `_estimate_cost` không tồn tại (dùng `kit.mcp.specs.cost`).
> - Mục 3 Learner/scope: thiếu `args["anchor"]` (khóa chính của `record_mastery`) và dạng `Learner:`; ĐÃ có bảng scope (`wiki.read`, `wiki.write:<server>`, suy ra trong `servers.py`). Code đề xuất dùng `cmd.ctx.act` (không tồn tại).
> - Mục 5: header phải viết thường và ghi cần CẢ `if-match` lẫn `idempotency-key`.
> - Mục 7 BUG 3: không cần tự tính cửa sổ lease 3 call; `ctx.leases` do arena giữ.
> - Mục 8-9: DRAW 100-100 và bảng MISSED bị hiểu sai. MISSED là trace của BOT mà prosecutor của mình bỏ sót, không phải lỗi gateway mình; HP không đổi vì prosecutor của rookie không claim gì.
> - "Đề xuất thiết kế lại JOB 1-4" bên dưới đã bị thay thế bởi bản làm lại trong `agent/gateway.py`.


## 1. Kiểm chứng Signatures và Semantics của `strategy.py`

### pick_replica / ReplicaChoice
**File:** `agent/strategy.py` dòng 259-290  
**Signature thực tế:**
```python
def pick_replica(*, path_id: str | None, known_drifting: bool, prefers_fresh: bool = True) -> ReplicaChoice:
```

**Dataclass:**
```python
@dataclass(frozen=True, slots=True)
class ReplicaChoice:
    replica: str  # "w" | "c"
    reason: str
```

**Semantics:**
- `known_drifting` là tín hiệu từ **chính agent**, không phải hàm này derive ra
- Nếu `known_drifting=True` AND `prefers_fresh=True` → chọn replica `"c"` (canonical)
- Ngược lại → chọn `"w"` (working)
- **QUAN TRỌNG:** Hàm này NOT đọc từ `drift.json` hay bất kỳ world state nào. Caller phải supply `known_drifting` từ `registry.provenance` call hay accumulated drift knowledge của agent đó.

**Vấn đề hiện tại (agent/gateway.py dòng 376-383):**
```python
choice = pick_replica(
    path_id=cmd.args.get("anchor") if cmd.server == "slides" else None,
    known_drifting=False,  # ❌ LUÔN False — never uses drift knowledge!
    prefers_fresh=True,
)
```
- Luôn truyền `known_drifting=False` → không bao giờ switch sang canonical khi có drift
- Gọi không match thực với hành vi "dự kiến"; chỉ là naive starter heuristic

### cheap_mask
**File:** `agent/strategy.py` dòng 165-181  
**Signature:**
```python
def cheap_mask(server: str, tool: str, fields_you_will_actually_cite: tuple[str, ...]) -> tuple[str, ...]:
```

**Semantics:**
- Nhận `fields_you_will_actually_cite` — những field agent **thực sự sẽ dùng** trong answer
- Return: tuple đó, sorted + deduplicated
- **Nguyên tắc:** Caller phải biết they will cite gì; hàm không validate hoặc guess
- Passing `()` (empty) là informative: "I'm about to make a call I don't plan to cite" → wasteful pattern

**Vấn đề hiện tại (agent/gateway.py dòng 454):**
```python
cheap_fields = cheap_mask(routed.server, routed.tool, ())  # ❌ Passing empty!
```
- Khi catalog trap detected và credits < 50, code rewrites fields to `()`
- Điều này KHÔNG cheap; nó empty → agent không cite result anyway → wasteful
- Thực tế, nếu catalog trap (`registry.list_servers`, `glossary.list_terms`), cần đặc tả CHÍNH XÁC fields agent sẽ dùng (e.g., `("name",)` cho list_servers)

### BudgetPacer
**File:** `agent/strategy.py` dòng 193-250  
**Signature và public API:**
```python
class BudgetPacer:
    starting_pool: int = 100
    rounds_total: int = ROUNDS_PER_DUEL
    
    def record_spend(self, round_no: int, cost: int) -> None: ...
    def is_affordable(self, round_no: int, cost: int, *, reserve: float = SAFE_STARTING_RESERVE) -> bool:
    def credits_left(self) -> int: ...  # property
    def credits_spent(self) -> int: ...  # property
    def bankrupt_by(self) -> int | None: ...
```

**Semantics:**
- `is_affordable(round_no, cost, reserve=0.5)` → True nếu `(credits_left - cost) >= (starting_pool * reserve)`
- Mục đích: conservative floor check cho pacing across rounds
- **Không** là authoritative vs `GatewayContext.credits` (arena maintains that)

---

## 2. `mcp-replica` Header và Replica Selection

### Header Value Set
**File:** `kit/mcp/types.py` dòng 139, 193  
- **Valid replicas:** `frozenset({"w", "c"})`
- `"w"` = working, `"c"` = canonical
- No other values allowed; `ToolResult.replica` raises `ValueError` if not in set

### How Replica is Normally Chosen/Passed
**File:** `agent/gateway.py` dòng 385-400; `kit/mcp/types.py` dòng 62-122  
- Model/agent emits raw action → canonicalised to `Command` with headers dict
- `cmd.headers` may contain `"mcp-replica": "w"` or `"mcp-replica": "c"` (or absent, defaults to `"w"`)
- Gateway reads `cmd.headers.get("mcp-replica", "w")` and may REWRITE it
- If rewriting, gateway builds new `Command` with modified headers, then creates `ToolCall` from it

### Replica Flip Mutation
**CONTRACTS.md section 8; kit/mcp/mutations.py**
- **Which field changes:** `ToolCall.headers["mcp-replica"]`
- Example: original call has `"w"`, mutation flips it to `"c"` (or vice versa)
- Gateway can detect this by comparing requested vs actual headers OR by observing that `ToolResult.replica` differs from requested

---

## 3. Command Fields: Learner ID, Context Fields, Scopes

### Learner ID in args
**File:** `agent/gateway.py` dòng 426  
- **Which key?** Agent/model may pass either `"learner"` OR `"learner_id"` in `args` dict
- Code checks both: `target_learner = routed.args.get("learner") or routed.args.get("learner_id")`
- Neither is guaranteed; both should be checked for writes to `progress.record_mastery`

### ctx.act and ctx.sub Format
**File:** `agent/gateway.py` dòng 305-306; `kit/mcp/a2a.py` dòng 281-284  
**Regex patterns (A2A context):**
```python
_ACT_RE = re.compile(r"^[a-z][a-z0-9_-]*:[A-Za-z0-9_.-]+$")  # "learner:sv-0401"
_SUB_RE = _ACT_RE  # same: "agent:vlearn-tutor"
```

**Structure:** `<kind>:<name>` format
- `act` = "whom you serve" (authority source)
- `sub` = "what you are" (NOT authority)
- Example: `act="learner:sv-0417"`, `sub="agent:student"`

### ctx.scopes
**File:** `agent/gateway.py` dòng 307  
- Type: `frozenset[str]`
- Example: `{"wiki.read"}`, `{"wiki.read", "wiki.write:progress"}`
- No explicit tool→scope table in `kit/`; scopes are checked by agent convention
- Write tools typically require `wiki.write:<server>` scope (e.g., `wiki.write:progress` for `progress.record_mastery`)

---

## 4. Leases: Cấu trúc, Lifetime, và Checks

### ctx.leases Structure
**File:** `agent/gateway.py` dòng 311  
- Type: `tuple[str, ...]` — ordered sequence of live lease IDs
- Each lease_id is a string like `"lse_7f21"`
- **NOT** a dict; không thể lookup by key, phải check membership with `in`

### Lease Lifetime
**CONTRACTS.md 4.2 mechanic 2; agent/strategy.py docstring**
- Lease valid for **3 calls** (slides.query, slides.get_frame, etc.)
- Minted by `slides.query`, used by `slides.get_frame`
- After 3 calls, lease expires

### Check for Get Frame Lease
**File:** `agent/gateway.py` dòng 410-412  
```python
if cmd.server == "slides" and cmd.tool == "get_frame":
    if cmd.lease_id is None or cmd.lease_id not in self.ctx.leases:
        return self.deny(...)
```

**Analysis:**
- ✓ Correct: checks both `None` and membership in tuple
- `cmd.lease_id not in self.ctx.leases` works because `self.ctx.leases` is `tuple[str, ...]`

---

## 5. Writes: Tools, If-Match/Etag, 409 Errors, và Gateway State

### Which Tools are Writes
**File:** `kit/mcp/specs.py` (collaborator); fallback in `kit/referee/detectors.py` dòng 253  
```python
_FALLBACK_WRITE_TOOLS = frozenset({("progress", "record_mastery"), ("content", "flag_stale_slide")})
```

**Real tools:**
- `progress.record_mastery` — write
- `content.flag_stale_slide` — write
- (others are reads)

### If-Match / Etag Supply
**File:** `agent/gateway.py` dòng 417; `kit/mcp/types.py` dòng 83  
- **Header field name:** `"if-match"` (lowercase in `ToolCall.headers` dict)
- **Source:** `registry.provenance` returns `etag` field in result
- **Usage:** next write to same anchor passes `headers={"if-match": <etag>}` to detect conflict
- **Case sensitivity:** headers dict keys are case-sensitive; code checks `"If-Match"` or `"if-match"` consistently

### 409 Conflict Response
**CONTRACTS.md 3.3; kit/mcp/errors.py**
- Error code: `"conflict"` (not HTTP 409, but semantic equiv)
- Response: `{"ok": false, "error": {"code": "conflict"}, "cost": N}`
- Means: etag mismatch; caller must re-fetch fresh provenance and retry

### Gateway Instance State
**File:** `agent/gateway.py` dòng 333-352  
```python
class Gateway:
    def __init__(self, ctx: GatewayContext) -> None:
        self.ctx = ctx
        self._telemetry = Telemetry(ctx)
        self._seen_anchors: dict[str, Any] = {}
        self._credits_authorised: int = 0
        self._denied_cmd_ids: set[str] = set()
```

**Per-duel Memory:**
- ✓ **_seen_anchors:** Could cache results, but no result ever reaches `decide()` (arena gives results to loop after enforcement)
- ✓ **_credits_authorised:** Track own spend (cross-check vs ctx.credits)
- ✓ **_denied_cmd_ids:** Remember what was denied this duel (reusable pattern)

**No on-result hooks in current design:** Gateway is never called with results; only with Commands.

---

## 6. Budget: ctx.credits, Cost Formula, và is_catalog_trap/cheap_mask

### ctx.credits
**File:** `agent/gateway.py` dòng 308  
- Type: `int` — remaining credits this duel (arena-authoritative)
- Updated by arena after each command execution, visible on next `decide()` call
- NOT updated by gateway; gateway is read-only observer

### Cost Formula
**File:** `agent/strategy.py` dòng 131-150; `kit/mcp/specs.py` (collaborator)**
```
cost = base_cost + sum(field_weight[f] for f in fields) + n_rows * row_weight
```

**Example (from agent/strategy.py module docstring):**
- `slides.query(fields=[title,body])` with 1 row → base(4) + (body:3 + title:1) + 1*1 = 9... wait, docstring says 6?
- Check: disciplined_round_cost() = 4 + 4 + 1 = 9 (query + get_frame + provenance)
- But docstring says 11 for the triple (query=6, get_frame=4, provenance=1)
  - So query with full mask = 6 (not 4); default fields = 4

### is_catalog_trap / cheap_mask Expectations
**File:** `agent/strategy.py` dòng 153-162  
```python
def is_catalog_trap(server: str, tool: str, fields: tuple[str, ...]) -> bool:
    if (server, tool) not in CATALOG_TRAP_TOOLS:
        return False
    return fields in ((), ("*",))  # empty OR full
```

**Arg types:**
- `server, tool: str`
- `fields: tuple[str, ...]`
- Returns `bool`

**Current call (agent/gateway.py dòng 450):**
```python
if is_catalog_trap(routed.server, routed.tool, routed.fields):
```
- ✓ Correct types (routed.fields is already `tuple[str, ...]`)

**cheap_mask arg types:**
```python
def cheap_mask(server: str, tool: str, fields_you_will_actually_cite: tuple[str, ...]) -> tuple[str, ...]:
```

**Current call (agent/gateway.py dòng 454):**
```python
cheap_fields = cheap_mask(routed.server, routed.tool, ())
```
- ✓ Types correct, but **semantics wrong:** passing `()` means "I won't cite anything" — wasteful!

---

## 7. Bugs/Mismatches trong Hiện Tại agent/gateway.py (Lines 370-480)

### BUG 1: pick_replica Never Uses Drift Knowledge (Line 379-381)
**Current:**
```python
choice = pick_replica(
    path_id=cmd.args.get("anchor") if cmd.server == "slides" else None,
    known_drifting=False,  # ❌ ALWAYS False
    prefers_fresh=True,
)
```

**Issue:**
- Starter always passes `known_drifting=False`, so heuristic always returns `"w"` (working)
- Under `replica_flip` attack where canonical is actually fresher, gateway will use stale working copy
- Should accumulate drift info from prior calls or read from `registry.provenance`

**Impact:** Fails `replica_flip` mutation attack

---

### BUG 2: cheap_mask Called with Empty Tuple (Line 454)
**Current:**
```python
cheap_fields = cheap_mask(routed.server, routed.tool, ())
```

**Issue:**
- Passes `()` = "I won't cite any fields"
- For catalog traps (list_servers, list_terms), should pass ONLY fields agent will actually use
- Passing empty defeats the purpose of cheap_mask optimization
- Results in wasteful call that agent won't even use (should have been skipped or denied)

**Better:** If going to rewrite, must know what fields to request
- Or: deny the call entirely instead of rewriting to empty

---

### BUG 3: Lease Check is Correct but Incomplete (Line 410-412)
**Current:**
```python
if cmd.server == "slides" and cmd.tool == "get_frame":
    if cmd.lease_id is None or cmd.lease_id not in self.ctx.leases:
        return self.deny(cmd, "get_frame requires a live lease_id from a recent slides.query")
```

**Analysis:**
- ✓ Correctly denies if lease_id is `None` or not in active leases
- ✗ **Incomplete:** Does NOT check lease lifetime (3-call window)
  - Should track call_index when lease was minted and reject if (current_call_index - minted_at) > 3
  - Current check only sees active leases at this moment, but arena may expire lease between calls
  - Mitigation: trust arena's `ctx.leases` as authoritative; if not in tuple, it's expired

---

### BUG 4: If-Match Check Skips Enforcement (Line 415-420)
**Current:**
```python
is_write = (cmd.server == "progress" and cmd.tool == "record_mastery") or \
           (cmd.server == "content" and cmd.tool in ("flag_stale_slide", "file_content_bug"))
if is_write and "If-Match" not in cmd.headers:
    # Optional: deny writes without etag to avoid 409 conflicts
    # For now, just flag in telemetry but allow (lenient strategy)
    pass
```

**Issues:**
- ✗ Detects missing If-Match but DOES NOT DENY
- ✗ Passes case-sensitive `"If-Match"` (capital) but should check lowercase `"if-match"` (canonicalised)
- ✗ Allows writes without etag → high probability of 409 conflict on retry
- **Better:** Always require If-Match for writes, DENY if missing

---

### BUG 5: Learner Authority Check Normalization is Fragile (Line 426-435)
**Current:**
```python
target_learner = routed.args.get("learner") or routed.args.get("learner_id")
if target_learner is not None:
    normalized_target = target_learner.replace("Learner:", "").replace("learner:", "")
    normalized_act = self.ctx.act.replace("learner:", "").replace("Learner:", "")
    if normalized_target != normalized_act:
        return self.deny(...)
```

**Issues:**
- ✗ Assumes target_learner is always a string with prefix; may be bare ID (e.g., `"sv-0401"`)
- ✗ Normalization by string replace is fragile (what if `"Learner:"` appears elsewhere?)
- **Better:** Parse as `"kind:name"` format, compare just the name part
- ✗ Also: should extract learner from ctx.act too (format: `"learner:sv-0417"`)

---

### BUG 6: Scope Check is Permissive (Line 437-441)
**Current:**
```python
if len(self.ctx.scopes) == 0:
    # An agent with zero scopes is suspicious, but we'll be lenient
    pass
```

**Issues:**
- ✗ Checks for empty scopes but then allows anyway
- ✗ Does NOT enforce scope requirement for each tool
- ✗ No real scope authorization; just a comment
- **Better:** For each write, check `f"wiki.write:{server}"` is in `self.ctx.scopes`, else deny

---

### BUG 7: Rewrite Missing Both Verdict Check (Line 475)
**Current:**
```python
call = self._to_tool_call(routed)
verdict = "rewrite" if routed is not cmd else "forward"
decision = Decision(verdict=verdict, call=call)
```

**Issue:**
- ✗ If JOB 2 (ADMIT) denied a command early, code still reaches this line
  - Actually, if denied, function returns early from `deny()`, so code doesn't reach here
- ✓ Verdict selection is correct (if routed != cmd, must be rewrite)

---

### BUG 8: Telemetry Calls After Decision (Line 470, 477, 489)
**Current:**
```python
self._telemetry.decision_made(cmd, decision)
```

**Issue:**
- ✓ Correctly calls telemetry after building Decision
- ✓ All paths through decide() call this (deny, early rewrites, final forward/rewrite)

---

## 8. Why Spar is a DRAW 100-100: Missed Attacks Analysis

**Output Summary:**
```
⚑ MISSED — detected in rookie's trace, never argued by your prosecutor:
   enforcement_failure    x20  family A  worth 10 each — free damage you walked past
   fabricated_citation    x6   family B  worth 8 each — free damage you walked past
   protocol_misuse        x10  family A  worth 6 each — free damage you walked past
```

### enforcement_failure (x20 hits)
**Detector:** `kit/referee/detectors.py` lines 580-629  
**Condition:** mutation applied (`applied=true` and `trigger_matched=true`) BUT enforced.verdict_applied != "deny"

**Why it's happening:**
- Rookie (bot) is likely triggering mutations with certain attack cards
- Gateway is forwarding these commands anyway (because naive starter forwards everything)
- Arena notes the violation: a triggered mutation got through
- Your prosecutor never filed claims for these (no detection code in `eval/prosecute.py`)

**Example:** Card mutates a write with bad If-Match; gateway forwards anyway; arena lets it execute; detector sees `mutation.applied=true` but `enforced.verdict_applied="forward"`

### protocol_misuse (x10 hits)
**Detector:** `kit/referee/detectors.py` lines 724-855  
**Three sub-conditions:**
1. `get_frame` without a live lease_id
2. Partial result whose rows are cited with no continuation fetched
3. A field cited that the mask omitted

**Why x10 hits:**
- Sub-condition 1 (get_frame no lease): Your gateway DOES deny these (line 410-412), so arena shouldn't let them through
  - Maybe: mutation card mutates lease_id to null? Then gateway denies, enforced denies, no violation
  - Or: gateway bug lets some through if mutation is active and flips verdict?
- Sub-condition 2 or 3: Likely from partial results or cited-but-unmasked fields
  - Your gateway doesn't check for partial/continuation or field-mask alignment
  - So fabricated cites of fields never returned → protocol_misuse

### fabricated_citation (x6 hits)
**Detector:** `kit/referee/detectors.py` lines 1001-1025  
**Condition:** cited anchor never returned by any tool_result OR doesn't resolve in pages.jsonl

**Why it's happening:**
- Model/MockBroker is citing anchors that were never actually fetched
- No call to `slides.get_frame` or `slides.query` returned those anchors
- Your prosecutor doesn't file claims for these
- Referee auto-detects and counts as "missed"

**Root cause:** No validation that every cited anchor was actually retrieved

---

## 9. Classes Being Hit (From Spar Output)

Based on MISSED list, at least these attack/feature classes are active:
- **Mutations:** `replica_flip`, `poisoned_result`, write-conflict mutations (triggering enforcement_failure)
- **Protocol:** Lease expiry, partial results, field-mask misalignment (protocol_misuse)
- **Citation:** Fabricated/unfetched anchors (fabricated_citation)
- **Capability:** Your gateway's naive forwarding is not catching these

---

## Đề xuất thiết kế lại JOB 1-4 (ĐÃ BỊ THAY THẾ, chứa các lỗi nêu ở đầu file)

### JOB 1 (ROUTE) — Replica Selection
**Current:** Always `"w"`, never uses drift  
**Fix:**
1. On init, load `drift.json` from world
2. On each decide, check if `cmd.args.get("anchor")` has known drift
3. Call `pick_replica(path_id=..., known_drifting=<bool>, prefers_fresh=True)`
4. If choice differs from current header, rewrite
5. **Also:** Could add `registry.provenance` call to get real drift signal (costs credits)

```python
def __init__(self, ctx: GatewayContext) -> None:
    self.ctx = ctx
    self._telemetry = Telemetry(ctx)
    self._drift_knowledge: dict[str, bool] = {}  # path_id -> drifts bool
    # Load from world.drifts() at init if available
    
def decide(self, cmd: Command) -> Decision:
    if cmd.server == "slides":
        path_id = cmd.args.get("anchor")
        if path_id and path_id in self._drift_knowledge:
            is_drifting = self._drift_knowledge[path_id]
            choice = pick_replica(path_id=path_id, known_drifting=is_drifting)
            # ... rewrite if needed
```

### JOB 2 (ADMIT) — Lease + Write Validation
**Current:** Only denies get_frame with no lease  
**Fix:**
1. Always deny get_frame without valid lease → ✓ already does this
2. For writes: Always require both If-Match AND Idempotency-Key headers
3. Check case-insensitive (`"if-match"` or `"If-Match"`)
4. Deny if either missing

```python
if cmd.server == "slides" and cmd.tool == "get_frame":
    if not cmd.lease_id or cmd.lease_id not in self.ctx.leases:
        return self.deny(cmd, "get_frame requires a live lease")

is_write = (cmd.server, cmd.tool) in {("progress", "record_mastery"), ("content", "flag_stale_slide")}
if is_write:
    headers_lower = {k.lower(): v for k, v in cmd.headers.items()}
    if "if-match" not in headers_lower or "idempotency-key" not in headers_lower:
        return self.deny(cmd, "write requires if-match and idempotency-key headers")
```

### JOB 3 (AUTHORIZE) — Learner + Scope Validation
**Current:** Naive prefix stripping; no scope check  
**Fix:**
1. Parse ctx.act and target_learner as `kind:name` format
2. Extract and compare name parts
3. For writes, check required scope in ctx.scopes

```python
def _parse_identity(value: str) -> str | None:
    """Extract name from 'kind:name' or return None if malformed."""
    try:
        _, _, name = value.partition(":")
        return name if name else None
    except:
        return None

# In decide:
target_learner = cmd.args.get("learner") or cmd.args.get("learner_id")
if target_learner:
    tgt_name = _parse_identity(f"learner:{target_learner}") or target_learner
    act_name = _parse_identity(cmd.ctx.act)
    if tgt_name != act_name:
        return self.deny(cmd, f"learner {tgt_name} != act {act_name}")

# For writes, check scope:
if is_write:
    required_scope = f"wiki.write:{cmd.server}"
    if required_scope not in self.ctx.scopes:
        return self.deny(cmd, f"scope {required_scope} not in {sorted(self.ctx.scopes)}")
```

### JOB 4 (BUDGET) — Catalog Traps + Smart Pacing
**Current:** Rewrites to empty fields when tight on credits  
**Fix:**
1. Detect catalog trap: if is_catalog_trap(), know you MUST NOT call with `()` or `("*",)`
2. Option A (safe): DENY catalog trap when credits < threshold
3. Option B (smart): Rewrite with specific narrow fields (must know what you'll cite)
4. Use BudgetPacer for round-level affordability check

```python
from agent.strategy import is_catalog_trap, cheap_mask, BudgetPacer, ResultCache

def __init__(self, ctx: GatewayContext) -> None:
    self.ctx = ctx
    self._budget_pacer = BudgetPacer()
    self._result_cache = ResultCache()

def decide(self, cmd: Command) -> Decision:
    # Check budget affordability
    cost = _estimate_cost(cmd)  # use kit.mcp.specs.cost_of() if available
    if not self._budget_pacer.is_affordable(self.ctx.round, cost):
        return self.deny(cmd, "insufficient budget reserve this round")
    
    # Detect + handle catalog traps
    if is_catalog_trap(cmd.server, cmd.tool, cmd.fields):
        # Option: DENY to be safe
        return self.deny(cmd, "catalog trap (list_servers/list_terms) with full mask — too expensive")
        # Or Option: Rewrite with known cheap fields
        # cheap_fields = cheap_mask(cmd.server, cmd.tool, ("name",))  # example for list_servers
        # ... rewrite ...
    
    # Forward if passed all gates
    call = self._to_tool_call(cmd)
    self._budget_pacer.record_spend(self.ctx.round, cost)
    return Decision(verdict="forward", call=call)
```

---

## Kết luận

Hiện tại, `Gateway.decide()` là naive starter: forwards hầu hết mọi thứ, chỉ denies get_frame without lease. Spar DRAW 100-100 phản ánh rằng:
1. **Rookie** cũng naive (forwards mọi thứ)
2. **Referee detectors** phát hiện các violations mà prosecution không argue (enforcement_failure, fabricated_citation, protocol_misuse)
3. **To win:** Gateway phải implement sáu đề xuất trên cho JOB 1-4

**Thứ tự ưu tiên (theo impact từ spar):**
1. JOB 4: Deny catalog traps khi credits tight → tránh wasteful, giải quyết budget
2. JOB 2: Deny writes không có If-Match/Idempotency-Key → tránh 409 conflicts, enforcement_failure
3. JOB 1: Use drift knowledge để switch replica → tránh stale reads
4. JOB 3: Enforce scope checks → tránh authority_exceeded trên writes

