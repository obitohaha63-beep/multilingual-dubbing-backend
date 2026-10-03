"""
Client gọi Wikimedia API để:
  1. Tìm langlink (bài cùng chủ đề ở wiki ngôn ngữ khác) qua
     action=query&prop=langlinks với lllang=<target_lang>.
  2. Lấy đoạn giới thiệu (intro extract) của bài đó ở wiki đích qua
     prop=extracts|info với exintro=1&explaintext=1&inprop=url.

Thiết kế:
  - Endpoint API khác nhau theo ngôn ngữ: https://<lang>.wikipedia.org/w/api.php
  - Hostname KHÔNG được ghép từ dữ liệu tùy ý — chỉ từ allowlist ALLOWED_LANGS.
  - User-Agent lấy từ biến môi trường WIKIMEDIA_USER_AGENT.
  - Timeout hữu hạn, request tuần tự (không async pool).
  - Retry tối đa 1 lần khi gặp 429 (Retry-After) hoặc 5xx.
  - Lỗi mạng / upstream -> WikimediaError, caller chuyển thành HTTP 503.
"""

from __future__ import annotations

import os
import time
from typing import Optional

import httpx

# ---------------------------------------------------------------------------
# Allowlist ngôn ngữ được phép — KHÔNG thêm lang code tùy ý từ input user.
# ---------------------------------------------------------------------------
ALLOWED_LANGS = {"vi", "en", "ja"}


class WikimediaError(Exception):
    """Lỗi khi gọi Wikimedia.
    code: 'timeout' | 'rate_limited' | 'upstream_error'
    """

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


# ---------------------------------------------------------------------------
# Helpers nội bộ
# ---------------------------------------------------------------------------

REQUEST_TIMEOUT = httpx.Timeout(8.0, connect=4.0)
MAX_RETRY = 1  # tối đa 1 lần retry, giống content-service


def _user_agent() -> str:
    ua = os.environ.get("WIKIMEDIA_USER_AGENT")
    if ua:
        return ua
    print(
        "[wikimedia_client] CANH BAO: thieu WIKIMEDIA_USER_AGENT trong .env, "
        "dang dung gia tri mac dinh tam thoi."
    )
    return "translate-audio-service-do-an-cnpm/1.0"


def _api_url(lang: str) -> str:
    """Xay URL API cho wiki theo ngon ngu.

    Chi dung lang code tu ALLOWED_LANGS — da duoc validate o tang tren.
    Khong bao gio ghep hostname tu du lieu nguoi dung cung cap.
    """
    assert lang in ALLOWED_LANGS, f"Lang '{lang}' khong nam trong allowlist"
    return f"https://{lang}.wikipedia.org/w/api.php"


def _request_with_retry(
    client: httpx.Client, url: str, params: dict
) -> dict:
    """Goi Wikimedia API, retry toi da 1 lan khi gap 429/5xx."""
    attempts = 0
    while True:
        attempts += 1
        try:
            resp = client.get(
                url,
                params=params,
                headers={"User-Agent": _user_agent()},
            )
        except httpx.TimeoutException as exc:
            raise WikimediaError(
                "timeout", "Wikimedia API khong phan hoi kip thoi"
            ) from exc
        except httpx.RequestError as exc:
            raise WikimediaError(
                "upstream_error", f"Loi ket noi Wikimedia: {exc}"
            ) from exc

        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            wait_s = (
                float(retry_after)
                if retry_after and retry_after.replace(".", "", 1).isdigit()
                else 1.0
            )
            wait_s = min(wait_s, 3.0)
            if attempts <= MAX_RETRY:
                time.sleep(wait_s)
                continue
            raise WikimediaError(
                "rate_limited",
                "Wikimedia tra ve 429 (rate limited) qua so lan retry cho phep",
            )

        if resp.status_code >= 500 and attempts <= MAX_RETRY:
            time.sleep(0.5)
            continue

        if resp.status_code >= 400:
            raise WikimediaError(
                "upstream_error",
                f"Wikimedia tra ve loi HTTP {resp.status_code}",
            )

        try:
            return resp.json()
        except ValueError as exc:
            raise WikimediaError(
                "upstream_error",
                "Phan hoi Wikimedia khong phai JSON hop le",
            ) from exc


