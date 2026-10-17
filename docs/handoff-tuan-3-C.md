# Handoff Tuần 3 — Thành viên C (gateway-service & Web Client)

## 1. Tóm tắt công việc đã thực hiện

Phát triển `gateway-service` (port 8000) và ứng dụng Web Client tĩnh chạy trên trình duyệt di động:
- **Web Client Di Động**: Tạo `index.html`, `style.css`, `app.js` phục vụ tại đường dẫn `/app/` trên cùng origin với Gateway.
- **GPS Trình Duyệt**:
  - Dùng `navigator.geolocation.watchPosition` với các callback thành công và bắt lỗi chi tiết (PERMISSION_DENIED, POSITION_UNAVAILABLE, TIMEOUT).
  - Tự động gửi fix đầu tiên ngay khi nhận; các lần gửi tiếp theo kích hoạt khi di chuyển `>= 20m` hoặc sau `10s` khi đứng yên.
  - Có timer chạy nền mỗi 1s độc lập với callback của Geolocation API để đảm bảo thiết bị đứng yên vẫn được refresh dữ liệu sau mỗi 10s.
  - Áp dụng cơ chế Throttle: khoảng cách giữa hai request liên tiếp tối thiểu `3s`.
  - In-flight guard: chỉ cho phép tối đa một request detect đang chạy tại một thời điểm, tránh tình trạng race conditions hoặc spam mạng.
  - Hủy theo dõi định vị sạch sẽ qua `clearWatch` khi người dùng bấm "Dừng GPS".
  - Chặn sử dụng các fix GPS đã cũ quá `30s`.
- **API `POST /locations/detect`**:
  - Nhận `{latitude, longitude, language, accuracy_m?}`.
  - Validation chặt chẽ: kiểm tra tọa độ hữu hạn trong khoảng `[-90, 90]` và `[-180, 180]`, ngôn ngữ theo allowlist `vi, en, ja`, độ chính xác không âm.
  - Điều phối hai bước với Content Service:
    1. Gọi `/locations/nearby` (500m) kiểm tra các địa điểm đã có trong SQLite; chọn địa điểm gần nhất thỏa mãn bán kính kích hoạt `distance_m <= radius`.
    2. Nếu chưa có địa điểm kích hoạt, gọi `/locations/discover` để tìm bài viết từ Wikimedia (kèm cache 60s) và upsert vào SQLite, sau đó gọi lại `/nearby` để cập nhật distance_m.
  - Response có cấu trúc cố định theo contract: `{detected, reason, location, distance_m, requested_lang, lang, translated_text, audio_url, source_url, is_fallback, narration_status, candidates}`.
  - Tuần 3: trong bán kính kích hoạt trả `detected=true`, `narration_status='not_implemented'`, các trường âm thanh null; ngoài bán kính trả `detected=false`, `reason='outside_activation_radius'`; không có địa điểm trả `reason='no_place_found'`; lỗi mạng trả `reason='upstream_unavailable'`.
- **Hạ tầng & Triển khai Docker**:
  - Cập nhật `docker-compose.yml` chạy đồng bộ 3 service: `gateway-service:8000`, `content-service:8001`, `translate-audio-service:8002`.
  - Cấu hình biến môi trường `CONTENT_SERVICE_URL`, `TTS_SERVICE_URL`, `WIKIMEDIA_USER_AGENT` và các named volume (`content_db_data`, `audio_cache_data`).
  - Tạo `.env.example` và bổ sung `.gitignore` bảo vệ database, audio và file môi trường thật.
- **Tài liệu bàn giao**:
  - `docs/mobile-demo.md`: Giải thích cơ chế Secure Context cho Geolocation, lưu ý về localhost và IP-LAN, hướng dẫn thiết lập HTTPS Tunnel qua Cloudflare/Ngrok hoặc reverse proxy.
  - `docs/client-testing.md`: Hướng dẫn kiểm thử toàn diện các luồng Geolocation, timer, throttle và in-flight request.
  - `docs/kien-truc.md`: Giải thích luồng dữ liệu GPS -> Gateway -> Content -> Wikimedia -> SQLite.
  - `docs/api-contract.md`: Hợp nhất contract API của cả 3 services.

