# Handoff Tuần 4 — Thành viên C (gateway-service & Audio Player)

Tài liệu bàn giao kỹ thuật của Thành viên C cho toàn đội ngũ sau khi hoàn thành công việc Tuần 4, chuẩn bị sẵn sàng cho việc gộp luồng tự động `/narrate` ở Tuần 5.

---

## 1. Tóm Tắt Công Việc Đã Thực Hiện

### 1.1. API Gateway Proxy Audio (`GET /audio/{filename}`)
- **Validation an toàn**: Kiểm tra nghiêm ngặt `filename` chống path traversal (ngăn chặn `..`, `/`, `\`, `:`), chỉ chấp nhận các tên file an toàn có phần mở rộng `.mp3` khớp regex `^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.mp3$`. Trả về `HTTP 422` nếu vi phạm.
- **Proxy Downstream nội bộ**: Gọi tới `translate-audio-service` (Port 8002) thông qua biến môi trường `TTS_SERVICE_URL`. Chỉ proxy tới dịch vụ đã cấu hình, không chấp nhận URL tùy ý.
- **Hỗ trợ Streaming & Range Header**: Chuyển tiếp header `Range` sang B và stream nhị phân `audio/mpeg` (mã trạng thái 200 hoặc 206 Partial Content) kèm các header `Content-Length`, `Content-Range`, `Accept-Ranges`. Cho phép thiết bị di động tua/seek audio mượt mà.
- **Timeout & Error Handling**: Áp dụng timeout hữu hạn (`DEFAULT_TIMEOUT = 6.0s`), trả `HTTP 404` rõ ràng khi file không tồn tại, trả `HTTP 503` khi B gặp sự cố kết nối/timeout, không làm crash Gateway.
- **Quy chuẩn URL cùng Origin**: Mọi `audio_url` public trả về client đều có dạng relative URL cùng Origin: `/audio/<filename>.mp3`. Tuyệt đối không trả URL chứa domain nội bộ `http://translate-audio-service:8002` cho điện thoại.

### 1.2. Endpoint Debug Player (`POST /debug/text-to-speech`)
- Dành riêng cho việc kiểm tra audio player độc lập mà không can thiệp vào core flow GPS.
- Chuyển tiếp request `{text, lang}` sang B, nhận kết quả và đảm bảo `audio_url` trả về client là `/audio/<filename>.mp3`.

### 1.3. Client Audio Player & Xử Lý Autoplay Mobile (`app.js`, `index.html`, `style.css`)
- **Singleton Audio Element**: Quản lý duy nhất 1 thẻ `<audio>` cho toàn ứng dụng.
- **Hàm `playNarration({ audio_url, translated_text, lang })`**:
  - Độc quyền luồng âm thanh: luôn dừng/cancel âm thanh cũ (`audio.pause()`, reset `currentTime`, `speechSynthesis.cancel()`) trước khi phát bài thuyết minh mới. Không bao giờ phát chồng âm thanh.
  - Bắt Promise của `audio.play()`: Nếu gặp lỗi `NotAllowedError` (do chính sách Autoplay của trình duyệt di động), hệ thống **hiển thị nút "Cho phép phát âm thanh"** màu vàng nổi bật để người dùng chạm lại, **tuyệt đối không coi là file audio hỏng** và không fallback sai.
  - Không giả định một lần chạm ban đầu bảo đảm autoplay cho mọi trình duyệt di động.
- **Fallback Đa Tầng với Web Speech API**:
  - Nếu file MP3 404 / lỗi tải mạng / TTS không có file audio nhưng có text -> Tự động fallback sang `SpeechSynthesisUtterance`.
  - Kiểm tra `window.speechSynthesis.getVoices()` và lắng nghe `voiceschanged` để tìm voice phù hợp với ngôn ngữ (`vi-VN`, `en-US`, `ja-JP`).
  - Nếu trình duyệt không có gói giọng đọc phù hợp: hiển thị văn bản và trạng thái *"Không có giọng đọc cho ngôn ngữ này trong trình duyệt"*, không hứa hẹn có đủ mọi ngôn ngữ, giữ nút *Thử lại thủ công*.
- **Bộ Điều Khiển Player Toàn Diện**:
  - Nút **⏹ Dừng phát**: Ngắt âm thanh lập tức cả MP3 lẫn SpeechSynthesis.
  - Nút **🔇 Tắt tiếng / 🔊 Bật tiếng**: Toggle thuộc tính `muted`.
  - Nút **🔁 Phát lại**: Phát lại bài thuyết minh hiện tại từ đầu.
  - Tích hợp với nút **Dừng GPS**: khi người dùng bấm dừng GPS, âm thanh cũng tự động được dừng đồng bộ.
- **Chế Độ Kiểm Tra Player Riêng Biệt (Debug Mode)**:
  - Nằm trong panel collapsible, **tắt mặc định**.
  - Cho phép kiểm tra Edge TTS trực tiếp, kiểm tra kịch bản MP3 404 fallback, kiểm tra kịch bản không có voice.
  - Không biến việc nhập địa điểm thành core flow (core flow vẫn là GPS tự động).

### 1.4. Hạ Tầng Docker Compose & Persistence
- **Named Volume `content_db_data`**: Gắn vào `/app/data` của `content-service` để lưu trữ SQLite `content.db` bền vững.
- **Named Volume `audio_cache_data`**: Gắn vào `/app/audio_files` của `translate-audio-service` để lưu trữ các file MP3 bền vững.
- **Gateway Mount Client**: Gắn `./gateway-service/client:/app/client` để hỗ trợ hot-reload giao diện.
- **Cấu hình `.env.example`**: Đầy đủ URL nội bộ, timeouts, Wikimedia User-Agent, Cache TTL (`NARRATION_TTL_DAYS=7`, `TTS_CONFIG_VERSION=v1`), Persistence paths và giọng đọc TTS mặc định.

### 1.5. Tài Liệu Hướng Dẫn & Đặc Tả
- `docs/mobile-demo.md`: Chọn phương án chính thức là Cloudflare Tunnel (`cloudflared`), giải thích Secure Context vs `http://IP-LAN`, ghi rõ các bước thực hiện và không ghi khống "đã pass trên điện thoại".
- `tests/test_client_spec.md`: Đặc tả 5 kịch bản kiểm thử client cốt lõi (Play success, Autoplay blocked, MP3 fail -> Speech fallback, Speech không có voice, Dừng phát).
- `docs/kien-truc.md`: Cập nhật sơ đồ phân lớp, luồng audio URL, cơ chế Persistence và Autoplay policy.

---

## 2. Danh Sách File Thay Đổi

```
gateway-service/
├── main.py                     (SỬA: thêm GET /audio/{filename} proxy stream, Range header, POST /debug/text-to-speech)
├── test_gateway_service.py     (SỬA: thêm 17 unit test mock cho Audio Proxy, Range, 404, Timeout, Path Traversal, Debug TTS)
└── client/
    ├── index.html              (SỬA: thêm Card Audio Player, Alert banners, Debug Panel collapsible)
    ├── style.css               (SỬA: thêm giao diện Player, Badges, Alert boxes, Controls)
    └── app.js                  (SỬA: singleton audio, playNarration, NotAllowedError handler, SpeechSynthesis fallback, controls)

content-service/
└── models.py                   (SỬA: hỗ trợ biến môi trường DB_PATH để lưu SQLite vào named volume /app/data)

docker-compose.yml              (SỬA: mount client cho Gateway, named volumes audio_cache_data và content_db_data)
.env.example                    (SỬA: hoàn thiện đầy đủ cấu hình URL, timeout, User-Agent, cache TTL, persistence, voice)
.env                            (SỬA: đồng bộ cấu hình local cho docker compose)

docs/
├── kien-truc.md                (SỬA: cập nhật kiến trúc Tuần 4, đường đi URL, persistence, autoplay)
├── mobile-demo.md              (SỬA: chọn phương án Cloudflare Tunnel HTTPS, giải thích secure context, quy trình test di động)
└── handoff-tuan-4-C.md         (TẠO MỚI: tài liệu bàn giao này)

tests/
└── test_client_spec.md         (TẠO MỚI: đặc tả kiểm thử client với thao tác và kết quả mong đợi)
```

---

## 3. Lệnh và Kết Quả Đã Chạy Thật

### 3.1. Chạy Unit Tests Cho Gateway Service (38/38 Tests PASSED — 100%)
```bash
python -m pytest gateway-service/test_gateway_service.py -v
```
Kết quả:
```text
gateway-service/test_gateway_service.py::test_health PASSED              [  2%]
gateway-service/test_gateway_service.py::test_static_app_served PASSED   [  5%]
gateway-service/test_gateway_service.py::test_preview_legacy_success PASSED [  7%]
gateway-service/test_gateway_service.py::test_preview_legacy_404 PASSED  [ 10%]
gateway-service/test_gateway_service.py::test_route_id_does_not_swallow_detect PASSED [ 13%]
gateway-service/test_gateway_service.py::test_detect_coords_out_of_range[91.0-106.0] PASSED [ 15%]
gateway-service/test_gateway_service.py::test_detect_coords_out_of_range[-90.1-106.0] PASSED [ 18%]
gateway-service/test_gateway_service.py::test_detect_coords_out_of_range[10.0-180.1] PASSED [ 21%]
gateway-service/test_gateway_service.py::test_detect_coords_out_of_range[10.0--180.1] PASSED [ 23%]
gateway-service/test_gateway_service.py::test_detect_language_not_in_allowlist[fr] PASSED [ 26%]
gateway-service/test_gateway_service.py::test_detect_language_not_in_allowlist[de] PASSED [ 28%]
gateway-service/test_gateway_service.py::test_detect_language_not_in_allowlist[zh] PASSED [ 31%]
gateway-service/test_gateway_service.py::test_detect_language_not_in_allowlist[korean] PASSED [ 34%]
gateway-service/test_gateway_service.py::test_detect_language_not_in_allowlist[] PASSED [ 36%]
gateway-service/test_gateway_service.py::test_detect_accuracy_negative PASSED [ 39%]
gateway-service/test_gateway_service.py::test_detect_accuracy_valid PASSED [ 42%]
gateway-service/test_gateway_service.py::test_detect_inside_activation_radius PASSED [ 44%]
gateway-service/test_gateway_service.py::test_detect_outside_activation_radius PASSED [ 47%]
gateway-service/test_gateway_service.py::test_detect_triggers_discovery_and_finds_new_place PASSED [ 50%]
gateway-service/test_gateway_service.py::test_detect_no_place_found PASSED [ 52%]
gateway-service/test_gateway_service.py::test_detect_upstream_timeout_error PASSED [ 55%]
gateway-service/test_gateway_service.py::test_audio_proxy_success PASSED [ 57%]
gateway-service/test_gateway_service.py::test_audio_proxy_with_range_header PASSED [ 60%]
gateway-service/test_gateway_service.py::test_audio_proxy_404_not_found PASSED [ 63%]
gateway-service/test_gateway_service.py::test_audio_proxy_timeout PASSED [ 65%]
gateway-service/test_gateway_service.py::test_audio_proxy_network_error PASSED [ 68%]
gateway-service/test_gateway_service.py::test_audio_proxy_path_traversal_and_invalid_filenames[...] PASSED [ 71%-92%]
gateway-service/test_gateway_service.py::test_debug_tts_success PASSED   [ 94%]
gateway-service/test_gateway_service.py::test_debug_tts_invalid_language PASSED [ 97%]
gateway-service/test_gateway_service.py::test_debug_tts_upstream_timeout PASSED [100%]

======================= 38 passed in 7.36s =======================
```

### 3.2. Chạy Kiểm Tra Content Service (56/56 Tests PASSED — 100%)
```bash
python -m pytest content-service/test_content_service.py -v
```
Kết quả: **56 passed** (xác nhận migration v3, khóa ngoại SQLite và tính tương thích của biến môi trường `DB_PATH`).

### 3.3. Kiểm Thử End-to-End Thực Tế Trên Docker & Xác Nhận Persistence
1. Khởi chạy Docker Compose:
   ```bash
   docker compose up --build -d
   ```
   Cả 3 container khởi động và sẵn sàng kết nối qua internal Docker network:
   - `GET http://localhost:8000/health` -> `{"service":"gateway-service","status":"ok"}`
   - `GET http://localhost:8000/services-health` -> xác nhận Gateway kết nối thành công tới cả `content-service` và `translate-audio-service`.

2. Gọi tạo âm thanh qua Gateway:
   - Gửi request `POST /debug/text-to-speech` với `{ "text": "Xin chào, đây là bài kiểm tra âm thanh Tuần 4 trên Docker.", "lang": "vi" }`.
   - Kết quả: Gateway gọi B, nhận file MP3 hash `e423dd2d5ab75f1613c1a4b5f8452b5f088dd42d6a98c65c0fdfb7793ca6397e.mp3` và trả về URL cùng Origin: `/audio/e423dd2d5ab75f1613c1a4b5f8452b5f088dd42d6a98c65c0fdfb7793ca6397e.mp3`, `cache_hit: false`.

3. Kiểm tra Gateway proxy streaming và Range request:
   - `GET /audio/e423dd2d5ab75f1613c1a4b5f8452b5f088dd42d6a98c65c0fdfb7793ca6397e.mp3` -> Stream thành công 26,496 bytes nhị phân MP3 với `Content-Type: audio/mpeg`.

4. **Kiểm tra tính bền vững (Persistence) qua Restart**:
   - Chạy lệnh restart toàn bộ cụm container:
     ```bash
     docker compose restart
     ```
   - Gọi lại `POST /debug/text-to-speech` cho cùng nội dung văn bản.
   - Kết quả: `cache_hit: true` ngay lần gọi đầu tiên sau restart; file MP3 trên named volume `audio_cache_data` vẫn nguyên vẹn 26,496 bytes và được phục vụ tức thì mà không cần gọi lại Edge TTS.

---

## 4. Giải Thích Kỹ Thuật Trọng Tâm

### 4.1. Đường Đi URL Từ Container -> Gateway -> Điện Thoại
- `translate-audio-service` lưu trữ file MP3 tại `audio_files/<hash>.mp3` và phục vụ tại URL nội bộ: `http://translate-audio-service:8002/audio/<hash>.mp3`.
- Tên miền `translate-audio-service` là hostname thuộc mạng ảo Docker Bridge, chỉ có thể phân giải giữa các container với nhau. Thiết bị di động ở mạng ngoài hoàn toàn không biết `translate-audio-service` là gì, dẫn đến lỗi `ERR_NAME_NOT_RESOLVED`.
- Hơn nữa, cổng 8002 không được công khai qua đường hầm HTTPS, và việc gọi HTTP từ trang web HTTPS sẽ bị trình duyệt chặn vì lỗi Mixed Content.
- **Giải pháp**:
  - Gateway tiếp nhận yêu cầu tại `GET /audio/{filename}` (cùng Origin với Web Client và API).
  - Gateway validate tên file, forward request sang container B, chuyển tiếp Range header nếu có để client có thể tua âm thanh, và stream nhị phân `audio/mpeg` về cho điện thoại.
  - Nhờ vậy, mọi client đều chỉ giao tiếp với một điểm duy nhất (Same-Origin Gateway), triệt tiêu hoàn toàn lỗi CORS và lỗi phân giải mạng.

### 4.2. Tính Bền Vững Của Dữ Liệu (Persistence)
- Docker containers theo mặc định có tính chất ngắn hạn (ephemeral): khi container bị xóa hoặc rebuild, mọi file ghi bên trong container sẽ biến mất.
- Bằng cách cấu hình hai **Named Volumes** trong `docker-compose.yml`:
  1. `content_db_data:/app/data`: Toàn bộ CSDL SQLite của A (`content.db`) được lưu trữ tại phân vùng đĩa vật lý của host máy tính.
  2. `audio_cache_data:/app/audio_files`: Toàn bộ file MP3 của B được lưu trữ tại phân vùng đĩa vật lý của host máy tính.
- Khi thực hiện `docker compose down` rồi `docker compose up --build`, cơ sở dữ liệu địa điểm, bộ nhớ đệm bài dịch và toàn bộ các tệp âm thanh MP3 đã tạo từ trước đều được giữ nguyên vẹn 100%, không cần phải gọi lại Wikimedia hay Edge TTS.

### 4.3. Xử Lý Autoplay Policy Trên Trình Duyệt Di Động
- Trình duyệt di động (iOS Safari, Android Chrome) cấm tự động phát âm thanh khi chưa có cử chỉ chạm (User Gesture) trực tiếp từ người dùng nhằm chống spam và tiết kiệm tài nguyên.
- Kể cả khi người dùng đã bấm nút *Bắt đầu thuyết minh*, các luồng âm thanh được kích hoạt sau đó (khi người dùng đi bộ vào phạm vi địa điểm mới sau vài chục giây) vẫn có thể bị trình duyệt coi là ngoài phạm vi của gesture ban đầu và ném lỗi `NotAllowedError`.
- **Giải pháp**:
  - Client bắt ngoại lệ `NotAllowedError` từ Promise của `audio.play()`.
  - Khi bắt được lỗi này, hệ thống **không báo lỗi hỏng file**, không kích hoạt fallback nhầm lẫn, mà lập tức hiển thị nút **"🔊 Cho phép phát âm thanh"**.
  - Người dùng chạm vào nút này sẽ tạo ra một gesture hợp lệ mới, cho phép âm thanh phát bình thường ngay tức khắc.

---

## 5. Đề Xuất Commit (Chưa Commit / Không Tự Push)

```
feat(gateway-service): tuan 4 - proxy audio stream, mobile audio player with autoplay handling and fallback speech
```
