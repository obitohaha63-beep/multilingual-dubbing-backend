"""
Unit tests cho translate-audio-service — Tuan 3.

Chay bang lenh (tu thu muc translate-audio-service/):
    pytest tests/test_translate_audio.py -v

Khong goi Wikimedia that trong unit test: dung unittest.mock.patch de
mock wikimedia_client.get_langlink, get_extract_by_title,
get_extract_by_pageid.

Test coverage:
  - GET /health
  - POST /translate (legacy tuan 2, giu nguyen)
  - POST /localized-content:
      * nguon vi -> en (co langlink)
      * nguon vi -> ja (co langlink)
      * cung ngon ngu (vi -> vi)
      * thieu langlink -> found=false, reason=target_language_unavailable
      * extract rong -> found=false, reason=empty_extract
      * redirect / tieu de Unicode
      * ngon ngu khong duoc ho tro -> 422
      * 429 (rate_limited) -> 503
      * timeout -> 503
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from main import app, MAX_CHARS

client = TestClient(app)


# ===========================================================================
# Fixtures / helpers
# ===========================================================================

VALID_PAYLOAD = {
    "source_title": "Ha Noi",
    "source_lang": "vi",
    "target_lang": "en",
    "source_page_id": "12345",
}


def _mock_langlink(title: str):
    """Tra ve mock ham get_langlink -> title."""
    return MagicMock(return_value=title)


def _mock_extract(title: str, extract: str, url: str = "https://en.wikipedia.org/wiki/Hanoi"):
    """Tra ve mock ham get_extract_by_title hoac get_extract_by_pageid."""
    return MagicMock(return_value={"title": title, "extract": extract, "url": url})


# ===========================================================================
# GET /health
# ===========================================================================


def test_health_returns_ok():
    """GET /health phai tra ve status ok va dung ten service."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "translate-audio-service"


# ===========================================================================
# POST /translate (Legacy tuan 2 — giu nguyen, khong sua)
# ===========================================================================


def test_translate_returns_200():
    resp = client.post("/translate", json={"text": "Chao buoi sang", "target_lang": "en"})
    assert resp.status_code == 200


def test_translate_response_has_translated_text_field():
    resp = client.post("/translate", json={"text": "Xin chao", "target_lang": "ja"})
    assert "translated_text" in resp.json()


def test_translate_mock_contains_original_text():
    original = "Ho Hoan Kiem la ho dep nhat Ha Noi"
    resp = client.post("/translate", json={"text": original, "target_lang": "en"})
    assert original in resp.json()["translated_text"]


def test_translate_mock_contains_uppercased_lang_code():
    resp = client.post("/translate", json={"text": "Xin chao", "target_lang": "ko"})
    assert "KO" in resp.json()["translated_text"]


def test_translate_missing_text_returns_422():
    resp = client.post("/translate", json={"target_lang": "en"})
    assert resp.status_code == 422


def test_translate_missing_target_lang_returns_422():
    resp = client.post("/translate", json={"text": "Xin chao"})
    assert resp.status_code == 422


def test_translate_empty_body_returns_422():
    resp = client.post("/translate", json={})
    assert resp.status_code == 422


def test_translate_multiple_languages():
    """Mo phong cach content-service goi translate lien tiep."""
    text = "Cho Ben Thanh la bieu tuong cua thanh pho Ho Chi Minh"
    for lang in ["en", "ja"]:
        resp = client.post("/translate", json={"text": text, "target_lang": lang})
        assert resp.status_code == 200
        assert "translated_text" in resp.json()


# ===========================================================================
# POST /localized-content — validate dau vao
# ===========================================================================


def test_localized_content_unsupported_source_lang_returns_422():
    """source_lang khong trong allowlist -> 422."""
    payload = {**VALID_PAYLOAD, "source_lang": "zh"}
    resp = client.post("/localized-content", json=payload)
    assert resp.status_code == 422


def test_localized_content_unsupported_target_lang_returns_422():
    """target_lang khong trong allowlist -> 422."""
    payload = {**VALID_PAYLOAD, "target_lang": "ko"}
    resp = client.post("/localized-content", json=payload)
    assert resp.status_code == 422


def test_localized_content_invalid_page_id_returns_422():
    """source_page_id khong phai so -> 422."""
    payload = {**VALID_PAYLOAD, "source_page_id": "abc"}
    resp = client.post("/localized-content", json=payload)
    assert resp.status_code == 422


