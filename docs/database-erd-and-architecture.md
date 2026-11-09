# TÀI LIỆU THIẾT KẾ CƠ SỞ DỮ LIỆU & KIẾN TRÚC SUPABASE (DFD MỨC 1 & 2)

> **Dự án**: Multilingual Dubbing Backend  
> **Cơ sở dữ liệu**: Supabase PostgreSQL 16 + Supabase Storage  
> **Kiến trúc**: Microservices (API Gateway, Content Service, Translate & Audio Service)  
> **Phiên bản Schema**: 6 bảng chuẩn hóa, bảo mật Row Level Security (RLS) toàn diện.

---

## 1. Sơ đồ Quan hệ Thực thể (Entity Relationship Diagram - ERD)

```mermaid
erDiagram
    locations ||--o{ narrations : "1. location_id (RESTRICT / NO ACTION)"
    locations ||--o{ narration_cache : "2. location_id (CASCADE / NO ACTION)"
    narrations |o--o{ narration_cache : "3. narration_id (SET NULL / CASCADE)"
    locations |o--o{ analytics_events : "4. location_id (SET NULL / CASCADE)"
    visitor_sessions |o--o{ analytics_events : "5. client_id (SET NULL / CASCADE)"
    visitor_sessions |o--o{ visitor_access_logs : "6. client_id (SET NULL / CASCADE)"

    locations {
        serial id PK "Khóa chính tự tăng"
        varchar name "Tên địa danh"
        varchar description_vi "Mô tả tiếng Việt chuẩn"
        varchar target_languages "Danh sách ngôn ngữ hỗ trợ"
        varchar description_source "Nội dung gốc từ nguồn"
        float latitude "Vĩ độ WGS84 (-90 đến 90)"
        float longitude "Kinh độ WGS84 (-180 đến 180)"
        float radius "Bán kính kích hoạt (m, mặc định 80)"
        varchar source "Nguồn (manual / wikimedia)"
        varchar source_lang "Mã ngôn ngữ nguồn"
        varchar source_title "Tiêu đề bài viết nguồn"
        int source_page_id "ID bài viết Wikipedia"
        varchar source_url "URL bài viết Wikipedia"
        timestamptz last_synced_at "Thời điểm đồng bộ cuối"
        bool is_ready "Sẵn sàng phát âm thanh"
    }

    narrations {
        varchar id PK "UUID string định danh bản thuyết minh"
        int location_id FK "Liên kết locations.id"
        varchar language "Ngôn ngữ thuyết minh (vi, en, ja,...)"
        varchar voice "Mã giọng đọc Microsoft Neural"
        varchar content_version "Phiên bản nội dung (v1, v2,...)"
        text location_snapshot "JSON snapshot thông tin địa điểm"
        text source_text "Văn bản gốc Wikimedia"
        text source_url "URL tham chiếu nguồn"
        text final_text "Văn bản tinh chỉnh bởi Gemini"
        varchar text_hash "SHA256 của final_text"
        varchar audio_filename "Tên file MP3"
        varchar storage_path "Đường dẫn file trên Supabase Storage"
        varchar status "Trạng thái (pending/processing/ready/failed)"
        int attempts "Số lần thử xử lý (>= 0)"
        varchar lease_token "Token khóa phân tán"
        timestamptz lease_until "Hạn chót lease phân tán"
        timestamptz retry_at "Thời điểm thử lại nếu lỗi tạm thời"
        varchar error_code "Mã lỗi kỹ thuật"
        timestamptz created_at "Thời điểm tạo"
        timestamptz updated_at "Thời điểm cập nhật"
    }

    narration_cache {
        serial id PK "Khóa chính tự tăng"
        int location_id FK "Liên kết locations.id"
        varchar narration_id FK "Liên kết mềm narrations.id"
        varchar requested_lang "Ngôn ngữ yêu cầu từ client"
        varchar lang "Ngôn ngữ thực tế của audio"
        text text "Văn bản thuyết minh"
        varchar source_url "URL nguồn tham chiếu"
        varchar target_title "Tiêu đề đã dịch"
        bool is_fallback "Cờ dự phòng ngôn ngữ"
        varchar reason "Lý do fallback hoặc ghi chú"
        varchar voice "Giọng đọc sử dụng"
        varchar text_hash "SHA256 kiểm tra trùng lặp"
        varchar audio_filename "Tên file âm thanh MP3"
        varchar audio_status "Trạng thái (text_ready/audio_ready)"
        timestamptz created_at "Thời điểm tạo cache"
        timestamptz updated_at "Thời điểm làm mới cache"
        timestamptz expires_at "Hạn hết hiệu lực (TTL)"
    }

    visitor_sessions {
        varchar client_id PK "Mã định danh ẩn danh client (<= 64 ký tự)"
        timestamptz started_at "Thời điểm bắt đầu phiên"
        timestamptz last_seen_at "Thời điểm hoạt động gần nhất"
        int heartbeat_count "Số nhịp tim nhận được (>= 0)"
        varchar user_agent_short "Tóm tắt trình duyệt/thiết bị"
    }

    analytics_events {
        bigint id PK "Khóa chính tự tăng (GENERATED ALWAYS)"
        varchar event_type "1 trong 7 loại sự kiện chuẩn hóa"
        timestamptz occurred_at "Thời điểm xảy ra sự kiện"
        varchar client_id FK "Liên kết visitor_sessions.client_id"
        int location_id FK "Liên kết locations.id"
        varchar language "Ngôn ngữ liên quan"
        varchar path "Đường dẫn HTTP (đã loại query string)"
        float distance_m "Khoảng cách tới POI (mét, không lưu GPS)"
        float duration_ms "Thời gian xử lý TTS / API (ms)"
        bool is_fallback "Có sử dụng ngôn ngữ dự phòng"
        bool cache_hit "Trúng cache âm thanh hay tạo mới"
        varchar reason "Lý do trạng thái / lỗi"
        varchar service "Microservice ghi nhận"
        varchar request_type "Endpoint hoặc loại nghiệp vụ"
        int status_code "Mã phản hồi HTTP (100 - 599)"
        varchar error_type "Phân loại ngoại lệ"
        varchar message "Thông điệp (đã làm sạch bí mật & IP)"
        jsonb metadata "Metadata mở rộng (phẳng, <= 4KB)"
    }

    visitor_access_logs {
        bigserial id PK "Khóa chính tự tăng"
        varchar client_id FK "Liên kết visitor_sessions.client_id"
        varchar path "Đường dẫn trang truy cập (/app/)"
        varchar user_agent_short "User Agent rút gọn"
        timestamptz created_at "Thời điểm truy cập"
    }
```

