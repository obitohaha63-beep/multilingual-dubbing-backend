# API Contract — content-service (Tuần 3 + Tuần 4)

Port: `8001`. Dữ liệu lưu SQLite (`content.db`), ORM SQLAlchemy.

## Khái niệm cốt lõi: 2 bán kính khác nhau

- **Discovery radius** (tham số `radius` của `/locations/discover`, mặc định
  **500m**): bán kính TÌM KIẾM bài viết Wikipedia quanh 1 tọa độ. Rộng, để
  không bỏ sót địa điểm.
- **Activation radius** (`radius` lưu trên từng `Location`, mặc định **80m**):
  bán kính để GATEWAY (thành viên C) quyết định có "kích hoạt" phát audio hay
  chưa khi người dùng đang ở gần. Hẹp, vì chỉ kích hoạt khi thực sự đứng
  gần địa điểm đó.

content-service **không tự quyết định activation** — chỉ cung cấp dữ liệu
GPS + distance_m, gateway-service mới là nơi so `distance_m` với `radius`
của từng location để quyết định kích hoạt.

## Trường `id`

`id` là khóa chính tự tăng của bảng `locations`, dùng xuyên suốt cho mọi
record bất kể nguồn (`manual` hay `wikimedia`). B/C chỉ cần lưu `id` này để
tham chiếu tới 1 địa điểm, không cần quan tâm `source_page_id` (chỉ có ý
nghĩa nội bộ để chống trùng dữ liệu Wikimedia).

## Model `Location`

| Trường | Kiểu | Ghi chú |
|---|---|---|
| id | int | khóa chính |
| name | string | tên hiển thị |
| description_vi | string | mô tả gốc tiếng Việt (tương thích tuần 2) |
| target_languages | string | ds ngôn ngữ cần dịch, cách nhau dấu phẩy |
| description_source | string, null | nội dung mô tả theo nguồn (manual: = description_vi; wikimedia: đoạn intro lấy từ bài) |
| latitude, longitude | float, null | GPS. NULL với record manual thiếu tọa độ |
| radius | float, null | bán kính kích hoạt (mét), mặc định 80 |
| source | `"manual"` \| `"wikimedia"`, null | |
| source_lang | string, null | hiện tại luôn `"vi"` |
| source_title | string, null | tiêu đề CHÍNH XÁC trên Wikipedia (có thể khác `name`) |
| source_page_id | int, null | pageid Wikipedia, chỉ duy nhất TRONG 1 wiki |
| source_url | string, null | link bài gốc |
| last_synced_at | datetime, null | lần cuối đồng bộ với Wikimedia (UTC) |
| is_ready | bool, null | cờ cho các tuần sau (vd: đã dịch + tạo audio xong chưa) |

Ràng buộc duy nhất: `unique(source, source_lang, source_page_id)` — chống
lưu trùng 1 bài Wikipedia. Record `manual` có `source_page_id = NULL` nên
không bị ràng buộc này chặn (SQL: nhiều NULL không coi là trùng nhau).

## Endpoints

### `GET /health`
`{"service": "content-service", "status": "ok"}`

### CRUD `/locations`

- `POST /locations` — tạo record `source="manual"`. Body: `name`,
  `description_vi`, `target_languages` (bắt buộc), `latitude`, `longitude`,
  `radius` (tùy chọn — thiếu GPS vẫn tạo được bình thường).
- `GET /locations` — danh sách toàn bộ.
- `GET /locations/{id}` — 1 record theo id, 404 nếu không có.
- `PUT /locations/{id}` — cập nhật, body giống `POST`. 404 nếu không có.
- `DELETE /locations/{id}` — xóa, 404 nếu không có. Trả
  `{"deleted": true, "id": <id>}`.

Validate chung: tọa độ (nếu có) phải hữu hạn, `latitude ∈ [-90,90]`,
`longitude ∈ [-180,180]`; `radius > 0`; `name`/`description_vi`/
`target_languages` không được rỗng. Vi phạm -> `422`.

### `GET /locations/nearby`

