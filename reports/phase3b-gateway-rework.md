# Phase 3B: làm lại JOB 1-4 của `Gateway.decide()` (có kiểm chứng)

Mọi khẳng định dưới đây đã đối chiếu với code thật (`agent/strategy.py`, `kit/mcp/{specs,servers,hardmode,mutations,types}.py`, `kit/loop/agent.py`, `kit/referee/detectors.py`, `bots/*`, `spar.py`, `kit/world/*/drift.json`). File findings của Haiku được coi là chưa kiểm chứng; các chỗ sai được ghi ở mục 3 và sửa trong `phase3b-findings.md`.

## 1. Thiết kế từng JOB và sự thật làm căn cứ

**JOB 1 ROUTE**
- Từ chối route khai báo trong body (`args.route/_route/replica`): định tuyến chỉ theo header `mcp-replica` (operator/adversary đều làm vậy; `swap_replica`/`drop_header` trong `spar.py` đặt route vào args).
- `slides.search` -> `slides.query` qua `successor_of` (search deprecated, `TOOL_SPECS`); field không có trên query (vd `snippet`) bị bỏ.
- Replica: nếu yêu cầu `c` (header hoặc rev trong anchor) mà path_id nằm trong tập drift hoặc không xác định -> ép header `mcp-replica: w`. Căn cứ: `drift.json` có 16/22 deck drift và ở MỌI record `w_frames >= c_frames`; `detectors.stale_read` tính lỗi khi trích anchor `rev=c` của path drift. Tập drift được nhúng thành hằng `DRIFTING_PATH_IDS` (decide() không được I/O, ctx không có world). Không rewrite rev trong anchor vì 811/2015 frame `c` không có bản `w` cùng idx.
- Không dùng `pick_replica(known_drifting=True)`: nó trả `c`, ngược hướng với detector.

**JOB 2 ADMIT**
- `get_frame` (spec `needs_lease`) bị deny nếu `lease_id` rỗng hoặc không nằm trong `ctx.leases` (tuple các id sống do arena theo dõi; tin arena, không tự tính cửa sổ 3 call).
- Ghi (`is_write` trong `TOOL_SPECS` + `file_content_bug`, `registry.pin`) cần CẢ `if-match` và `idempotency-key` (`WRITE_HEADERS`, key viết thường). Thiếu -> deny (server sẽ trả `precondition_missing`, detector tính `write_violation`).
- Idempotency-key đã dùng cho một write đã cho qua -> deny (detector: key lặp = `write_violation`; hardmode: key lặp = `conflict`). State là thuộc tính instance.
- Retry y hệt sau `conflict` (409) -> deny. Đọc từ `ctx.history`; hình dạng entry KHÔNG được đặc tả trong kit (spar để rỗng) nên parser phòng thủ, không nhận ra thì bỏ qua. Đây là phần chưa kiểm chứng được trên arena thật.

**JOB 3 AUTHORIZE**
- `ctx.act` dạng `learner:sv-0417`; anchor trên dây là `Learner:sv-0417`; `record_mastery` dùng `args["anchor"]` (chính) hoặc `args["learner"]`; roster dùng `args["learner"]`. Parse `kind:name`, so sánh không phân biệt hoa thường, chấp nhận cả id trần `sv-0417`. Mục tiêu khác `act` -> deny (`authority_exceeded`). Không bao giờ dùng `ctx.sub`.
- `aud` (nếu có) phải là `server`, `mcp:server` hoặc `a2a:server`.
- Scope: đọc cần `wiki.read`; ghi cần `wiki.write:<server>` (đúng cách `registry.list_servers` dựng scope trong `servers.py`).

**JOB 4 BUDGET**
- Catalog trap (`is_catalog_trap`): bare/`*` tốn 12/10 cr. Viết lại thành MỘT field định danh qua `cheap_mask` (`name` cho list_servers, `term` cho list_terms) = 2 cr. Luôn viết lại, vì 12 cr vượt `ROUND_ALLOWANCE=11`.
- `fields=("*",)` trên tool không phải ghi: thu về mask mặc định nếu rẻ hơn và (vượt phần chia đều `credits/rounds_left` hoặc `BudgetPacer.is_affordable` với reserve động = 4 cr x số round còn lại). `BudgetPacer` được đồng bộ xuống theo `ctx.credits` (arena là nguồn chuẩn).
- Deny khi chi phí ước tính (`kit.mcp.specs.cost`) vượt số credit còn lại.

`decide()` đồng bộ, thuần, không I/O, không raise (lỗi nội bộ: ghi -> deny, đọc -> forward nguyên trạng). Import đã đưa lên đầu file.

## 2. Kết quả (spar, seed 1)

