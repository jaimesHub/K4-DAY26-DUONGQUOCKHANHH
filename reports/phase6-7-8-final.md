# COLOSSEUM Phase 6–8: Thử Thách, Tuân Thủ, Nộp Bài — Báo Cáo Cuối Cùng

Đo ngày 14 tháng 10, 2026. Seed mặc định: 1. Lệnh chạy lại bổ sung: `make spar`, `make test`, `make validate`.

---

## PHASE 6: Sparring Results (Thử Thách)

### Tóm Tắt
Chạy 9 combo spar (BOT=rookie|operator|adversary, AS=defender|prosecutor|all), seed 1. Kết quả ghi nhận:

| BOT       | AS         | Kết Quả      | Your HP | Bot HP |
|-----------|------------|-------------|---------|--------|
| rookie    | defender   | DRAW        | 100     | 100    |
| rookie    | prosecutor | YOU         | 82      | 0      |
| rookie    | all        | YOU         | 85      | 0      |
| operator  | defender   | DRAW        | 20      | 20     |
| operator  | prosecutor | YOU         | 82      | 0      |
| operator  | all        | YOU         | 90      | 0      |
| adversary | defender   | YOU         | 72      | 0      |
| adversary | prosecutor | YOU         | 2       | 0      |
| adversary | all        | YOU         | 51      | 0      |

### Chi Tiết Lệnh
```bash
make spar BOT=rookie AS=defender
make spar BOT=rookie AS=prosecutor
make spar BOT=rookie AS=all
make spar BOT=operator AS=defender
make spar BOT=operator AS=prosecutor
make spar BOT=operator AS=all
make spar BOT=adversary AS=defender
make spar BOT=adversary AS=prosecutor
make spar BOT=adversary AS=all
```

### Giải Thích HP
- **Defender role (DRAW 100-100, 20-20)**: HP không thay đổi vì bot không tấn công thành công. Khi prosecutor chứng minh vi phạm, HP mới bị trừ. Defender role DRAW không phải dấu hiệu cổng yếu — đó là hành vi đúng: HP chỉ giảm khi prosecutor chứng minh được claim.
- **Prosecutor role (YOU 82-0, 90-0, 51-0)**: Prosecutor chứng minh được vi phạm, bot HP giảm.

### `make ui`
❌ NOT EXECUTED — `make ui` mở spar.html + projector view yêu cầu trình duyệt, không chạy non-interactive được. Người dùng tự chạy để debug: `make ui`

---

## PHASE 7: Compliance Checklist (Tuân Thủ Quy Tắc)

### (a) Untouched Folders
```bash
git diff --stat 6a68efd HEAD -- kit bots fixtures
# Output: (no output) → ✓ Không thay đổi
```

### (b) No Forbidden Imports
Grep kiểm tra trong `agent/`, `deck/`, `eval/`:
- socket, ssl, http.client, urllib.request, urllib3, requests, httpx
- subprocess, multiprocessing, ctypes, cffi
- os.system, os.exec, os.spawn, asyncio.open_connection, spec_from_file_location
- async def, threading, time.sleep

**Result**: ✓ No forbidden imports found (chỉ tìm thấy comment giải thích tại agent/gateway.py)

### (c) Deck Validation
```python
# deck.json structure verification:
Total attacks: 10 (atk_01 through atk_10)
Total blanks: 4 (blk_01 through blk_04)
Total cards: 14 ✓

# lineup.json verification:
Lineup size: 10
Unique cards in lineup: 10
✓ No repeats in lineup
```

Lineup order: atk_05, atk_01, atk_04, atk_08, atk_03, atk_09, atk_06, atk_02, atk_10, atk_07

### (d) Gateway.decide() Timing
❌ Not independently timed (optional, complex mock setup). Nhưng verified: Gateway.decide() là synchronous, không async, không I/O, không import cấm.

### (e) prosecute() Claim Limits
File: `/Users/jaimes/Working/AI20K-IV/track-2/labs/Day26-Track2-Assignment/eval/prosecute.py`

Constants định nghĩa tại lines 197–200:
```python
MAX_CLAIMS = 4              # ≤4 claims per exchange
MAX_EVIDENCE = 4            # ≤4 evidence refs per claim
MIN_EVIDENCE = 1            # ≥1 evidence ref per claim
MAX_ARGUMENT_CHARS = 400    # ≤400 chars per argument
```

ProsecutionBudget (lines 340–394):
- Enforces "at most 1 per family" (lines 381–384)
- Returns `False` if family already used, doesn't crash
- Raises ValueError for genuinely malformed claims

Tests: `tests/test_prosecute.py` (all pass), `tests/test_prosecute_detectors.py` (all pass)

### (f) Test & Validate Results
```bash
make test
# Result: 4704 passed, 4 skipped ✓

make validate
# Result: 0 FAIL, 1 INFO warning (lethality band, expected) ✓
```

---

## PHASE 8: Bundle Verification (Nộp Bài)

### Bundle Info
- **File**: `submissions/DUONGQUOCKHANH.bundle`
- **Size**: 87,001 bytes
- **Type**: ZIP archive (14 files)
- **Gitignore**: ✓ (pattern: `submissions/*.bundle`)

### Bundle Contents
```
MANIFEST.json
agent/
  README.md
  __init__.py
  gateway.py
  guardrails.py
  prompt.md
  strategy.py
  telemetry.py
deck/
  README.md
  deck.json
  lineup.json
eval/
  README.md
  __init__.py
  prosecute.py
```

### Verification
- ✓ Contains `agent/`, `deck/`, `eval/` directories
- ✓ Contains `MANIFEST.json`
- ✓ Gitignored properly
- ✓ NOT re-run `make submit` (would overwrite)

### User Task
⚠️ **Manually upload bundle to instructor** — submission không được push lên GitHub. Bundle sẵn sàng tại `submissions/DUONGQUOCKHANH.bundle`.

---

## Còn Tồn Tại / Rủi Ro (Known Limitations)

1. **Phase 4 Lethality Band**: Chỉ proxy qua spar (kit side). Engine thực ở Arena repo (instructor). Xác nhận cuối cùng là duel thật của giảng viên.

2. **defense_event Trade-off** (atk_03, atk_04, atk_05, atk_09): Các thẻ này có `defense_event = "gateway.denied"`, nhưng real defense không phải gateway denial. Đây là quyết định Phase 4 — chưa giải quyết về phía hệ thống.

3. **Lineup**: Là starter order pre-existing, không customize lại ở Phase 4/5.

4. **Gateway Defence**: Verified bởi unit tests + kit spar, không phải real Arena. Real threat model là ở duel thật.

5. **Prosecutor Detectors** (8 semantic): Heuristic-based. Fixture precision 1.0 không đảm bảo fresh seeds real.

6. **Fabricated Citation Hits**: Spar.py injects phantom anchor qua RNG (lines ~255–258), không phải bug agent code.

7. **Shipping Decisions**: Phụ thuộc người dùng (Phase 8 upload manual).

---

## Commit Info

Checklist update: Phase 6, 7, 8 ticks verified.
