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
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, field_validator

app = FastAPI(title="gateway-service")

@app.get("/", include_in_schema=False)
def home():
    return RedirectResponse(url="/app/", status_code=302)


# Regex validate tên file MP3 an toàn chống path traversal
SAFE_AUDIO_FILENAME_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.mp3$",
    re.IGNORECASE,
)

# Đường dẫn downstream services từ biến môi trường
CONTENT_SERVICE_URL = os.getenv("CONTENT_SERVICE_URL", "http://content-service:8001")
TTS_SERVICE_URL = os.getenv("TTS_SERVICE_URL", "http://translate-audio-service:8002")

# HTTP client timeouts (giây)
DEFAULT_TIMEOUT = float(os.getenv("GATEWAY_TIMEOUT", "6.0"))
GATEWAY_NARRATE_TIMEOUT = float(os.getenv("GATEWAY_NARRATE_TIMEOUT", "30.0"))

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
        # Nằm trong bán kính kích hoạt -> Gọi B /narrate
        dist = active_candidate.get("distance_m")
        source_url = active_candidate.get("source_url")

        source_title = str(active_candidate.get("source_title") or active_candidate.get("name") or "Địa điểm").strip()
        source_lang = active_candidate.get("source_lang") or "vi"
        if source_lang not in ALLOWED_LANGS:
            source_lang = "vi"
        source_text = str(
            active_candidate.get("description_source")
            or active_candidate.get("description_vi")
            or active_candidate.get("name")
            or "Không có mô tả"
        ).strip()
        source_page_id = str(active_candidate.get("source_page_id") or "0")
        if not source_page_id.lstrip("-").isdigit():
            source_page_id = "0"

        narrate_payload = {
            "location_id": int(active_candidate["id"]),
            "source_title": source_title,
            "source_lang": source_lang,
            "target_lang": req.language,
            "source_text": source_text,
            "source_page_id": source_page_id,
            "source_url": source_url,
        }

        narrate_timeout = httpx.Timeout(GATEWAY_NARRATE_TIMEOUT, connect=5.0, read=GATEWAY_NARRATE_TIMEOUT)
        try:
            async with httpx.AsyncClient(timeout=narrate_timeout) as narrate_client:
                narrate_resp = await narrate_client.post(
                    f"{TTS_SERVICE_URL}/narrate",
                    json=narrate_payload,
                )
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            # Lỗi mạng hoặc timeout từ B
            return DetectResponse(
                detected=True,
                reason="narrate_unavailable",
                location=active_candidate,
                distance_m=round(dist, 1) if dist is not None else None,
                requested_lang=req.language,
                lang=None,
                translated_text=None,
                audio_url=None,
                source_url=source_url,
                is_fallback=False,
                narration_status="error",
                candidates=candidates,
            )

        if narrate_resp.status_code == 200:
            b_data = narrate_resp.json()
            raw_audio_url = b_data.get("audio_url")
            public_audio_url = None
            if raw_audio_url:
                # Đổi sang relative URL /audio/<filename>.mp3 cùng origin Gateway
                audio_filename = b_data.get("audio_filename") or raw_audio_url.split("/")[-1]
                public_audio_url = f"/audio/{audio_filename}"

            return DetectResponse(
                detected=True,
                reason=b_data.get("reason"),
                location=active_candidate,
                distance_m=round(dist, 1) if dist is not None else None,
                requested_lang=b_data.get("requested_lang", req.language),
                lang=b_data.get("lang"),
                translated_text=b_data.get("translated_text"),
                audio_url=public_audio_url,
                source_url=b_data.get("source_url") or source_url,
                is_fallback=b_data.get("is_fallback", False),
                narration_status=b_data.get("narration_status", "audio_ready"),
                candidates=candidates,
            )
        else:
            # B trả lỗi 4xx hoặc 5xx
            try:
                err_data = narrate_resp.json()
                detail = err_data.get("detail", narrate_resp.text)
            except Exception:
                detail = narrate_resp.text

            return DetectResponse(
                detected=True,
                reason=f"narrate_error: {detail}",
                location=active_candidate,
                distance_m=round(dist, 1) if dist is not None else None,
                requested_lang=req.language,
                lang=None,
                translated_text=None,
                audio_url=None,
                source_url=source_url,
                is_fallback=False,
                narration_status="error",
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


# ===========================================================================
# Tuần 4: Proxy Stream Audio & Debug TTS Endpoint
# ===========================================================================

@app.get("/audio/{filename}")
async def proxy_audio_stream(filename: str, request: Request):
    """
    Proxy stream file âm thanh MP3 từ translate-audio-service (B).
    - Validate basename/hash chống path traversal (chỉ nhận file an toàn có đuôi .mp3).
    - Chỉ proxy tới B đã cấu hình qua hostname TTS_SERVICE_URL, không nhận URL tùy ý.
    - Hỗ trợ và chuyển tiếp header Range để mobile player có thể tua/seek stream.
    - Timeout hữu hạn (GATEWAY_TIMEOUT), 404 rõ ràng khi file không tồn tại trên B.
    - Mọi audio_url public trả về client là /audio/<filename>.mp3 cùng origin Gateway.
    """
    # 1. Validate an toàn tuyệt đối chống path traversal
    if ".." in filename or "/" in filename or "\\" in filename or ":" in filename:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Tên file audio không hợp lệ hoặc chứa ký tự bị cấm",
        )

    if not SAFE_AUDIO_FILENAME_RE.match(filename):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Tên file audio không hợp lệ hoặc không có định dạng .mp3",
        )

    # 2. Xây dựng request forward tới B
    target_url = f"{TTS_SERVICE_URL}/audio/{filename}"
    req_headers: Dict[str, str] = {}
    range_header = request.headers.get("range")
    if range_header:
        req_headers["range"] = range_header

    client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
    try:
        upstream_req = client.build_request("GET", target_url, headers=req_headers)
        upstream_resp = await client.send(upstream_req, stream=True)
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        await client.aclose()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Dịch vụ âm thanh tạm thời không khả dụng: {str(exc)}",
        )

    # 3. Xử lý các mã trạng thái từ B
    if upstream_resp.status_code == 404:
        await upstream_resp.aclose()
        await client.aclose()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File âm thanh không tồn tại trên máy chủ audio",
        )

    if upstream_resp.status_code not in (200, 206):
        status_code = upstream_resp.status_code
        await upstream_resp.aclose()
        await client.aclose()
        raise HTTPException(
            status_code=status_code,
            detail=f"Máy chủ audio phản hồi lỗi HTTP {status_code}",
        )

    # 4. Trích xuất headers cần thiết cho streaming và mobile range request
    response_headers: Dict[str, str] = {
        "Content-Type": upstream_resp.headers.get("Content-Type", "audio/mpeg"),
        "Accept-Ranges": upstream_resp.headers.get("Accept-Ranges", "bytes"),
    }
    if "Content-Length" in upstream_resp.headers:
        response_headers["Content-Length"] = upstream_resp.headers["Content-Length"]
    if "Content-Range" in upstream_resp.headers:
        response_headers["Content-Range"] = upstream_resp.headers["Content-Range"]

    async def stream_generator():
        try:
            async for chunk in upstream_resp.aiter_bytes(chunk_size=16384):
                yield chunk
        finally:
            await upstream_resp.aclose()
            await client.aclose()

    return StreamingResponse(
        stream_generator(),
        status_code=upstream_resp.status_code,
        media_type=response_headers["Content-Type"],
        headers=response_headers,
    )


