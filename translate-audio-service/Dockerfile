# Sử dụng Python 3.11 phiên bản slim làm image nền
FROM python:3.11-slim
 # Đặt thư mục làm việc bên trong container là /app
WORKDIR /app

# Cài đặt các thư viện cần thiết từ requirements.txt
# --no-cache-dir: không lưu cache để giảm dung lượng image
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Copy toàn bộ source code của project vào container
COPY . .
# Khai báo container sẽ sử dụng port 8002
EXPOSE 8002

# Chạy ứng dụng FastAPI bằng Uvicorn khi container khởi động
#dùng Uvicorn để chạy biến app nằm trong file main.py
# --host 0.0.0.0: cho phép nhận kết nối từ bên ngoài container
# --port 8002: chạy server trên port 8002
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]

