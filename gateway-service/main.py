from fastapi import FastAPI
import httpx

app = FastAPI(title="gateway-service")


@app.get("/health")
def health_check():
    """
    Endpoint kiểm tra tình trạng API Gateway.
    Dùng để Docker/K8s/CI biết Gateway vẫn hoạt động bình thường.
    """
    return {"service": "gateway-service", "status": "ok"}


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
