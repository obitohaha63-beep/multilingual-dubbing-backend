"""
Client gọi Wikimedia (Wikipedia tiếng Việt) để tìm bài viết gần 1 tọa độ
(geosearch) và lấy đoạn giới thiệu (extract) của các bài đó.

Dùng httpx (đồng bộ) với timeout hữu hạn, User-Agent lấy từ biến môi
trường WIKIMEDIA_USER_AGENT (xem .env.example) - Wikimedia yêu cầu mọi
client gọi API phải có User-Agent rõ ràng, nếu không dễ bị họ chặn.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx

API_URL = "https://vi.wikipedia.org/w/api.php"
REQUEST_TIMEOUT = httpx.Timeout(5.0, connect=3.0)
MAX_RETRY = 1  # tối đa 1 lần thử lại, như yêu cầu

# Cache rất đơn giản, lưu trong bộ nhớ (dict) - đủ dùng cho demo/đồ án.
# key: (lat làm tròn ~100m, lon làm tròn ~100m, radius, limit)
# value: (candidates, hết_hạn_lúc_epoch)
_CACHE: dict[tuple, tuple[list[dict], float]] = {}
CACHE_TTL_SECONDS = 60

# Làm tròn tọa độ tới 3 chữ số thập phân ~ sai số 100m ở vĩ độ VN -> dùng
# làm "ô lưới" để gộp các tọa độ gần nhau vào chung 1 cache key.
GRID_DECIMALS = 3


class WikimediaError(Exception):
    """Lỗi khi gọi Wikimedia. code: 'timeout' | 'rate_limited' | 'upstream_error'."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _user_agent() -> str:
    ua = os.environ.get("WIKIMEDIA_USER_AGENT")
    if ua:
        return ua
    # Không tự bịa email liên hệ - chỉ dùng tên ứng dụng chung chung khi
    # thiếu cấu hình, và cảnh báo ra log để người chạy biết cần set .env.
    print(
        "[wikimedia_client] CẢNH BÁO: thiếu WIKIMEDIA_USER_AGENT trong .env, "
        "đang dùng giá trị mặc định tạm thời."
    )
    return "content-service-do-an-cnpm/1.0"


def _cache_key(lat: float, lon: float, radius: float, limit: int) -> tuple:
    return (round(lat, GRID_DECIMALS), round(lon, GRID_DECIMALS), radius, limit)


def _request_with_retry(client: httpx.Client, params: dict) -> dict:
    """Gọi API Wikimedia, retry tối đa 1 lần khi gặp lỗi tạm thời (429/5xx)."""
    attempts = 0
    while True:
        attempts += 1
        try:
            resp = client.get(API_URL, params=params, headers={"User-Agent": _user_agent()})
        except httpx.TimeoutException as exc:
            raise WikimediaError("timeout", "Wikimedia API không phản hồi kịp thời") from exc
        except httpx.RequestError as exc:
            raise WikimediaError("upstream_error", f"Lỗi kết nối Wikimedia: {exc}") from exc

        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            wait_s = float(retry_after) if retry_after and retry_after.isdigit() else 1.0
            # Tôn trọng Retry-After nhưng không chờ vượt quá thời hạn request
            # còn lại (ở đây giới hạn tối đa 3s để không treo request của client).
            wait_s = min(wait_s, 3.0)
            if attempts <= MAX_RETRY:
                time.sleep(wait_s)
                continue
            raise WikimediaError(
                "rate_limited", "Wikimedia trả về 429 (rate limited) quá số lần retry cho phép"
            )

        if resp.status_code >= 500 and attempts <= MAX_RETRY:
            time.sleep(0.5)
            continue

        if resp.status_code >= 400:
            raise WikimediaError(
                "upstream_error", f"Wikimedia trả về lỗi HTTP {resp.status_code}"
            )

        try:
            return resp.json()
        except ValueError as exc:
            raise WikimediaError("upstream_error", "Phản hồi Wikimedia không phải JSON hợp lệ") from exc


def _geosearch(client: httpx.Client, lat: float, lon: float, radius: float, limit: int) -> list[dict]:
    params = {
        "action": "query",
        "list": "geosearch",
        "gscoord": f"{lat}|{lon}",
        "gsradius": int(radius),
        "gsnamespace": 0,
        "gslimit": limit,
        "format": "json",
    }
    data = _request_with_retry(client, params)
    return data.get("query", {}).get("geosearch", [])


def _extracts(client: httpx.Client, pageids: list[int]) -> dict:
    if not pageids:
        return {}
    params = {
        "action": "query",
        "prop": "extracts|info",
        "exintro": 1,
        "explaintext": 1,
        "inprop": "url",
        "pageids": "|".join(str(p) for p in pageids),
        "format": "json",
    }
    data = _request_with_retry(client, params)
    return data.get("query", {}).get("pages", {})


def discover(lat: float, lon: float, radius: float, limit: int) -> tuple[list[dict], bool]:
    """
    Trả về (candidates, cache_hit).
    candidates: list dict {source_page_id, source_title, description_source,
    source_url, latitude, longitude} - GPS lấy từ chính bài viết Wikipedia,
    KHÔNG dùng GPS người dùng truyền vào.
    """
    key = _cache_key(lat, lon, radius, limit)
    cached = _CACHE.get(key)
    now = time.monotonic()
    if cached and cached[1] > now:
        return cached[0], True

    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        geo_results = _geosearch(client, lat, lon, radius, limit)
        pageids = [item["pageid"] for item in geo_results if "pageid" in item]
        pages = _extracts(client, pageids)

    geo_by_pageid = {item["pageid"]: item for item in geo_results}
    synced_at = datetime.now(timezone.utc)

    candidates = []
    for pageid_str, page in pages.items():
        pageid = page.get("pageid")
        extract = page.get("extract")
        geo = geo_by_pageid.get(pageid)
        # Bỏ bài thiếu tọa độ (không có trong geosearch) hoặc thiếu extract.
        if not geo or not extract:
            continue
        candidates.append(
            {
                "source_page_id": pageid,
                "source_title": page.get("title"),
                "description_source": extract,
                "source_url": page.get("fullurl") or page.get("canonicalurl"),
                "latitude": geo.get("lat"),
                "longitude": geo.get("lon"),
                "last_synced_at": synced_at,
            }
        )

    # Cache cả khi rỗng (tránh gọi Wikimedia liên tục nếu khu vực không có bài).
    _CACHE[key] = (candidates, now + CACHE_TTL_SECONDS)
    return candidates, False