---

## 2. Chi Tiết Khóa Chính (PK), Khóa Ngoại (FK) và Hành Vi Ràng Buộc

| Bảng nguồn | Cột FK | Bảng đích (PK) | ON DELETE | ON UPDATE | Giải thích ý nghĩa nghiệp vụ |
|---|---|---|---|---|---|
| `narrations` | `location_id` | `locations (id)` | **RESTRICT** | **NO ACTION** | **Bảo vệ toàn vẹn dữ liệu**: Tuyệt đối không cho phép xóa địa điểm nếu đã có bản thuyết minh bền vững liên kết. API `DELETE /locations/{id}` trả mã `409 Conflict`. |
| `narration_cache` | `location_id` | `locations (id)` | **CASCADE** | **NO ACTION** | Khi một địa điểm bị xóa, toàn bộ bản ghi cache tạm thời của địa điểm đó sẽ tự động bị xóa theo. |
| `narration_cache` | `narration_id` | `narrations (id)` | **SET NULL** | **CASCADE** | **Liên kết mềm**: `narration_cache` đóng vai trò tầng cache truy cập nhanh. Nếu bản ghi gốc trong `narrations` bị tái cấu trúc hoặc gỡ bỏ, cache vẫn tồn tại với `narration_id = NULL` mà không gây lỗi sập hệ thống. |
| `analytics_events` | `location_id` | `locations (id)` | **SET NULL** | **CASCADE** | Nếu một địa điểm bị xóa trong tương lai, lịch sử thống kê lượt nghe thuyết minh và phát hiện POI vẫn được giữ nguyên vẹn để phục vụ báo cáo kinh doanh tổng thể. |
| `analytics_events` | `client_id` | `visitor_sessions (client_id)` | **SET NULL** | **CASCADE** | Khi dọn dẹp các phiên khách cũ hơn 30 ngày (`analytics_cleanup`), các sự kiện thống kê vẫn được bảo lưu nhưng tách rời định danh người dùng (đảm bảo quyền riêng tư người dùng). |
| `visitor_access_logs` | `client_id` | `visitor_sessions (client_id)` | **SET NULL** | **CASCADE** | Đảm bảo tính bất biến của bộ đếm tổng lượt xem trang (`page_views_total`). Khi xóa session cũ, access log vẫn còn nguyên vẹn. |