Tìm trong **dữ liệu đã lưu sẵn trong content-service** (không gọi
Wikimedia). Chỉ xét các location có GPS.

Query: `latitude`, `longitude` (bắt buộc), `max_distance` (mét, mặc định
`500`).

```json
{
  "candidates": [
    {
      "id": 1, "name": "...", "description_source": "...",
      "latitude": 10.77, "longitude": 106.70, "radius": 80,
      "distance_m": 42.3,
      "source": "wikimedia", "source_lang": "vi",
      "source_title": "...", "source_page_id": 12345,
      "source_url": "...", "last_synced_at": "2026-10-01T10:00:00Z"
    }
  ]
}
```
Sắp theo `distance_m` tăng dần. Chỉ trả record có GPS và `distance_m <=
max_distance`.

### `GET /locations/discover`

Tìm bài Wikipedia tiếng Việt quanh 1 tọa độ, **lưu (upsert) vào CSDL**,
trả lại danh sách vừa lưu.

Query: `latitude`, `longitude` (bắt buộc), `radius` (mét, mặc định `500`),
`limit` (mặc định `5`, tối đa `50`).

```json
{
  "candidates": [ { "id": 7, "name": "...", "source": "wikimedia", "...": "..." } ],
  "cache_hit": false
}
```

- `cache_hit=true` nghĩa là kết quả lấy từ cache nội bộ (TTL 60 giây theo
  ô tọa độ ~100m + radius), không gọi Wikimedia API lần này.
- Có candidate trong kết quả **không đồng nghĩa đã trong bán kính kích
  hoạt** — đây là bán kính khám phá rộng (mặc định 500m), gateway tự so
  với `radius` (activation, mặc định 80m) của từng candidate.
- Khi Wikimedia lỗi: trả **503** dạng
  `{"detail": {"code": "timeout"|"rate_limited"|"upstream_error", "message": "..."}}`.
  Không bao giờ trả lỗi upstream dưới dạng `candidates: []` (dễ gây hiểu
  nhầm "khu vực này không có địa điểm nào").

## Thứ tự route quan trọng

`/locations/nearby` và `/locations/discover` được đăng ký **trước**
`/locations/{id}` trong `main.py`. Nếu đảo ngược thứ tự, request tới
`/locations/nearby` sẽ bị route động bắt nhầm (`location_id="nearby"`),
FastAPI báo lỗi 422 vì không ép kiểu được sang `int`.

## Narration cache (Tuần 4)

Content là nơi DUY NHẤT truy cập SQLite. B/Gateway đọc-ghi cache narration
qua REST dưới đây, không mở trực tiếp `content.db`.

Một dòng cache = 1 địa điểm × 1 **ngôn ngữ yêu cầu** (`requested_lang`),
unique `(location_id, requested_lang)`.

| Trường | Ghi chú |
|---|---|
| location_id | FK tới `locations.id` (SQLite `foreign_keys=ON`) |
| requested_lang | ngôn ngữ người dùng yêu cầu: `vi` \| `en` \| `ja`; bằng `{language}` trên path |
| lang | ngôn ngữ THỰC TẾ của `text`; khác `requested_lang` khi fallback |
| text | nội dung thuyết minh, không rỗng, tối đa 10000 ký tự |
| source_url, target_title | URL / tiêu đề bài wiki đã dùng (có thể null) |
| is_fallback, reason | `is_fallback=true` bắt buộc có `reason` và `lang != requested_lang`; `false` thì `reason=null` và `lang == requested_lang` |
| voice | vd `ja-JP-NanamiNeural`; phải bắt đầu bằng `lang + "-"` |
| text_hash | hash của text đã dùng để tạo audio (B dùng để phát hiện text đổi) |
| audio_filename | CHỈ tên file `.mp3` (regex `^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.mp3$`, không chứa `..`, `/`, `\\`, `:`); KHÔNG lưu URL |
| audio_status | `text_ready` (có text, chưa có MP3) \| `audio_ready` |
| created_at, updated_at, expires_at | UTC, ISO-8601 **không có hậu tố múi giờ** |

