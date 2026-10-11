# Hướng Dẫn Kiểm Thử Trên Thiết Bị Di Động Thực Tế (Mobile Demo & HTTPS Guide)

Tài liệu này chuẩn hóa quy trình triển khai thử nghiệm Web Client (`/app/`), các API Gateway và luồng phát âm thanh (`/audio/{filename}.mp3`) trên thiết bị di động (smartphone Android / iOS).

---

## 1. Nguyên Tắc Cốt Lõi: Secure Context & Same-Origin

### 1.1. Geolocation và Autoplay trên Trình Duyệt Di Động
Theo đặc tả bảo mật W3C và chính sách của các trình duyệt di động hiện đại (Safari trên iOS, Chrome trên Android):
1. **Geolocation API (`navigator.geolocation.watchPosition`)**:
   - **Bắt buộc** phải thực thi trong **Secure Context (Ngữ cảnh an toàn)**.
   - Khi truy cập qua môi trường không an toàn (Insecure Context), trình duyệt sẽ vô hiệu hóa hoàn toàn API hoặc lập tức quăng lỗi `PERMISSION_DENIED` mà không hiển thị popup xin quyền.
2. **Audio Autoplay Policy (Chính sách tự động phát âm thanh)**:
   - Các trình duyệt di động kiểm soát rất nghiêm ngặt hành vi tự động phát âm thanh để tránh làm phiền người dùng và tiết kiệm dữ liệu di động / pin.
   - Khi chưa có cử chỉ chạm (user gesture) tương tác trực tiếp hoặc khi luồng phát sinh sau một khoảng thời gian async, `audio.play()` sẽ bị từ chối với ngoại lệ `NotAllowedError`.

### 1.2. Sai Lầm Phổ Biến: `http://IP-LAN` KHÔNG PHẢI là Secure Context
- Chuẩn Web chỉ định nghĩa `http://localhost` và `http://127.0.0.1` là Secure Context **trên chính thiết bị đang chạy tiến trình trình duyệt**.
- **Tuyệt đối không giả định `http://192.168.x.x:8000` là Secure Context**: Khi mở trình duyệt trên điện thoại và truy cập vào IP mạng LAN của máy tính phát triển (`http://192.168.1.xxx:8000`), điện thoại xem đây là kết nối HTTP công cộng không mã hóa. GPS sẽ bị chặn hoàn toàn!

---

## 2. Phương Án HTTPS Được Lựa Chọn: Cloudflare Tunnel (`cloudflared`)

Để đảm bảo toàn bộ hệ thống hoạt động ổn định trên điện thoại với chứng chỉ SSL/TLS được tin cậy tuyệt đối, dự án lựa chọn **Cloudflare Tunnel (Quick Tunnel)** làm giải pháp chính thức.

### 2.1. Ưu Điểm Kỹ Thuật
1. **Cùng Origin (Same-Origin)**:
   - Cả giao diện Web Client (`/app/`), các endpoint định vị (`/locations/detect`), endpoint debug (`/debug/text-to-speech`) và luồng stream file MP3 (`/audio/{filename}.mp3`) đều đi qua một domain duy nhất (ví dụ: `https://example-demo.trycloudflare.com`).
   - Loại bỏ hoàn toàn vấn đề Cross-Origin Resource Sharing (CORS), Mixed Content (HTTP gọi HTTPS) và sự cố không phân giải được hostname nội bộ Docker (`translate-audio-service:8002`).
