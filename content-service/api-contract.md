# API Contract — content-service (Tuần 3)

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

## Migration

Xem `migrate.py` — chạy tự động mỗi khi service khởi động (`run_migrations()`
được gọi đầu `main.py`), an toàn khi chạy lặp lại (idempotent), tự sao lưu
DB trước khi đổi schema. Chi tiết: xem docstring trong file.

---

# API Contract — translate-audio-service (Tuần 3)

*(Phần này do thành viên B bổ sung)*

Port: `8002`. Không dùng database. Gọi Wikimedia API trực tiếp.

## Khái niệm: Bản địa hóa ≠ Dịch máy

**Bản địa hóa (localization)**: Lấy bài viết *gốc* do cộng đồng bản ngữ viết trực tiếp trên Wikipedia ngôn ngữ đích. Ví dụ: bài "Hanoi" trên en.wikipedia.org do người viết tiếng Anh biên soạn — không phải bản dịch tự động từ tiếng Việt.

**Dịch máy (machine translation)**: Phần mềm (Google Translate, DeepL, ...) tự chuyển ngữ từ văn bản gốc. Kết quả thường kém tự nhiên hơn, không cập nhật theo ngữ cảnh bản địa, dễ sai tên riêng và thuật ngữ địa phương.

Service này dùng **langlinks API của Wikimedia** — luôn trả nội dung bản địa hóa thật, không bao giờ dịch máy.

## Allowlist ngôn ngữ

Chỉ chấp nhận: `vi`, `en`, `ja`. Hostname Wikipedia được xây dựng **chỉ từ allowlist** — không ghép từ input người dùng.

## Endpoints

### `GET /health`

```json
{"service": "translate-audio-service", "status": "ok"}
```

### `POST /localized-content` *(Core flow tuần 3)*

**Body:**
```json
{
  "source_title":   "Hà Nội",
  "source_lang":    "vi",
  "target_lang":    "en",
  "source_page_id": "12345"
}
```

`source_page_id` là chuỗi pageid Wikipedia ở wiki nguồn (lấy từ trường `source_page_id` của Location trong content-service — tuy nhiên kiểu `int` trong DB, cần stringify khi gọi).

**Logic:**
- `target_lang == source_lang`: lấy extract bài nguồn qua `pageids`.
- `target_lang != source_lang`: (1) query langlinks từ wiki nguồn → (2) lấy extract ở wiki đích theo title chuẩn hóa API trả về.

**Response thành công (HTTP 200):**
```json
{
  "found": true,
  "requested_lang": "en",
  "target_lang": "en",
  "lang": "en",
  "target_title": "Hanoi",
  "text": "Hanoi is the capital of Vietnam...",
  "source_url": "https://en.wikipedia.org/wiki/Hanoi",
  "is_fallback": false,
  "reason": null
}
```

`lang` là ngôn ngữ **thật** của `text` — không bao giờ gán nhãn sai (ví dụ: không gán `"en"` cho text tiếng Việt).

**Response không tìm được (HTTP 200, found=false):**
```json
{
  "found": false,
  "requested_lang": "en",
  "target_lang": "en",
  "lang": null,
  "target_title": null,
  "text": null,
  "source_url": null,
  "is_fallback": false,
  "reason": "target_language_unavailable"
}
```

`reason`: `"target_language_unavailable"` | `"empty_extract"`.

**Lỗi mạng (HTTP 503):**
```json
{"detail": {"code": "timeout", "message": "..."}}
```

`code`: `"timeout"` | `"rate_limited"` | `"upstream_error"`. Không bao giờ trả `found=false` khi nguyên nhân là lỗi mạng.

### `POST /translate` *(LEGACY — Tuần 2, giữ để tương thích)*

Body: `{"text": "...", "target_lang": "en"}` → Response: `{"translated_text": "[EN] ..."}` (giả lập).
Không dùng trong core flow. Không được nâng cấp bằng GoogleTranslator/deep-translator.

## Cài đặt & Chạy

```bash
cd translate-audio-service
pip install -r requirements.txt
# copy .env.example -> .env, điền WIKIMEDIA_USER_AGENT
uvicorn main:app --host 0.0.0.0 --port 8002 --reload
```

## Test

```bash
cd translate-audio-service
pytest tests/test_translate_audio.py -v
```

Tất cả test dùng mock — không gọi Wikimedia thật.
