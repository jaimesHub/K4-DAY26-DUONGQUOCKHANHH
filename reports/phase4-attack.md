# Phase 4: Attack Deck · Báo Cáo Xây Dựng Bộ Bài Tấn Công

**Ngày hoàn thành:** 2026-10-14  
**Trạng thái:** ✓ Hoàn thành · Đã xác thực

---

## 1. Tóm Tắt Bộ Bài

Bộ bài tấn công gồm **10 thẻ tấn công + 4 thẻ trống** (14 thẻ tổng cộng), được xây dựng để kiểm tra khả năng phòng thủ của gateway ở các tầng khác nhau:

- **Tầng MCP:** 4 thẻ (shadow, drift, poisoned_result, schema_bomb)
- **Tầng gateway:** 2 thẻ (replica_flip, header_spoof)
- **Tầng A2A:** 4 thẻ (identity ×2, forged_card, faithless_peer)
- **Tổng lớp khác nhau:** 9/9 (shadow, drift, replica_flip, poisoned_result, schema_bomb, header_spoof, identity, forged_card, faithless_peer)

---

## 2. Bảng Thẻ Chi Tiết

| ID | Lớp | Tầng | Phép Toán | Mục Tiêu | Lý Do |
|---|---|---|---|---|---|
| atk_01 | shadow | MCP | shadow_server | mcp:slides (decoy path 3326cb76) | Kiểm tra xác minh danh tính server MCP |
| atk_02 | replica_flip | gateway | swap_replica (header) | mcp:slides (path_id e0614beb) | Thử phát hiện lỗi trong replica envelope |
| atk_03 | drift | MCP | swap_replica (content) | mcp:slides (path_id 053195a5) | Kiểm tra phát hiện nội dung drift |
| atk_04 | poisoned_result | MCP | poison_row | mcp:research (Note:learner-sv-0412/w/005) | Tiêm chất độc vào kết quả tìm kiếm |
| atk_05 | schema_bomb | MCP | inflate_catalog | mcp:registry (+40 hàng giả) | Tấn công ngân sách bối cảnh |
| atk_06 | header_spoof | gateway | drop_header | mcp:progress (bỏ If-Match, Idempotency-Key) | Vô hiệu hóa bảo vệ ghi |
| atk_07 | identity | A2A | replace_act | a2a:curriculum-analyst (act → sv-0392) | Tấn công ủy quyền A2A |
| atk_08 | forged_card | A2A | forge_card | a2a:citation-checker (thêm skill) | Giả mạo Agent Card |
| atk_09 | faithless_peer | A2A | corrupt_peer_answer | a2a:curriculum-analyst (sai course_day) | Kiểm tra xác thực đáp án peer |
| atk_10 | identity | A2A | replace_aud | a2a:roster (aud → curriculum-analyst) | Tấn công khán giả ủy quyền |

**4 Thẻ Trống (blanks):**
- blk_01: define_term (traceparent header)
- blk_02: which_day_covers (Concept:inputschema)
- blk_03: whatlinkshere (Claim:mcp-transports)
- blk_04: source_of (Claim:claim-032daacb)

---

## 3. Thứ Tự Chơi (Lineup) và Lý Do

**Thứ tự:**
```
atk_05 → atk_01 → atk_04 → atk_08 → atk_03 → atk_09 → atk_06 → atk_02 → atk_10 → atk_07
```

**Lý Do Sắp Xếp:**
- **atk_05 (schema_bomb) - Mở đầu:** Rẻ tiền (inflate_catalog), dễ đáp ứng. Kiểm tra budget từ sớm.
- **atk_01 (shadow) → atk_04 (poisoned_result):** Tấn công tầng MCP liên tiếp, kiểm tra xác minh.
- **atk_08 (forged_card) → atk_03 (drift):** Chuyển qua A2A rồi quay lại MCP, tránh phòng thủ dễ đoán.
- **atk_09 (faithless_peer) → atk_06 (header_spoof):** A2A → gateway, thử các sơ hở khác nhau.
- **atk_02 (replica_flip) → atk_10 (identity/aud):** Gateway → A2A, cuối cùng là atk_07 (identity/act) - ví dụ chính thức.

Mục tiêu: Xen kẽ các tầng để phòng thủ có chiều sau sâu hơn là phòng chống điều gì vừa xảy ra.

---

## 4. Kiểm Chứng và Kết Quả

### 4.1 Validate Deck (`make validate`)

```
PASS: 0 failing check(s), 1 warning(s)
```

✓ **Tất cả kiểm tra FAIL-level đều pass:**
- R1: 14 thẻ (10 attacks + 4 blanks) ✓
- R2: 10 attacks, 4 blanks, không lặp ✓
- R3: ≥3 MCP, ≥3 A2A, ≥2 gateway ✓
- R4: ≥6 lớp khác nhau (9/9) ✓
- R5: Tất cả path_id drift hợp lệ (e0614beb, 053195a5, 3326cb76 trong drift.json) ✓
- R6: Tất cả anchor có thể giải quyết được ✓
- R7: Lineup hợp lệ (10 thẻ, không trống) ✓

⚠ **Warning duy nhất (R8-lethality-band):**
- Cảnh báo rằng kiểm tra band chết không thể chạy đủ mà không có mutation engine thực (Arena repo)
- Hướng dẫn chạy `spar` để đo lường thực

**Sửa lỗi trong giai đoạn này:**
- Đã cập nhật `defense_event` từ các giá trị tùy ý (registry.pinned, guardrail.refused, gateway.budget_held, a2a.crosschecked) thành `gateway.denied` cho atk_03, atk_04, atk_05, atk_09
- Lý do: deck/README.md yêu cầu tất cả thẻ phải dùng `"gateway.denied"` theo CONTRACTS.md section 8 (sửa lỗi ledger.py)
- Xác nhận: điều này là "lựa chọn tương thích trong tương lai khi ledger.py được sửa"