def test_localized_content_blank_title_returns_422():
    """source_title rong -> 422."""
    payload = {**VALID_PAYLOAD, "source_title": "   "}
    resp = client.post("/localized-content", json=payload)
    assert resp.status_code == 422


def test_localized_content_missing_fields_returns_422():
    """Thieu truong bat buoc -> 422."""
    resp = client.post("/localized-content", json={"source_title": "Ha Noi"})
    assert resp.status_code == 422


# ===========================================================================
# POST /localized-content — vi -> en (co langlink, co extract)
# ===========================================================================


@patch("wikimedia_client.get_extract_by_title")
@patch("wikimedia_client.get_langlink")
def test_localized_vi_to_en_success(mock_langlink, mock_extract):
    """vi -> en: co langlink va extract -> found=true, lang='en'."""
    mock_langlink.return_value = "Hanoi"
    mock_extract.return_value = {
        "title": "Hanoi",
        "extract": "Hanoi is the capital of Vietnam. It has many lakes.",
        "url": "https://en.wikipedia.org/wiki/Hanoi",
    }

    resp = client.post("/localized-content", json=VALID_PAYLOAD)
    assert resp.status_code == 200
    data = resp.json()

    assert data["found"] is True
    assert data["lang"] == "en"
    assert data["requested_lang"] == "en"
    assert data["target_lang"] == "en"
    assert data["target_title"] == "Hanoi"
    assert "Hanoi" in data["text"]
    assert data["source_url"] == "https://en.wikipedia.org/wiki/Hanoi"
    assert data["is_fallback"] is False
    assert data["reason"] is None

    # Kiem tra mock duoc goi dung tham so
    mock_langlink.assert_called_once()
    mock_extract.assert_called_once()


# ===========================================================================
# POST /localized-content — vi -> ja
# ===========================================================================


