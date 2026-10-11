# Hướng Dẫn Chạy Demo Trên Thiết Bị Di Động (Mobile Demo Guide)

Tài liệu này hướng dẫn cách kết nối và chạy thử nghiệm Web Client (`/app/`) trên trình duyệt điện thoại thực tế trong bối cảnh ứng dụng sử dụng Geolocation API (GPS).

---

## 1. Yêu cầu Cốt lõi: Geolocation & Secure Context (HTTPS)

Theo chuẩn bảo mật W3C và chính sách của các trình duyệt hiện đại (Chrome, Safari, Firefox trên Android & iOS):
- **Geolocation API (`navigator.geolocation.watchPosition`) BẮT BUỘC phải chạy trong Secure Context (Ngữ cảnh an toàn).**
- Trong môi trường `Insecure Context` (HTTP thông thường):
  - Trình duyệt sẽ **vô hiệu hóa hoàn toàn API** hoặc tự động trả về lỗi `PERMISSION_DENIED` / không thể xin quyền GPS.
- **Quy tắc về Localhost:**
  - Chuẩn Web chỉ coi `http://localhost` hoặc `http://127.0.0.1` là Secure Context **trên chính thiết bị đang chạy trình duyệt đó**.
  - **Lưu ý quan trọng**: Khi bạn mở trình duyệt trên điện thoại và gõ `http://<IP-LAN-MÁY-TÍNH>:8000/app/`, điện thoại xem địa chỉ IP mạng nội bộ này là **Insecure HTTP**, KHÔNG PHẢI localhost! Vì vậy, GPS sẽ bị chặn và báo lỗi ngay lập tức.

---

## 2. Giải pháp Cung cấp HTTPS Tin cậy Cùng Origin

Cả Web Client (`/app/`), các API Gateway (`/locations/detect`), và sau này là file Audio đều nằm **chung Origin** (same-origin) tại Gateway port 8000. Để điện thoại truy cập được GPS, bạn cần một đường truyền HTTPS mà hệ điều hành điện thoại tin cậy.

### Cách 1: Sử dụng Đường hầm HTTPS (HTTPS Tunnel) — Khuyến nghị cho Buổi Demo

Đây là cách nhanh nhất và tiện lợi nhất để demo thực địa hoặc trong lớp học mà không cần cài đặt chứng chỉ thủ công lên điện thoại.

1. **Công cụ gợi ý**: `ngrok`, `cloudflare tunnel (cloudflared)`, hoặc `localtunnel`.
2. **Khởi chạy Gateway Backend**:
   ```bash
   # Khởi chạy toàn bộ hệ thống bằng Docker Compose hoặc Uvicorn
   docker-compose up
   ```
   *(Gateway đang lắng nghe tại port 8000).*

3. **Mở đường hầm HTTPS tới port 8000**:
   - Sử dụng **Cloudflare Tunnel (miễn phí, không cần đăng ký tài khoản)**:
     ```bash
     cloudflared tunnel --url http://localhost:8000
     ```
     Console sẽ in ra một đường dẫn HTTPS công khai, ví dụ:
     `https://random-subdomain.trycloudflare.com`
   - Hoặc sử dụng **Ngrok**:
     ```bash
     ngrok http 8000
     ```
     Nhận URL HTTPS dạng: `https://xxxx.ngrok-free.app`

4. **Trải nghiệm trên điện thoại**:
   - Mở trình duyệt điện thoại (Safari trên iOS hoặc Chrome trên Android).
   - Truy cập: `https://<URL-TUNNEL>/app/`
   - Bấm **"Bắt đầu thuyết minh"** -> Trình duyệt sẽ hiển thị popup hỏi quyền vị trí: Chọn **"Cho phép khi dùng ứng dụng"**.

---

### Cách 2: Sử dụng Reverse Proxy với Chứng chỉ SSL Nội bộ (mkcert)

Phù hợp khi test trong mạng Wi-Fi nội bộ mà không muốn mở Internet ra ngoài:

1. **Cài đặt `mkcert` trên máy tính phát triển**:
   - Tạo Local CA và cấp chứng chỉ cho IP mạng LAN (ví dụ `192.168.1.50`).
2. **Cài Root CA của mkcert vào điện thoại**:
   - Gửi file root CA sang điện thoại và cài đặt vào mục *Tin cậy chứng chỉ người dùng (Trusted Certificates)*.
3. **Cấu hình Nginx / Caddy làm Reverse Proxy**:
   - Lắng nghe cổng 443 với chứng chỉ SSL vừa tạo, forward toàn bộ traffic tới `http://localhost:8000`.
4. **Truy cập từ điện thoại**: `https://192.168.1.50/app/`.

---

## 3. Phạm vi Hoạt động: Foreground Mobile Web

- **Trọng tâm Tuần 3**: Ứng dụng là **Foreground Web App** trên trình duyệt di động.
- Người dùng mở tab trình duyệt, giữ màn hình sáng khi di chuyển.
- Trình duyệt sẽ liên tục cập nhật GPS qua `watchPosition` và timer nền 1s.
- *Lưu ý về Background*: Các trình duyệt di động (đặc biệt là Safari iOS) sẽ đóng băng (freeze) JavaScript và Geolocation khi khóa màn hình hoặc chuyển tab khác trừ khi tích hợp cơ chế Service Worker/PWA chuyên sâu hoặc Native App (sẽ được phát triển ở các giai đoạn sau).

---

## 4. Các bước đã cấu hình và phần cần chuẩn bị thêm khi demo

| Thành phần | Trạng thái | Chi tiết |
|---|---|---|
| **Web Client (`/app/`)** | **Đã hoàn thành** | Giao diện HTML/CSS/JS responsive cho màn hình di động, xử lý callback GPS, cờ trạng thái rõ ràng. |
| **API `/locations/detect`** | **Đã hoàn thành** | Điều phối kiểm tra Nearby và tự động Discover dữ liệu Wikipedia. |
| **Same-origin Serving** | **Đã hoàn thành** | Gateway tự phục vụ client tại `/app/` và nhận API tại `/locations/detect`, không bị lỗi CORS. |
| **Đường hầm HTTPS / SSL** | **Cần kích hoạt khi demo** | Chưa tự động đăng ký dịch vụ bên ngoài (như ngrok/cloudflare). Người demo chỉ cần chạy 1 câu lệnh tunnel (như hướng dẫn ở Mục 2) trước khi trình diễn. |
