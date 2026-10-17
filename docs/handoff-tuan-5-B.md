# Handoff Tuần 5 — Thành viên B (translate-audio-service)

Tài liệu bàn giao kỹ thuật cho Thành viên C (gateway-service) và toàn đội sau khi hoàn thành `POST /narrate` Tuần 5.

---

## 1. Tóm tắt công việc Tuần 5 của B

- **Thêm `POST /narrate`**: Endpoint thuyết minh tích hợp — thay thế việc C phải tự gọi chuỗi 3 HTTP (localize → TTS → cache). C chỉ cần gọi 1 endpoint này.
- **Tái sử dụng hàm nghiệp vụ**: Không tự gọi HTTP vòng lại `/localized-content` hay `/text-to-speech`; gọi trực tiếp `_resolve_localized_text()` và `tts.synthesize_speech()`.
- **Cache-first qua content-service (A)**: B luôn hỏi A trước (`GET /locations/{id}/narrations/{lang}`); chỉ khi miss mới gọi Wikimedia/Edge.
- **Hai trạng thái cache**: `text_ready` (text đã lưu, TTS chưa xong) và `audio_ready` (MP3 đã có).
- **Lock theo (location_id, target_lang)** trong một worker: GPS tick liên tục không gây nhiều Wikimedia/TTS.
- **Fallback graceful**: Thiếu bài ngôn ngữ đích → dùng source_text, `is_fallback=true`, `reason` rõ ràng.
- **Kiểm tra file vật lý**: Cache `audio_ready` nhưng MP3 mất → tái tạo, không trả `cache_hit` giả.
- **Giữ nguyên**: `/translate` (legacy), `/localized-content`, `/text-to-speech`, `/audio/{filename}/status`.

---

## 2. Input / Output `POST /narrate`

### 2.1. Request Body (JSON)

```json
{
  "location_id": 1,
  "source_title": "Nhà thờ Đức Bà Sài Gòn",
  "source_lang": "vi",
  "target_lang": "en",
  "source_text": "Nhà thờ chính tòa Đức Bà Sài Gòn là...",
  "source_page_id": "12345",
  "source_url": "https://vi.wikipedia.org/wiki/..."
}
```

| Trường | Kiểu | Bắt buộc | Validate |
|---|---|---|---|
| `location_id` | int | Có | > 0 |
| `source_title` | str | Có | Không được rỗng |
| `source_lang` | str | Có | Allowlist: `vi`, `en`, `ja` |
| `target_lang` | str | Có | Allowlist: `vi`, `en`, `ja` |
| `source_text` | str | Có | Không được rỗng (description_source của Content) |
| `source_page_id` | str | Có | Chuỗi số nguyên |
| `source_url` | str | Không | URL http/https |

Trường sai → `422 Unprocessable Entity`.

### 2.2. Response Body (JSON — 200 OK)

```json
{
  "requested_lang": "en",
  "lang": "en",
  "translated_text": "Notre-Dame Cathedral Basilica of Saigon is...",
  "audio_url": "/audio/f7e8d9...mp3",
  "audio_filename": "f7e8d9...mp3",
  "voice": "en-US-JennyNeural",
  "text_hash": "a1b2c3...64chars",
  "source_url": "https://en.wikipedia.org/wiki/Notre-Dame_Cathedral...",
  "is_fallback": false,
  "reason": null,
  "narration_status": "audio_ready",
  "cache_hit": true
}
```

| Trường | Ý nghĩa |
|---|---|
| `requested_lang` | Ngôn ngữ người dùng yêu cầu |
| `lang` | Ngôn ngữ **thực tế** của text (có thể khác `requested_lang` khi fallback) |
| `translated_text` | Text dùng cho TTS |
| `audio_url` | Đường dẫn `/audio/<filename>.mp3` (B-local); C phải ghép host |
| `audio_filename` | Tên file MP3 thuần (basename) |
| `voice` | Voice Edge TTS thực tế |
| `text_hash` | SHA256 của text chuẩn hóa (để A lưu SQLite) |
| `source_url` | URL Wikipedia thực tế (của bài ngôn ngữ đích nếu có) |
| `is_fallback` | `true` khi không có bài ngôn ngữ đích → dùng text nguồn |
| `reason` | `null` hoặc `target_language_unavailable` / `empty_extract` / `localization_unavailable` |
| `narration_status` | `audio_ready` (có MP3) hoặc `text_ready` (chỉ có text) |
| `cache_hit` | `true` nếu kết quả từ cache của A (không gọi Wikimedia/TTS mới) |