| Lệnh | Trước (c0d93ec) | Sau |
|---|---|---|
| rookie AS=defender | DRAW 100-100 | DRAW 100-100 |
| operator AS=defender | DRAW 20-20 | DRAW 20-20 |
| adversary AS=defender | YOU 72-0 | YOU 72-0 |
| `AS=all` rookie / operator / adversary | YOU 64-21 / YOU 82-0 / BOT 54-63 | giống hệt |

Điểm số HP không đổi, và đó là trung thực: HP chỉ giảm khi prosecutor của đối thủ chứng minh được claim; prosecutor của rookie không claim gì, nên DRAW 100-100 là do harness + prosecutor thụ động chứ không phải dấu hiệu gateway lỗi. Tín hiệu thật nằm ở trace của gateway mình (script chẩn đoán chạy `spar._exchange` trên 14 thẻ x 3 bot, đếm violation referee phát hiện trên trace CỦA MÌNH):

| Violation trên trace của gateway mình | Trước | Sau |
|---|---|---|
| enforcement_failure (cộng dồn 42 lượt chạy thẻ) | 26 | 14 (còn lại chỉ ở thẻ shadow, forged_card, replace_aud) |
| protocol_misuse / write_violation / wasteful | 0 | 0 |
| fabricated_citation | 15 | 15 (do `rng` của spar chèn anchor ma, không phụ thuộc gateway) |

Bảng MISSED của spar liệt kê trace của BOT (prosecutor của mình bỏ sót), không phải lỗi của gateway mình; findings mục 8 đã hiểu nhầm điểm này. `make test`: 4643 passed, 4 skipped (31 test mới trong `tests/test_gateway_jobs.py`).

## 3. Đính chính findings của Haiku
1. `pick_replica`: findings khuyên drift -> `c` và nói "canonical fresher". SAI: working mới là bản mới hơn ở mọi record drift, và `stale_read` phạt trích `rev=c`. Docstring của chính `strategy.py` cũng cảnh báo.
2. "Load `drift.json` lúc init / `world.drifts()`": không khả thi (cấm I/O, ctx không có world). Dùng hằng nhúng sẵn.
3. `cheap_mask(..., ())` KHÔNG "rỗng vô dụng": `()` là sentinel "mask mặc định", tức vẫn trả 12 cr, nên bản cũ không tiết kiệm gì. Cũng không nên deny catalog trap: thu hẹp là đủ và không chặn discovery.
4. Header: key phải viết thường (`kit/loop/agent.py` lowercase), và ghi cần cả `if-match` lẫn `idempotency-key` (findings chỉ nói "If-Match" hoa/thường lẫn lộn).
5. "Không có bảng tool->scope": có, suy ra trong `servers.py` (`wiki.read` + `wiki.write:<server>`).
6. Learner: bỏ sót `args["anchor"]` (khóa chính của `record_mastery`) và dạng `Learner:`; đề xuất của findings dùng `cmd.ctx.act` (không tồn tại) và `f"learner:{target}"` gây tiền tố đôi.
7. BudgetPacer: `credits_left` là property, không phải method; `is_affordable` mặc định reserve 0.5 sẽ deny mọi call sau khi tiêu 50 cr (over-deny); `_estimate_cost` là hàm bịa (dùng `kit.mcp.specs.cost(server, tool, fields, n_rows)`); `ResultCache` không dùng được vì decide() không thấy kết quả.
8. "Bug 3 thiếu cửa sổ lease 3 call": không cần; `ctx.leases` do arena giữ.
9. Giải thích DRAW 100-100 và danh sách MISSED sai (xem mục 2).
10. `registry.provenance` trả etag/rev của trang đó, không phải cờ drift.

## 4. Giới hạn còn lại
- `shadow`, `forged_card`, `replace_aud` (khi giá trị trùng server được gọi) vẫn lọt trong spar: tín hiệu duy nhất là header `x-server-fingerprint`/`x-card-signature` do `spar.py` tự bịa, kit không định nghĩa nghĩa của chúng; không thêm check theo harness để tránh gaming. Phòng thủ thật cho A2A là admission qua `note_card` (ngoài phạm vi JOB 1-4).
- Ép header `mcp-replica` chưa chắc có tác dụng thật: server trong kit không đọc header này; không thể rewrite rev của anchor vì thiếu bản `w` tương ứng.
- Tập drift gắn với world `df8c55dabb35`; world khác thì phải cập nhật.
- Parser `ctx.history` cho 409 chưa kiểm chứng trên arena thật.
- Prosecutor (`eval/prosecute.py`) mới là đòn bẩy làm HP đổi trong spar; chưa động tới.
- `python -m agent.strategy` demo vẫn fail (assert 11cr cũ so với giá đã retune 9cr): lỗi có sẵn, không thuộc phần này.
