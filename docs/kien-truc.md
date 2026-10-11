# Kiến Trúc Hệ Thống (Architecture Overview) — Tuần 4

Tài liệu mô tả toàn cảnh kiến trúc hệ thống Multilingual Dubbing Backend ở cột mốc Tuần 4, bao gồm pipeline định vị GPS, điều phối nội dung, cơ chế phát âm thanh, đường dẫn URL cùng origin, chính sách lưu trữ bền vững (Persistence) và xử lý Autoplay trên thiết bị di động.

---

## 1. Mô Hình Phân Lớp Microservices

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        THIẾT BỊ DI ĐỘNG (MOBILE CLIENT)                                │
│   - Foreground Web App tại /app/ (Same-Origin HTTPS)                                   │
│   - Định vị GPS thời gian thực (watchPosition + Timer 1s)                              │
│   - Singleton Audio Player + Xử lý Autoplay Blocked (NotAllowedError)                  │
│   - Fallback Web Speech API (SpeechSynthesisUtterance)                                 │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │
                                            │ (1) GET /app/ (Static HTML/CSS/JS)
                                            │ (2) POST /locations/detect (GPS Coordinates)
                                            │ (3) GET /audio/{filename}.mp3 (Stream MP3 cùng Origin)
                                            │ (4) POST /debug/text-to-speech (Debug Audio)
                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        API GATEWAY (gateway-service:8000)                              │
│   - Validate tọa độ & allowlist ngôn ngữ (vi, en, ja)                                  │
│   - Điều phối Nearby & Discovery sang content-service                                  │
│   - Audio Proxy Streamer: chuyển tiếp Range header, validate hash/basename             │
│   - Rewrite toàn bộ audio_url thành relative URL cùng Origin: /audio/<filename>.mp3   │
└───────────────────────┬────────────────────────────────────────┬───────────────────────┘
                        │                                        │
     (A) HTTP REST      │                     (B) HTTP REST      │
     Mạng nội bộ Docker │                     Mạng nội bộ Docker │
                        ▼                                        ▼
┌──────────────────────────────────────┐ ┌───────────────────────────────────────────────┐
│     CONTENT SERVICE (Port 8001)      │ │    TRANSLATE & AUDIO SERVICE (Port 8002)      │
│ - Quản lý địa điểm & tọa độ GPS      │ │ - Wikipedia Localized Content (Wikimedia API) │
│ - Tính khoảng cách Haversine         │ │ - Edge TTS Engine (Microsoft Neural Voices)   │
│ - Tự động Discovery bài viết Wiki    │ │ - Cache Key SHA256 tất định: <hash>.mp3       │
│ - SQLite DB: content.db              │ │ - Thư mục audio_files/ (Atomic Rename)        │
│   * Bảng locations                   │ │ - Route GET /audio/{filename}/status          │
│   * Bảng narration_cache (TTL 7 ngày)│ │ - StaticFiles /audio/{filename}               │
│ - Mounted Named Volume:              │ │ - Mounted Named Volume:                       │
│   content_db_data -> /app/data       │ │   audio_cache_data -> /app/audio_files        │
└──────────────────────────────────────┘ └───────────────────────────────────────────────┘
```

---

## 2. Đường Đi URL Từ Container -> Gateway -> Điện Thoại (URL Resolution)

Một trong những thách thức cốt lõi của hệ thống Microservices chạy trong Docker là **sự tách biệt giữa mạng nội bộ container (Bridge Network) và mạng của thiết bị di động bên ngoài**.

```
[ translate-audio-service:8002 ]
         │
         │ (Tạo file MP3 nội bộ)
         │ Tên file: d35fbc...345.mp3
         │ URL nội bộ: http://translate-audio-service:8002/audio/d35fbc...345.mp3
         ▼
[ gateway-service:8000 ]
         │
         │ 1. Rewrite URL: Đổi thành relative URL "/audio/d35fbc...345.mp3"
         │ 2. Lắng nghe GET /audio/{filename}
         │ 3. Validate basename/hash chống path traversal
         │ 4. Gọi HTTP tới http://translate-audio-service:8002/audio/{filename}
         │ 5. Chuyển tiếp Range header và stream nhị phân audio/mpeg về client
         ▼
