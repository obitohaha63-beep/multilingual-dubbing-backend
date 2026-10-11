# API Contract — translate-audio-service (Tuần 4)

Port: `8002`. Không dùng database.
- Lấy nội dung bản địa hóa Wikipedia qua Wikimedia API (Tuần 3).
- Chuyển văn bản thành giọng nói (Text-to-Speech) qua Edge TTS với cache file bền vững (Tuần 4).

---

## Tổng quan Endpoints

| Endpoint | Giao thức | Vai trò | Tuần |
|---|---|---|---|
| `GET /health` | HTTP GET | Kiểm tra sức khỏe service | Tuần 1 |
| `POST /localized-content` | HTTP POST | Lấy nội dung bản địa hóa Wikipedia (không dịch máy) | Tuần 3 |
| `POST /text-to-speech` | HTTP POST | Tạo file âm thanh MP3 từ văn bản qua Edge TTS | **Tuần 4 (mới)** |
| `GET /audio/{filename}/status` | HTTP GET | Kiểm tra file audio đã tồn tại trong cache chưa | **Tuần 4 (mới)** |
| `GET /audio/{filename}` | HTTP GET | Phục vụ file audio MP3 tĩnh (StaticFiles) | **Tuần 4 (mới)** |
| `POST /translate` | HTTP POST | Giả lập dịch văn bản (legacy, tương thích tuần 2) | Tuần 2 (deprecated) |

**Nguyên tắc kiến trúc:**
- C (gateway-service) chỉ gọi `translate-audio-service` qua HTTP — không import code trực tiếp.
- Tuần 4: `/text-to-speech` chỉ nhận text đã đúng ngôn ngữ; **tuần 5 mới gộp `/narrate` và gọi cache SQLite của A**.
- Không dùng gTTS, không dùng deep-translator, không tạo thêm service thứ tư.

---

## Allowlist ngôn ngữ & Voice mapping

Chỉ chấp nhận các mã ngôn ngữ: `vi`, `en`, `ja`. Gửi ngôn ngữ khác trả về HTTP `422 Unprocessable Entity`.

### Voice mapping mặc định (Microsoft Edge Neural Voices):

| Ngôn ngữ (`lang`) | Voice mặc định | Giới tính | Ghi chú |
|---|---|---|---|
| `vi` | `vi-VN-HoaiMyNeural` | Nữ | Giọng đọc tiếng Việt tự nhiên |
| `en` | `en-US-JennyNeural` | Nữ | Giọng đọc tiếng Anh chuẩn |
| `ja` | `ja-JP-NanamiNeural` | Nữ | Giọng đọc tiếng Nhật chuẩn |

- **Kiểm tra online thủ công**: Dùng lệnh CLI `edge-tts --list-voices` trong terminal để xem danh sách giọng đọc mới nhất từ máy chủ Microsoft.
- **Cấu hình override**: Có thể ghi đè từng giọng đọc qua các biến môi trường:
  - `TTS_VOICE_VI` (mặc định: `vi-VN-HoaiMyNeural`)
  - `TTS_VOICE_EN` (mặc định: `en-US-JennyNeural`)
  - `TTS_VOICE_JA` (mặc định: `ja-JP-NanamiNeural`)
- **Timeout**: `TTS_TIMEOUT_SECONDS` (mặc định: `10.0` giây).
- **Phiên bản cấu hình cache**: `TTS_CONFIG_VERSION` (mặc định: `"v1"`).

---

## Chi tiết Endpoints

### 1. `GET /health`

```json
{"service": "translate-audio-service", "status": "ok"}
```

---

### 2. `POST /text-to-speech` *(Tuần 4 — Mới)*

Chuyển văn bản thành giọng nói MP3.

**Body (JSON):**

```json
{
  "text": "Nhà thờ Đức Bà là công trình kiến trúc nổi tiếng tại trung tâm Thành phố Hồ Chí Minh.",
  "lang": "vi"
}
```

| Trường | Kiểu | Bắt buộc | Ràng buộc |
|---|---|---|---|
| `text` | string | Có | Không rỗng, chuẩn hóa khoảng trắng, tối đa 1200 ký tự. Không nhận tên file từ client. |
| `lang` | string | Có | Ngôn ngữ THẬT của `text`. Phải thuộc allowlist `vi`, `en`, `ja`. |

Validate lỗi (thiếu trường, text rỗng, > 1200 ký tự, `lang` không hỗ trợ) → HTTP `422 Unprocessable Entity`.

**Response thành công (HTTP 200):**

```json
{
  "lang": "vi",
  "voice": "vi-VN-HoaiMyNeural",
  "text_hash": "a1b2c3d4e5f6...64_hex_chars",
  "audio_filename": "f7e8d9c0...64_hex_chars.mp3",
  "audio_url": "/audio/f7e8d9c0...64_hex_chars.mp3",
  "cache_hit": false
}
```

