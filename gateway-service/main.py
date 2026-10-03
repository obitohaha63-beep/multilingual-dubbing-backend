"""
gateway-service - port 8000.
Tuần 1: /health, /services-health.
Tuần 2: GET /locations/{id}/preview.
Tuần 3:
  - Phục vụ Web Client tại /app/ (HTML/CSS/JS tĩnh).
  - POST /locations/detect: tiếp nhận GPS từ client, điều phối Content-service và Translate-audio-service.
"""

from __future__ import annotations

import os
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, field_validator

app = FastAPI(title="gateway-service")

# Đường dẫn downstream services từ biến môi trường
CONTENT_SERVICE_URL = os.getenv("CONTENT_SERVICE_URL", "http://content-service:8001")
TTS_SERVICE_URL = os.getenv("TTS_SERVICE_URL", "http://translate-audio-service:8002")

# HTTP client timeouts (giây)
DEFAULT_TIMEOUT = float(os.getenv("GATEWAY_TIMEOUT", "6.0"))

ALLOWED_LANGS = {"vi", "en", "ja"}
CLIENT_DIR = Path(__file__).resolve().parent / "client"


# ===========================================================================
# Pydantic Schemas
# ===========================================================================

class DetectRequest(BaseModel):
    latitude: float
    longitude: float
    language: str
    accuracy_m: Optional[float] = None

    @field_validator("latitude")
    @classmethod
    def _validate_latitude(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("latitude phải là số thực hữu hạn")
        if not (-90.0 <= v <= 90.0):
            raise ValueError("latitude phải nằm trong khoảng [-90, 90]")
        return float(v)

    @field_validator("longitude")
    @classmethod
    def _validate_longitude(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("longitude phải là số thực hữu hạn")
        if not (-180.0 <= v <= 180.0):
            raise ValueError("longitude phải nằm trong khoảng [-180, 180]")
        return float(v)

    @field_validator("language")
    @classmethod
    def _validate_language(cls, v: str) -> str:
        lang = (v or "").strip().lower()
        if lang not in ALLOWED_LANGS:
            raise ValueError(f"Ngôn ngữ '{v}' không được hỗ trợ. Chỉ hỗ trợ: {sorted(ALLOWED_LANGS)}")
        return lang

    @field_validator("accuracy_m")
    @classmethod
    def _validate_accuracy(cls, v: Optional[float]) -> Optional[float]:
        if v is not None:
            if not math.isfinite(v):
                raise ValueError("accuracy_m phải là số thực hữu hạn")
            if v < 0:
                raise ValueError("accuracy_m phải >= 0")
        return v


class DetectResponse(BaseModel):
    detected: bool
    reason: Optional[str] = None
    location: Optional[Dict[str, Any]] = None
    distance_m: Optional[float] = None
    requested_lang: str
    lang: Optional[str] = None
    translated_text: Optional[str] = None
    audio_url: Optional[str] = None
    source_url: Optional[str] = None
    is_fallback: bool = False
    narration_status: Optional[str] = None
    candidates: Optional[List[Dict[str, Any]]] = None

    model_config = ConfigDict(extra="ignore")


# ===========================================================================
# Static Files & Client
# ===========================================================================

if CLIENT_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=str(CLIENT_DIR), html=True), name="client_app")


# ===========================================================================
# API Endpoints
# ===========================================================================

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
            resp_content = await client.get(f"{CONTENT_SERVICE_URL}/health", timeout=DEFAULT_TIMEOUT)
            results["content-service"] = resp_content.json()
        except Exception as e:
            results["content-service"] = {"status": "error", "message": str(e)}

        # Gọi sang translate-audio-service
        try:
            resp_translate = await client.get(f"{TTS_SERVICE_URL}/health", timeout=DEFAULT_TIMEOUT)
            results["translate-audio-service"] = resp_translate.json()
        except Exception as e:
            results["translate-audio-service"] = {"status": "error", "message": str(e)}

    return {"gateway": "ok", "downstream_services": results}


