# COLOSSEUM · Checklist Hoàn Thành Lab

## PHASE 1: Setup · Chuẩn Bị (30 phút)

- [x] `make install` — python 3.12 venv + pytest
- [x] Download world: `gh release download world-df8c55dabb35 -R VinUni-AI20k/Day26-Colosseum-Agent-Arena-Kit --pattern '*.zip'` (repo fork has no release; use -R for upstream)
- [x] Kiểm tra SHA256: `shasum -a 256 -c colosseum-world-df8c55dabb35.zip.sha256`
- [x] Unzip: `unzip colosseum-world-df8c55dabb35.zip` → `kit/world/df8c55dabb35/manifest.json`
- [x] `make doctor` — đạt: "ready to spar" (kiểm tra: no truth.json)

## PHASE 2: Understand · Nắm Chắc (1 giờ)

- [x] Đọc README.md: 3 tasks, 5 stages, 17 error classes, 8 mechanisms
- [x] Đọc RULES.md: ownership (agent/, deck/, eval/), denied imports, constraints
- [x] Đọc agent/README.md: 5 files, which class each saves you from
- [x] Đọc deck/README.md: 14 cards, 10 attacks + 4 blanks, layer/class balance
- [x] Đọc eval/README.md: prosecute(), 4 claims max, 1 per family, false = -0.8×weight
- [x] Đọc agent/gateway.py header: trusted envelope, why no execute()

## PHASE 3: Task 3 — DEFEND · agent/ (2–3 giờ)

**Four TODO jobs in `Gateway.decide()` at `/Users/jaimes/Working/AI20K-IV/track-2/labs/Day26-Track2-Assignment/agent/gateway.py`:**

- [x] **JOB 1 — ROUTE** (line 372): Implement replica choice
  - Use `agent/strategy.py`'s `pick_replica()` / `ReplicaChoice`
  - Rewrite `cmd.headers["mcp-replica"]` on drift risk → verdict="rewrite"
  - Saves: `stale_read` (8)

- [x] **JOB 2 — ADMIT** (line 384): Deny doomed calls
  - Check lease expiry, write conflicts (409), missing `If-Match`
  - Return `self.deny(cmd, reason)` → costs 0 credits, saves defense
  - Saves: `protocol_misuse` (6)

- [x] **JOB 3 — AUTHORIZE** (line 395): Check act + scopes
  - Deny if `cmd.args` targets learner ≠ `self.ctx.act`
  - Deny if call needs scope ∉ `self.ctx.scopes`
  - Saves: `authority_exceeded` (10, tied heaviest)

- [x] **JOB 4 — BUDGET** (line 411): Rewrite expensive masks
  - If `self.ctx.credits` tight, rewrite `cmd.fields` → cheap default
  - Use `agent/strategy.py`'s `cheap_mask()`, `BudgetPacer`
  - Saves: `wasteful` (3)

**Guardrails stubs** in `/Users/jaimes/Working/AI20K-IV/track-2/labs/Day26-Track2-Assignment/agent/guardrails.py`:

- [x] `check_grounding()` — REAL, working: validate cited_anchors
- [x] `scan_for_injected_instructions()` — STUB (line 164): implement refusal logic
- [x] `redact()` — STUB (line 195): implement PII redaction
- [x] `verify_arithmetic()` — STUB (line 227): verify numeric claims
- [x] `abstention_policy()` — REAL: abstain if grounding fails

## PHASE 4: Task 1 — ATTACK · deck/ (1–2 giờ)

- [ ] Edit `deck/deck.json`: customize 10 attack cards (atk_01..atk_10)
  - Layers: ≥3 MCP · ≥2 gateway · ≥3 A2A
  - Classes: ≥6 distinct (9 total exist)
  - Each `replica_flip`/`drift` card: `path_id` must be in world.drifts()
  - `defense_event` = exactly `"gateway.denied"`
- [ ] Edit `deck/lineup.json`: order of 10 attacks (all play, no blanks in starter)
- [ ] `make validate` — must pass all FAIL-level checks
  - Read errors: R1–R8, lethality band warns

## PHASE 5: Task 2 — PROSECUTE · eval/ (1–2 giờ)

Implement detectors in `/Users/jaimes/Working/AI20K-IV/track-2/labs/Day26-Track2-Assignment/eval/prosecute.py`:

- [ ] Read `detect_enforcement_failure()` — template for all 17 detectors
- [ ] Implement `_hook_*` stubs (16 remaining classes):
  - A: `stale_read` (8), `write_violation` (8), `protocol_misuse` (6)
  - B: `wrong_answer` (8), `fabricated_citation` (8), `hallucination` (7), `ungrounded` (5), `unsupported_precision` (4)
  - C: `guardrail_breach` (8), `privacy_leak` (8)
  - D: `unflagged_conflict` (6), `overreach` (5), `incoherent` (4), `non_responsive` (4)
  - E: `wasteful` (3)
- [ ] Wire into `prosecute()` loop: `budget.try_add(cls, evidence_refs, ...)`
- [ ] Test: `python -m eval.prosecute` (measure precision/recall/f1)
- [ ] Run: `pytest tests/test_prosecute.py -v`

## PHASE 6: Sparring · Thử Thách (30 phút)

- [ ] `make spar BOT=rookie AS=defender` — test defense
- [ ] `make spar BOT=operator AS=all` — test all three tasks
- [ ] `make spar BOT=adversary AS=prosecutor` — test prosecution
- [ ] `make ui` — open spar.html + projector view to debug

## PHASE 7: Compliance · Tuân Thủ Quy Tắc (15 phút)

- [ ] ✓ agent/, deck/, eval/ owned; kit/, bots/, fixtures/ untouched
- [ ] ✓ Stdlib only: no socket, http.client, subprocess, asyncio.open_connection, etc.
- [ ] ✓ Gateway.decide(): synchronous, 250 ms, no I/O, pure
- [ ] ✓ prosecute(): ≤4 claims/exchange, ≤1 per family, no I/O, 5 s
- [ ] ✓ deck.json: 14 cards, 10 attacks + 4 blanks, no repeats
- [ ] `make test` — public conformance suite must pass

## PHASE 8: Submit · Nộp Bài (5 phút)

- [ ] `make validate` — deck valid against real world
- [ ] `make test` — all tests pass
- [ ] `make submit TEAM=<team-name>` → submissions/&lt;team-name&gt;.bundle
- [ ] ✓ Bundle contains: agent/, deck/, eval/, manifest, kit/ hashes

---

**Key References:**
- Gateway TODO jobs: `@/Day26-Track2-Assignment/agent/gateway.py` line 354–426
- 8 mechanisms: README.md section 6
- 17 classes: README.md section 5; eval/prosecute.py
- Cost arithmetic: agent/strategy.py docstring
- Deck validation: validate_deck.py rules R1–R8