# ---------------------------------------------------------------------------
# Ham cong khai
# ---------------------------------------------------------------------------


def get_langlink(
    client: httpx.Client,
    source_lang: str,
    source_page_id: str,
    target_lang: str,
) -> Optional[str]:
    """Truy van wiki nguon de lay title bai cung chu de o wiki dich.

    Dung action=query&prop=langlinks&pageids=<id>&lllang=<target>&lllimit=max.
    Tra ve title tim duoc, hoac None neu khong co langlink sang target_lang.

    Khong doan title, khong dich may — chi dung ket qua langlinks cua API.
    """
    url = _api_url(source_lang)
    params = {
        "action": "query",
        "prop": "langlinks",
        "pageids": source_page_id,
        "lllang": target_lang,
        "lllimit": "max",
        "format": "json",
        "formatversion": "2",
    }
    data = _request_with_retry(client, url, params)
    pages = data.get("query", {}).get("pages", [])
    if not pages:
        return None
    # formatversion=2 -> pages la list, khong phai dict
    page = pages[0] if isinstance(pages, list) else next(iter(pages.values()), {})
    langlinks = page.get("langlinks", [])
    for ll in langlinks:
        if ll.get("lang") == target_lang:
            return ll.get("title")
    return None


def get_extract_by_title(
    client: httpx.Client,
    lang: str,
    title: str,
) -> dict:
    """Lay intro extract va URL cua bai theo title o wiki ngon ngu <lang>.

    Dung prop=extracts|info voi exintro=1, explaintext=1, inprop=url,
    redirects=1 de theo redirect. Title chuan hoa va URL do API tra ve.

    Tra ve dict voi cac key:
      title   - tieu de chuan hoa do API tra ve (sau redirect)
      extract - doan gioi thieu plain text (co the rong)
      url     - fullurl hoac canonicalurl
    Hoac {} neu page khong tim duoc.
    """
    url = _api_url(lang)
    params = {
        "action": "query",
        "prop": "extracts|info",
        "titles": title,
        "exintro": 1,
        "explaintext": 1,
        "inprop": "url",
        "redirects": 1,
        "format": "json",
        "formatversion": "2",
    }
    data = _request_with_retry(client, url, params)
    pages = data.get("query", {}).get("pages", [])
    if not pages:
        return {}
    page = pages[0] if isinstance(pages, list) else next(iter(pages.values()), {})
    # pageid=-1 nghia la khong tim thay
    if page.get("pageid", -1) == -1 or page.get("missing"):
        return {}
    return {
        "title": page.get("title", title),
        "extract": page.get("extract", ""),
        "url": page.get("fullurl") or page.get("canonicalurl", ""),
    }


def get_extract_by_pageid(
    client: httpx.Client,
    lang: str,
    page_id: str,
) -> dict:
    """Lay intro extract cua bai theo page_id o wiki ngon ngu <lang>.

    Dung khi source_lang == target_lang (khong can langlinks).
    Tra ve dict voi title, extract, url; hoac {} neu khong tim thay.
    """
    url = _api_url(lang)
    params = {
        "action": "query",
        "prop": "extracts|info",
        "pageids": page_id,
        "exintro": 1,
        "explaintext": 1,
        "inprop": "url",
        "redirects": 1,
        "format": "json",
        "formatversion": "2",
    }
    data = _request_with_retry(client, url, params)
    pages = data.get("query", {}).get("pages", [])
    if not pages:
        return {}
    page = pages[0] if isinstance(pages, list) else next(iter(pages.values()), {})
    if page.get("pageid", -1) == -1 or page.get("missing"):
        return {}
    return {
        "title": page.get("title", ""),
        "extract": page.get("extract", ""),
        "url": page.get("fullurl") or page.get("canonicalurl", ""),
    }
