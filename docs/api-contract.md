# API Contract — Toàn Hệ Thống Multilingual Dubbing Backend (Tuần 3)

Tài liệu hợp nhất API Contract của 3 Microservices:
- `gateway-service` (Port 8000)
- `content-service` (Port 8001)
- `translate-audio-service` (Port 8002)

---

# 1. API Contract — gateway-service (Port 8000)

Thành viên C phụ trách. Là Single Entry Point cho Client, phục vụ ứng dụng web tĩnh và điều phối logic vị trí.

## Endpoints

### `GET /health`
Kiểm tra sức khỏe của Gateway.
```json
{"service": "gateway-service", "status": "ok"}
```

### `GET /services-health`
Kiểm tra kết nối nội bộ từ Gateway tới các downstream microservices (`content-service` và `translate-audio-service`).

### `GET /app/`
Phục vụ giao diện Web Client (HTML, CSS, JS tĩnh).

### `GET /locations/{id}/preview` *(Legacy Tuần 2 — Debug)*
Chuyển tiếp tới `content-service:8001/locations/{id}` để xem trước dữ liệu địa điểm.

---

### `POST /locations/detect` *(Core Flow Tuần 3)*

Tiếp nhận tọa độ GPS thời gian thực từ thiết bị người dùng, kiểm tra địa điểm lân cận và tự động khám phá địa điểm mới từ Wikimedia.

#### Request Body (JSON)
```json
{
  "latitude": 10.77978,
  "longitude": 106.69902,
  "language": "vi",
  "accuracy_m": 15.0
}
```

| Trường | Kiểu | Bắt buộc | Ràng buộc / Validate |
|---|---|---|---|
| `latitude` | float | Có | Số thực hữu hạn, `[-90, 90]` |
| `longitude` | float | Có | Số thực hữu hạn, `[-180, 180]` |
| `language` | string | Có | Allowlist: `vi`, `en`, `ja` |
| `accuracy_m` | float | Không | Số thực hữu hạn, `>= 0` nếu có |

Validate không hợp lệ trả về HTTP `422 Unprocessable Entity`.

#### Response Body (JSON - Cố định cấu trúc)

```json
{
  "detected": true,
  "reason": null,
  "location": {
    "id": 1,
    "name": "Nhà thờ Đức Bà Sài Gòn",
    "description_source": "Nhà thờ chính tòa Đức Bà Sài Gòn...",
    "latitude": 10.77978,
    "longitude": 106.69902,
    "radius": 80.0,
    "distance_m": 25.4,
    "source": "wikimedia",
    "source_url": "https://vi.wikipedia.org/wiki/Nh%C3%A0_th%E1%BB%9D_%C4%90%E1%BB%A9c_B%C3%A0_S%C3%A0i_G%C3%B2n"
  },
  "distance_m": 25.4,
  "requested_lang": "vi",
  "lang": null,
  "translated_text": null,
  "audio_url": null,
  "source_url": "https://vi.wikipedia.org/wiki/Nh%C3%A0_th%E1%BB%9D_%C4%90%E1%BB%A9c_B%C3%A0_S%C3%A0i_G%C3%B2n",
  "is_fallback": false,
  "narration_status": "not_implemented",
  "candidates": [...]
}
```

#### Quy tắc các trường Response:
- `detected` (boolean): `true` nếu có địa điểm thỏa mãn bán kính kích hoạt (`distance_m <= radius`). `false` nếu ngoài bán kính hoặc không tìm thấy.
- `reason` (string hoặc `null`):
  - `null`: khi `detected = true`.
  - `"outside_activation_radius"`: có địa điểm trong 500m nhưng chưa vào bán kính kích hoạt.
  - `"no_place_found"`: không tìm thấy địa điểm nào trong bán kính khám phá (500m).
  - `"upstream_unavailable"`: Content Service gặp sự cố mạng hoặc timeout.
  - `"narration_not_ready"`: đã kích hoạt nhưng nội dung chưa sẵn sàng (dự phòng các tuần sau).
- `narration_status`:
  - Trong Tuần 3: Trả về `"not_implemented"` khi `detected = true`. Các trường audio/dịch (`lang`, `translated_text`, `audio_url`) giữ giá trị `null` do tuần này chưa phát âm thanh.
- `location`: Thông tin địa điểm kích hoạt (hoặc địa điểm gần nhất khi ngoài bán kính).
- `candidates`: Danh sách các ứng viên trong phạm vi 500m để phục vụ debug và hiển thị giao diện.

---

# 2. API Contract — content-service (Tuần 3)

Port: `8001`. Dữ liệu lưu SQLite (`content.db`), ORM SQLAlchemy.

## Khái niệm cốt lõi: 2 bán kính khác nhau
- **Discovery radius** (tham số `radius` của `/locations/discover`, mặc định **500m**): bán kính TÌM KIẾM bài viết Wikipedia quanh 1 tọa độ.
- **Activation radius** (`radius` lưu trên từng `Location`, mặc định **80m**): bán kính để GATEWAY quyết định kích hoạt thuyết minh khi người dùng đang ở gần.

## Endpoints

### `GET /health`
```json
{"service": "content-service", "status": "ok"}
```

### CRUD `/locations`
- `POST /locations` — tạo record `source="manual"`.
- `GET /locations` — danh sách toàn bộ.
- `GET /locations/{id}` — 1 record theo id, 404 nếu không có.
- `PUT /locations/{id}` — cập nhật record.
- `DELETE /locations/{id}` — xóa record.

### `GET /locations/nearby`
Tìm trong dữ liệu đã có sẵn trong CSDL.
- Query: `latitude`, `longitude`, `max_distance` (mặc định 500m).
- Trả về: `{"candidates": [...]}` sắp xếp theo `distance_m` tăng dần.

### `GET /locations/discover`
Tìm bài Wikipedia tiếng Việt quanh tọa độ, **upsert vào CSDL**, trả lại danh sách vừa lưu kèm cờ `cache_hit` (TTL 60s).

---

# 3. API Contract — translate-audio-service (Tuần 3)

Port: `8002`. Không dùng database. Gọi Wikimedia API để lấy nội dung bản địa hóa.

## Endpoints

### `GET /health`
```json
{"service": "translate-audio-service", "status": "ok"}
```

### `POST /localized-content` *(Tuần 3 — Core flow)*
- Body: `{"source_title": "...", "source_lang": "vi", "target_lang": "en", "source_page_id": "12345"}`
- Dùng langlinks API của Wikimedia để lấy bài bản địa hóa viết bởi người bản ngữ (không dịch máy).

### `POST /translate` *(Legacy Tuần 2)*
Giữ lại để tương thích ngược.
