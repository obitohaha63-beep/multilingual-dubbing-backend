# Handoff Tuần 5 — content-service (Thành viên A)

## 1. Đã làm (so với Tuần 4)

- `/locations/nearby` và `/locations/discover` trả thêm `is_ready` (đủ field
  Location). `/nearby` trả mọi điểm trong `max_distance`, bỏ record thiếu GPS.
- **Mới** `DELETE /locations/{id}/narrations/{language}`: 404 nếu location
  không có, 422 nếu ngôn ngữ sai, key chưa có vẫn 200 (`deleted:false`).
- **Discovery khi Wikimedia lỗi**: có bài Wikimedia đã lưu trong bán kính →
  200 + `stale:true` + `upstream_error` (giữ nguyên `last_synced_at`);
  không có → 503 + `detail.code` (`timeout|rate_limited|upstream_error`).
- Fixture offline (2 điểm GPS radius 80m cách ~800m; điểm 1 thiếu `ja`, điểm 2 đủ `ja`)
  nằm ngay trong test, không có file/script seed riêng nên không đụng DB thật.
- Không đổi schema → **không cần migration mới**. `models.py`, `requirements.txt`,
  `Dockerfile`, `.env.example` giữ nguyên.

## 2. File thay đổi

```
content-service/
├── main.py                  (viết gọn 946 -> ~600 dòng; + is_ready ở candidate, DELETE narration, discover fallback SQLite)
├── docs/api-contract.md     (sửa)
├── docs/content-flow.md     (sửa)
├── docs/handoff-tuan-5-A.md (file này)
└── tests/test_content_service.py (viết gọn lại, gộp test trùng, vẫn đủ tuần 3-5)
```

## 3. Lệnh và kết quả đã chạy thật

```bash
python -m pytest tests/ -v                 # 53 passed
python -m uvicorn main:app --port 8001
```
- Đã chạy uvicorn + curl (DB tạm): `DELETE .../narrations/en` lần 1 `deleted:true`,
  lần 2 `deleted:false`.
- Test mới bao gồm: nearby nhiều điểm/không GPS/không gọi Wikimedia; DELETE
  idempotent; discover stale (429, timeout) và 503 khi không có dữ liệu; cache
  discovery lưu cả ô rỗng; `text_ready → audio_ready → mark-ready` + restart giữ
  dữ liệu; fixture có điểm trong và ngoài radius.
- **Chưa kiểm tra**: Docker/Compose, Wikimedia thật (test dùng mock).

## 4. JSON mẫu cho B

Upsert text trước, audio sau (cùng URL, xem Tuần 4):
```http
PUT /locations/12/narrations/ja
{"lang":"vi","text":"Chợ Bến Thành là ...","source_url":"https://vi.wikipedia.org/wiki/Chợ_Bến_Thành",
 "target_title":"Chợ Bến Thành","is_fallback":true,"reason":"target_language_unavailable",
 "voice":"vi-VN-HoaiMyNeural","text_hash":"9f2c1a","audio_filename":"9f2c1a.mp3",
 "audio_status":"audio_ready"}
→ 200 {"created":true|false,"narration":{...}}
```
Loại cache hỏng (ví dụ mất file MP3):
```http
DELETE /locations/12/narrations/ja → 200 {"deleted":true,"id":12,"language":"ja"}
```
Discover khi upstream lỗi nhưng có dữ liệu cũ:
```json
{"candidates":[{"id":7,"name":"...","is_ready":false}],"cache_hit":true,"stale":true,"upstream_error":"rate_limited"}
```
`requested_lang` = `{language}` trên path; `lang` = ngôn ngữ thật của `text`.

## 5. Giải thích ngắn

- **Data ownership**: chỉ Content mở SQLite; B/C đọc ghi qua REST. Nhờ vậy khóa
  ngoại, TTL, unique, invalidation nằm một chỗ; không bị khóa file khi nhiều
  process ghi; mỗi service đổi độc lập miễn giữ contract.
- **Vì sao không trả `[]` khi Wikimedia lỗi**: `[]` nghĩa là "khu vực không có địa
  điểm" — sai sự thật khi thực ra bị 429. Dùng dữ liệu đã lưu (`stale`) hoặc 503.

## 6. Đề xuất commit (chưa commit/push)

```
feat(content-service): tuan 5 - delete narration, stale discovery fallback, is_ready in candidates
```
Chỉ `git add content-service/`.