---

## 3. Bảng Đối Chiếu Các Luồng Dữ Liệu D1 – D9 Theo DFD Mức 1 & 2

| Ký hiệu DFD | Tên luồng / Kho dữ liệu | Vị trí lưu trữ thực tế | Cấu trúc vật lý | Chính sách bảo mật & Vòng đời |
|---|---|---|---|---|
| **D1** | Kho Địa điểm (Locations) | Supabase PostgreSQL | Bảng `public.locations` | RLS bật, chỉ `service_role` ghi/đọc; Index không gian `ix_locations_geo`. Bán kính mặc định 80m. |
| **D2** | Kho Thuyết minh bền vững (Narrations) | Supabase PostgreSQL | Bảng `public.narrations` | RLS bật; Khóa định danh `(location_id, language, voice, content_version)` là bất biến (Trigger bảo vệ). File MP3 lưu tại D4. |
| **D3** | Cache Thuyết minh (Narration Cache) | Supabase PostgreSQL | Bảng `public.narration_cache` | Tương thích 100% API cũ (`narration_to_dict`). Có thời hạn `expires_at` (TTL 7 ngày). Dọn định kỳ bằng hàm `purge_expired_narration_cache()`. |
| **D4** | Kho File Âm thanh (Audio Storage) | Supabase Storage Bucket | Bucket `narration-audio` (Private) | Đặt `public = false`. URL được ký động bởi Content Service với thời hạn hiệu lực 3600 giây. Không lưu URL cố định trong DB. |
| **D5** | Kho Dữ liệu Thống kê & Phân tích (Analytics) | Supabase PostgreSQL | 2 bảng: `visitor_sessions` & `analytics_events` | RLS bật. Giữ 30 ngày; trigger tự động tẩy rửa mật khẩu/token/IPv4. Tuyệt đối không lưu GPS chính xác (chỉ lưu khoảng cách `distance_m`). |
| **D6** | Phiên Trình duyệt & Tọa độ Khách (Client Context) | Trình duyệt Client (Frontend) | `localStorage` (`gps_anon_cid`) + RAM trình duyệt | Không lưu tọa độ GPS lên bất kỳ bảng nào trên DB; Gateway chỉ nhận tọa độ qua body HTTP POST để tính toán khoảng cách tức thời rồi giải phóng. |
| **D7** | Cấu hình & Bộ đếm Đăng nhập Quản trị (Admin Rate Limit) | Biến môi trường + Gateway RAM | Biến môi trường Render (`ADMIN_PASSWORD`,...) + Python Dict trong RAM | Khóa brute-force 5 phút theo IP lưu tại RAM Gateway. Thất bại được log ẩn danh vào `analytics_events` (không kèm username/password/IP). |
| **D8** | Cache Nội dung Wikipedia (Wikimedia Content Cache) | RAM Content Service | `_wiki_cache` (Python dictionary kèm thời gian `time.monotonic()`) | Cache trong bộ nhớ RAM có TTL 10 phút, chia grid cell `(lat, lon, radius)` để chống quá tải Wikimedia API. |
| **D9** | Nhật ký Lượt Xem Trang (Visitor Access Logs) | Supabase PostgreSQL | Bảng `public.visitor_access_logs` | Nguồn duy nhất cho chỉ số lượt xem trang vĩnh viễn (tránh ghi trùng vào `analytics_events`). Trigger DB tự động sinh session mới khi mở trang. |