---

## 2. Kết quả kiểm thử thực tế

Tất cả các test suite của dự án đều chạy thật bằng pytest và **100% PASS**:

### 1) Gateway Service (`gateway-service/test_gateway_service.py`):
```
============================= test session starts =============================
collected 21 items

gateway-service/test_gateway_service.py::test_health PASSED              [  4%]
gateway-service/test_gateway_service.py::test_static_app_served PASSED   [  9%]
gateway-service/test_gateway_service.py::test_preview_legacy_success PASSED [ 14%]
gateway-service/test_gateway_service.py::test_preview_legacy_404 PASSED  [ 19%]
gateway-service/test_gateway_service.py::test_route_id_does_not_swallow_detect PASSED [ 23%]
gateway-service/test_gateway_service.py::test_detect_coords_out_of_range[91.0-106.0] PASSED [ 28%]
gateway-service/test_gateway_service.py::test_detect_coords_out_of_range[-90.1-106.0] PASSED [ 33%]
gateway-service/test_gateway_service.py::test_detect_coords_out_of_range[10.0-180.1] PASSED [ 38%]
gateway-service/test_gateway_service.py::test_detect_coords_out_of_range[10.0--180.1] PASSED [ 42%]
gateway-service/test_gateway_service.py::test_detect_language_not_in_allowlist[fr] PASSED [ 47%]
gateway-service/test_gateway_service.py::test_detect_language_not_in_allowlist[de] PASSED [ 52%]
gateway-service/test_gateway_service.py::test_detect_language_not_in_allowlist[zh] PASSED [ 57%]
gateway-service/test_gateway_service.py::test_detect_language_not_in_allowlist[korean] PASSED [ 61%]
gateway-service/test_gateway_service.py::test_detect_language_not_in_allowlist[] PASSED [ 66%]
gateway-service/test_gateway_service.py::test_detect_accuracy_negative PASSED [ 71%]
gateway-service/test_gateway_service.py::test_detect_accuracy_valid PASSED [ 76%]
gateway-service/test_gateway_service.py::test_detect_inside_activation_radius PASSED [ 80%]
gateway-service/test_gateway_service.py::test_detect_outside_activation_radius PASSED [ 85%]
gateway-service/test_gateway_service.py::test_detect_triggers_discovery_and_finds_new_place PASSED [ 90%]
gateway-service/test_gateway_service.py::test_detect_no_place_found PASSED [ 95%]
gateway-service/test_gateway_service.py::test_detect_upstream_timeout_error PASSED [100%]

======================== 21 passed in 2.68s ========================
```

### 2) Content Service (`content-service/test_content_service.py`):
```
======================== 15 passed in 4.51s ========================
```

### 3) Translate Audio Service (`translate-audio-service/tests/test_translate_audio.py`):
```
======================== 29 passed in 7.93s ========================
```

**Tổng cộng: 65 / 65 test cases PASSED.**

---

## 3. Lệnh chạy và kiểm tra

### Khởi chạy bằng Docker Compose:
```bash
docker-compose up --build
```
- Web Client: [http://localhost:8000/app/](http://localhost:8000/app/)
- Gateway Health: [http://localhost:8000/health](http://localhost:8000/health)
- Liên lạc nội bộ: [http://localhost:8000/services-health](http://localhost:8000/services-health)

### Chạy test toàn bộ hệ thống:
```bash
python -m pytest gateway-service/test_gateway_service.py -v
python -m pytest content-service/test_content_service.py -v
python -m pytest translate-audio-service/tests/test_translate_audio.py -v
```

---

## 4. Đề xuất Commit Git

```bash
git add gateway-service/ client/ docker-compose.yml .gitignore .env.example docs/
git commit -m "feat(gateway-service): add POST /locations/detect, mobile web client and docker compose setup (Week 3)"
```
*(Ghi chú: Antigravity tuân thủ nguyên tắc không tự ý push; bạn hãy chủ động kiểm tra và thực hiện push lên remote branch).*