> **Quan trọng**: `lang` luôn là ngôn ngữ thực tế của `translated_text` — **không bao giờ gán `requested_lang` vào text nguồn gốc**.

---

## 3. Flow xử lý chi tiết

```
POST /narrate
  ↓
[Lock theo (location_id, target_lang)]
  ↓
GET /locations/{id}/narrations/{target_lang} → content-service (A)
  ├── Cache HIT audio_ready
  │     ├── File MP3 tồn tại → trả ngay (cache_hit=True), KHÔNG gọi Wikimedia/Edge
  │     └── File MP3 mất → tái tạo TTS từ cached text
  ├── Cache HIT text_ready → retry TTS (KHÔNG gọi Wikimedia)
  │     ├── TTS thành công → PUT audio_ready → PATCH mark-ready → trả audio_ready
  │     └── TTS vẫn lỗi → trả 200 text_ready, audio_url=null
  └── Cache MISS
        ↓
    _resolve_localized_text()  [gọi hàm nghiệp vụ TRỰC TIẾP, không HTTP]
        ├── target_lang == source_lang → dùng source_text, không gọi Wikimedia
        └── target_lang != source_lang → get_langlink() + get_extract_by_title()
              ├── Không có langlink → fallback source_text, is_fallback=True, reason='target_language_unavailable'
              ├── Extract rỗng → fallback source_text, is_fallback=True, reason='empty_extract'
              └── Wikimedia lỗi → fallback source_text, reason='localization_unavailable'
        ↓
    PUT text_ready vào A (TRƯỚC KHI chạy TTS)
    ↓ PUT thất bại → 503 cache_upsert_failed (KHÔNG gọi TTS)
        ↓
    tts.synthesize_speech()  [tái sử dụng hàm Tuần 4]
        ├── Thành công → PUT audio_ready → PATCH mark-ready → trả audio_ready
        └── Lỗi/Timeout → giữ text_ready trong A, trả 200 text_ready, audio_url=null
```

---

## 4. Bảng lỗi và Fallback

| Tình huống | HTTP | `code` / `narration_status` | Mô tả |
|---|---|---|---|
| Validate lỗi | 422 | - | Ngôn ngữ không hợp lệ, thiếu trường |
| location không tồn tại | 404 | `location_not_found` | A trả 404 |
| Content-service timeout/down | 503 | `cache_service_unavailable` | Không tạo audio vô tội vạ |
| PUT upsert thất bại | 503 | `cache_upsert_failed` | Không gọi TTS |
| TTS lỗi, đã có text | 200 | `text_ready` | `audio_url=null`, client dùng SpeechSynthesis |
| TTS lỗi, chưa có text | 503 | `no_text_available` | Không có gì để trả |
| Thiếu bài ngôn ngữ đích | 200 | `audio_ready`, `is_fallback=true` | Dùng source_text với voice nguồn |
| Wikimedia lỗi | 200 | `audio_ready`, `is_fallback=true` | `reason='localization_unavailable'` |

---

## 5. Giải thích kỹ thuật cốt lõi

### 5.1. Cached Text (text_ready)

Khi B có text nhưng TTS chưa xong (hoặc lỗi):
- B `PUT` vào SQLite của A với `audio_status: text_ready`, **không có `audio_filename`** (rule của A).
- Response trả `translated_text` + `lang` → Client có thể dùng **Web Speech API** (`SpeechSynthesis`) tạm thời.
- Lần sau B hỏi A thấy `text_ready` → **retry TTS mà không gọi Wikimedia** nữa.

### 5.2. Cached File (audio_ready)

Khi cache A báo `audio_ready`:
- B đọc `audio_filename` từ A rồi **kiểm tra file vật lý** (`is_file() and stat().st_size > 0`) bằng logic local của B.
- **Không HTTP vào `/audio/{filename}/status` của chính mình** (tránh circular call).
- Nếu file mất → tái tạo từ `text` đã cache trong A — **không cần gọi Wikimedia**.

### 5.3. SQLite Ownership

