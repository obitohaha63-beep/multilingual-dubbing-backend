# Handoff Tuần 4 — Thành viên B (translate-audio-service)

Tài liệu bàn giao kỹ thuật cho Thành viên C (gateway-service) và toàn đội ngũ sau khi hoàn thành công việc Tuần 4.

---

## 1. Tóm tắt công việc Tuần 4 của B

- **Thêm Edge TTS**: Tích hợp thư viện `edge-tts` vào `translate-audio-service` (Port `8002`), triển khai module [`tts.py`](file:///d:/multilingual-dubbing-backend/translate-audio-service/tts.py) dùng `async edge_tts.Communicate(text, voice).save(...)` với timeout hữu hạn.
- **Endpoint mới `POST /text-to-speech`**: Nhận `{text, lang}`, chuyển văn bản sang file âm thanh MP3. Không cho phép client truyền tên file tùy ý; độ dài tối đa 1200 ký tự.
- **Cache file bền vững trong `audio_files/`**:
  - Tên file sinh từ hash SHA256 (`text chuẩn hóa + lang + voice + phiên bản TTS`), tránh sinh file mới ngẫu nhiên (UUID) mỗi khi client gửi GPS tick.
  - Khóa đồng thời theo key (`asyncio.Lock`) chống request trùng lặp gọi TTS nhiều lần.
  - Ghi file tạm (`.tmp`) rồi thực hiện **atomic rename** (`os.replace`).
  - Kiểm tra file vật lý trên đĩa: nếu file bị xóa/mất thì tự động tạo lại, **không trả `cache_hit` giả**.
- **Route trạng thái & Tệp tĩnh**:
  - `GET /audio/{filename}/status`: Trả về `{"exists": bool}` với filename đã validate chặt chẽ (ngăn chặn path traversal).
  - Đăng ký route status **TRƯỚC** khi mount `StaticFiles /audio`.
  - Phục vụ tệp âm thanh tĩnh qua `GET /audio/{filename}` (MIME: `audio/mpeg`).
- **An toàn & Độ tin cậy**:
  - Lỗi TTS trả về HTTP `503` dạng `{"detail": {"code": "tts_unavailable", "message": "..."}}`, không làm crash server.
  - Không log toàn bộ nội dung text nhạy cảm của người dùng (chỉ log `text_hash`, `lang`, `voice`, `text_len`).
- **Tương thích ngược**:
  - Giữ nguyên `POST /localized-content` (Tuần 3) và `POST /translate` (Legacy Tuần 2).
  - Tuần 4: `/text-to-speech` chỉ nhận text đã đúng ngôn ngữ. **Tuần 5 mới tích hợp gộp flow `/narrate` và gọi SQLite cache của A**.
  - Không sử dụng gTTS / deep-translator; không tạo service thứ tư.

---

## 2. Thông số kỹ thuật cho C (gateway-service)

### 2.1. Audio URL nội bộ và Public URL
- `translate-audio-service` trả về trường `audio_url` là đường dẫn nội bộ:
  ```json
  "audio_url": "/audio/<audio_filename>.mp3"
  ```
- **Nhiệm vụ của Gateway (C)**: Khi chuyển tiếp hoặc tạo response cho mobile / web client, Gateway sẽ ghép tiền tố host public (ví dụ: `http://<gateway_host>:8000/audio/<audio_filename>.mp3` hoặc proxy trực tiếp qua Gateway) tương tự như hợp đồng đã thống nhất với A.

### 2.2. Kiểm tra Cache qua Status Route
- Để kiểm tra xem file MP3 của một nội dung đã tồn tại trên đĩa chưa (chuẩn bị cho logic tuần 5):
  ```http
  GET http://translate-audio-service:8002/audio/<audio_filename>.mp3/status
  ```
- Response:
  ```json
  {"exists": true}
  ```
  hoặc
  ```json
  {"exists": false}
  ```
- Nếu `filename` không hợp lệ (chứa `..`, ký tự lạ, không phải `.mp3`), API trả về HTTP `422`.

### 2.3. Voice Mapping & Timeout
- **Allowlist**: `vi`, `en`, `ja`. Ngôn ngữ khác trả `422`.
- **Default Voices**:
  - `vi`: `vi-VN-HoaiMyNeural` (Nữ, tiếng Việt tự nhiên)
  - `en`: `en-US-JennyNeural` (Nữ, tiếng Anh chuẩn)
  - `ja`: `ja-JP-NanamiNeural` (Nữ, tiếng Nhật chuẩn)
- **Kiểm tra trực tuyến**: Chạy `edge-tts --list-voices` trong terminal để xem danh sách giọng đọc từ Microsoft.
- **Biến môi trường override**:
  - `TTS_VOICE_VI`: override giọng tiếng Việt
  - `TTS_VOICE_EN`: override giọng tiếng Anh
  - `TTS_VOICE_JA`: override giọng tiếng Nhật
  - `TTS_TIMEOUT_SECONDS`: timeout cho mỗi lượt gọi Edge TTS (mặc định `10.0` giây)
  - `TTS_CONFIG_VERSION`: phiên bản thiết lập TTS (mặc định `"v1"`, đổi khi muốn invalidate cache)
  - `AUDIO_DIR`: thư mục lưu file audio (mặc định: `audio_files`)

---

## 3. Mẫu Request / Response thực tế

### 3.1. Gọi tạo âm thanh lần đầu (`cache_hit = false`)
```http
POST http://localhost:8002/text-to-speech
Content-Type: application/json

{
  "text": "Nhà thờ Đức Bà Sài Gòn là một trong những công trình kiến trúc biểu tượng của thành phố.",
  "lang": "vi"
}
```
Response `200 OK`:
```json
{
  "lang": "vi",
  "voice": "vi-VN-HoaiMyNeural",
  "text_hash": "2ff1b48bdfb13f1eb94645398cf291bc9a7ea2b73db64f9bfcb161b589c379a7",
  "audio_filename": "d35fbc0841f23ee6fbf3cfa5c276ad63d330bbd7e089aa1416e755255057d345.mp3",
  "audio_url": "/audio/d35fbc0841f23ee6fbf3cfa5c276ad63d330bbd7e089aa1416e755255057d345.mp3",
  "cache_hit": false
}
```

### 3.2. Gọi lại cùng nội dung (`cache_hit = true`)
```http
POST http://localhost:8002/text-to-speech
Content-Type: application/json

{
  "text": "Nhà thờ Đức Bà Sài Gòn là một trong những công trình kiến trúc biểu tượng của thành phố.",
  "lang": "vi"
}
```
Response `200 OK`:
```json
{
  "lang": "vi",
  "voice": "vi-VN-HoaiMyNeural",
  "text_hash": "2ff1b48bdfb13f1eb94645398cf291bc9a7ea2b73db64f9bfcb161b589c379a7",
  "audio_filename": "d35fbc0841f23ee6fbf3cfa5c276ad63d330bbd7e089aa1416e755255057d345.mp3",
  "audio_url": "/audio/d35fbc0841f23ee6fbf3cfa5c276ad63d330bbd7e089aa1416e755255057d345.mp3",
  "cache_hit": true
}
```

### 3.3. Kiểm tra status của file
```http
GET http://localhost:8002/audio/d35fbc0841f23ee6fbf3cfa5c276ad63d330bbd7e089aa1416e755255057d345.mp3/status
```
Response `200 OK`:
```json
{
  "exists": true
}
```

### 3.4. Tải file tĩnh
```http
GET http://localhost:8002/audio/d35fbc0841f23ee6fbf3cfa5c276ad63d330bbd7e089aa1416e755255057d345.mp3
```
Response `200 OK`:
- `Content-Type: audio/mpeg`
- Body: dữ liệu nhị phân MP3

### 3.5. Lỗi ngôn ngữ không hỗ trợ (HTTP 422)
```http
POST http://localhost:8002/text-to-speech
Content-Type: application/json

{"text": "Bonjour", "lang": "fr"}
```
Response `422 Unprocessable Entity`:
```json
{
  "detail": "Mã ngôn ngữ 'fr' không được hỗ trợ. Chỉ chấp nhận: ['en', 'ja', 'vi']"
}
```

### 3.6. Lỗi dịch vụ TTS timeout hoặc sự cố mạng (HTTP 503)
Response `503 Service Unavailable`:
```json
{
  "detail": {
    "code": "tts_unavailable",
    "message": "Edge TTS không phản hồi sau 10.0 giây"
  }
}
```

---

## 4. Giải thích kỹ thuật cốt lõi (Bằng Tiếng Việt)

### 4.1. Bất đồng bộ (`async / await`)
- Quá trình tổng hợp giọng nói từ Microsoft Edge đòi hỏi kết nối mạng WebSocket từ xa. Nếu dùng các hàm đồng bộ (blocking I/O) như các thư viện TTS cũ, mỗi khi 1 người dùng gọi TTS, toàn bộ tiến trình FastAPI sẽ bị "đóng băng" (treo) trong suốt 1–3 giây đó, khiến mọi request khác (như định vị GPS, health check, wiki extract) đều bị hoãn lại.
- Bằng cách sử dụng `async edge_tts.Communicate(text, voice).save(...)` kết hợp với `asyncio.wait_for(...)`, luồng thực thi được trả lại cho Event Loop của Python trong thời gian chờ dữ liệu mạng từ Microsoft. Nhờ đó, server duy trì khả năng phản hồi cao và có thể phục vụ nhiều client cùng lúc.

### 4.2. Cache băm (`Cache Hash SHA256`)
- Khi người dùng di chuyển quanh một địa điểm, thiết bị liên tục phát ra tọa độ GPS tick (cứ 2–5 giây một lần). Nếu mỗi lần tick lại tạo ra một tên file ngẫu nhiên (UUID), ổ đĩa server sẽ nhanh chóng bị tràn ngập hàng ngàn file audio trùng lặp nội dung.
- B giải quyết bằng cách băm SHA256 kết hợp từ:
  $$\text{Key} = \text{SHA256}(\text{text đã chuẩn hóa} + \text{lang} + \text{voice} + \text{phiên bản cấu hình TTS})$$
- Tên file lưu trên đĩa là `<key>.mp3`. Nhờ tính chất tất định của hàm băm, cùng một nội dung văn bản và giọng đọc sẽ luôn sinh ra cùng một tên file. Khi tick mới gửi đến, service kiểm tra ngay file trên đĩa và trả về `cache_hit = true` trong vài mili-giây mà không tốn tài nguyên mạng.
- Trường `text_hash` được tính riêng từ text chuẩn hóa để tương thích với trường `text_hash` trong bảng `narration_cache` của A.

### 4.3. Ghi đĩa nguyên tử (`Atomic Write / Atomic Rename`)
- Quá trình tải và ghi một file MP3 xuống đĩa mất một khoảng thời gian nhất định (vài chục đến vài trăm mili-giây). Nếu ghi trực tiếp vào tên file đích `<key>.mp3`, một request khác đến ngay lúc đó có thể thấy file đã tồn tại nhưng bên trong chỉ mới có 0 bytes hoặc một nửa dữ liệu MP3 hỏng.
- Giải pháp: Ghi toàn bộ luồng âm thanh vào một file tạm thời riêng biệt (`tmp_<key>_<uuid>.tmp`) trong cùng thư mục `audio_files/`. Chỉ khi file tạm đã hoàn thành trọn vẹn và xác nhận dung lượng `> 0 bytes`, lệnh `os.replace(temp_file, final_file)` mới được thực thi. Trên cùng một hệ thống tệp (filesystem), thao tác đổi tên là **nguyên tử (atomic)** — nghĩa là file đích chỉ xuất hiện khi nó đã hoàn chỉnh 100%, loại bỏ hoàn toàn rủi ro client tải phải file hỏng.

### 4.4. Giới hạn khóa in-memory khi đa tiến trình (Concurrency Limits)
- Trong bản demo này, B sử dụng cơ chế khóa in-memory `asyncio.Lock` cho từng `cache_key` trên một worker duy nhất. Khóa này đảm bảo 2 request gửi cùng lúc đến cùng một key sẽ không tạo trùng file.
- **Giới hạn**: Khóa in-memory chỉ có hiệu lực bên trong 1 tiến trình Python. Nếu trong tương lai triển khai production với nhiều process (như `uvicorn --workers 4` hoặc cụm container phía sau cân bằng tải), bộ nhớ giữa các worker là tách biệt hoàn toàn. Để ngăn race condition giữa nhiều worker, hệ thống sẽ cần nâng cấp lên Distributed Lock (như Redis Lock) hoặc File Advisory Lock cấp hệ điều hành (`portalocker` / `fcntl`).

### 4.5. Phụ thuộc trực tuyến của Edge TTS (Online Dependency)
- Thư viện `edge-tts` không lưu trữ mô hình AI cục bộ trong container mà giao tiếp qua WebSocket tới endpoint dịch vụ chuyển văn bản thành giọng nói trên đám mây của Microsoft Edge.
- **Ưu điểm**: Tạo ra âm thanh nơ-ron cực kỳ tự nhiên, mượt mà (chất lượng tương đương Azure Speech cao cấp), không cần tải model nặng hàng gigabyte, không tốn tài nguyên GPU, và không cần đăng ký tài khoản / thẻ visa Azure.
- **Hạn chế**: Phải có kết nối Internet để hoạt động. Do là endpoint không chính thức, Microsoft không cam kết SLA uptime hay duy trì miễn phí vĩnh viễn. Việc lưu cache bền vững trên đĩa như B đã xây dựng là lá chắn quan trọng nhất: một khi audio đã được sinh ra một lần, các lần sau không còn phụ thuộc vào mạng nữa.

---

## 5. Danh sách file thay đổi

```
translate-audio-service/
├── tts.py                              (TẠO MỚI: module Edge TTS, voice mapping, key lock, cache hash, atomic rename)
├── main.py                             (SỬA: thêm POST /text-to-speech, GET /audio/{filename}/status, DynamicAudioStaticFiles)
├── requirements.txt                    (SỬA: thêm edge-tts)
├── .env.example                        (SỬA: bổ sung cấu hình TTS_VOICE_*, TTS_TIMEOUT_*, TTS_CONFIG_VERSION, AUDIO_DIR)
├── api-contract.md                     (SỬA: tài liệu chi tiết các endpoint TTS tuần 4)
└── tests/test_translate_audio.py       (SỬA: bổ sung 12 test cases mock cho TTS tuần 4)

docs/
├── api-contract.md                     (SỬA: hợp nhất phần 3 translate-audio-service tuần 4)
└── handoff-tuan-4-B.md                 (TẠO MỚI: tài liệu bàn giao này)

.env.example                            (SỬA: cập nhật mẫu biến môi trường TTS ở thư mục gốc)
.gitignore                              (SỬA: thêm audio_files/ và *.tmp vào danh sách bỏ qua)
README.md                               (SỬA: bổ sung mục 4 tài liệu công nghệ Edge TTS)
```

---

## 6. Lệnh và Kết quả đã kiểm tra thực tế

### 6.1. Kiểm tra Unit Test (Toàn bộ dùng Mock — Không gọi mạng ngoài)

Chạy test từ thư mục `translate-audio-service/`:
```bash
cd translate-audio-service
.venv\Scripts\pytest tests/test_translate_audio.py -v
```

**Kết quả: 41/41 tests PASSED (100%)**
- 29 tests cũ (Tuần 1, 2, 3: `/health`, `/translate`, `/localized-content`, Wikimedia allowlist, retry, error handling) giữ nguyên và pass.
- 12 tests mới (Tuần 4: tạo MP3 thành công, cache hit, file bị mất tự tái tạo không trả cache_hit giả, concurrency lock với 2 request cùng key, timeout 503 không crash, unsupported lang 422, filename độc hại bị chặn, static serving 200/404, voice override qua env).

Kiểm tra chéo các service khác trong repository:
- `content-service`: **56/56 tests PASSED** (kiểm tra SQLite cache của A hoạt động hoàn hảo).
- `gateway-service`: **21/21 tests PASSED** (kiểm tra GPS detect và routing hoạt động ổn định).

### 6.2. Kiểm tra trực tuyến thủ công với Microsoft Edge TTS
- Đã chạy lệnh kiểm tra danh sách giọng đọc:
  ```bash
  edge-tts --list-voices
  ```
  Xác nhận các giọng đọc `vi-VN-HoaiMyNeural`, `en-US-JennyNeural`, `ja-JP-NanamiNeural` tồn tại và đang hoạt động tốt trên hệ thống Microsoft.
- Đã chạy thử nghiệm sinh file audio MP3 tiếng Việt thật bằng lệnh:
  ```bash
  edge-tts --text "Xin chào, đây là bài kiểm tra giọng nói Hoài My tiếng Việt" --voice vi-VN-HoaiMyNeural --write-media test_sample_vi.mp3
  ```
  File sinh ra có dung lượng 25,200 bytes, nghe rõ ràng và tự nhiên.

---

## 7. Đề xuất Commit (Chưa commit / Không tự push)

```
feat(translate-audio-service): tuan 4 - edge-tts text-to-speech, persistent audio cache, and audio status route
```
