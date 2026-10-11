# Multilingual Video Dubbing Backend Architecture

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