- **A (content-service)** sở hữu SQLite `content.db` và bảng `narration_cache`.
- **B** truy cập qua REST API của A: `GET/PUT /locations/{id}/narrations/{lang}`, `PATCH /locations/{id}/mark-ready`.
- **B không bao giờ mở `content.db` trực tiếp**.
- `PUT text_ready` xảy ra **TRƯỚC** TTS để tránh TTS chạy nhiều lần không kiểm soát nếu crash giữa chừng.

### 5.4. Mark-Ready

- Sau khi `PUT audio_ready` thành công, B gọi `PATCH /locations/{id}/mark-ready`.
- A tính lại `is_ready` của Location dựa trên số ngôn ngữ `audio_ready` còn hạn.
- **`is_ready` của Location KHÔNG thay thế** kiểm tra cache theo từng ngôn ngữ. B luôn hỏi `GET narrations/{lang}` cụ thể.

### 5.5. Cache Key và Invalidation

- Cache key trong A: `(location_id, requested_lang)` — duy nhất theo địa điểm và ngôn ngữ yêu cầu.
- `requested_lang` là key để nhiều GPS tick cùng location + ngôn ngữ không tạo trùng.
- `text_hash` và `voice` trong row giúp phát hiện nội dung thay đổi (invalidation đúng nội dung).
- Khi A thay đổi `description_source` → A tự `_invalidate_narrations()` → B sẽ gặp cache miss và rebuild toàn bộ.

### 5.6. Concurrent Lock trong một Worker

- Lock `asyncio.Lock` theo `(location_id:target_lang)` trong bộ nhớ — hiệu quả trong demo single-worker.
- 2 GPS tick đồng thời cùng địa điểm + ngôn ngữ → request thứ 2 đợi request thứ 1 xong (không gọi Wikimedia/TTS trùng).
- **Giới hạn**: Lock in-memory chỉ hiệu quả trong 1 tiến trình Python. Scale lên cần Distributed Lock (Redis Lock, portalocker).

---

## 6. Lệnh và kết quả test

### 6.1. Chạy toàn bộ test suite

```bash
cd translate-audio-service
.venv\Scripts\pytest tests/test_translate_audio.py -v
```

**Kết quả: 58/58 tests PASSED (100%)**

- 44 tests cũ (Tuần 1, 2, 3, 4) giữ nguyên và pass.
- 14 tests mới (Tuần 5 — `/narrate`):

| Test Case | Kịch bản kiểm tra |
|---|---|
| `test_narrate_vi_vi_cache_miss_success` | Cache miss, vi→vi, dùng source_text |
| `test_narrate_vi_en_cache_miss_success` | Cache miss, vi→en, Wikimedia thành công |
| `test_narrate_vi_ja_cache_miss_success` | Cache miss, vi→ja, Wikimedia thành công |
| `test_narrate_ja_missing_langlink_fallback_vi` | Thiếu ja langlink → fallback vi, is_fallback=True |
| `test_narrate_cache_hit_audio_ready_file_ok_no_upstream` | Cache HIT audio_ready + file OK, KHÔNG gọi Wikimedia/Edge |
| `test_narrate_cache_hit_audio_ready_file_missing_regenerates` | Cache HIT audio_ready + file mất → tái tạo, cache_hit=False |
| `test_narrate_cache_hit_text_ready_retry_tts_success` | text_ready → retry TTS → audio_ready, cache_hit=True |
| `test_narrate_tts_fails_after_text_stored_returns_text_ready` | TTS lỗi sau PUT text_ready → 200 text_ready |
| `test_narrate_content_service_timeout_returns_503` | A timeout → 503 cache_service_unavailable |
| `test_narrate_upsert_fails_returns_503` | PUT lỗi → 503 cache_upsert_failed, TTS không được gọi |
| `test_narrate_location_not_found_returns_404` | A trả 404 → 404 location_not_found |
| `test_narrate_concurrent_same_key_lock` | 2 request đồng thời → TTS chỉ gọi ≤ 1 lần |
| `test_narrate_response_audio_url_format_and_metadata` | audio_url dạng `/audio/`, đủ 12 trường metadata |
| `test_narrate_unsupported_language_returns_422` | Lang không hợp lệ → 422 |
| `test_narrate_wikimedia_error_fallback` | Wikimedia lỗi → fallback, reason=localization_unavailable |

