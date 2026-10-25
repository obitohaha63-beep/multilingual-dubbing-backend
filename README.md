# Multilingual Video Dubbing Backend Architecture

> **Cập nhật GPS Audio Guide:** luồng mới do Content Service quản lý:
> Wikimedia → Gemini → Edge TTS → Supabase PostgreSQL/Storage.
> Đọc [hướng dẫn cấu hình và vận hành](docs/gemini-supabase.md),
> chạy [SQL migration Supabase](docs/supabase-schema.sql) trước khi deploy.
> Các mô tả SQLite/cache MP3 ở phần cũ bên dưới chỉ áp dụng cho chế độ tương thích.

Hệ thống backend xử lý dịch thuật và lồng tiếng video đa ngôn ngữ được thiết kế theo kiến trúc **Microservices**.

---

## 1. Tổng quan Kiến trúc Hệ thống (System Architecture)

Hệ thống gồm 3 microservices chính hoạt động độc lập:

```
                  +-----------------------+
                  |     Client / User     |
                  +-----------+-----------+
                              |
                              | HTTP REST (Port 8000)
                              v
                  +-----------------------+
                  |    gateway-service    |
                  |      (Port 8000)      |
                  +-----------+-----------+
                              |
       +----------------------+----------------------+
       | Internal HTTP REST                          | Internal HTTP REST
       | http://content-service:8001                 | http://translate-audio-service:8002
       v                                             v
+-----------------------+                   +-----------------------+
|    content-service    |                   |translate-audio-service|
|      (Port 8001)      |                   |      (Port 8002)      |
+-----------------------+                   +-----------------------+
```

### Vai trò của từng Service:
1. **`gateway-service` (Port 8000)**:
   - Đóng vai trò là **API Gateway** (Điểm truy cập duy nhất - Single Entry Point) cho toàn bộ ứng dụng backend.
   - Tiếp nhận request từ Client, thực hiện kiểm tra quyền (authentication), giới hạn lưu lượng (rate limiting) và chuyển tiếp (routing) đến các service xử lý phía trong.
   - Cung cấp endpoint: `GET /health` và `GET /services-health` (dùng để test giao tiếp tới các dịch vụ nội bộ).

2. **`content-service` (Port 8001)**:
   - Chịu trách nhiệm quản lý nội dung video, thông tin bài đăng, tiêu đề, mô tả và các dữ liệu liên quan.
   - Cung cấp endpoint: `GET /health`.

3. **`translate-audio-service` (Port 8002)**:
   - Chịu trách nhiệm xử lý chuyển đổi ngôn ngữ, dịch văn bản, tách/ghép và lồng tiếng âm thanh (Audio Translation & Dubbing).
   - Cung cấp endpoint: `GET /health`.

### Giao tiếp qua HTTP REST:
- **Bên ngoài (External Client)**: Client (Web/Mobile) chỉ cần kết nối tới **API Gateway** ở cổng `8000`.
- **Nội bộ (Internal Network)**: Trong cùng Docker Network, các service giao tiếp trực tiếp qua giao thức **HTTP REST API** bằng **Tên Service (Service Name)** được Docker tự động phân giải tên miền (DNS Resolution). 
  - Ví dụ: Gateway có thể gọi HTTP request tới `http://content-service:8001/health` hoặc `http://translate-audio-service:8002/health` mà không cần quan tâm IP thực tế của container là gì.

---

## 2. Hướng dẫn khởi chạy ứng dụng

### Yêu cầu:
- Đã cài đặt **Docker** và **Docker Compose**.

### Lệnh khởi chạy:
Tại thư mục gốc dự án (`multilingual-video-dubbing-backend`), chạy lệnh:

```bash
docker-compose up --build
```
*(Thêm cờ `-d` nếu muốn ứng dụng chạy ẩn dưới nền - detached mode)*

