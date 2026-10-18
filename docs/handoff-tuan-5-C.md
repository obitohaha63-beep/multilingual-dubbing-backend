# Handoff Tuần 5 — Thành viên C (gateway-service & Web Client)

Tài liệu bàn giao kỹ thuật của Thành viên C sau khi hoàn thành tích hợp Core Flow Tuần 5 toàn hệ thống.

---

## 1. Tóm Tắt Công Việc Đã Hoàn Thành (Tuần 5)

1. **Hoàn thiện Core Flow `POST /locations/detect` (Gateway Service)**:
   - Tiếp nhận GPS và ngôn ngữ yêu cầu từ Client.
   - Gọi Content Service (`A`) `GET /locations/nearby` (500m), lọc candidate theo bán kính kích hoạt `distance_m <= radius` (mặc định 80m).
   - Nếu chưa có candidate kích hoạt, tự động gọi `A` `/locations/discover` (500m) kèm cache, sau đó gọi lại `/nearby` và xét lại candidate. Phân định rạch ròi không nhầm lẫn giữa bán kính tìm kiếm discovery (500m) và bán kính kích hoạt thuyết minh (80m).
   - Nếu không có địa điểm hoặc ngoài bán kính kích hoạt: trả về `detected=False` với `reason` rõ ràng (`no_place_found` / `outside_activation_radius`); **tuyệt đối không gọi Translate/TTS Service (`B`)**.
   - Trong bán kính kích hoạt: gọi `B` `POST /narrate` với Location metadata và language.
   - Chuẩn hóa response theo contract: đổi `audio_url` sang relative URL `/audio/<filename>.mp3` cùng origin Gateway.
   - Nếu `B` trả `text_ready` (lỗi TTS nhưng có văn bản): trả về `narration_status='text_ready'`, text và lang cho Client Web Speech.
   - Nếu `B` lỗi không có text hoặc timeout: trả về `narration_status='error'`, **không giả là narration thành công**.

2. **Quản lý Ngân Sách Timeout Phân Tầng**:
   - Edge TTS: 15s.
   - `B` internal request: 25s.
   - Gateway gọi `B`: `GATEWAY_NARRATE_TIMEOUT=30.0s` (connect timeout 5s, read timeout 30s).
   - Client abort: `40s` qua `AbortController`.
   - In-flight guard: Tối đa 1 request đang chạy per client; request mới không chồng lên lần cũ.

3. **Client State Machine & Chống Phát Lặp Thông Minh (`app.js`)**:
   - `activeLocationId` & `activeLocationCoords` (`{ latitude, longitude, radius }`).
   - `playedKeys`: `Set<string>` lưu theo cặp `location_id:requested_lang` trong một lần ghé.
   - Vòng đời trạng thái: `idle` -> `pending` -> `playing` -> `played` / `error`.
   - **Chỉ ghi `played` khi âm thanh thực sự phát thành công hoặc speech onstart**.
   - Khi phát lỗi: giữ nút thử lại thủ công, không tự động retry liên tục ở mỗi GPS tick.
   - Đứng yên lâu tại địa điểm: không tự động phát lặp bài đã nghe trong cùng lần ghé.
   - Đổi ngôn ngữ: cho phép phát ngôn ngữ mới 1 lần; đổi lại ngôn ngữ đã nghe không tự lặp. Nút *Phát lại* là thao tác chủ động bypass `playedKeys`.

4. **Hysteresis Exit Detection & Chuyển Địa Điểm (A -> B)**:
   - Chuyển A -> B: dừng âm thanh A lập tức, reset `playedKeys` của lần ghé mới, kích hoạt B.
   - Xác nhận rời địa điểm A: Phải thỏa mãn **2 fix hợp lệ cách nhau >= 10s** và khoảng cách **> radius + 30m**; sau khi rời rõ ràng mới reset `playedKeys`.
   - **Tuyệt đối không reset `playedKeys` từ một response `detected=false` tạm thời hoặc lỗi mạng.**
   - GPS accuracy > 50m: hiển thị cảnh báo GPS chưa ổn định, bỏ qua không tạo sự kiện enter/exit.
   - Sequence Token: Hủy bỏ các response trễ (stale response) khi ngôn ngữ hoặc phiên đã thay đổi.

5. **Phân Định Trách Nhiệm Chống Lặp**:
   - **Backend Cache (A & B)**: Tránh tải Wikimedia và tránh tạo file MP3 trùng lặp giữa các người dùng khác nhau.
   - **Client `playedKeys`**: Tránh phát lặp bài thuyết minh cho cùng một du khách khi họ đang đứng yên tại chỗ.

---

## 2. Danh Sách File Thay Đổi & Bổ Sung