@patch("wikimedia_client.get_extract_by_title")
@patch("wikimedia_client.get_langlink")
def test_localized_vi_to_ja_success(mock_langlink, mock_extract):
    """vi -> ja: co langlink va extract -> found=true, lang='ja'."""
    mock_langlink.return_value = "ハノイ"
    mock_extract.return_value = {
        "title": "ハノイ",
        "extract": "ハノイはベトナムの首都である。",
        "url": "https://ja.wikipedia.org/wiki/%E3%83%8F%E3%83%8E%E3%82%A4",
    }

    payload = {**VALID_PAYLOAD, "target_lang": "ja"}
    resp = client.post("/localized-content", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["found"] is True
    assert data["lang"] == "ja"
    assert data["target_title"] == "ハノイ"
    assert "ハノイ" in data["text"]


# ===========================================================================
# POST /localized-content — cung ngon ngu (vi -> vi)
# ===========================================================================


@patch("wikimedia_client.get_extract_by_pageid")
def test_localized_same_lang_vi(mock_pageid):
    """vi -> vi: lay extract tu wiki nguon, lang='vi'."""
    mock_pageid.return_value = {
        "title": "Ha Noi",
        "extract": "Ha Noi la thu do nuoc Viet Nam.",
        "url": "https://vi.wikipedia.org/wiki/H%C3%A0_N%E1%BB%99i",
    }

    payload = {**VALID_PAYLOAD, "target_lang": "vi", "source_lang": "vi"}
    resp = client.post("/localized-content", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["found"] is True
    assert data["lang"] == "vi"
    assert data["target_title"] == "Ha Noi"
    mock_pageid.assert_called_once()


# ===========================================================================
# POST /localized-content — thieu langlink
# ===========================================================================


@patch("wikimedia_client.get_langlink")
def test_localized_missing_langlink(mock_langlink):
    """Khong co langlink sang target_lang -> found=false, reason=target_language_unavailable."""
    mock_langlink.return_value = None

    resp = client.post("/localized-content", json=VALID_PAYLOAD)
    assert resp.status_code == 200
    data = resp.json()

    assert data["found"] is False
    assert data["reason"] == "target_language_unavailable"
    assert data["lang"] is None
    assert data["target_title"] is None
    assert data["text"] is None
    assert data["source_url"] is None
    assert data["is_fallback"] is False


# ===========================================================================
# POST /localized-content — extract rong
# ===========================================================================


@patch("wikimedia_client.get_extract_by_title")
@patch("wikimedia_client.get_langlink")
def test_localized_empty_extract(mock_langlink, mock_extract):
    """Co langlink nhung extract rong -> found=false, reason=empty_extract."""
    mock_langlink.return_value = "Hanoi"
    mock_extract.return_value = {
        "title": "Hanoi",
        "extract": "",
        "url": "https://en.wikipedia.org/wiki/Hanoi",
    }

    resp = client.post("/localized-content", json=VALID_PAYLOAD)
    assert resp.status_code == 200
    data = resp.json()

    assert data["found"] is False
    assert data["reason"] == "empty_extract"


# ===========================================================================
# POST /localized-content — whitespace-only extract
# ===========================================================================


@patch("wikimedia_client.get_extract_by_title")
@patch("wikimedia_client.get_langlink")
def test_localized_whitespace_only_extract(mock_langlink, mock_extract):
    """Extract chi co whitespace -> found=false, reason=empty_extract."""
    mock_langlink.return_value = "Hanoi"
    mock_extract.return_value = {
        "title": "Hanoi",
        "extract": "   \n\n   ",
        "url": "https://en.wikipedia.org/wiki/Hanoi",
    }

    resp = client.post("/localized-content", json=VALID_PAYLOAD)
    assert resp.status_code == 200
    assert resp.json()["reason"] == "empty_extract"


# ===========================================================================
# POST /localized-content — redirect / Unicode title
# ===========================================================================


@patch("wikimedia_client.get_extract_by_title")
@patch("wikimedia_client.get_langlink")
def test_localized_unicode_title_redirect(mock_langlink, mock_extract):
    """Title Unicode sau redirect: API tra ve title chuan hoa, service dung nguyen."""
    mock_langlink.return_value = "Ho Chi Minh City"
    # API tra ve title chuan hoa sau redirect (vi du: "Ho Chi Minh City" -> "Ho Chi Minh City")
    mock_extract.return_value = {
        "title": "Ho Chi Minh City",
        "extract": "Ho Chi Minh City, commonly known as Saigon, is the largest city in Vietnam.",
        "url": "https://en.wikipedia.org/wiki/Ho_Chi_Minh_City",
    }

    payload = {**VALID_PAYLOAD, "source_title": "Thanh pho Ho Chi Minh", "source_page_id": "99999"}
    resp = client.post("/localized-content", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["found"] is True
    assert data["target_title"] == "Ho Chi Minh City"
    assert "Saigon" in data["text"]


# ===========================================================================
# POST /localized-content — cat text tai ranh gioi cau
# ===========================================================================


@patch("wikimedia_client.get_extract_by_title")
@patch("wikimedia_client.get_langlink")
def test_localized_text_truncated_at_sentence(mock_langlink, mock_extract):
    """Text dai hon MAX_CHARS phai bi cat tai dau cham cau."""
    # Tao text dai hon 1200 ky tu
    long_text = "This is a sentence. " * 100  # ~2000 ky tu
    mock_langlink.return_value = "SomePage"
    mock_extract.return_value = {
        "title": "SomePage",
        "extract": long_text,
        "url": "https://en.wikipedia.org/wiki/SomePage",
    }

    resp = client.post("/localized-content", json=VALID_PAYLOAD)
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is True
    assert len(data["text"]) <= MAX_CHARS


# ===========================================================================
# POST /localized-content — 429 (rate_limited) -> 503
# ===========================================================================


@patch("wikimedia_client.get_langlink")
def test_localized_429_returns_503(mock_langlink):
    """WikimediaError(rate_limited) -> HTTP 503 voi detail.code."""
    from wikimedia_client import WikimediaError

    mock_langlink.side_effect = WikimediaError(
        "rate_limited", "Wikimedia tra ve 429"
    )

    resp = client.post("/localized-content", json=VALID_PAYLOAD)
    assert resp.status_code == 503
    data = resp.json()
    assert data["detail"]["code"] == "rate_limited"
    assert "message" in data["detail"]


# ===========================================================================
# POST /localized-content — timeout -> 503
# ===========================================================================


@patch("wikimedia_client.get_langlink")
def test_localized_timeout_returns_503(mock_langlink):
    """WikimediaError(timeout) -> HTTP 503."""
    from wikimedia_client import WikimediaError

    mock_langlink.side_effect = WikimediaError(
        "timeout", "Wikimedia API khong phan hoi kip thoi"
    )

    resp = client.post("/localized-content", json=VALID_PAYLOAD)
    assert resp.status_code == 503
    data = resp.json()
    assert data["detail"]["code"] == "timeout"


# ===========================================================================
# POST /localized-content — upstream_error -> 503
# ===========================================================================


@patch("wikimedia_client.get_langlink")
def test_localized_upstream_error_returns_503(mock_langlink):
    """WikimediaError(upstream_error) -> HTTP 503, khong gia vo khong co ngu."""
    from wikimedia_client import WikimediaError

    mock_langlink.side_effect = WikimediaError(
        "upstream_error", "Wikimedia tra ve loi HTTP 500"
    )

    resp = client.post("/localized-content", json=VALID_PAYLOAD)
    assert resp.status_code == 503
    data = resp.json()
    assert data["detail"]["code"] == "upstream_error"


# ===========================================================================
# POST /localized-content — cung ngon ngu, extract rong
# ===========================================================================


@patch("wikimedia_client.get_extract_by_pageid")
def test_localized_same_lang_empty_extract(mock_pageid):
    """Cung ngon ngu nhung extract rong -> found=false, reason=empty_extract."""
    mock_pageid.return_value = {
        "title": "Ha Noi",
        "extract": "",
        "url": "https://vi.wikipedia.org/wiki/Ha_Noi",
    }

    payload = {**VALID_PAYLOAD, "target_lang": "vi", "source_lang": "vi"}
    resp = client.post("/localized-content", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is False
    assert data["reason"] == "empty_extract"


# ===========================================================================
# POST /localized-content — cung ngon ngu, page khong tim thay
# ===========================================================================


@patch("wikimedia_client.get_extract_by_pageid")
def test_localized_same_lang_page_not_found(mock_pageid):
    """Cung ngon ngu nhung API tra {} (page khong ton tai) -> found=false."""
    mock_pageid.return_value = {}

    payload = {**VALID_PAYLOAD, "target_lang": "vi", "source_lang": "vi"}
    resp = client.post("/localized-content", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is False
    assert data["reason"] == "target_language_unavailable"


# ===========================================================================
# POST /localized-content — kiem tra lang dung, khong gan nhan sai
# ===========================================================================


@patch("wikimedia_client.get_extract_by_title")
@patch("wikimedia_client.get_langlink")
def test_localized_lang_field_matches_actual_language(mock_langlink, mock_extract):
    """lang phai la ngon ngu that cua text — khong gan 'en' cho text tieng Viet."""
    mock_langlink.return_value = "ハノイ"
    mock_extract.return_value = {
        "title": "ハノイ",
        "extract": "ハノイはベトナムの首都である。",
        "url": "https://ja.wikipedia.org/wiki/Hanoi",
    }

    payload = {**VALID_PAYLOAD, "target_lang": "ja"}
    resp = client.post("/localized-content", json=payload)
    data = resp.json()

    # lang phai la 'ja', khong phai 'vi' hay 'en'
    assert data["lang"] == "ja"
    assert data["lang"] != "vi"
    assert data["lang"] != "en"


# ===========================================================================
# POST /localized-content — en -> vi
# ===========================================================================


@patch("wikimedia_client.get_extract_by_title")
@patch("wikimedia_client.get_langlink")
def test_localized_en_to_vi_success(mock_langlink, mock_extract):
    """en -> vi: co langlink, extract tieng Viet, lang='vi'."""
    mock_langlink.return_value = "Ha Noi"
    mock_extract.return_value = {
        "title": "Ha Noi",
        "extract": "Ha Noi la thu do nuoc Cong hoa xa hoi chu nghia Viet Nam.",
        "url": "https://vi.wikipedia.org/wiki/Ha_Noi",
    }

    payload = {
        "source_title": "Hanoi",
        "source_lang": "en",
        "target_lang": "vi",
        "source_page_id": "54321",
    }
    resp = client.post("/localized-content", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["found"] is True
    assert data["lang"] == "vi"
    # Khong duoc gan nhan "en" cho text tieng Viet
    assert data["lang"] != "en"
