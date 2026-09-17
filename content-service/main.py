from fastapi import FastAPI

# Tạo 1 "app" FastAPI - đây là đối tượng trung tâm, mọi endpoint (route) đều được khai báo thông qua app.
app = FastAPI(title="content-service")


@app.get("/health")
def health_check():
    """
    Endpoint kiểm tra tình trạng service (health check).
    Dùng để Docker/Gateway/CI biết service này còn sống và trả lời được request hay không.
    """
    return {"service": "content-service", "status": "ok"}