| Trường | Ý nghĩa |
|---|---|
| `lang` | Ngôn ngữ thực tế đã xử lý (`vi`, `en`, `ja`) |
| `voice` | Tên giọng đọc đã sử dụng |
| `text_hash` | SHA256 hex của `text` đã chuẩn hóa (dùng để lưu trường `text_hash` trong cache của A) |
| `audio_filename` | Tên file MP3 dạng `<cache_key>.mp3` (khớp regex `^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.mp3$` của Content Service) |
| `audio_url` | URL nội bộ: `/audio/<audio_filename>`. Gateway sẽ đổi thành URL public khi trả cho mobile client |
| `cache_hit` | `true` nếu file âm thanh đã có sẵn trên đĩa; `false` nếu vừa tạo mới |

**Lỗi dịch vụ TTS (HTTP 503):**

Khi kết nối Edge TTS timeout hoặc WebSocket gián đoạn:

```json
{
  "detail": {
    "code": "tts_unavailable",
    "message": "Edge TTS không phản hồi sau 10.0 giây"
  }
}
```

Service bắt mọi ngoại lệ, trả 503 với mã lỗi chuẩn, **tuyệt đối không làm crash server**.

---

### 3. `GET /audio/{filename}/status` *(Tuần 4 — Mới)*

Kiểm tra nhanh xem file âm thanh đã có sẵn trên đĩa hay chưa (dùng cho tuần 5 tối ưu hóa flow cache).

> **Lưu ý thứ tự đăng ký**: Route này được đăng ký **TRƯỚC** khi mount `StaticFiles /audio` để tránh bị route static nuốt nhầm.

**Path parameter:**
- `filename`: tên file MP3 cần kiểm tra (ví dụ: `a1b2c3...mp3`).
- **Bảo mật**: Validate chống path traversal (từ chối `..`, `/`, `\`, `:`, chỉ nhận file `.mp3` khớp regex an toàn). Nếu vi phạm → HTTP `422`.

**Response (HTTP 200):**

```json
{
  "exists": true
}
```

hoặc khi file chưa có trong thư mục:

```json
{
  "exists": false
}
```

---

### 4. `GET /audio/{filename}` *(Tuần 4 — StaticFiles)*

Phục vụ phát trực tiếp stream file âm thanh MP3 cho client hoặc Gateway.
- Header: `Content-Type: audio/mpeg`.
- Tải file thành công: HTTP `200`.
- File không tồn tại: HTTP `404 Not Found`.

---

### 5. `POST /localized-content` *(Tuần 3 — Giữ nguyên)*

Lấy nội dung bản địa hóa Wikipedia qua Wikimedia API (langlinks + extracts).

**Body (JSON):**
```json
{
  "source_title": "Hà Nội",
  "source_lang": "vi",
  "target_lang": "en",
  "source_page_id": "12345"
}
```

**Response (HTTP 200):**
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

Lỗi mạng Wikimedia → HTTP `503` `{"detail": {"code": "timeout"|"rate_limited"|"upstream_error", "message": "..."}}`.

---

### 6. `POST /translate` *(Tuần 2 — Legacy deprecated)*

Giả lập dịch văn bản, giữ lại để tương thích ngược.

---

## Cơ chế Cache & An toàn dữ liệu trong `audio_files/`

1. **Cache key SHA256**:
   - `cache_key = SHA256(normalized_text + ":" + lang + ":" + voice + ":" + TTS_CONFIG_VERSION)`.
   - `audio_filename = f"{cache_key}.mp3"`.
   - Cùng một nội dung thì tên file luôn cố định. Khi người dùng đứng yên hoặc GPS tick nhiều lần, hash giữ nguyên, không sinh file rác và tái sử dụng cache 100%.
2. **Khóa đồng thời theo key (Key Lock)**:
   - Sử dụng `asyncio.Lock` cho từng `cache_key`.
   - Khi nhiều request gửi đồng thời cho cùng một nội dung, chỉ 1 request thực hiện gọi TTS, request còn lại chờ và nhận cache hit sau khi request đầu tạo xong.
   - *Giới hạn kiến trúc demo 1 worker*: Khóa hoạt động in-memory trong cùng 1 process Python. Nếu chạy production đa process (nhiều worker uvicorn), cần khóa phân tán (Redis lock) hoặc file advisory lock OS (`fcntl`/`portalocker`).
3. **Atomic Rename (Ghi đĩa nguyên tử)**:
   - Quá trình tổng hợp TTS ghi vào file tạm `tmp_<cache_key>_<uuid>.tmp` trong cùng thư mục `audio_files/`.
   - Sau khi ghi xong và xác nhận dung lượng file `> 0 bytes`, tiến hành đổi tên nguyên tử bằng `os.replace` sang tên file cuối cùng `<cache_key>.mp3`.
   - Không bao giờ xảy ra tình trạng client đọc phải file MP3 đang ghi dở hoặc 0 bytes.
4. **Phục hồi khi mất file**:
   - Mọi lượt kiểm tra cache đều kiểm tra file vật lý trên đĩa (`filepath.is_file() and filepath.stat().st_size > 0`).
   - Nếu file bị xóa hoặc mất trên hệ thống tệp, hệ thống tự động tái tổng hợp và ghi lại, **tuyệt đối không trả `cache_hit` giả**.
5. **Bảo mật logging**:
   - Không bao giờ log toàn bộ nội dung text nhạy cảm của người dùng vào console/file log.
   - Chỉ log `text_hash`, `lang`, `voice` và độ dài text (`text_len`).
