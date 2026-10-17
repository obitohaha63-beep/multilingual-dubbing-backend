"""
translate-audio-service — port 8002.

Tuan 2 (legacy): POST /translate — gia lap, giu lai de tuong thich.
Tuan 3 (moi):    POST /localized-content — lay noi dung ban dia hoa tu Wikipedia.
Tuan 4 (moi):    POST /text-to-speech — chuyen van ban thanh giong noi voi Edge TTS.
                 GET /audio/{filename}/status — kiem tra ton tai cua file audio cache.
                 Mount StaticFiles /audio de phuc vu cac file MP3 da tao.
Tuan 5 (moi):    POST /narrate — tich hop toan bo flow: cache SQLite cua A, ban dia hoa,
                 TTS, fallback, text_ready / audio_ready.

Khong import code xuyen thu muc (khong dung module cua content-service).
C goi /localized-content va /text-to-speech qua HTTP, khong import truc tiep.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
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

# Khóa đồng thời theo (location_id, target_lang) trong một worker
_narrate_locks: Dict[str, asyncio.Lock] = {}
_narrate_manager_lock = asyncio.Lock()

app = FastAPI(
    title="translate-audio-service",
    description=(
        "Service dich thuat, ban dia hoa noi dung va tao am thanh TTS. "
        "POST /narrate: tich hop toan bo flow ban dia hoa + TTS + SQLite cache cua A (Tuan 5). "
        "POST /localized-content: lay ban ban dia hoa chinh thuc tu Wikipedia. "
        "POST /text-to-speech: chuyen van ban thanh giong noi Edge TTS (Tuan 4). "
        "GET /audio/{filename}/status: kiem tra trang thai cache file am thanh (Tuan 4). "
        "POST /translate: giu lai de tuong thich tuan 2 (legacy, khong dung trong core flow)."
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


async def _get_narrate_lock(key: str) -> asyncio.Lock:
    """Lấy hoặc tạo lock theo (location_id, target_lang) trong một worker."""
    async with _narrate_manager_lock:
        if key not in _narrate_locks:
            _narrate_locks[key] = asyncio.Lock()
        return _narrate_locks[key]


async def _cs_get_narration(
    client: httpx.AsyncClient,
    location_id: int,
    target_lang: str,
) -> Optional[Dict[str, Any]]:
    """
    GET /locations/{location_id}/narrations/{target_lang} từ content-service.

    Trả về dict narration nếu cache_hit=True (còn hạn), None nếu miss.
    Ném ValueError nếu service A không trả lời hoặc lỗi không phục hồi được.
    """
    url = f"{CONTENT_SERVICE_URL}/locations/{location_id}/narrations/{target_lang}"
    try:
        resp = await client.get(url)
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        raise ValueError(f"cache_service_timeout: {exc}") from exc

    if resp.status_code == 404:
        # location không tồn tại — trả lỗi rõ ràng
        raise ValueError("location_not_found")
    if resp.status_code != 200:
        raise ValueError(f"cache_service_error: HTTP {resp.status_code}")

    try:
        data = resp.json()
    except Exception as exc:
        raise ValueError(f"cache_service_invalid_response: {exc}") from exc

    if not data.get("cache_hit"):
        return None
    return data.get("narration")


async def _cs_put_narration(
    client: httpx.AsyncClient,
    location_id: int,
    language: str,
    payload: Dict[str, Any],
) -> bool:
    """
    PUT /locations/{location_id}/narrations/{language} lên content-service.

    Trả về True nếu thành công, False nếu thất bại (caller xử lý tiếp).
    """
    url = f"{CONTENT_SERVICE_URL}/locations/{location_id}/narrations/{language}"
    try:
        resp = await client.put(url, json=payload)
        if resp.status_code in (200, 201):
            return True
        logger.warning(
            "PUT narration thất bại: location_id=%s lang=%s status=%s body=%s",
            location_id, language, resp.status_code, resp.text[:200],
        )
        return False
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        logger.warning(
            "PUT narration timeout/lỗi mạng: location_id=%s lang=%s err=%s",
            location_id, language, exc,
        )
        return False


async def _cs_patch_mark_ready(
    client: httpx.AsyncClient,
    location_id: int,
) -> None:
    """PATCH /locations/{location_id}/mark-ready để A tính lại is_ready."""
    url = f"{CONTENT_SERVICE_URL}/locations/{location_id}/mark-ready"
    try:
        resp = await client.patch(url)
        if resp.status_code != 200:
            logger.warning(
                "PATCH mark-ready thất bại: location_id=%s status=%s",
                location_id, resp.status_code,
            )
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        logger.warning(
            "PATCH mark-ready timeout: location_id=%s err=%s", location_id, exc
        )


def _resolve_localized_text(
    source_title: str,
    source_lang: str,
    target_lang: str,
    source_page_id: str,
    source_text: str,
    source_url: Optional[str],
) -> Dict[str, Any]:
    """
    Giải quyết nội dung text cho target_lang bằng cách tái sử dụng logic nghiệp vụ
    của /localized-content (gọi hàm trực tiếp, không HTTP vào chính endpoint).

    Trả về dict:
    {
        "text": str,          # text thực tế dùng cho TTS
        "lang": str,          # ngôn ngữ thực tế của text
        "source_url": str|None,
        "is_fallback": bool,
        "reason": str|None,   # 'target_language_unavailable'|'empty_extract'|
                              # 'localization_unavailable'
    }
    """
    # Nếu same-lang: dùng source_text trực tiếp (không gọi Wikimedia)
    if target_lang == source_lang:
        normalized = _normalize_whitespace(source_text)
        text = _truncate_at_sentence(normalized, MAX_CHARS) if normalized else ""
        return {
            "text": text,
            "lang": source_lang,
            "source_url": source_url,
            "is_fallback": False,
            "reason": None,
        }

    # Khác ngôn ngữ: gọi Wikimedia API (logic giống /localized-content)
    try:
        with httpx.Client(timeout=wikimedia_client.REQUEST_TIMEOUT) as client:
            target_title = wikimedia_client.get_langlink(
                client, source_lang, source_page_id, target_lang
            )
            if not target_title:
                # Không có bài ngôn ngữ đích → fallback source_text
                normalized = _normalize_whitespace(source_text)
                return {
                    "text": _truncate_at_sentence(normalized, MAX_CHARS),
                    "lang": source_lang,
                    "source_url": source_url,
                    "is_fallback": True,
                    "reason": "target_language_unavailable",
                }

            page_data = wikimedia_client.get_extract_by_title(
                client, target_lang, target_title
            )
            if not page_data:
                normalized = _normalize_whitespace(source_text)
                return {
                    "text": _truncate_at_sentence(normalized, MAX_CHARS),
                    "lang": source_lang,
                    "source_url": source_url,
                    "is_fallback": True,
                    "reason": "target_language_unavailable",
                }

            extract = _normalize_whitespace(page_data.get("extract", ""))
            if not extract:
                normalized = _normalize_whitespace(source_text)
                return {
                    "text": _truncate_at_sentence(normalized, MAX_CHARS),
                    "lang": source_lang,
                    "source_url": source_url,
                    "is_fallback": True,
                    "reason": "empty_extract",
                }

            return {
                "text": _truncate_at_sentence(extract, MAX_CHARS),
                "lang": target_lang,
                "source_url": page_data.get("url") or source_url,
                "is_fallback": False,
                "reason": None,
            }

    except WikimediaError:
        # Wikimedia lỗi → fallback source_text, reason='localization_unavailable'
        normalized = _normalize_whitespace(source_text)
        return {
            "text": _truncate_at_sentence(normalized, MAX_CHARS),
            "lang": source_lang,
            "source_url": source_url,
            "is_fallback": True,
            "reason": "localization_unavailable",
        }


# ---------------------------------------------------------------------------
# Tuần 5 — POST /narrate (Core narration flow)
# ---------------------------------------------------------------------------


@app.post(
    "/narrate",
    summary="Tổng hợp thuyết minh địa điểm (Tuần 5 — flow đầy đủ)",
    description=(
        "Flow tích hợp: kiểm tra cache SQLite của A → bản địa hóa Wikimedia → "
        "TTS Edge → PUT text_ready/audio_ready → PATCH mark-ready. "
        "Tái sử dụng logic nghiệp vụ, không tự gọi HTTP vòng lại endpoint của B. "
        "Lock theo (location_id, target_lang) trong phạm vi một worker."
    ),
    tags=["narrate"],
)
async def narrate(req: NarrateRequest):
    """
    POST /narrate — Endpoint thuyết minh tích hợp Tuần 5.

    Input: {location_id, source_title, source_lang, target_lang,
            source_text, source_page_id, source_url}

    Output: {requested_lang, lang, translated_text, audio_url, audio_filename,
             voice, text_hash, source_url, is_fallback, reason,
             narration_status, cache_hit}

    narration_status: 'audio_ready' | 'text_ready'
    lang: luôn là ngôn ngữ thực tế của text.
    """
    location_id = req.location_id
    requested_lang = req.target_lang   # ngôn ngữ người dùng yêu cầu
    source_lang = req.source_lang
    source_url = req.source_url

    lock_key = f"{location_id}:{requested_lang}"
    narrate_lock = await _get_narrate_lock(lock_key)

    timeout = httpx.Timeout(CONTENT_SERVICE_TIMEOUT, connect=3.0)

    async with narrate_lock:
        async with httpx.AsyncClient(timeout=timeout) as cs_client:

            # ----------------------------------------------------------------
            # Bước 1: Kiểm tra cache của A (GET narration)
            # ----------------------------------------------------------------
            try:
                narration = await _cs_get_narration(cs_client, location_id, requested_lang)
            except ValueError as exc:
                err_msg = str(exc)
                if err_msg == "location_not_found":
                    return JSONResponse(
                        status_code=404,
                        content={"detail": {"code": "location_not_found",
                                            "message": "Không tìm thấy địa điểm"}},
                    )
                # Cache service hỏng → trả lỗi rõ ràng, không tạo audio bừa
                logger.error("Cache service lỗi khi GET narration: %s", exc)
                return JSONResponse(
                    status_code=503,
                    content={"detail": {"code": "cache_service_unavailable",
                                        "message": f"Content-service không phản hồi: {exc}"}},
                )

            # ----------------------------------------------------------------
            # Bước 1a: Cache HIT audio_ready — kiểm tra file vật lý
            # ----------------------------------------------------------------
            if narration and narration.get("audio_status") == "audio_ready":
                audio_filename = narration.get("audio_filename")
                cached_lang = narration.get("lang", requested_lang)
                cached_text = narration.get("text", "")
                cached_voice = narration.get("voice")
                cached_hash = narration.get("text_hash")

                # Kiểm tra file MP3 có thực sự tồn tại và đọc được không
                file_exists = False
                if audio_filename and tts.validate_audio_filename(audio_filename):
                    try:
                        audio_dir = tts.ensure_audio_dir()
                        fp = (audio_dir / audio_filename).resolve()
                        if fp.parent == audio_dir.resolve():
                            file_exists = fp.is_file() and fp.stat().st_size > 0
                    except Exception:
                        file_exists = False

                if file_exists:
                    # File OK → trả ngay không gọi Wikimedia/Edge
                    logger.info(
                        "Cache HIT audio_ready (file OK): location_id=%s lang=%s file=%s",
                        location_id, requested_lang, audio_filename,
                    )
                    return JSONResponse(
                        status_code=200,
                        content={
                            "requested_lang": requested_lang,
                            "lang": cached_lang,
                            "translated_text": cached_text,
                            "audio_url": f"/audio/{audio_filename}",
                            "audio_filename": audio_filename,
                            "voice": cached_voice,
                            "text_hash": cached_hash,
                            "source_url": narration.get("source_url") or source_url,
                            "is_fallback": bool(narration.get("is_fallback")),
                            "reason": narration.get("reason"),
                            "narration_status": "audio_ready",
                            "cache_hit": True,
                        },
                    )

                # File mất → tái tạo từ text đã cache
                logger.info(
                    "Cache audio_ready nhưng file mất, tái tạo TTS: location_id=%s lang=%s",
                    location_id, requested_lang,
                )
                if cached_text:
                    try:
                        tts_result = await tts.synthesize_speech(
                            text=cached_text,
                            lang=cached_lang,
                        )
                        # PUT lại audio_ready
                        audio_payload = {
                            "requested_lang": requested_lang,
                            "lang": cached_lang,
                            "text": cached_text,
                            "source_url": narration.get("source_url") or source_url,
                            "is_fallback": bool(narration.get("is_fallback")),
                            "reason": narration.get("reason"),
                            "voice": tts_result["voice"],
                            "text_hash": tts_result["text_hash"],
                            "audio_filename": tts_result["audio_filename"],
                            "audio_status": "audio_ready",
                        }
                        await _cs_put_narration(cs_client, location_id, requested_lang, audio_payload)
                        await _cs_patch_mark_ready(cs_client, location_id)
                        return JSONResponse(
                            status_code=200,
                            content={
                                "requested_lang": requested_lang,
                                "lang": cached_lang,
                                "translated_text": cached_text,
                                "audio_url": f"/audio/{tts_result['audio_filename']}",
                                "audio_filename": tts_result["audio_filename"],
                                "voice": tts_result["voice"],
                                "text_hash": tts_result["text_hash"],
                                "source_url": narration.get("source_url") or source_url,
                                "is_fallback": bool(narration.get("is_fallback")),
                                "reason": narration.get("reason"),
                                "narration_status": "audio_ready",
                                "cache_hit": False,
                            },
                        )
                    except (tts.TTSError, tts.TTSTimeoutError) as exc:
                        logger.warning("TTS thất bại khi tái tạo file mất: %s", exc)
                        return JSONResponse(
                            status_code=200,
                            content={
                                "requested_lang": requested_lang,
                                "lang": cached_lang,
                                "translated_text": cached_text,
                                "audio_url": None,
                                "audio_filename": None,
                                "voice": cached_voice,
                                "text_hash": cached_hash,
                                "source_url": narration.get("source_url") or source_url,
                                "is_fallback": bool(narration.get("is_fallback")),
                                "reason": narration.get("reason"),
                                "narration_status": "text_ready",
                                "cache_hit": True,
                            },
                        )

            # ----------------------------------------------------------------
            # Bước 1b: Cache HIT text_ready — retry TTS (không gọi Wikimedia)
            # ----------------------------------------------------------------
            if narration and narration.get("audio_status") == "text_ready":
                cached_text = narration.get("text", "")
                cached_lang = narration.get("lang", requested_lang)
                logger.info(
                    "Cache text_ready, retry TTS: location_id=%s lang=%s",
                    location_id, requested_lang,
                )
                if cached_text:
                    try:
                        tts_result = await tts.synthesize_speech(
                            text=cached_text,
                            lang=cached_lang,
                        )
                        audio_payload = {
                            "requested_lang": requested_lang,
                            "lang": cached_lang,
                            "text": cached_text,
                            "source_url": narration.get("source_url") or source_url,
                            "is_fallback": bool(narration.get("is_fallback")),
                            "reason": narration.get("reason"),
                            "voice": tts_result["voice"],
                            "text_hash": tts_result["text_hash"],
                            "audio_filename": tts_result["audio_filename"],
                            "audio_status": "audio_ready",
                        }
                        await _cs_put_narration(cs_client, location_id, requested_lang, audio_payload)
                        await _cs_patch_mark_ready(cs_client, location_id)
                        return JSONResponse(
                            status_code=200,
                            content={
                                "requested_lang": requested_lang,
                                "lang": cached_lang,
                                "translated_text": cached_text,
                                "audio_url": f"/audio/{tts_result['audio_filename']}",
                                "audio_filename": tts_result["audio_filename"],
                                "voice": tts_result["voice"],
                                "text_hash": tts_result["text_hash"],
                                "source_url": narration.get("source_url") or source_url,
                                "is_fallback": bool(narration.get("is_fallback")),
                                "reason": narration.get("reason"),
                                "narration_status": "audio_ready",
                                "cache_hit": True,
                            },
                        )
                    except (tts.TTSError, tts.TTSTimeoutError) as exc:
                        # TTS vẫn lỗi → giữ text_ready, trả 200 để client dùng SpeechSynthesis
                        logger.warning("TTS vẫn lỗi sau retry từ text_ready: %s", exc)
                        return JSONResponse(
                            status_code=200,
                            content={
                                "requested_lang": requested_lang,
                                "lang": cached_lang,
                                "translated_text": cached_text,
                                "audio_url": None,
                                "audio_filename": None,
                                "voice": None,
                                "text_hash": narration.get("text_hash"),
                                "source_url": narration.get("source_url") or source_url,
                                "is_fallback": bool(narration.get("is_fallback")),
                                "reason": narration.get("reason"),
                                "narration_status": "text_ready",
                                "cache_hit": True,
                            },
                        )

            # ----------------------------------------------------------------
            # Bước 2: Cache MISS — giải quyết nội dung text
            # ----------------------------------------------------------------
            logger.info(
                "Cache miss, giải quyết nội dung: location_id=%s lang=%s",
                location_id, requested_lang,
            )
            resolved = _resolve_localized_text(
                source_title=req.source_title,
                source_lang=source_lang,
                target_lang=requested_lang,
                source_page_id=req.source_page_id,
                source_text=req.source_text,
                source_url=source_url,
            )

            resolved_text = resolved["text"]
            resolved_lang = resolved["lang"]
            resolved_source_url = resolved["source_url"]
            is_fallback = resolved["is_fallback"]
            reason = resolved["reason"]

            if not resolved_text:
                return JSONResponse(
                    status_code=503,
                    content={
                        "detail": {
                            "code": "no_text_available",
                            "message": "Không có text khả dụng để tạo audio",
                        }
                    },
                )

            # Tính text_hash trước để dùng trong PUT
            normalized_for_hash = tts.normalize_tts_text(resolved_text)
            text_hash_val = tts.compute_text_hash(normalized_for_hash)

            # Bước 2a: PUT text_ready vào SQLite của A TRƯỚC khi chạy TTS
            text_ready_payload = {
                "requested_lang": requested_lang,
                "lang": resolved_lang,
                "text": resolved_text,
                "source_url": resolved_source_url,
                "is_fallback": is_fallback,
                "reason": reason,
                "text_hash": text_hash_val,
                "audio_status": "text_ready",
                # text_ready không được có audio_filename (rule của A)
            }
            upsert_ok = await _cs_put_narration(
                cs_client, location_id, requested_lang, text_ready_payload
            )
            if not upsert_ok:
                # Không lưu được cache text → trả 503 thay vì tạo audio vô tội vạ
                return JSONResponse(
                    status_code=503,
                    content={
                        "detail": {
                            "code": "cache_upsert_failed",
                            "message": "Không thể lưu text vào cache, bỏ qua TTS",
                        }
                    },
                )

            # ----------------------------------------------------------------
            # Bước 3: Tổng hợp TTS (tái sử dụng tts.synthesize_speech)
            # ----------------------------------------------------------------
            try:
                tts_result = await tts.synthesize_speech(
                    text=resolved_text,
                    lang=resolved_lang,
                )
            except (tts.TTSError, tts.TTSTimeoutError) as exc:
                # TTS thất bại nhưng text đã được lưu → trả text_ready
                logger.warning(
                    "TTS thất bại sau khi lưu text_ready: location_id=%s err=%s",
                    location_id, exc,
                )
                return JSONResponse(
                    status_code=200,
                    content={
                        "requested_lang": requested_lang,
                        "lang": resolved_lang,
                        "translated_text": resolved_text,
                        "audio_url": None,
                        "audio_filename": None,
                        "voice": None,
                        "text_hash": text_hash_val,
                        "source_url": resolved_source_url,
                        "is_fallback": is_fallback,
                        "reason": reason,
                        "narration_status": "text_ready",
                        "cache_hit": False,
                    },
                )
            except Exception as exc:
                logger.error("TTS lỗi không mong đợi: %s", exc)
                return JSONResponse(
                    status_code=200,
                    content={
                        "requested_lang": requested_lang,
                        "lang": resolved_lang,
                        "translated_text": resolved_text,
                        "audio_url": None,
                        "audio_filename": None,
                        "voice": None,
                        "text_hash": text_hash_val,
                        "source_url": resolved_source_url,
                        "is_fallback": is_fallback,
                        "reason": reason,
                        "narration_status": "text_ready",
                        "cache_hit": False,
                    },
                )

            # TTS thành công → PUT audio_ready
            audio_ready_payload = {
                "requested_lang": requested_lang,
                "lang": resolved_lang,
                "text": resolved_text,
                "source_url": resolved_source_url,
                "is_fallback": is_fallback,
                "reason": reason,
                "voice": tts_result["voice"],
                "text_hash": tts_result["text_hash"],
                "audio_filename": tts_result["audio_filename"],
                "audio_status": "audio_ready",
            }
            await _cs_put_narration(cs_client, location_id, requested_lang, audio_ready_payload)
            await _cs_patch_mark_ready(cs_client, location_id)

            return JSONResponse(
                status_code=200,
                content={
                    "requested_lang": requested_lang,
                    "lang": resolved_lang,
                    "translated_text": resolved_text,
                    "audio_url": f"/audio/{tts_result['audio_filename']}",
                    "audio_filename": tts_result["audio_filename"],
                    "voice": tts_result["voice"],
                    "text_hash": tts_result["text_hash"],
                    "source_url": resolved_source_url,
                    "is_fallback": is_fallback,
                    "reason": reason,
                    "narration_status": "audio_ready",
                    "cache_hit": False,
                },
            )


# ---------------------------------------------------------------------------
# Tuần 4 — POST /text-to-speech (Edge TTS)
# ---------------------------------------------------------------------------


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
        result = await tts.synthesize_speech(text=req.text, lang=req.lang)
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
