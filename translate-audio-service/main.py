"""Translate Audio Service (8002): tạo MP3 bằng Edge TTS.

/text-to-speech nhận văn bản cuối cùng và voice từ Content Service.
/narrate cũ chỉ chuyển tiếp về Content Service, không điều phối nội dung.
/audio, /localized-content và /translate được giữ để tương thích API cũ.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

import tts
import wikimedia_client
from wikimedia_client import ALLOWED_LANGS, WikimediaError

logger = logging.getLogger("translate_audio.main")

# ---------------------------------------------------------------------------
# Cấu hình URL nội bộ đến content-service (A)
# ---------------------------------------------------------------------------
CONTENT_SERVICE_URL = os.getenv("CONTENT_SERVICE_URL", "http://localhost:8001")

# Timeout cho HTTP đi đến content-service (A) — ngân sách hữu hạn
CONTENT_SERVICE_TIMEOUT = float(os.getenv("CONTENT_SERVICE_TIMEOUT", "5.0"))

app = FastAPI(
    title="translate-audio-service",
    description=(
        "Tạo file MP3 từ văn bản cuối cùng bằng Edge TTS. "
        "Content Service quản lý luồng Wikimedia, Gemini và Supabase. "
        "Các API bản địa hóa/dịch cũ được giữ để tương thích."
    ),
)

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

MAX_CHARS = 1200  # do dai toi da tra ve / chap nhan


class LocalizedContentRequest(BaseModel):
    """Body cho POST /localized-content."""

    source_title: str
    source_lang: str
    target_lang: str
    source_page_id: str  # pageid dang chuan de truyen vao API

    @field_validator("source_lang", "target_lang")
    @classmethod
    def _validate_lang(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ALLOWED_LANGS:
            raise ValueError(
                f"Ma ngon ngu '{v}' khong duoc ho tro. "
                f"Chi chap nhan: {sorted(ALLOWED_LANGS)}"
            )
        return v

    @field_validator("source_page_id")
    @classmethod
    def _validate_page_id(cls, v: str) -> str:
        v = v.strip()
        if not v.lstrip("-").isdigit():
            raise ValueError("source_page_id phai la chuoi so nguyen")
        return v

    @field_validator("source_title")
    @classmethod
    def _not_blank_title(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("source_title khong duoc de trong")
        return v.strip()


class LocalizedContentResponse(BaseModel):
    """Response cho POST /localized-content."""

    found: bool
    requested_lang: str   # target_lang nguoi dung yeu cau
    target_lang: str      # ngon ngu thuc su duoc hoi (= requested_lang)
    lang: Optional[str]   # ngon ngu that cua text tra ve (co the None)
    target_title: Optional[str]
    text: Optional[str]
    source_url: Optional[str]
    is_fallback: bool
    reason: Optional[str]


# Tuần 4: Schemas cho POST /text-to-speech
class TextToSpeechRequest(BaseModel):
    """Body cho POST /text-to-speech."""

    text: str
    lang: str
    voice: Optional[str] = None

    @field_validator("lang")
    @classmethod
    def _validate_lang(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ALLOWED_LANGS:
            raise ValueError(
                f"Ma ngon ngu '{v}' khong duoc ho tro. "
                f"Chi chap nhan: {sorted(ALLOWED_LANGS)}"
            )
        return v

    @field_validator("text")
    @classmethod
    def _validate_text(cls, v: str) -> str:
        norm = tts.normalize_tts_text(v)
        if not norm:
            raise ValueError("text khong duoc de trong")
        if len(norm) > MAX_CHARS:
            raise ValueError(
                f"text khong duoc vuot qua {MAX_CHARS} ky tu (nhan {len(norm)} ky tu)"
            )
        return norm


class TextToSpeechResponse(BaseModel):
    """Response cho POST /text-to-speech."""

    lang: str
    voice: str
    text_hash: str
    audio_filename: str
    audio_url: str
    cache_hit: bool


# Legacy (Tuan 2) — giu lai de tuong thich, KHONG dung trong core flow.
class TranslateRequest(BaseModel):
    """[LEGACY - Tuan 2] Du lieu gui len de yeu cau dich."""

    text: str
    target_lang: str


class TranslateResponse(BaseModel):
    """[LEGACY - Tuan 2] Ket qua dich tra ve."""

    translated_text: str


# ---------------------------------------------------------------------------
# Tuần 5: Schemas cho POST /narrate
# ---------------------------------------------------------------------------

class NarrateRequest(BaseModel):
    """Body cho POST /narrate (Tuần 5)."""

    location_id: int
    source_title: str
    source_lang: str
    target_lang: str
    source_text: str          # description_source của Content (text gốc)
    source_page_id: str       # pageid dạng chuỗi số
    source_url: Optional[str] = None

    @field_validator("source_lang", "target_lang")
    @classmethod
    def _validate_lang(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ALLOWED_LANGS:
            raise ValueError(
                f"Ma ngon ngu '{v}' khong duoc ho tro. "
                f"Chi chap nhan: {sorted(ALLOWED_LANGS)}"
            )
        return v

    @field_validator("source_page_id")
    @classmethod
    def _validate_page_id(cls, v: str) -> str:
        v = v.strip()
        if not v.lstrip("-").isdigit():
            raise ValueError("source_page_id phai la chuoi so nguyen")
        return v

    @field_validator("source_title")
    @classmethod
    def _not_blank_title(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("source_title khong duoc de trong")
        return v.strip()

    @field_validator("source_text")
    @classmethod
    def _not_blank_text(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("source_text khong duoc de trong")
        return v.strip()

    @field_validator("location_id")
    @classmethod
    def _positive_location_id(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("location_id phai la so nguyen duong")
        return v


# ---------------------------------------------------------------------------
# Utils xu ly text (cho Wikimedia)
# ---------------------------------------------------------------------------

_MULTI_SPACE = re.compile(r"[ \t]+")
_MULTI_NEWLINE = re.compile(r"\n{3,}")


def _normalize_whitespace(text: str) -> str:
    """Chuan hoa whitespace: thu gon khoang trang nhieu, giu xuong dong don."""
    text = _MULTI_SPACE.sub(" ", text)
    text = _MULTI_NEWLINE.sub("\n\n", text)
    return text.strip()


def _truncate_at_sentence(text: str, max_chars: int) -> str:
    """Cat text tai ranh gioi cau gan nhat neu vuot qua max_chars.

    Tim dau cham (. ! ?) gan nhat truoc hoac tai vi tri max_chars.
    Neu khong co dau cham nao, cat cung.
    """
    if len(text) <= max_chars:
        return text
    # Tim dau ket cau trong phan [max_chars-200 : max_chars]
    window_start = max(0, max_chars - 200)
    window = text[window_start:max_chars]
    # Tim vi tri dau cham cuoi cung trong window
    match = None
    for m in re.finditer(r"[.!?][\"')}\s]|[.!?]$", window):
        match = m
    if match:
        cut_pos = window_start + match.end()
        return text[:cut_pos].rstrip()
    # Khong co dau cham -> cat cung tai max_chars
    return text[:max_chars].rstrip()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health():
    """Endpoint kiem tra tinh trang service."""
    return {"service": "translate-audio-service", "status": "ok"}


@app.post("/localized-content")
def localized_content(req: LocalizedContentRequest):
    """
    Lay noi dung ban dia hoa tu Wikipedia (khong dich may).

    Logic:
    - Neu target_lang == source_lang: lay gioi thieu bai nguon.
    - Neu target_lang != source_lang:
        1. Query langlinks tu wiki nguon de tim bai cung chu de o wiki dich.
        2. Lay intro extract tu wiki dich bang title chuan hoa do API tra ve.
    - Khong doan title, khong dich may. Chi dung du lieu API Wikipedia chinh thuc.

    Response thanh cong (found=true):
      {found, requested_lang, target_lang, lang, target_title, text, source_url,
       is_fallback: false, reason: null}

    Response khong co du lieu (found=false, HTTP 200):
      {found: false, ..., lang: null, target_title: null, text: null,
       source_url: null, is_fallback: false,
       reason: "target_language_unavailable" | "empty_extract"}

    Loi mang (HTTP 503):
      {detail: {code: "timeout"|"rate_limited"|"upstream_error", message: "..."}}
    """
    source_lang = req.source_lang
    target_lang = req.target_lang
    page_id = req.source_page_id

    _not_found = dict(
        found=False,
        requested_lang=target_lang,
        target_lang=target_lang,
        lang=None,
        target_title=None,
        text=None,
        source_url=None,
        is_fallback=False,
    )

    try:
        with httpx.Client(timeout=wikimedia_client.REQUEST_TIMEOUT) as client:

            # ---- Truong hop cung ngon ngu: lay extract tu wiki nguon ----
            if target_lang == source_lang:
                page_data = wikimedia_client.get_extract_by_pageid(
                    client, source_lang, page_id
                )
                if not page_data:
                    return JSONResponse(
                        status_code=200,
                        content={**_not_found, "reason": "target_language_unavailable"},
                    )
                extract = _normalize_whitespace(page_data.get("extract", ""))
                if not extract:
                    return JSONResponse(
                        status_code=200,
                        content={**_not_found, "reason": "empty_extract"},
                    )
                text = _truncate_at_sentence(extract, MAX_CHARS)
                return JSONResponse(
                    status_code=200,
                    content={
                        "found": True,
                        "requested_lang": target_lang,
                        "target_lang": target_lang,
                        "lang": source_lang,  # ngon ngu that = nguon
                        "target_title": page_data["title"],
                        "text": text,
                        "source_url": page_data["url"],
                        "is_fallback": False,
                        "reason": None,
                    },
                )

            # ---- Truong hop khac ngon ngu: tim langlink -> extract ----

            # Buoc 1: Lay title bai o wiki dich qua langlinks
            target_title = wikimedia_client.get_langlink(
                client, source_lang, page_id, target_lang
            )
            if not target_title:
                return JSONResponse(
                    status_code=200,
                    content={**_not_found, "reason": "target_language_unavailable"},
                )

            # Buoc 2: Lay extract bai o wiki dich (theo title chuan hoa, theo redirect)
            page_data = wikimedia_client.get_extract_by_title(
                client, target_lang, target_title
            )
            if not page_data:
                return JSONResponse(
                    status_code=200,
                    content={**_not_found, "reason": "target_language_unavailable"},
                )

            extract = _normalize_whitespace(page_data.get("extract", ""))
            if not extract:
                return JSONResponse(
                    status_code=200,
                    content={**_not_found, "reason": "empty_extract"},
                )

            text = _truncate_at_sentence(extract, MAX_CHARS)
            return JSONResponse(
                status_code=200,
                content={
                    "found": True,
                    "requested_lang": target_lang,
                    "target_lang": target_lang,
                    "lang": target_lang,  # ngon ngu that = dich (khong gan nhan sai)
                    "target_title": page_data["title"],
                    "text": text,
                    "source_url": page_data["url"],
                    "is_fallback": False,
                    "reason": None,
                },
            )

    except WikimediaError as exc:
        # Loi mang / upstream -> 503; KHONG gia vo khong co ngu
        return JSONResponse(
            status_code=503,
            content={"detail": {"code": exc.code, "message": exc.message}},
        )


# ---------------------------------------------------------------------------
# Tuần 5 — POST /narrate: Helpers giao tiếp với content-service (A)
# ---------------------------------------------------------------------------


@app.post("/narrate", deprecated=True)
async def narrate(req: NarrateRequest):
    """Tương thích client cũ: chuyển điều phối về Content Service."""
    try:
        async with httpx.AsyncClient(timeout=CONTENT_SERVICE_TIMEOUT) as client:
            response = await client.post(f"{CONTENT_SERVICE_URL}/narrate", json={
                "location_id": req.location_id, "target_lang": req.target_lang})
        return JSONResponse(status_code=response.status_code, content=response.json())
    except (httpx.RequestError, ValueError):
        raise HTTPException(503, "content_service_unavailable")


_tts_slots = asyncio.Semaphore(max(1, int(os.getenv("TTS_MAX_CONCURRENT", "2"))))


@app.post(
    "/text-to-speech",
    response_model=TextToSpeechResponse,
    summary="Chuyển văn bản thành giọng nói (Tuần 4 — Edge TTS)",
    description=(
        "Chuyển văn bản đã đúng ngôn ngữ thành file âm thanh MP3. "
        "Sử dụng Edge online TTS và cache bền vững trên đĩa theo hash nội dung. "
        "Khóa đồng thời theo key để tránh tạo trùng lặp."
    ),
)
async def text_to_speech(req: TextToSpeechRequest):
    """
    Tiếp nhận {text, lang} và trả về thông tin file âm thanh MP3.
    - lang: ngôn ngữ thật của text ('vi', 'en', 'ja').
    - text: chuỗi tối đa 1200 ký tự. Không cho phép người dùng tự đặt tên file.
    - Lỗi TTS sẽ trả về HTTP 503 với detail.code='tts_unavailable', không làm sập server.
    """
    try:
        await asyncio.wait_for(_tts_slots.acquire(), timeout=2)
        try:
            options = {"voice": req.voice} if req.voice else {}
            result = await tts.synthesize_speech(text=req.text, lang=req.lang, **options)
        finally:
            _tts_slots.release()
        return JSONResponse(status_code=200, content=result)
    except tts.UnsupportedLanguageError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    except (tts.TTSError, tts.TTSTimeoutError) as exc:
        return JSONResponse(
            status_code=503,
            content={
                "detail": {
                    "code": "tts_unavailable",
                    "message": str(exc),
                }
            },
        )
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={
                "detail": {
                    "code": "tts_unavailable",
                    "message": f"Dịch vụ TTS gặp sự cố nội bộ: {str(exc)}",
                }
            },
        )


# ---------------------------------------------------------------------------
# Tuần 4 — GET /audio/{filename}/status (Kiểm tra trạng thái file cache)
# ĐĂNG KÝ ROUTE NÀY TRƯỚC KHI MOUNT /audio StaticFiles!
# ---------------------------------------------------------------------------


@app.get(
    "/audio/{filename}/status",
    summary="Kiểm tra trạng thái tồn tại của file audio trong cache",
    description=(
        "Kiểm tra xem file audio có tồn tại trên đĩa và có dung lượng hợp lệ (> 0 bytes) hay không. "
        "Validate filename để ngăn chặn path traversal. Route này được đăng ký trước StaticFiles /audio."
    ),
)
def audio_status(filename: str):
    """
    Endpoint kiểm tra file cache cho Tuần 5.
    Trả về {"exists": true|false}.
    Nếu filename vi phạm định dạng hoặc chứa path traversal -> HTTP 422.
    """
    # 1. Validate định dạng tên file và ngăn chặn path traversal cơ bản
    if not tts.validate_audio_filename(filename):
        raise HTTPException(
            status_code=422,
            detail="Tên file audio không hợp lệ hoặc chứa đường dẫn không an toàn",
        )

    # 2. Ngăn chặn path traversal ở mức đường dẫn hệ thống tệp
    try:
        target_dir = tts.ensure_audio_dir()
        safe_path = (target_dir / filename).resolve()
        if safe_path.parent != target_dir.resolve():
            raise HTTPException(
                status_code=422,
                detail="Path traversal detected",
            )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=422,
            detail="Tên file không hợp lệ",
        )

    # 3. Kiểm tra file vật lý và dung lượng thực tế
    exists = safe_path.is_file() and safe_path.stat().st_size > 0
    return {"exists": bool(exists)}


# ---------------------------------------------------------------------------
# Tuần 4 — Mount StaticFiles cho /audio
# ---------------------------------------------------------------------------


class DynamicAudioStaticFiles(StaticFiles):
    """
    StaticFiles phục vụ các file âm thanh từ thư mục tts.AUDIO_DIR.
    Tự động cập nhật thư mục tra cứu khi chạy trong môi trường test (tmp_path).
    """

    def lookup_path(self, path: str):
        current_dir = str(tts.ensure_audio_dir())
        self.directory = current_dir
        self.all_directories = [current_dir]
        return super().lookup_path(path)


# ĐĂNG KÝ ROUTE STATUS TRƯỚC MOUNT /audio (yêu cầu Tuần 4)
app.mount(
    "/audio",
    DynamicAudioStaticFiles(directory=str(tts.ensure_audio_dir())),
    name="audio",
)


# ---------------------------------------------------------------------------
# LEGACY — POST /translate (Tuan 2, giu lai de tuong thich)
# ---------------------------------------------------------------------------


@app.post(
    "/translate",
    response_model=TranslateResponse,
    summary="[LEGACY - Tuan 2] Gia lap dich van ban",
    description=(
        "Endpoint giu lai de tuong thich voi code va test tuan 2. "
        "KHONG dung trong core flow tuan 3 tro di. "
        "Tra ve ket qua GIA LAP (khong co engine dich that). "
        "Khong nang cap bang GoogleTranslator/deep-translator."
    ),
    tags=["legacy"],
)
def translate(request: TranslateRequest):
    """
    [LEGACY - Tuan 2] Nhan van ban va ngon ngu dich, tra ve ban dich GIA LAP.

    Giu nguyen de khong lam hong test/code cu. KHONG duoc nang cap bang
    deep-translator hay GoogleTranslator. Dung /localized-content cho core flow.
    """
    mock_translated = f"[{request.target_lang.upper()}] {request.text}"
    return TranslateResponse(translated_text=mock_translated)