[ Thiết Bị Di Động ]
         │
         │ Truy cập cùng Origin Gateway (ví dụ https://demo.trycloudflare.com/audio/...)
         │ Tuyệt đối KHÔNG gọi domain nội bộ translate-audio-service
```

### Tại sao không trả URL của container cho điện thoại?
1. **Lỗi phân giải tên miền (DNS Resolution)**:
   Hostname `translate-audio-service` chỉ tồn tại trong DNS nội bộ do Docker daemon quản lý. Điện thoại bên ngoài không thể phân giải tên miền này và sẽ gặp lỗi `ERR_NAME_NOT_RESOLVED`.
2. **Không mở cổng 8002 ra ngoài**:
   Trong môi trường production hoặc demo qua HTTPS Tunnel, chỉ có cổng `8000` của Gateway được công khai.
3. **Lỗi Mixed Content & CORS**:
   Nếu điện thoại đang mở web qua `https://...` mà audio lại trỏ về `http://...:8002`, trình duyệt sẽ ngay lập tức chặn tải file do vi phạm chính sách Mixed Content.
4. **Giải pháp Same-Origin của Gateway**:
   Mọi `audio_url` trả về client đều có dạng chuẩn relative path `/audio/<filename>.mp3`. Điện thoại tải trực tiếp từ cùng origin với trang web, hỗ trợ Range header để tua âm thanh mượt mà.

---

## 3. Cơ Chế Lưu Trữ Dữ Liệu Bền Vững (Persistence)

Hệ thống sử dụng **Docker Named Volumes** để đảm bảo dữ liệu không bị mất sau khi container restart hoặc rebuild (`docker compose down && docker compose up`):

1. **Named Volume `content_db_data`**:
   - Ánh xạ vào thư mục `/app/data` của `content-service`.
   - File cơ sở dữ liệu SQLite: `/app/data/content.db`.
   - Lưu trữ toàn bộ bảng `locations` (địa điểm, tọa độ GPS, metadata) và bảng `narration_cache` (bản dịch, text_hash, audio_filename, thời gian hết hạn TTL 7 ngày).
2. **Named Volume `audio_cache_data`**:
   - Ánh xạ vào thư mục `/app/audio_files` của `translate-audio-service`.
   - Lưu trữ các tệp âm thanh nhị phân `.mp3` được sinh bởi Edge TTS.
   - Khi restart container, các file MP3 cũ vẫn nằm nguyên vẹn trên volume. Khi client gửi lại GPS tick cho cùng nội dung, `translate-audio-service` kiểm tra thấy file vật lý tồn tại và trả về `cache_hit = true` ngay lập tức mà không cần gọi lại Edge TTS.
3. **Gateway mount client**:
   - Ánh xạ thư mục `./gateway-service/client:/app/client` giúp cập nhật nóng (hot reload) mã HTML/CSS/JS mà không cần rebuild container Gateway.

---

## 4. Chính Sách Autoplay & Cơ Chế Phát Âm Thanh Trên Mobile

Trình duyệt di động áp dụng cơ chế bảo mật khắt khe đối với âm thanh:

### 4.1. Vấn Đề
- Trình duyệt chỉ cho phép phát âm thanh khi có cử chỉ chạm trực tiếp của người dùng (User Gesture).
- Ngay cả khi người dùng đã bấm nút *Bắt đầu thuyết minh* ở bước đầu tiên, luồng kích hoạt âm thanh sau đó (khi người dùng đi vào địa điểm mới sau vài chục giây) vẫn có thể bị trình duyệt coi là ngoài phạm vi của gesture ban đầu và ném lỗi `NotAllowedError`.

### 4.2. Giải Pháp Xử Lý
1. **Bắt Promise `audio.play()`**:
   ```javascript
   const playPromise = audioElement.play();
   if (playPromise !== undefined) {
     playPromise.catch(err => {
       if (err.name === "NotAllowedError") {
         // Autoplay bị chặn: Hiện nút "Cho phép phát âm thanh"
         showPermissionButton();
       } else {
         // Lỗi tải mạng/file hỏng: Chuyển sang Web Speech API
         fallbackToSpeech(text, lang);
       }
     });
   }
   ```
2. **Nút "Cho phép phát âm thanh"**:
   - Xuất hiện nổi bật khi bị `NotAllowedError`. Người dùng chạm vào nút này sẽ tạo gesture mới để kích hoạt lại âm thanh ngay lập tức.
   - Hệ thống **không coi đây là file audio hỏng**.
3. **Fallback Đa Tầng (Graceful Degradation)**:
   - **Tầng 1 (Ưu tiên)**: File MP3 chất lượng cao tạo bởi Microsoft Neural Edge TTS phục vụ qua Gateway.
   - **Tầng 2**: Nếu MP3 bị 404 hoặc lỗi mạng -> Tự động chuyển sang `SpeechSynthesisUtterance` của trình duyệt với đúng mã ngôn ngữ BCP-47 (`vi-VN`, `en-US`, `ja-JP`).
   - **Tầng 3**: Nếu trình duyệt không có gói voice phù hợp -> Hiển thị văn bản mô tả trên màn hình kèm trạng thái rõ ràng, giữ nút `Thử lại thủ công`.
   - **Tầng 4**: Đảm bảo nguyên tắc độc quyền luồng âm: luôn dừng/cancel âm thanh cũ trước khi phát narration mới.