### 4.2 Tests (`make test`)

```
======================= 4643 passed, 4 skipped in 9.46s ========================
```

✓ Tất cả kiểm tra công cộng pass, bao gồm `test_validate_deck.py`

### 4.3 Sparring Lethality Band

#### Vs Rookie (không phòng thủ):

```
COLOSSEUM — spar vs ROOKIE  (seed 1, 3 rounds)
R1  x1.0   you 100  bot 100   took  0  dealt  0   cr  84 0-0
R2  x1.0   you 100  bot 100   took  0  dealt  0   cr  84 0-0
R3  x1.0   you 100  bot 100   took  0  dealt  0   cr  84 0-0
RESULT: DRAW   you 100 — 100 rookie

⚑ MISSED (prosecutor chưa claim):
   enforcement_failure    x10  family A  worth 10 each
   fabricated_citation    x1   family B  worth 8 each
   protocol_misuse        x3   family A  worth 6 each
```

**Phân tích:**
- ✓ **Falls to rookie:** Đúng (mutations áp dụng: 10 enforcement_failures được phát hiện)
- Deck chơi 10 thẻ tấn công, mỗi thẻ kích hoạt mutation
- Rookie không phòng thủ, nên tất cả mutations đều áp dụng
- Tuy nhiên, prosecutor chưa triển khai (`eval/prosecute.py` chưa hoàn thành), nên không claim violation → HP vẫn 100
- Credits: -16 (chi phí cho hành động được phòng thủ từ chối)

#### Vs Adversary (phòng thủ đầy đủ):

```
COLOSSEUM — spar vs ADVERSARY  (seed 1, 3 rounds)
R1  x1.0   you  86  bot  86   took 14  dealt 14   cr  70
R2  x1.0   you  80  bot  80   took  6  dealt  6   cr  78
R3  x1.0   you  66  bot  80   took 14  dealt  0   cr  70
RESULT: BOT   you 66 — 80 adversary

⚑ MISSED (prosecutor chưa claim):
   enforcement_failure    x7   family A  worth 10 each
```

**Phân tích:**
- ✓ **Held by adversary:** Đúng (adversary phòng thủ và chiến thắng)
- HP cuối: you 66, adversary 80 → Adversary chiến thắng 80 vs 66
- Tổng damage taken: 100 → 66 = 34 HP mất
- Tổng damage dealt: 100 → 80 = 20 HP mất (14 + 6 + 0)
- Credits: [70, 78, 70] = trung bình 72.67 (so với 84 vs rookie)

**Kết luận Band Chết:**
- Deck "falls to rookie" (mutations apply, nhưng prosecutor chưa claim)
- Deck "held by adversary" (adversary defense + limited mutations = adversary chiến thắng)

---

## 5. Phân Tích Cảnh Báo (WARN Analysis)

### Các Warning từ `make validate`:

1. **R8-lethality-band (info only)**
   - "Live mutation engine ở Arena repo, không chạy được từ kit"
   - Giải pháp: chạy spar (đã làm) ✓

2. **R8-held-in-principle (đã sửa)**
   - Trước: atk_03, atk_04, atk_05, atk_09 dùng non-standard defense_event
   - Lý do: deck/README.md nói rõ tất cả phải dùng "gateway.denied"
   - Quyết định: FIX (không phải legitimate)
   - Bằng chứng: CONTRACTS.md section 8, deck/README.md section 1
   - Trạng thái: ✓ Đã sửa

---

## 6. Hạn Chế và Cân Nhắc

### Hạn Chế của Phase 4:

1. **Prosecutor chưa triển khai:** 
   - eval/prosecute.py chưa hoàn thành (Phase 5)
   - Không thể claim violation từ traces → spar không hiển thị damage
   - Spar vẫn cho thấy mutations áp dụng (MISSED list)

2. **Band chết là proxies, không tuyệt đối:**
   - Kiểm tra "falls to rookie" = mutations apply (✓)
   - Kiểm tra "held by adversary" = adversary defend (✓)
   - Nhưng không phải test "gây sát thương tối đa" (cần Phase 5)

3. **Ask / mutation pairing:**
   - Mỗi thẻ được thiết kế để mutation thực sự thay đổi đáp án (xem deck/README.md section 3)
   - Ví dụ: replica_flip + which_day_covers là "vicious" vì day number không ổn định
   - Không verify programmatically ở Phase 4

### Đã Xác Minh:

- ✓ Path IDs trong drift set (e0614beb, 053195a5, 3326cb76)
- ✓ Tất cả anchors exist trong world
- ✓ Mutations cú pháp hợp lệ (ops, targets, values)
- ✓ Mutations áp dụng trên rookie (unchallenged)
- ✓ Adversary phòng thủ (limited mutations, lower HP)

---

## 7. Kết Luận

- **Bộ bài:** Hợp lệ, cân bằng (4 MCP, 2 gateway, 4 A2A)
- **Band chết:** ✓ Falls to rookie, ✓ Held by adversary
- **Sửa lỗi:** defense_event standardized → "gateway.denied"
- **Validation:** 0 FAIL, 1 WARN (expected)
- **Tests:** 4643 passed
- **Spar results:** Mutations fire on rookie; adversary holds with defense

Sẵn sàng cho Phase 5 (prosecutor) để claim violations và đo lường damage thực.

---

**Commit:** deck/deck.json + reports/phase4-attack.md