### 6.2. Kiểm tra thủ công (khi cả A và B đang chạy)

```bash
# 1. Tạo location trong A trước
curl -X POST http://localhost:8001/locations \
  -H "Content-Type: application/json" \
  -d '{"name":"Nhà thờ Đức Bà","description_vi":"Nhà thờ chính tòa Đức Bà Sài Gòn.", "target_languages":"vi,en,ja"}'

# 2. Gọi narrate (location_id từ response trên)
curl -X POST http://localhost:8002/narrate \
  -H "Content-Type: application/json" \
  -d '{
    "location_id": 1,
    "source_title": "Nhà thờ Đức Bà Sài Gòn",
    "source_lang": "vi",
    "target_lang": "en",
    "source_text": "Nhà thờ chính tòa Đức Bà Sài Gòn là công trình kiến trúc biểu tượng.",
    "source_page_id": "12345",
    "source_url": "https://vi.wikipedia.org/wiki/..."
  }'
# → narration_status: "audio_ready", cache_hit: false

# 3. Gọi lần 2 (cùng payload) → cache_hit: true
# 4. Xóa file MP3 → gọi lại → tái tạo, cache_hit: false
# 5. Kiểm tra is_ready trong A
curl http://localhost:8001/locations/1
# → is_ready: true
```

---

## 7. Cấu hình môi trường mới (Tuần 5)

Thêm vào `.env` (và `docker-compose.yml` nếu dùng):

```env
# URL nội bộ của content-service (A)
CONTENT_SERVICE_URL=http://content-service:8001

# Timeout HTTP đến content-service (giây)
CONTENT_SERVICE_TIMEOUT=5.0
```

---

## 8. Danh sách file thay đổi

```
translate-audio-service/
├── main.py    (SỬA: NarrateRequest, _resolve_localized_text,
│              _cs_get_narration, _cs_put_narration, _cs_patch_mark_ready,
│              _get_narrate_lock, POST /narrate full flow)
└── tests/test_translate_audio.py  (SỬA: 14 test cases Tuần 5)

docs/
├── api-contract.md     (SỬA: bổ sung POST /narrate)
└── handoff-tuan-5-B.md (TẠO MỚI: tài liệu này)
```

---

## 9. Lưu ý cho C (gateway-service)

1. **Gọi `POST /narrate` thay vì chuỗi 3 HTTP**: C không cần tự gọi `/localized-content` → `/text-to-speech` → PUT cache.
2. **`audio_url` là đường dẫn nội bộ B**: Dạng `/audio/<filename>.mp3`. C ghép host thành URL public: `http://<gateway>:8000/audio/<filename>` hoặc proxy.
3. **Xử lý `text_ready`**: Khi `narration_status: text_ready` và `audio_url: null`:
   - Client có thể dùng `translated_text` + `lang` với Web Speech API.
   - Hoặc retry lại `/narrate` sau vài giây (B sẽ retry TTS từ cached text).
4. **`is_fallback: true`**: `lang` thực tế khác `requested_lang`. Hiển thị "Đang phát tiếng Việt (chưa có bản dịch tiếng Nhật)" cho user nếu cần.
5. **Không dùng `is_ready` của Location thay cho `/narrate`**: `is_ready` chỉ là tổng hợp nhiều ngôn ngữ, không đủ chi tiết.

---

## 10. Đề xuất Commit (Chưa commit / Không tự push)

```
feat(translate-audio-service): tuan 5 - POST /narrate tich hop flow ban dia hoa + TTS + SQLite cache

- Them POST /narrate: cache-first (A REST) -> localize -> TTS -> PUT/PATCH A
- Tai su dung _resolve_localized_text() va tts.synthesize_speech() truc tiep
- Lock (location_id:target_lang) trong mot worker chong race condition GPS tick
- audio_ready: kiem tra file vat ly, tai tao neu mat, khong circular HTTP
- text_ready: giu cache, tra 200 de client dung SpeechSynthesis
- PUT text_ready TRUOC TTS; cache_service_unavailable thi tra 503, khong tao audio
- 14 test cases moi mock A/Wikimedia/Edge: vi/en/ja, fallback, concurrent, file mat, timeout
- Cap nhat docs/api-contract.md va them docs/handoff-tuan-5-B.md cho C
```