---

## 4. Hướng Dẫn Triển Khai Trên Render & Supabase

### 4.1 Thứ tự thực thi Migration và Chuyển dữ liệu

Để đảm bảo hệ thống không bị gián đoạn hoạt động (Zero Downtime), quy trình triển khai phải tuân thủ nghiêm ngặt theo 5 bước:

```
[Bước 1: Chạy 001_redesign_supabase.sql] 
               │
               ▼
[Bước 2: Chạy 001_verify.sql để xác thực]
               │
               ▼
[Bước 3: Deploy Backend Services lên Render (Gateway, Content, Translate)]
               │
               ▼
[Bước 4: Chạy 002_timestamptz_existing_tables.sql]
               │
               ▼
[Bước 5: Chạy migrate_sqlite_to_supabase.py (Dry-run -> Live)]
```

#### Chi tiết từng bước:
1. **Bước 1: Chạy migration nền tảng**:
   - Mở **Supabase Dashboard** > **SQL Editor**.
   - Mở file `docs/migrations/001_redesign_supabase.sql` và nhấn **Run**.
   - Kết quả: Tạo `visitor_sessions`, `analytics_events`, hàm RPC, trigger làm sạch dữ liệu, RLS cho 6 bảng, và ép bucket `narration-audio` private.
2. **Bước 2: Xác thực tính toàn vẹn**:
   - Chạy file `docs/migrations/001_verify.sql` trong SQL Editor.
   - Kiểm tra kết quả: Mọi dòng kiểm tra phải có cột `status` là `OK`.
3. **Bước 3: Triển khai mã nguồn Backend mới lên Render**:
   - Commit và push code lên repository GitHub.
   - Render tự động build và deploy 3 dịch vụ: `gateway-service`, `content-service`, `translate-audio-service`.
   - Lúc này code Python đã có hàm `as_utc()` xử lý an toàn cả datetime naive và aware.
4. **Bước 4: Chuyển đổi TIMESTAMPTZ cho 3 bảng cũ**:
   - Chạy `docs/migrations/002_timestamptz_existing_tables.sql` trong SQL Editor.
   - Kết quả: Các cột thời gian của `locations`, `narrations`, `narration_cache` đồng bộ sang kiểu `TIMESTAMPTZ` (UTC).
5. **Bước 5: Chuyển đổi dữ liệu SQLite cũ sang Supabase**:
   - Chạy thử nghiệm kiểm tra tính hợp lệ dữ liệu:
     ```bash
     python scripts/migrate_sqlite_to_supabase.py --dry-run
     ```
   - Chạy chuyển dữ liệu thực tế:
     ```bash
     python scripts/migrate_sqlite_to_supabase.py --batch-size 100
     ```

---

### 4.2 Cấu hình Biến Môi Trường Trên Render Dashboard

#### A. Dịch vụ API Gateway (`gateway-service`)
| Tên biến | Giá trị mẫu | Mục đích |
|---|---|---|
| `CONTENT_SERVICE_URL` | `http://content-service:8001` | URL mạng nội bộ Render trỏ sang Content Service |
| `TTS_SERVICE_URL` | `http://translate-audio-service:8002` | URL mạng nội bộ Render trỏ sang Translate Service |
| `SUPABASE_URL` | `https://xxxx.supabase.co` | URL endpoint dự án Supabase |
| `SUPABASE_SECRET_KEY` | `sb_secret_...` | Khóa `service_role` bí mật (được gọi RPC & đọc ghi analytics) |
| `DATABASE_URL` | `postgresql+psycopg://postgres.xxx:pass@pooler.supabase.com:5432/postgres?sslmode=require` | Connection string PostgreSQL (Tùy chọn, dùng connection pooler) |
| `ADMIN_PATH` | `/admin-dashboard/` | Đường dẫn truy cập trang quản trị |
| `ADMIN_USERNAME` | `admin` | Tên đăng nhập Admin Dashboard |
| `ADMIN_PASSWORD` | `<chuỗi mật khẩu bí mật mạnh>` | Mật khẩu Admin Dashboard |
| `ADMIN_SESSION_SECRET` | `<chuỗi hex 64 ký tự ngẫu nhiên>` | Khóa ký HMAC-SHA256 phiên cookie Quản trị viên |
| `GATEWAY_NARRATE_TIMEOUT` | `15` | Timeout gọi `/narrate` (giây) |

