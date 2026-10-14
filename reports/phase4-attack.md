# Phase 4: Attack Deck · Báo Cáo Xây Dựng Bộ Bài Tấn Công

**Ngày hoàn thành:** 2026-10-14  
**Trạng thái:** ✓ Hoàn thành (xác thực cơ bản)

---

## 1. Thay Đổi Thực Tế Trong Phase 4

Commit cf34390 thay đổi **CHỈDUY NHẤT** `defense_event` trên 4 thẻ tấn công:
- `atk_03` (drift): `registry.pinned` → `gateway.denied`
- `atk_04` (poisoned_result): `guardrail.refused` → `gateway.denied`
- `atk_05` (schema_bomb): `gateway.budget_held` → `gateway.denied`
- `atk_09` (faithless_peer): `a2a.crosschecked` → `gateway.denied`

**Không thay đổi:**
- `deck/lineup.json` — thứ tự chơi là pre-existing (atk_05 → atk_01 → atk_04 → atk_08 → atk_03 → atk_09 → atk_06 → atk_02 → atk_10 → atk_07)
- Card set/composition — 10 attacks + 4 blanks là pre-existing; Phase 4 chỉ cập nhật `defense_event`

---

## 2. Quyết Định Về `gateway.denied` (Open Decision)

Theo `deck/README.md`, tất cả thẻ tấn công **PHẢI** dùng `"gateway.denied"` (CONTRACTS.md section 8).

**Tuy nhiên**, spar.py `_detect()` (line 313–317) ghi chú rằng:
- `schema_bomb` thực sự bị đánh bại bằng **narrow querying** (không phải gateway denial)
- `drift` bị đánh bại bằng **pinning** (không phải gateway denial)
- `poisoned_result` bị đánh bại bằng **guardrail refusal** (không phải gateway denial)
- `faithless_peer` bị đánh bại bằng **cross-check độc lập** (không phải gateway denial)

**Hệ quả:** Setting `defense_event = "gateway.denied"` cho 4 thẻ này làm chúng **deadlier against correct defenders** — nếu gateway phòng thủ đúng (bằng narrow querying, pinning, guardrail, hoặc cross-check), vẫn bị tính là `enforcement_failure` nếu forward request.

**Trạng thái:** Đây là **open decision** (trade-off giữa spec tuân thủ vs. modeling độc lập). Quyết định này **NOT documented** trong commit cf34390 và cần review/differentiating hoặc reverting ở Phase 5.

---

## 3. Validation & Tests

### `make validate`
```
PASS: 0 failing check(s), 1 warning(s).
```

- ✓ R1–R7: Tất cả FAIL-level checks pass
- ⚠ R8 (lethality-band): Cảnh báo thông tin (live engine ở Arena repo)

### `make test`
```
4643 passed, 4 skipped in 9.46s
```

---

## 4. Sparring (Lethality Band Preliminary)

### Vs Rookie (BOT=rookie, AS=attacker, seed=1, rounds=10)
```
RESULT: DRAW   you 100 — 100 rookie
Dealt: 0 in all 10 rounds
```

- ✓ Mutations áp dụng (27 enforcement_failure + 6 fabricated_citation + 10 protocol_misuse detected)
- **Nhưng:** Prosecutor chưa triển khai (Phase 5) → không claim violation → damage = 0
- **Kết luận tạm:** Deck không gây sát thương trên rookie (yêu cầu Phase 5)

### Vs Adversary (BOT=adversary, AS=attacker, seed=1, rounds=10)
```
RESULT: BOT   you 3 — 10 adversary
```

- Adversary phòng thủ và thắng (HP: 10 vs 3)
- ⚠ **Không thể kết luận** về lethality band cho đến khi Phase 5 xác thực damage claims

---

## 5. Hạn Chế & Chờ Phase 5

1. **Prosecutor chưa hoàn thành:** eval/prosecute.py vẫn là stub
   - Không claim violations → spar không tính damage
   - Cần Phase 5 để kiểm tra "falls to rookie / held by adversary" thực sự

2. **Open decision chưa giải quyết:** defense_event = "gateway.denied" trade-off
   - Cân nhắc: Reverting to original values? Differentiating per card?
   - Hoặc chấp nhận spec và document rằng 4 thẻ này "bonus deadly"?

3. **Deck redesign chưa xem xét:** 
   - Layer/class balance (4 MCP, 2 gateway, 4 A2A) ✓
   - Path IDs ∈ drift set ✓
   - Nhưng chưa optimize request pairing (ask/mutation) cho maximum viciousness

---

## 6. Kết Luận

- **Deck structure:** Hợp lệ (validate pass)
- **Phase 4 output:** defense_event standardized to `gateway.denied` (per spec)
- **Trade-off không documented:** 4 thẻ sử dụng defense_event không match real defense; cần decision & reversal/doc
- **Lethality band:** Chưa thể đo (chờ Phase 5 prosecutor)
- **Tiếp theo:** Phase 5 (prosecute), sau đó re-measure + decide defense_event trade-off

---

**Commit:** cf34390 (deck/deck.json changed defense_event only)