Quy tắc trạng thái:
- `audio_ready` bắt buộc đủ `audio_filename`, `voice`, `text_hash`.
- `text_ready` KHÔNG được có `audio_filename` (lỗi TTS vẫn lưu text để client fallback).
- DB không lưu hostname container; Gateway tự ghép `/audio/<audio_filename>`.

### `GET /locations/{id}/narrations/{language}`
- Location không tồn tại → `404`. `language` ngoài `vi|en|ja` → `422`.
- Còn hạn (mặc định **7 ngày**, đổi bằng `NARRATION_TTL_DAYS`):
  `{"cache_hit": true, "narration": {...}}`
- Chưa có hoặc đã hết hạn: `{"cache_hit": false, "narration": null}`

### `PUT /locations/{id}/narrations/{language}`
Upsert trong 1 transaction. Body đầy đủ (không có trường lạ, vd `audio_url` → `422`):
```json
{
  "lang": "vi", "text": "...", "source_url": "https://vi.wikipedia.org/wiki/...",
  "target_title": "Chợ Bến Thành", "is_fallback": true,
  "reason": "target_language_unavailable",
  "voice": "vi-VN-HoaiMyNeural", "text_hash": "9f2c...",
  "audio_filename": "9f2c.mp3", "audio_status": "audio_ready"
}
```
`requested_lang` trong body là tùy chọn; nếu gửi phải bằng `{language}` trên path.
`expires_at` tùy chọn (phải ở tương lai); thiếu thì `now + TTL`.
Trả `{"created": true|false, "narration": {...}}`. Location không có → `404`;
vi phạm validation → `422`. Hai PUT đồng thời cùng key không tạo trùng
(unique conflict → rollback, đọc lại, cập nhật).

### `PATCH /locations/{id}/mark-ready`
Không nhận giá trị từ client. Tính lại `is_ready` từ cache:
`true` ⇔ có ít nhất một narration `audio_ready` còn hạn (ở bất kỳ ngôn ngữ nào).
Trả `{"id": 1, "is_ready": true, "audio_ready_languages": ["ja"]}`; `404` nếu không có location.
`is_ready` chỉ là cờ tổng quát — **không** có nghĩa mọi ngôn ngữ đã sẵn sàng;
B/Gateway luôn kiểm tra cache theo `{language}`. (PUT narration cũng tự tính lại
cờ này để nó không bị lệch.)

### Invalidation
- `PUT /locations/{id}` làm đổi `description_source` (qua `description_vi`),
  `latitude` hoặc `longitude` → xóa toàn bộ narration của địa điểm, `is_ready=false`.
  Chỉ đổi `name`/`target_languages`/`radius` thì giữ cache.
- `/locations/discover` fetch thật mà nội dung/tọa độ bài wiki đã đổi → như trên.
- `DELETE /locations/{id}` xóa luôn narration của địa điểm (không để dòng mồ côi).

### `last_synced_at` và cache discovery
`last_synced_at` chỉ được refresh khi service **vừa fetch thành công** từ
Wikimedia (`cache_hit=false`). Khi `/locations/discover` trả từ cache 60 giây
(`cache_hit=true`), record đã có được giữ nguyên, không đổi `last_synced_at`.
Hai discovery đồng thời cùng một `source_page_id` không tạo record trùng
(unique conflict → rollback → đọc lại record đã có).

## Migration

Nằm trong `main.py` (khu vực 1), tự chạy khi service khởi động. Mỗi version
chạy trong 1 transaction, chạy lặp lại an toàn, và chỉ sao lưu DB
(`content.db.bak-<timestamp>`) khi thực sự có version chờ áp dụng.

| Version | Nội dung |
|---|---|
| 2 (Tuần 3) | thêm cột GPS + metadata nguồn vào `locations`, backfill, unique index Wikimedia |
| 3 (Tuần 4) | tạo bảng `narration_cache` (FK `ON DELETE CASCADE`, unique `(location_id, requested_lang)`) |

Kết nối SQLite luôn bật `PRAGMA foreign_keys=ON`.