2. **Hệ Điều Hành Di Động Tin Cậy 100%**:
   - Cloudflare tự động cấp chứng chỉ SSL/TLS từ các cơ quan chứng thực công khai hàng đầu (Let's Encrypt / DigiCert / Google Trust Services).
   - Thiết bị iOS và Android chấp nhận chứng chỉ ngay lập tức, không có màn hình cảnh báo đỏ "Kết nối không riêng tư" như phương án self-signed certificate.
3. **Không cần mở port router (NAT/Port Forwarding)**:
   - Hoạt động ngay cả khi máy tính kết nối mạng Wi-Fi trường học, công ty hoặc 4G/5G.

---

## 3. Các Bước Cần Thực Hiện Để Test Bằng Điện Thoại Thực Tế

> **Ghi chú về trạng thái kiểm thử:**
> Môi trường phát triển hiện tại đang chạy trên máy chủ lập trình/container Docker. Khi chuẩn bị buổi demo với thiết bị di động thật, người thực hiện cần tuân theo các bước dưới đây và **chỉ xác nhận pass sau khi đã thao tác trực tiếp trên màn hình điện thoại**.

### Bước 1: Khởi động hệ thống Docker Compose trên máy tính
```powershell
# Tại thư mục gốc multilingual-dubbing-backend
copy .env.example .env     # (nếu chưa có .env)
docker compose up --build -d
```
Xác nhận 3 containers đã hoạt động bình thường qua lệnh:
```powershell
docker compose ps
```

### Bước 2: Khởi chạy Cloudflare Tunnel
1. Tải công cụ `cloudflared` (nếu chưa có):
   - Windows (winget): `winget install Cloudflare.cloudflared`
   - Hoặc tải trực tiếp file `.exe` từ [trang phát hành Cloudflare](https://github.com/cloudflare/cloudflared/releases).
2. Chạy lệnh mở đường hầm HTTPS trỏ tới API Gateway (Port 8000):
   ```bash
   cloudflared tunnel --url http://localhost:8000
   ```
3. Quan sát màn hình dòng lệnh, tìm dòng thông báo có chứa URL công khai:
   ```text
   +--------------------------------------------------------------------------------------------+
   |  Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):  |
   |  https://random-subdomain-1234.trycloudflare.com                                           |
   +--------------------------------------------------------------------------------------------+
   ```

### Bước 3: Thao tác trên điện thoại
1. Mở trình duyệt di động: **Safari** (trên iPhone / iPad) hoặc **Chrome** (trên Android).
2. Nhập địa chỉ:
   ```text
   https://random-subdomain-1234.trycloudflare.com/app/
   ```
3. Bấm nút **"Bắt đầu thuyết minh"**:
   - Trình duyệt sẽ xuất hiện thông báo xin cấp quyền truy cập vị trí.
   - Nhấn **"Cho phép khi dùng ứng dụng"** (Allow while using app).
4. Quan sát các trạng thái:
   - Huy hiệu định vị: Chuyển sang màu xanh `Đang nhận GPS` kèm tọa độ thực tế và độ chính xác (ví dụ `±15m`).
   - Huy hiệu Gateway: `Đã phản hồi`.
5. Kiểm tra phát âm thanh:
   - **Kịch bản A (Autoplay tự động được phát)**: Thiết bị phát âm thanh thuyết minh MP3 qua loa/tai nghe, huy hiệu hiển thị `Đang phát MP3`.
   - **Kịch bản B (Autoplay bị chặn bởi chính sách trình duyệt)**:
     - Huy hiệu hiển thị `Trình duyệt chặn tự động phát (Chạm để nghe)`.
     - Hộp thoại cảnh báo màu vàng xuất hiện với nút **"🔊 Cho phép phát âm thanh"**.
     - Người dùng chạm vào nút này -> âm thanh lập tức phát mượt mà, phân biệt rõ với lỗi hỏng file.
6. Kiểm tra các nút điều khiển:
   - Thử chạm **"🔇 Tắt tiếng"** -> âm thanh tắt, nút đổi thành **"🔊 Bật tiếng"**.
   - Thử chạm **"⏹ Dừng phát"** -> âm thanh ngừng ngay lập tức.
   - Thử chạm **"🔁 Phát lại"** -> âm thanh phát lại từ đầu.

---

## 4. Bảng Theo Dõi Trạng Thái Kiểm Thử

| Hạng mục kiểm thử | Môi trường kiểm thử | Trạng thái hiện tại | Ghi chú kỹ thuật |
|---|---|---|---|
| **Same-Origin Serving (`/app/` + API + Audio)** | Docker / Localhost (Port 8000) | **ĐÃ PASS (Automated Tests)** | Đã test 38/38 unit/integration tests trên Gateway. |
| **Proxy Stream MP3 & Range header** | Gateway -> translate-audio-service | **ĐÃ PASS (Automated Tests)** | Hỗ trợ 200, 206 partial content, 404, 503, 422. |
| **Khởi động Cloudflare Tunnel** | CLI máy host | **ĐÃ XÁC NHẬN CƠ CHẾ** | Hoạt động trên port 8000. |
| **Kiểm thử trên điện thoại thật ngoài thực địa** | Thiết bị di động vật lý (iOS/Android) | **CẦN THỰC HIỆN TRƯỚC DEMO** | Không ghi pass trước khi có thiết bị thực tế kết nối. |
