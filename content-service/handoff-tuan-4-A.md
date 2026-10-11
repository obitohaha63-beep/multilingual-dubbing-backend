# Handoff Tuần 4 — content-service (Thành viên A)

## 1. Đã làm

- Migration **v3**: bảng `narration_cache` (FK `location_id` → `locations.id`,
  `ON DELETE CASCADE`, unique `(location_id, requested_lang)`). Mọi kết nối
  SQLite bật `PRAGMA foreign_keys=ON`.
- `GET` / `PUT /locations/{id}/narrations/{language}`: cache hit/miss/hết hạn
  (TTL mặc định 7 ngày, `NARRATION_TTL_DAYS`), upsert 1 transaction, validate
  `lang`/`voice`/`text`/`audio_filename`.
- `PATCH /locations/{id}/mark-ready`: tính `is_ready` từ cache, không nhận
  giá trị từ client.
- Invalidation: sửa nội dung/tọa độ nguồn, discover fetch thấy bài đổi, hoặc
  xóa Location → xóa narration liên quan và đặt lại `is_ready`.
- Discovery: cache hit không đổi `last_synced_at`; unique conflict giữa hai
  discovery đồng thời được xử lý bằng rollback rồi đọc lại.
- Giữ nguyên CRUD cũ, `/nearby`, `/discover` (vẫn trả `source_title`,
  `source_page_id`, `source_url`, `distance_m`) và migration tuần 3.

Hai điểm mình làm thêm so với đề, cần biết khi tích hợp:
1. `PUT narration` cũng tự tính lại `is_ready` (cùng transaction) để cờ không
   bị lệch khi B hạ `audio_ready` xuống `text_ready`. B vẫn nên gọi `PATCH`.
2. Migration chỉ sao lưu DB khi **có version chờ áp dụng** (trước đó mỗi lần
   khởi động đều sinh một file `.bak`).

## 2. File thay đổi

```
content-service/
├── main.py                  (sửa: migration v3, NarrationPut, route narration, mark-ready, invalidation)
├── models.py                (sửa: bật foreign_keys, thêm NarrationCache)
├── .env.example             (sửa: + NARRATION_TTL_DAYS)
├── docs/api-contract.md     (sửa: phần narration, invalidation, migration)
├── docs/content-flow.md     (mới)
├── docs/handoff-tuan-4-A.md (file này)
└── tests/test_content_service.py (sửa: + test tuần 4, giữ test tuần 3)
```
Không thêm file code mới; `requirements.txt` và `Dockerfile` không đổi.

## 3. Lệnh và kết quả đã chạy thật

```bash
pip install -r requirements.txt
python -m pytest tests/ -v                 # kết quả: 56 passed
python -m uvicorn main:app --port 8001     # migration tự chạy khi khởi động
```

- **56/56 test pass** (15 test tuần 3 giữ nguyên + 41 test tuần 4: migration v3,
  FK enforced, cache miss/hit/expired, fallback đúng `lang`, upsert cùng key hai
  lần, location thiếu, validation filename/voice/trạng thái, mark-ready theo
  ngôn ngữ, invalidation khi sửa/xóa, discovery cache-hit/refresh/trùng/race).
- Đã thử cố ý phá 3 chỗ (tắt FK, cho cache-hit ghi đè `last_synced_at`,
  `is_ready` luôn true): test tương ứng đều chuyển đỏ.
- **Chạy thật qua uvicorn + curl**: tạo DB bằng code tuần 3 (1 địa điểm) →
  khởi động code tuần 4: tự sao lưu và áp dụng v3 → `PUT` audio_ready
  (fallback ja→vi) → `PATCH mark-ready` trả `is_ready=true` → filename
  `../../etc/x.mp3` bị `422` → **restart**: không migrate/sao lưu lại, `GET`
  narration vẫn `cache_hit=true`, id/nội dung cũ còn nguyên → `DELETE`
  location xóa luôn cache (còn 0 dòng).
- **Chưa kiểm tra**: Docker/Compose, và gọi Wikimedia thật (test dùng mock).

## 4. Mẫu request/response cho B

Lưu text trước khi chạy TTS:
```http
PUT /locations/12/narrations/ja
{"lang":"vi","text":"Chợ Bến Thành là ...","source_url":"https://vi.wikipedia.org/wiki/Chợ_Bến_Thành",
 "target_title":"Chợ Bến Thành","is_fallback":true,"reason":"target_language_unavailable",
 "audio_status":"text_ready"}
→ 200 {"created":true,"narration":{... "audio_status":"text_ready","audio_filename":null,
        "expires_at":"2026-10-18T09:30:00"}}
```
Sau khi có MP3:
```http
PUT /locations/12/narrations/ja
{... cùng nội dung ..., "voice":"vi-VN-HoaiMyNeural","text_hash":"9f2c1a",
 "audio_filename":"9f2c1a.mp3","audio_status":"audio_ready"}
→ 200 {"created":false,"narration":{...}}

PATCH /locations/12/mark-ready
→ 200 {"id":12,"is_ready":true,"audio_ready_languages":["ja"]}
```
Đọc cache:
```http
GET /locations/12/narrations/ja → 200 {"cache_hit":true,"narration":{...}}
GET /locations/12/narrations/en → 200 {"cache_hit":false,"narration":null}
GET /locations/999/narrations/ja → 404
```
Lỗi thường gặp: `422` khi `audio_ready` thiếu `audio_filename/voice/text_hash`,
filename có đường dẫn, `voice` không khớp `lang`, `is_fallback` mâu thuẫn với
`lang`, hoặc body có trường lạ (vd `audio_url`).
**Lưu ý:** `requested_lang` là `{language}` trên path; `lang` là ngôn ngữ thật
của `text`. Gateway tự ghép `/audio/<audio_filename>`.

## 5. Giải thích ngắn (để trình bày)

- **Cache nội dung ≠ cache phát lại ở client.** Cache nội dung (bảng
  `narration_cache`, 7 ngày) lưu text và tên MP3 đã tạo, bền vững qua restart,
  dùng chung cho mọi người dùng — để không dịch/tạo audio lại. Cache phát lại
  nằm trong trình duyệt, chỉ nhớ "lần ghé này đã phát `location_id + ngôn ngữ`
  chưa" để không phát lặp; Content không biết gì về nó.
- **`is_ready` không thay thế cache theo ngôn ngữ.** Nó chỉ nói "có ít nhất 1
  ngôn ngữ đã có MP3". Muốn biết ngôn ngữ cụ thể phải `GET .../narrations/{language}`.
- **Vì sao SQLite chỉ Content truy cập:** một nơi giữ khóa ngoại, TTL và
  invalidation; B/C không thể làm lệch dữ liệu.

## 6. Đề xuất commit (chưa commit/push)

```
feat(content-service): tuan 4 - narration cache, mark-ready, invalidation, migration v3
```
Chỉ `git add content-service/`.