@app.post("/locations/detect", response_model=DetectResponse)
async def detect_location(req: DetectRequest):
    """
    Tiếp nhận tọa độ GPS từ Client, điều phối kiểm tra nearby và discovery:
    1. Gọi content-service GET /locations/nearby (max_distance=500m)
    2. Chọn candidate gần nhất thỏa distance_m <= radius (activation radius).
    3. Nếu không có candidate kích hoạt:
       - Gọi content-service GET /locations/discover (bán kính 500m) để tìm bài viết từ Wikimedia (kèm cache)
       - Sau khi discover, gọi lại /locations/nearby và xét lại candidate kích hoạt
    4. Nếu có candidate kích hoạt (distance_m <= radius):
       - Tuần 3: detected=True, narration_status='not_implemented', chưa gọi TTS
    5. Nếu ngoài radius (khoảng cách > radius):
       - detected=False, reason='outside_activation_radius'
    6. Nếu không có địa điểm nào trong 500m:
       - detected=False, reason='no_place_found'
    """
    candidates: List[Dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as http_client:
        # Bước 1: Gọi /locations/nearby
        try:
            nearby_resp = await http_client.get(
                f"{CONTENT_SERVICE_URL}/locations/nearby",
                params={"latitude": req.latitude, "longitude": req.longitude, "max_distance": 500.0},
            )
        except httpx.RequestError as exc:
            return DetectResponse(
                detected=False,
                reason="upstream_unavailable",
                requested_lang=req.language,
                is_fallback=False,
                narration_status=None,
            )

        if nearby_resp.status_code == 503 or nearby_resp.is_server_error:
            return DetectResponse(
                detected=False,
                reason="upstream_unavailable",
                requested_lang=req.language,
                is_fallback=False,
                narration_status=None,
            )
        elif nearby_resp.is_error:
            # Lỗi client 4xx hoặc khác
            raise HTTPException(
                status_code=nearby_resp.status_code,
                detail=f"Lỗi từ content-service: {nearby_resp.text}",
            )

        nearby_data = nearby_resp.json()
        candidates = nearby_data.get("candidates", [])

        # Kiểm tra xem có candidate nào thỏa activation radius chưa
        active_candidate = _find_active_candidate(candidates)

        # Bước 2: Nếu chưa có candidate kích hoạt, gọi /locations/discover
        if not active_candidate:
            try:
                discover_resp = await http_client.get(
                    f"{CONTENT_SERVICE_URL}/locations/discover",
                    params={"latitude": req.latitude, "longitude": req.longitude, "radius": 500.0, "limit": 5},
                )
            except httpx.RequestError:
                return DetectResponse(
                    detected=False,
                    reason="upstream_unavailable",
                    requested_lang=req.language,
                    is_fallback=False,
                    narration_status=None,
                    candidates=candidates or None,
                )

            if discover_resp.status_code == 503 or discover_resp.is_server_error:
                return DetectResponse(
                    detected=False,
                    reason="upstream_unavailable",
                    requested_lang=req.language,
                    is_fallback=False,
                    narration_status=None,
                    candidates=candidates or None,
                )
            elif discover_resp.is_error:
                raise HTTPException(
                    status_code=discover_resp.status_code,
                    detail=f"Lỗi từ content-service /discover: {discover_resp.text}",
                )

            # Sau khi discover (đã upsert vào DB và có cache), gọi lại /nearby để lấy distance_m chuẩn
            try:
                second_nearby = await http_client.get(
                    f"{CONTENT_SERVICE_URL}/locations/nearby",
                    params={"latitude": req.latitude, "longitude": req.longitude, "max_distance": 500.0},
                )
                if second_nearby.is_success:
                    candidates = second_nearby.json().get("candidates", [])
                    active_candidate = _find_active_candidate(candidates)
            except httpx.RequestError:
                # Nếu lần nearby 2 lỗi mạng, vẫn giữ kết quả trước
                pass

    # Bước 3: Đánh giá kết quả tìm kiếm và kích hoạt
    if not candidates:
        return DetectResponse(
            detected=False,
            reason="no_place_found",
            location=None,
            distance_m=None,
            requested_lang=req.language,
            lang=None,
            translated_text=None,
            audio_url=None,
            source_url=None,
            is_fallback=False,
            narration_status=None,
            candidates=None,
        )

    # Có candidates nhưng kiểm tra activation
    if active_candidate:
        # Nằm trong bán kính kích hoạt
        dist = active_candidate.get("distance_m")
        source_url = active_candidate.get("source_url")
        return DetectResponse(
            detected=True,
            reason=None,
            location=active_candidate,
            distance_m=round(dist, 1) if dist is not None else None,
            requested_lang=req.language,
            lang=None,
            translated_text=None,
            audio_url=None,
            source_url=source_url,
            is_fallback=False,
            narration_status="not_implemented",
            candidates=candidates,
        )
    else:
        # Có địa điểm trong 500m nhưng ngoài bán kính kích hoạt (activation radius)
        closest = candidates[0]
        dist = closest.get("distance_m")
        return DetectResponse(
            detected=False,
            reason="outside_activation_radius",
            location=closest,
            distance_m=round(dist, 1) if dist is not None else None,
            requested_lang=req.language,
            lang=None,
            translated_text=None,
            audio_url=None,
            source_url=closest.get("source_url"),
            is_fallback=False,
            narration_status=None,
            candidates=candidates,
        )


def _find_active_candidate(candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Chọn candidate gần nhất thỏa điều kiện kích hoạt:
    distance_m <= radius (mặc định 80m nếu radius không có)
    candidates đã được content-service sắp xếp tăng dần theo distance_m.
    """
    for c in candidates:
        distance = c.get("distance_m")
        radius = c.get("radius")
        if radius is None:
            radius = 80.0
        if distance is not None and distance <= radius:
            return c
    return None


@app.get("/locations/{id}/preview")
async def get_location_preview(id: int):
    """
    Endpoint lấy thông tin xem trước của địa điểm theo id.
    Giữ lại từ Tuần 2 để debug.
    """
    target_url = f"{CONTENT_SERVICE_URL}/locations/{id}"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(target_url, timeout=DEFAULT_TIMEOUT)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Không thể kết nối tới content-service: {str(exc)}",
        )

    if response.is_error:
        try:
            error_data = response.json()
            detail = error_data.get("detail", response.text)
        except Exception:
            detail = response.text
        raise HTTPException(status_code=response.status_code, detail=detail)

    return response.json()