```
multilingual-dubbing-backend/
├── gateway-service/
│   ├── main.py                          # Tích hợp POST /locations/detect gọi B /narrate, timeout 30s, rewrite audio_url
│   ├── test_gateway_service.py          # Bổ sung unit tests cho Tuần 5 (audio_ready, text_ready, B error/timeout, mock 429)
│   └── client/
│       ├── app.js                       # Hoàn thiện State Machine, Hysteresis Exit, Sequence token, AbortController 40s
│       ├── index.html                   # UI đầy đủ, responsive mobile, autoplay banner & controls
│       └── style.css                    # Giao diện hiện đại, trạng thái trực quan
├── tests/
│   ├── test_client_logic.test.js        # 9 kịch bản kiểm thử client logic chạy bằng node:test
│   └── test_client_spec.md              # Đặc tả kịch bản test
├── docs/
│   ├── core-flow.md                     # Tài liệu kiến trúc luồng dữ liệu toàn cảnh Tuần 5
│   └── handoff-tuan-5-C.md              # File bàn giao kỹ thuật này
```

---

## 3. Lệnh & Kết Quả Kiểm Thử Thực Tế

### 3.1. Unit Test Backend (`gateway-service`)
Chạy bằng pytest:
```bash
pytest gateway-service/test_gateway_service.py -v
```
**Kết quả thực tế**:
```
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-9.0.3, pluggy-1.6.0
collected 41 items

gateway-service/test_gateway_service.py::test_health PASSED              [  2%]
gateway-service/test_gateway_service.py::test_static_app_served PASSED   [  4%]
gateway-service/test_gateway_service.py::test_preview_legacy_success PASSED [  7%]
gateway-service/test_gateway_service.py::test_preview_legacy_404 PASSED  [  9%]
gateway-service/test_gateway_service.py::test_route_id_does_not_swallow_detect PASSED [ 12%]
gateway-service/test_gateway_service.py::test_detect_coords_out_of_range[...] PASSED [ 21%]
gateway-service/test_gateway_service.py::test_detect_language_not_in_allowlist[...] PASSED [ 34%]
gateway-service/test_gateway_service.py::test_detect_accuracy_negative PASSED [ 36%]
gateway-service/test_gateway_service.py::test_detect_accuracy_valid PASSED [ 39%]
gateway-service/test_gateway_service.py::test_detect_inside_activation_radius_audio_ready PASSED [ 41%]
gateway-service/test_gateway_service.py::test_detect_inside_activation_radius_text_ready PASSED [ 43%]
gateway-service/test_gateway_service.py::test_detect_inside_radius_b_server_error PASSED [ 46%]
gateway-service/test_gateway_service.py::test_detect_inside_radius_b_timeout PASSED [ 48%]
gateway-service/test_gateway_service.py::test_detect_outside_activation_radius_never_calls_b PASSED [ 51%]
gateway-service/test_gateway_service.py::test_detect_triggers_discovery_and_finds_new_place PASSED [ 53%]
gateway-service/test_gateway_service.py::test_detect_no_place_found_never_calls_b PASSED [ 56%]
gateway-service/test_gateway_service.py::test_detect_upstream_timeout_error PASSED [ 58%]
gateway-service/test_gateway_service.py::test_audio_proxy_success PASSED [ 60%]
gateway-service/test_gateway_service.py::test_audio_proxy_with_range_header PASSED [ 63%]
gateway-service/test_gateway_service.py::test_audio_proxy_404_not_found PASSED [ 65%]
gateway-service/test_gateway_service.py::test_audio_proxy_timeout PASSED [ 68%]
gateway-service/test_gateway_service.py::test_audio_proxy_network_error PASSED [ 70%]
gateway-service/test_gateway_service.py::test_audio_proxy_path_traversal_and_invalid_filenames[...] PASSED [ 92%]
gateway-service/test_gateway_service.py::test_debug_tts_success PASSED   [ 95%]
gateway-service/test_gateway_service.py::test_debug_tts_invalid_language PASSED [ 97%]
gateway-service/test_gateway_service.py::test_debug_tts_upstream_timeout PASSED [100%]

======================= 41 passed, 7 warnings in 5.17s ========================
```

