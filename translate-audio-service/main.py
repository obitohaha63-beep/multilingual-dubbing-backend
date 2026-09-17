from fastapi import FastAPI # Import FastAPI để tạo ứng dụng Web API

app = FastAPI() # Tạo một đối tượng ứng dụng FastAPI


@app.get("/health") # đăng ký một địa chỉ API là /health với phương thức GET. health check endpoint: dùng để kiểm tra nhanh xem service có đang hoạt động hay không.

def health(): # Hàm xử lý khi client gọi GET /health
    return {"service": "translate-audio-service", "status": "ok"}  # dữ liệu mà API trả về cho client, FastAPI sẽ chuyển dictionary Python này thành JSON.
