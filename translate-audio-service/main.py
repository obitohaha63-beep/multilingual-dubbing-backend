"""
translate-audio-service — port 8002.

Tuan 2 (legacy): POST /translate — gia lap, giu lai de tuong thich.
Tuan 3 (moi):    POST /localized-content — lay noi dung ban dia hoa tu Wikipedia.

Khong import code xuyen thu muc (khong dung module cua content-service).
C goi /localized-content qua HTTP, khong import truc tiep.
"""

from __future__ import annotations

import re
from typing import Optional

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

import wikimedia_client
from wikimedia_client import ALLOWED_LANGS, WikimediaError

app = FastAPI(
    title="translate-audio-service",
    description=(
        "Service dich thuat & ban dia hoa noi dung Wikipedia. "
        "POST /localized-content: lay ban ban dia hoa chinh thuc tu Wikipedia. "
        "POST /translate: giu lai de tuong thich tuan 2 (legacy, khong dung trong core flow)."
    ),
)

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

MAX_CHARS = 1200  # do dai toi da tra ve


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


# Legacy (Tuan 2) — giu lai de tuong thich, KHONG dung trong core flow.
class TranslateRequest(BaseModel):
    """[LEGACY - Tuan 2] Du lieu gui len de yeu cau dich."""

    text: str
    target_lang: str


class TranslateResponse(BaseModel):
    """[LEGACY - Tuan 2] Ket qua dich tra ve."""

    translated_text: str


# ---------------------------------------------------------------------------
# Utils xu ly text
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