### Kiểm tra các endpoint:
- **Gateway Health Check**: [http://localhost:8000/health](http://localhost:8000/health)
- **Gateway Inter-Service Check**: [http://localhost:8000/services-health](http://localhost:8000/services-health)
- **Content Service Health Check**: [http://localhost:8001/health](http://localhost:8001/health)
- **Translate Audio Service Health Check**: [http://localhost:8002/health](http://localhost:8002/health)

Để dừng tất cả các service:
```bash
docker-compose down
```

---

## 3. Docker Compose hoạt động như thế nào?

### 💡 Docker Compose là gì?
Docker là công cụ giúp đóng gói ứng dụng cùng môi trường chạy vào một **Container**. Khi dự án phát triển thành kiến trúc Microservices gồm nhiều container (như 3 service ở trên), việc chạy từng lệnh `docker build` và `docker run` cho từng container sẽ rất rườm rà.

**Docker Compose** là công cụ điều phối (orchestration tool) cho phép định nghĩa và khởi chạy ứng dụng gồm **nhiều Docker container** chỉ bằng một file cấu hình duy nhất: `docker-compose.yml`.

### 🔍 Giải thích file `docker-compose.yml` trong dự án:

```yaml
version: '3.8'

services:
  gateway-service:
    build:
      context: ./gateway-service
      dockerfile: Dockerfile
    container_name: gateway-service
    ports:
      - "8000:8000"
    depends_on:
      - content-service
      - translate-audio-service
    restart: always

  content-service: ...
  translate-audio-service: ...
```

#### Chi tiết các thành phần:
1. `version`: Quy định phiên bản cú pháp cấu hình của Docker Compose (ở đây dùng phiên bản 3.8).
2. `services`: Danh sách các dịch vụ/container cần khởi chạy trong dự án.
3. `build`: Chỉ định vị trí nguồn để Docker tự xây dựng Image:
   - `context`: Đường dẫn tới thư mục chứa mã nguồn service.
   - `dockerfile`: Tên file Dockerfile hướng dẫn các bước đóng gói (cài Python, requirements.txt, copy code...).
4. `container_name`: Đặt tên cố định cho container khi hiển thị trong danh sách `docker ps`.
5. `ports`: Ánh xạ cổng giữa **Máy thật (Host)** và **Container**:
   - Cú pháp: `"PORT_MÁY_THẬT:PORT_CONTAINER"`.
   - Ví dụ: `"8000:8000"` giúp bạn có thể truy cập `http://localhost:8000` từ trình duyệt ngoài máy tính vào cổng 8000 bên trong container.
6. `depends_on`: Quản lý thứ tự khởi chạy. Trong cấu hình trên, `gateway-service` sẽ chờ `content-service` và `translate-audio-service` khởi động trước.
7. `restart: always`: Tự động khởi động lại container nếu xảy ra sự cố sập app hoặc khởi động lại máy tính.
8. **Mạng nội bộ tự động (Docker Bridge Network)**: Khi chạy `docker-compose up`, Docker Compose sẽ tự động tạo ra 1 mạng ảo chung (Default Network). Mọi container trong file đều kết nối vào mạng này và có thể gọi tới nhau bằng tên dịch vụ (DNS resolution như `http://content-service:8001`).

---

## 4. Công nghệ Text-to-Speech (Edge TTS — Tuần 4)

Trong `translate-audio-service` (Port 8002), tính năng chuyển văn bản thành giọng nói (`POST /text-to-speech`) được tích hợp thông qua thư viện **`edge-tts`**.

### ⚠️ Lưu ý quan trọng về `edge-tts`:
1. **Bản chất thư viện cộng đồng**:
   - `edge-tts` là một thư viện mã nguồn mở từ cộng đồng Python, hoạt động bằng cách kết nối tới endpoint WebSocket dịch vụ "Read Aloud" của trình duyệt Microsoft Edge.
   - **Đây KHÔNG phải là Azure Speech Services API chính thức** (không yêu cầu Azure Subscription Key hay thẻ tín dụng).
2. **Phụ thuộc kết nối Internet (Online Dependency)**:
   - Dịch vụ yêu cầu máy chủ/container phải có kết nối mạng Internet ra ngoài để gửi text và nhận audio stream từ máy chủ Microsoft.
3. **Không cam kết upstream miễn phí / vĩnh viễn**:
   - Do sử dụng endpoint nội bộ của Edge, Microsoft có thể thay đổi giao thức, hạn chế tần suất hoặc đóng endpoint bất kỳ lúc nào mà không có SLA hay cam kết duy trì dài hạn.
   - Giải pháp này hoàn toàn phù hợp cho môi trường học tập, đồ án sinh viên và bản demo thử nghiệm (Proof of Concept).
4. **Cơ chế Cache bền vững & Tối ưu hóa**:
   - Hệ thống lưu trữ các file MP3 trong thư mục `audio_files/` dựa trên khóa băm SHA256 (`text_chuẩn_hóa + lang + voice + phiên_bản_TTS`).
   - Cùng một nội dung thì tên file luôn không đổi giữa các lần định vị GPS tick, tránh tạo trùng file và giảm thiểu tối đa số lần gọi ra bên ngoài.
   - Sử dụng khóa đồng thời (`asyncio.Lock`) và ghi đĩa nguyên tử (`atomic rename` từ file tạm sang `.mp3`) để bảo đảm an toàn dữ liệu và tốc độ phản hồi.

---

## 5. Admin Dashboard & Giám Sát Hệ Thống

Dự án tích hợp trang **Admin Dashboard** dành riêng cho Quản trị viên để theo dõi hoạt động hệ thống GPS Audio Guide:

### Các chỉ số theo dõi:
1. **Khách đang online:** Số lượng trình duyệt có heartbeat hợp lệ trong 90 giây gần nhất (cập nhật tự động mỗi 30s từ client).
2. **Khách duy nhất 24 giờ & 7 ngày:** Số mã định danh ẩn danh (`Client ID`) hoạt động trong khung thời gian tương ứng.
3. **Lượt xem trang (Page Views):** Lượt mở giao diện web `/app/` thực tế (loại trừ các request polling, health-check, heartbeat).
4. **Top địa điểm quan tâm:** Danh sách địa điểm được tìm kiếm hoặc kích hoạt thuyết minh nhiều nhất qua GPS.
5. **Theo dõi AI & TTS:** Số yêu cầu thuyết minh, tỷ lệ thành công/thất bại, thời gian phản hồi trung bình của Edge TTS, phân bổ ngôn ngữ (`vi`, `en`, `ja`).
6. **Trạng thái Microservices:** Kiểm tra kết nối nội bộ giữa Gateway, Content Service và Translate Audio Service.
7. **Nhật ký lỗi (Sanitized Logs):** Ghi nhận các lỗi hệ thống gần nhất với dữ liệu nhạy cảm (IP, password, token) đã được tự động loại bỏ.

### Cấu hình biến môi trường Quản trị trên Render / Local (.env):
| Biến môi trường | Ý nghĩa | Ví dụ giá trị |
|-----------------|---------|---------------|
| `ADMIN_PATH` | Đường dẫn URL quản trị riêng (bảo mật URL) | `/admin-dashboard/` hoặc `/admin-sec-9a8f2/` |
| `ADMIN_USERNAME` | Tên đăng nhập của quản trị viên | `admin` |
| `ADMIN_PASSWORD` | Mật khẩu đăng nhập quản trị (cần đặt mạnh) | `MatKhauBaoMatCucManh2026!` |
| `ADMIN_SESSION_SECRET` | Khóa bí mật dùng ký Session Cookie (HMAC-SHA256) | Chuỗi hex ngẫu nhiên 64 ký tự |
| `ANALYTICS_DB_PATH` | Đường dẫn lưu CSDL SQLite thống kê | `/app/data/analytics.db` |

> **Cách tạo chuỗi ngẫu nhiên mạnh cho `ADMIN_SESSION_SECRET` và `ADMIN_PATH`:**
> ```bash
> python -c "import secrets; print('ADMIN_PATH=/admin-' + secrets.token_hex(4) + '/\nADMIN_SESSION_SECRET=' + secrets.token_hex(32))"
> ```

### Bảo mật & Quyền riêng tư:
- Không lưu địa chỉ IP và không lưu lịch sử tọa độ GPS người dùng.
- Mọi endpoint API quản trị (`/api/summary`, `/api/chart`, `/api/locations`, `/api/errors`) đều yêu cầu Session Cookie hợp lệ (`HttpOnly`, `SameSite=Lax`, `Secure` khi chạy HTTPS). Người chưa đăng nhập sẽ nhận mã `401 Unauthorized`.
- Tự động khóa IP (Rate-limiting) trong 5 phút nếu nhập sai mật khẩu quá 5 lần liên tiếp.

---

## 6. Lưu ý về Cơ chế Lưu trữ trên Render Free

- **Ephemeral Filesystem:** Trên gói **Render Free Tier**, hệ thống đĩa cứng là tạm thời (ephemeral). Khi dịch vụ khởi động lại (restart) hoặc khi deploy phiên bản mới, dữ liệu trong container có thể được làm mới.
- Dữ liệu địa điểm trong `content.db` và số liệu thống kê trong `analytics.db` được duy trì ổn định trong suốt vòng đời của container đang hoạt động.
