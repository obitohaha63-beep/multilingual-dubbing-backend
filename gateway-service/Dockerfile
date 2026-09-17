# Sử dụng Python 3.11 phiên bản slim làm image nền
FROM python:3.11-slim

# Đặt thư mục làm việc bên trong container là /app
WORKDIR /app

# Cài đặt các thư viện cần thiết từ requirements.txt
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy toàn bộ source code của gateway-service vào container
COPY . .

# Khai báo container sẽ sử dụng port 8000
EXPOSE 8000

# Chạy ứng dụng FastAPI bằng Uvicorn khi container khởi động
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