#### B. Dịch vụ Content (`content-service`)
| Tên biến | Giá trị mẫu | Mục đích |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://postgres.xxx:pass@pooler.supabase.com:5432/postgres?sslmode=require` | Chuỗi kết nối PostgreSQL Supabase (chế độ Session Pooler port 5432) |
| `SUPABASE_URL` | `https://xxxx.supabase.co` | URL REST API Supabase |
| `SUPABASE_SECRET_KEY` | `sb_secret_...` | Khóa `service_role` quản trị |
| `SUPABASE_AUDIO_BUCKET`| `narration-audio` | Tên bucket Supabase Storage chứa file âm thanh |
| `GEMINI_API_KEY` | `AIza...` | Khóa API Google Gemini AI |
| `GEMINI_MODEL` | `gemini-2.5-flash-lite` | Phiên bản mô hình AI Gemini tinh chỉnh nội dung |
| `TTS_SERVICE_URL` | `http://translate-audio-service:8002` | Endpoint nội bộ gọi dịch vụ tạo giọng đọc |
| `WIKIMEDIA_USER_AGENT` | `multilingual-dubbing-backend/1.0 (contact: email@domain.com)` | User-Agent bắt buộc theo Wikimedia Policy |
| `TTS_PROVIDER_VI` | `google` | Nhà cung cấp TTS tiếng Việt (`google` hoặc `edge`) |
| `GOOGLE_TTS_API_KEY` | `<google api key>` | Khóa API Google Cloud TTS (nếu dùng provider google) |

#### C. Dịch vụ Dịch & Giọng Đọc (`translate-audio-service`)
| Tên biến | Giá trị mẫu | Mục đích |
|---|---|---|
| `AUDIO_DIR` | `/app/audio_files` | Thư mục cache file âm thanh cục bộ trên container |
| `TTS_VOICE_VI` | `vi-VN-HoaiMyNeural` | Giọng đọc tiếng Việt Microsoft Neural mặc định |
| `TTS_VOICE_EN` | `en-US-JennyNeural` | Giọng đọc tiếng Anh Microsoft Neural mặc định |
| `TTS_VOICE_JA` | `ja-JP-NanamiNeural` | Giọng đọc tiếng Nhật Microsoft Neural mặc định |
| `TTS_VOICE_FR` | `fr-FR-DeniseNeural` | Giọng đọc tiếng Pháp Microsoft Neural mặc định |
| `TTS_VOICE_ES` | `es-ES-ElviraNeural` | Giọng đọc tiếng Tây Ban Nha Microsoft Neural mặc định |

---

## 5. Lịch Bảo Trì Tự Động Định Kỳ (Automation via pg_cron)

Để kích hoạt lịch dọn dẹp tự động trực tiếp tại tầng cơ sở dữ liệu Supabase, kích hoạt extension `pg_cron` trong **Supabase Dashboard > Database > Extensions**, sau đó chạy câu lệnh SQL sau:

```sql
-- 1. Lịch dọn dữ liệu Analytics cũ hơn 30 ngày (chạy mỗi ngày vào lúc 03:15 UTC)
SELECT cron.schedule(
  'analytics-retention-cleanup',
  '15 3 * * *',
  $$SELECT public.analytics_cleanup(30, NULL)$$
);

-- 2. Lịch dọn cache thuyết minh đã hết hạn (chạy mỗi ngày vào lúc 03:30 UTC)
SELECT cron.schedule(
  'narration-cache-purge',
  '30 3 * * *',
  $$SELECT public.purge_expired_narration_cache(interval '1 day')$$
);
```