### 3.2. Unit Test Logic Client bằng `node:test`
Chạy bằng Node.js test runner:
```bash
node --test tests/test_client_logic.test.js
```
**Kết quả thực tế**:
```
▶ Client State Machine & Core Flow (Tuần 5)
  ✔ Kịch bản 1: Lặp GPS 50 lần cùng location và lang -> chỉ kích hoạt phát đúng 1 lần (playedKeys guard) (1.98ms)
  ✔ Kịch bản 2: Đổi ngôn ngữ vi -> en phát 1 lần en; đổi ngược lại vi đã nghe không tự lặp lại (0.26ms)
  ✔ Kịch bản 3: Chuyển từ địa điểm A sang B: dừng âm thanh A, reset playedKeys và kích hoạt B (0.20ms)
  ✔ Kịch bản 4: Jitter ở rìa bán kính: dao động ra vào ngưỡng rời không làm reset trạng thái khi chưa đủ 2 fix >= 10s (0.20ms)
  ✔ Kịch bản 5: Rời A rõ ràng sau 2 fix cách nhau >=10s >radius+30m: reset playedKeys; khi quay lại được phát lại (0.29ms)
  ✔ Kịch bản 6: Autoplay bị chặn bởi trình duyệt (NotAllowedError) -> Chưa marked là played (0.16ms)
  ✔ Kịch bản 7: Response trễ và stale GPS -> sequence token và language guard bỏ qua kết quả cũ (0.15ms)
  ✔ Kịch bản 8: Tọa độ GPS có accuracy > 50m -> Bỏ qua, không cập nhật enter/exit hay gửi request mới (0.13ms)
  ✔ Kịch bản 9: Kịch bản điện thoại di động: yêu cầu Secure Context (HTTPS/localhost), xử lý Autoplay Blocked và User Gesture Unlock (0.19ms)
✔ Client State Machine & Core Flow (Tuần 5) (4.52ms)
ℹ tests 9
ℹ suites 1
ℹ pass 9
ℹ fail 0
ℹ duration_ms 88.9ms
```

---

## 4. Hướng Dẫn Chạy & Kịch Bản Demo (Bắt Đầu Từ GPS)

> **Lưu ý cốt lõi**: Trải nghiệm người dùng du lịch **hoàn toàn tự động bắt đầu từ tọa độ GPS**, người dùng **không cần và không được yêu cầu phải chọn hoặc nhập `location_id` thủ công**.

### 4.1. Khởi Chạy Hệ Thống Bằng Docker Compose
```bash
docker-compose up --build
```
Kiểm tra sức khỏe toàn hệ thống:
```bash
curl http://localhost:8000/health
curl http://localhost:8000/services-health
```

### 4.2. Kịch Bản Demo Thực Tế Trên Web Client
1. Mở trình duyệt truy cập: `http://localhost:8000/app/`.
2. Chọn ngôn ngữ mong muốn (mặc định: Tiếng Việt 🇻🇳).
3. Bấm nút **"▶ Bắt đầu thuyết minh"**:
   - Trình duyệt yêu cầu cấp quyền Geolocation -> Chọn **Allow**.
   - Trạng thái hiển thị tọa độ GPS thời gian thực kèm sai số bán kính.
4. Giả lập du khách bước vào khu vực Nhà thờ Đức Bà (`10.77978, 106.69902`):
   - Gateway phát hiện điểm trong bán kính 80m -> gọi B tạo thuyết minh -> trả về file MP3 `/audio/...mp3`.
   - Âm thanh tự động cất tiếng thuyết minh.
   - Thẻ địa điểm hiển thị thông tin bài viết gốc Wikipedia kèm huy hiệu "Trong bán kính kích hoạt".
5. Du khách đứng yên nghe và chụp ảnh (50 GPS ticks liên tiếp):
   - Bài thuyết minh **không tự lặp lại**. Trạng thái hiển thị "Đã phát xong".
6. Đổi ngôn ngữ sang tiếng Anh (🇬🇧 English):
   - Hệ thống tự động kích hoạt phát bản dịch tiếng Anh một lần.
   - Đổi lại tiếng Việt: Không tự lặp lại vì đã nghe trước đó trong cùng lần ghé.
7. Thao tác chủ động: Bấm nút **"🔁 Phát lại"** bất cứ lúc nào để nghe lại bài thuyết minh hiện tại.
8. Bấm nút **"⏹ Dừng GPS"**: Luồng định vị và âm thanh lập tức ngừng hoàn toàn.

---

## 5. Đề Xuất Commit Message

```
feat(gateway-service): tuan 5 - integrate core flow POST /locations/detect with narrate, audio guards, and hysteresis buffer

- Connect POST /locations/detect to translate-audio-service POST /narrate with 30s timeout
- Enforce strict separation between discovery radius (500m) and activation radius (80m)
- Implement client TourStateManager: playedKeys (location_id:lang), hysteresis exit buffer (2 fixes >=10s >radius+30m)
- Guard against stale responses using sequence tokens and drop unstable GPS (accuracy > 50m)
- Support audio proxy streaming with HTTP 206 Range headers and Web Speech fallback
- Add comprehensive pytest coverage (41 tests) and node:test suite (9 tests)
```
*(Chỉ chuẩn bị commit, không tự động push lên remote).*
