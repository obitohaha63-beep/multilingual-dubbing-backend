# Kiến Trúc Hệ Thống (Architecture Overview) — Tuần 3

## 1. Mô hình Phân lớp & Luồng Dữ liệu (GPS -> Gateway -> Content -> Wikimedia -> SQLite)

Hệ thống được thiết kế theo kiến trúc Microservices gồm 3 thành phần chính và Web Client:

```
[ Mobile Web Client (Foreground) ]
               │
               │ (1) GPS Fix thời gian thực (watchPosition + Timer 1s)
               │     HTTPS / Same-origin
               ▼
[ gateway-service (Port 8000) ]
   ├── Phục vụ Web Client tại /app/
   └── Điều phối POST /locations/detect
               │
               │ (2) HTTP GET /locations/nearby (max_distance=500m)
               │     (Kiểm tra Activation: distance_m <= radius)
               ▼
[ content-service (Port 8001) ] ──────── (Nếu chưa có trong DB) ─────────┐
   ├── SQLite: content.db                                                │
   │   (lưu trữ Location, GPS, metadata)                                 │
   │                                                                     │ (3) GET /locations/discover
   │ ◄───────────────────────────────────────────────────────────────────┘
   │   Gọi Wikimedia API (tìm bài tiếng Việt theo Geosearch & Extracts)
   ▼
[ Wikimedia API (vi.wikipedia.org) ]
   └── Trả về bài viết & tọa độ -> Upsert vào SQLite CSDL
```

### Luồng Dữ Liệu Chi Tiết:

1. **GPS từ Client**:
   - Thiết bị người dùng lấy tọa độ GPS thời gian thực qua `navigator.geolocation.watchPosition` kết hợp bộ định thời (timer) 1s.
   - Khi thỏa mãn điều kiện (gửi fix đầu tiên, hoặc di chuyển >= 20m, hoặc đứng yên sau 10s; tối thiểu cách nhau 3s và tối đa 1 request in-flight), Client gửi request tới `POST /locations/detect`.
2. **Gateway tiếp nhận & Điều phối**:
   - Gateway validate dữ liệu (tọa độ hữu hạn trong phạm vi cho phép, ngôn ngữ nằm trong allowlist `vi, en, ja`, độ chính xác accuracy_m >= 0).
   - Gateway gọi sang `content-service` endpoint `GET /locations/nearby?latitude=...&longitude=...&max_distance=500`.
3. **Content Service & Tìm kiếm CSDL**:
   - `content-service` tính toán khoảng cách Haversine giữa vị trí người dùng và các địa điểm đã có sẵn trong bảng `locations` của SQLite.
   - Trả về danh sách candidates sắp xếp theo khoảng cách tăng dần.
4. **Quyết định Kích hoạt (Activation Radius vs Discovery Radius)**:
   - Gateway duyệt danh sách candidates: tìm địa điểm có `distance_m <= radius` (mặc định 80m).
   - Nếu **chưa có** địa điểm nào đạt bán kính kích hoạt:
     - Gateway gọi tiếp `content-service` endpoint `GET /locations/discover?latitude=...&longitude=...&radius=500`.
     - `content-service` gọi sang Wikimedia Geosearch API tìm các bài viết quanh bán kính 500m (kèm cơ chế cache 60s).
     - Các bài viết mới được **Upsert vào SQLite** (`content.db`) với metadata `source="wikimedia"`.
     - Sau khi discover hoàn tất, Gateway gọi lại `/nearby` để cập nhật khoảng cách chuẩn xác.
5. **Đánh giá & Trả về Client**:
   - **Trong bán kính kích hoạt (`distance_m <= radius`)**: Trả lời `detected=true`, `narration_status='not_implemented'`, kèm thông tin địa điểm và nguồn gốc.
   - **Ngoài bán kính kích hoạt (`distance_m > radius`)**: Trả lời `detected=false`, `reason='outside_activation_radius'`, kèm địa điểm gần nhất và khoảng cách.
   - **Không tìm thấy địa điểm nào trong 500m**: Trả lời `detected=false`, `reason='no_place_found'`.
   - **Lỗi mạng nội bộ**: Phân biệt rõ với lỗi không có địa điểm bằng `reason='upstream_unavailable'`.
6. **Lưu ý về Audio / TTS Tuần 3**:
   - Trong tuần này, hệ thống tập trung hoàn thiện pipeline vị trí, cơ sở dữ liệu và bản địa hóa nội dung Wikipedia.
   - **Chưa phát âm thanh TTS**; các trường `audio_url` và `translated_text` ở Tuần 3 được giữ `null` với `narration_status='not_implemented'`.
