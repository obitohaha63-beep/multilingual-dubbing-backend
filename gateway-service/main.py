import os
from fastapi import FastAPI, HTTPException, status
import httpx

app = FastAPI(title="gateway-service")

# Địa chỉ của content-service trong Docker network (có thể ghi đè bằng biến môi trường)
CONTENT_SERVICE_URL = os.getenv("CONTENT_SERVICE_URL", "http://content-service:8001")


@app.get("/health")
def health_check():
    """
    Endpoint kiểm tra tình trạng API Gateway.
    Dùng để Docker/K8s/CI biết Gateway vẫn hoạt động bình thường.
    """
    return {"service": "gateway-service", "status": "ok"}


@app.get("/locations/{id}/preview")
async def get_location_preview(id: int):
    """
    Endpoint lấy thông tin xem trước của địa điểm theo id.
    Gọi GET /locations/{id} sang content-service (port 8001) qua HTTP REST bằng httpx,
    sau đó trả nguyên dữ liệu nhận được về cho client.
    """
    target_url = f"{CONTENT_SERVICE_URL}/locations/{id}"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(target_url, timeout=5.0)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Không thể kết nối tới content-service: {str(exc)}",
        )

    # Chuyển tiếp lỗi tương ứng nếu content-service trả về mã lỗi (ví dụ: 404 Không tìm thấy địa điểm)
    if response.is_error:
        try:
            error_data = response.json()
            detail = error_data.get("detail", response.text)
        except Exception:
            detail = response.text
        raise HTTPException(status_code=response.status_code, detail=detail)

    return response.json()


@app.get("/services-health")
async def check_services_health():
    """
    Endpoint kiểm tra khả năng kết nối giữa Gateway và các microservice 
    qua giao thức HTTP REST bằng tên service trong Docker network.
    """
    results = {}
    async with httpx.AsyncClient() as client:
        # Gọi sang content-service
        try:
            resp_content = await client.get("http://content-service:8001/health", timeout=5.0)
            results["content-service"] = resp_content.json()
        except Exception as e:
            results["content-service"] = {"status": "error", "message": str(e)}

        # Gọi sang translate-audio-service
        try:
            resp_translate = await client.get("http://translate-audio-service:8002/health", timeout=5.0)
            results["translate-audio-service"] = resp_translate.json()
        except Exception as e:
            results["translate-audio-service"] = {"status": "error", "message": str(e)}

    return {"gateway": "ok", "downstream_services": results}