class DebugTTSRequest(BaseModel):
    text: str
    lang: str

    @field_validator("lang")
    @classmethod
    def _validate_lang(cls, v: str) -> str:
        lang = (v or "").strip().lower()
        if lang not in ALLOWED_LANGS:
            raise ValueError(f"Ngôn ngữ '{v}' không được hỗ trợ. Chỉ hỗ trợ: {sorted(ALLOWED_LANGS)}")
        return lang

    @field_validator("text")
    @classmethod
    def _validate_text(cls, v: str) -> str:
        text = (v or "").strip()
        if not text:
            raise ValueError("Văn bản không được để trống")
        if len(text) > 1200:
            raise ValueError("Văn bản không được vượt quá 1200 ký tự")
        return text


@app.post("/debug/text-to-speech")
async def debug_text_to_speech(req: DebugTTSRequest):
    """
    Endpoint debug dành riêng cho việc kiểm tra audio player độc lập.
    Gọi translate-audio-service /text-to-speech, nhận kết quả và đảm bảo
    audio_url trả về client luôn là URL cùng origin Gateway (/audio/<filename>.mp3).
    Tắt mặc định, không biến nhập địa điểm thành core flow.
    """
    target_url = f"{TTS_SERVICE_URL}/text-to-speech"
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                target_url,
                json={"text": req.text, "lang": req.lang},
            )
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Quá thời gian chờ phản hồi từ dịch vụ TTS (Timeout)",
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Lỗi kết nối tới translate-audio-service: {str(exc)}",
        )

    if resp.is_error:
        try:
            err_data = resp.json()
            detail = err_data.get("detail", resp.text)
        except Exception:
            detail = resp.text
        raise HTTPException(status_code=resp.status_code, detail=detail)

    data = resp.json()
    filename = data.get("audio_filename")
    if filename:
        # Đảm bảo audio_url là same-origin relative URL trên Gateway
        data["audio_url"] = f"/audio/{filename}"
    return data
