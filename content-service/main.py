"""
content-service - port 8001.

File NÀY gộp chung mọi logic tuần 3 (trước đây tách ra nhiều file nhỏ:
migrate.py, utils.py, wikimedia_client.py - gộp lại cho dễ đọc/dễ học,
đánh đổi là file dài hơn). Đọc theo đúng 5 khu vực đánh số bên dưới,
từ trên xuống, là hiểu được toàn bộ:

  KHU VỰC 1: MIGRATION      - nâng cấp CSDL cũ (tuần 2) lên schema mới
  KHU VỰC 2: HÀM TIỆN ÍCH   - Haversine (tính khoảng cách) + validate GPS
  KHU VỰC 3: WIKIMEDIA      - gọi API Wikipedia để tìm bài viết gần 1 tọa độ
  KHU VỰC 4: SCHEMA (Pydantic) - hình dạng dữ liệu JSON vào/ra API
  KHU VỰC 5: ROUTES (FastAPI)  - các endpoint thật sự, http://.../...

Tuần 4 thêm: migration v3 (bảng narration_cache) ở khu vực 1, schema
NarrationPut ở khu vực 4, các route /locations/{id}/narrations/{language}
và /locations/{id}/mark-ready ở cuối khu vực 5.

(models.py tách riêng - đó là định nghĩa BẢNG trong CSDL, không đổi).
"""

from __future__ import annotations

import math
import os
import re
import shutil
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import httpx
from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import Base, Location, NarrationCache, SessionLocal, engine, DB_PATH


# ============================================================================
# KHU VỰC 1: MIGRATION
# ----------------------------------------------------------------------------
# Vì sao cần đoạn này? Base.metadata.create_all() (ở dưới cùng file) CHỈ tạo
# bảng khi bảng đó CHƯA TỒN TẠI. Nếu máy bạn đã có sẵn content.db từ tuần 2
# (bảng locations chỉ có 4 cột), create_all() sẽ thấy bảng đã có rồi và
# KHÔNG tự thêm cột mới -> phải tự viết ALTER TABLE bằng tay ở đây.
#
# "Chạy lặp an toàn" nghĩa là: chạy hàm run_migrations() 2-3 lần liên tiếp
# vẫn không lỗi, không ghi đè dữ liệu cũ - nhờ luôn kiểm tra "đã có chưa"
# trước khi thêm.
# ============================================================================

NEW_COLUMNS = [
    ("description_source", "TEXT"),
    ("latitude", "REAL"),
    ("longitude", "REAL"),
    ("radius", "REAL"),
    ("source_lang", "TEXT"),
    ("source", "TEXT"),
    ("source_title", "TEXT"),
    ("source_page_id", "INTEGER"),
    ("source_url", "TEXT"),
    ("last_synced_at", "TEXT"),
    ("is_ready", "INTEGER"),
]
def _migrate_v2(conn: sqlite3.Connection) -> None:
    """Tuần 3: thêm cột GPS + metadata nguồn, backfill dữ liệu cũ, unique index."""
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='locations'"
    ).fetchone()
    if not table_exists:
        # DB hoàn toàn mới: create_all() ở cuối file sẽ tạo đủ cột ngay từ đầu.
        return

    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(locations)")}
    for col_name, col_type in NEW_COLUMNS:
        if col_name not in existing_cols:
            conn.execute(f"ALTER TABLE locations ADD COLUMN {col_name} {col_type}")

    # Backfill CHỈ cho dòng còn thiếu description_source (record từ tuần 2).
    # Không gán tọa độ 0,0: latitude/longitude của record cũ giữ NULL.
    conn.execute(
        """
        UPDATE locations
        SET description_source = description_vi,
            source_lang = COALESCE(source_lang, 'vi'),
            source = COALESCE(source, 'manual'),
            radius = COALESCE(radius, 80),
            is_ready = COALESCE(is_ready, 0)
        WHERE description_source IS NULL
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_locations_source "
        "ON locations (source, source_lang, source_page_id)"
    )


def _migrate_v3(conn: sqlite3.Connection) -> None:
    """Tuần 4: tạo bảng narration_cache (khóa ngoại tới locations)."""
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='locations'"
    ).fetchone()
    if not table_exists:
        return  # DB mới: create_all() sẽ tạo cả locations lẫn narration_cache.

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS narration_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            location_id INTEGER NOT NULL
                REFERENCES locations (id) ON DELETE CASCADE,
            requested_lang VARCHAR NOT NULL,
            lang VARCHAR NOT NULL,
            text TEXT NOT NULL,
            source_url VARCHAR,
            target_title VARCHAR,
            is_fallback BOOLEAN NOT NULL DEFAULT 0,
            reason VARCHAR,
            voice VARCHAR,
            text_hash VARCHAR,
            audio_filename VARCHAR,
            audio_status VARCHAR NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            expires_at DATETIME NOT NULL,
            CONSTRAINT ux_narration_location_lang
                UNIQUE (location_id, requested_lang)
        )
        """
    )


# version -> hàm migration. Thêm version mới = thêm 1 dòng ở đây.
MIGRATIONS = {2: _migrate_v2, 3: _migrate_v3}


def _applied_versions(conn: sqlite3.Connection) -> set:
    has_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if not has_table:
        return set()
    return {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}


def run_migrations(verbose: bool = True) -> list:
    """
    Chạy các migration CHƯA áp dụng, theo thứ tự version. Trả về danh sách
    version vừa chạy. Chạy lặp lại an toàn: version đã chạy thì bỏ qua.
    """
    # Bước 1: xem còn migration nào chờ không. Không có thì thoát luôn
    # (không sao lưu, không đụng DB).
    conn = sqlite3.connect(DB_PATH)
    try:
        pending = [v for v in sorted(MIGRATIONS) if v not in _applied_versions(conn)]
    finally:
        conn.close()
    if not pending:
        if verbose:
            print("[migrate] Schema đã cập nhật, không có gì để làm.")
        return []

    # Bước 2: sao lưu file DB (nếu đã có dữ liệu) TRƯỚC khi đổi schema.
    if os.path.exists(DB_PATH) and os.path.getsize(DB_PATH) > 0:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        backup_path = f"{DB_PATH}.bak-{ts}"
        shutil.copy2(DB_PATH, backup_path)
        if verbose:
            print(f"[migrate] Đã sao lưu DB -> {backup_path}")

    # Bước 3: mỗi version chạy trong 1 transaction riêng (SQLite cho phép
    # DDL nằm trong transaction) -> lỗi giữa chừng thì rollback sạch.
    conn = sqlite3.connect(DB_PATH, isolation_level=None)  # tự quản lý BEGIN/COMMIT
    applied_now = []
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        for version in pending:
            conn.execute("BEGIN")
            try:
                MIGRATIONS[version](conn)
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, datetime.now(timezone.utc).isoformat()),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            applied_now.append(version)
            if verbose:
                print(f"[migrate] Đã áp dụng version {version}.")
    finally:
        conn.close()
    return applied_now


# ============================================================================
# KHU VỰC 2: HÀM TIỆN ÍCH - khoảng cách GPS
# ============================================================================


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Khoảng cách giữa 2 điểm GPS, đơn vị MÉT (coi Trái Đất là hình cầu)."""
    R = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * R * math.asin(math.sqrt(a))


def validate_coords(latitude: Optional[float], longitude: Optional[float]) -> None:
    """Raise ValueError nếu tọa độ không hữu hạn hoặc ngoài khoảng hợp lệ."""
    if latitude is None or longitude is None:
        return
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        raise ValueError("Tọa độ phải là số hữu hạn")
    if not (-90 <= latitude <= 90):
        raise ValueError("latitude phải nằm trong khoảng [-90, 90]")
    if not (-180 <= longitude <= 180):
        raise ValueError("longitude phải nằm trong khoảng [-180, 180]")


# ============================================================================
# KHU VỰC 3: WIKIMEDIA CLIENT - gọi API Wikipedia tiếng Việt
# ============================================================================

WIKI_API_URL = "https://vi.wikipedia.org/w/api.php"
WIKI_TIMEOUT = httpx.Timeout(5.0, connect=3.0)
WIKI_MAX_RETRY = 1
WIKI_CACHE_TTL_SECONDS = 60
WIKI_GRID_DECIMALS = 3  # làm tròn tọa độ ~100m để gộp cache

_wiki_cache: dict[tuple, tuple[list[dict], float]] = {}


WIKI_TOTAL_DEADLINE_S = 12.0  # tổng thời gian tối đa cho 1 lần discover (geosearch + extracts)


class WikimediaError(Exception):
    """
    Lỗi khi gọi Wikimedia. code ỔN ĐỊNH để Gateway dựa vào:
      'timeout' | 'rate_limited' | 'upstream_error'.
    retry_after: số giây Wikimedia yêu cầu chờ (nếu có) - chỉ với rate_limited.
    """

    def __init__(self, code: str, message: str, retry_after: Optional[float] = None):
        self.code = code
        self.message = message
        self.retry_after = retry_after
        super().__init__(message)


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Retry-After dạng số giây -> float (0 < x <= 3600); dạng khác -> None."""
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:  # có thể là HTTP-date, không hỗ trợ
        return None
    return seconds if 0 < seconds <= 3600 else None


def _wiki_user_agent() -> str:
    ua = os.environ.get("WIKIMEDIA_USER_AGENT")
    if ua:
        return ua
    print("[wikimedia] CẢNH BÁO: thiếu WIKIMEDIA_USER_AGENT trong .env.")
    return "content-service-do-an-cnpm/1.0"


def _wiki_request(client: httpx.Client, params: dict, deadline: Optional[float] = None) -> dict:
    """
    Gọi 1 request tới Wikimedia. Tối đa 1 retry (429/5xx), tôn trọng
    Retry-After nhưng không bao giờ ngủ/chờ vượt `deadline` (mốc time.monotonic()).
    Mọi lỗi (kể cả lỗi nằm trong body HTTP 200) đều ném WikimediaError - KHÔNG
    bao giờ trả dữ liệu rỗng giả để rồi bị hiểu nhầm là "không có địa điểm".
    """
    attempts = 0
    while True:
        attempts += 1
        timeout = 5.0
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0.2:
                raise WikimediaError("timeout", "Hết thời hạn xử lý yêu cầu Wikimedia")
            timeout = min(timeout, remaining)
        try:
            resp = client.get(
                WIKI_API_URL, params=params,
                headers={"User-Agent": _wiki_user_agent()}, timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise WikimediaError("timeout", "Wikimedia không phản hồi kịp") from exc
        except httpx.RequestError as exc:
            raise WikimediaError("upstream_error", f"Lỗi kết nối: {exc}") from exc

        if resp.status_code == 429:
            retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
            wait_s = min(retry_after if retry_after else 1.0, 3.0)
            can_wait = deadline is None or time.monotonic() + wait_s < deadline - 0.2
            if attempts <= WIKI_MAX_RETRY and can_wait:
                time.sleep(wait_s)
                continue
            raise WikimediaError(
                "rate_limited", "Wikimedia trả 429 (quá giới hạn yêu cầu)", retry_after=retry_after
            )

        if resp.status_code >= 500 and attempts <= WIKI_MAX_RETRY:
            can_wait = deadline is None or time.monotonic() + 0.5 < deadline - 0.2
            if can_wait:
                time.sleep(0.5)
                continue
        if resp.status_code >= 400:
            raise WikimediaError("upstream_error", f"HTTP {resp.status_code}")

        try:
            data = resp.json()
        except ValueError as exc:
            raise WikimediaError("upstream_error", "Phản hồi không phải JSON") from exc

        # MediaWiki có thể trả HTTP 200 nhưng body là {"error": {...}}.
        if not isinstance(data, dict):
            raise WikimediaError("upstream_error", "Phản hồi Wikimedia sai định dạng")
        if "error" in data:
            code = str(data["error"].get("code", "unknown")) if isinstance(data["error"], dict) else "unknown"
            if code == "ratelimited":
                raise WikimediaError("rate_limited", "Wikimedia báo ratelimited")
            raise WikimediaError("upstream_error", f"Wikimedia báo lỗi API: {code}")
        if "query" not in data:  # geosearch rỗng hợp lệ vẫn có khóa "query"
            raise WikimediaError("upstream_error", "Phản hồi Wikimedia thiếu dữ liệu 'query'")
        return data


def wikimedia_discover(lat: float, lon: float, radius: float, limit: int) -> tuple[list[dict], bool]:
    """
    Trả (candidates, cache_hit).
    1. geosearch: tìm pageid các bài gần tọa độ.
    2. extracts: lấy đoạn giới thiệu + url của các pageid đó.
    Bỏ bài thiếu tọa độ hoặc thiếu đoạn giới thiệu. GPS lấy từ chính bài
    viết (geosearch trả về), KHÔNG dùng GPS người dùng truyền vào.

    Cache 60 giây theo ô tọa độ ~100m + radius + limit, kể cả khi KHÔNG có
    địa điểm nào. Lỗi upstream thì ném WikimediaError và KHÔNG được cache
    (lần sau thử lại bình thường, không bị kẹt trong "không có địa điểm").
    """
    key = (round(lat, WIKI_GRID_DECIMALS), round(lon, WIKI_GRID_DECIMALS), radius, limit)
    now = time.monotonic()
    cached = _wiki_cache.get(key)
    if cached and cached[1] > now:
        return cached[0], True

    deadline = now + WIKI_TOTAL_DEADLINE_S
    with httpx.Client(timeout=WIKI_TIMEOUT) as client:
        geo_data = _wiki_request(
            client,
            {
                "action": "query", "list": "geosearch",
                "gscoord": f"{lat}|{lon}", "gsradius": int(radius),
                "gsnamespace": 0, "gslimit": limit, "format": "json",
            },
            deadline,
        )
        geo_results = geo_data["query"].get("geosearch", [])
        pageids = [item["pageid"] for item in geo_results if "pageid" in item]

        pages = {}
        if pageids:
            ext_data = _wiki_request(
                client,
                {
                    "action": "query", "prop": "extracts|info",
                    "exintro": 1, "explaintext": 1, "inprop": "url",
                    "pageids": "|".join(str(p) for p in pageids), "format": "json",
                },
                deadline,
            )
            pages = ext_data["query"].get("pages", {})

    geo_by_pageid = {item["pageid"]: item for item in geo_results}
    synced_at = datetime.now(timezone.utc)

    candidates = []
    for page in pages.values():
        pageid = page.get("pageid")
        extract = page.get("extract")
        geo = geo_by_pageid.get(pageid)
        if not geo or not extract:  # thiếu tọa độ hoặc thiếu giới thiệu -> bỏ
            continue
        candidates.append({
            "source_page_id": pageid,
            "source_title": page.get("title"),
            "description_source": extract,
            "source_url": page.get("fullurl") or page.get("canonicalurl"),
            "latitude": geo.get("lat"),
            "longitude": geo.get("lon"),
            "last_synced_at": synced_at,
        })

    _wiki_cache[key] = (candidates, now + WIKI_CACHE_TTL_SECONDS)  # cache cả khi rỗng
    return candidates, False


# ============================================================================
# KHU VỰC 4: PYDANTIC SCHEMA - hình dạng JSON vào/ra API
# (khác với Location trong models.py - đó là BẢNG thật trong CSDL)
# ============================================================================


class LocationCreate(BaseModel):
    name: str
    description_vi: str
    target_languages: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    radius: Optional[float] = 80

    @field_validator("name", "description_vi", "target_languages")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Không được để trống")
        return v

    @field_validator("radius")
    @classmethod
    def _radius_positive(cls, v):
        if v is not None and v <= 0:
            raise ValueError("radius phải > 0")
        return v

    @field_validator("longitude")
    @classmethod
    def _coords_valid(cls, v, info):
        lat = info.data.get("latitude")
        if lat is not None or v is not None:
            validate_coords(lat, v)
        return v


class LocationResponse(BaseModel):
    id: int
    name: str
    description_vi: str
    target_languages: str
    description_source: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    radius: Optional[float] = None
    source_lang: Optional[str] = None
    source: Optional[str] = None
    source_title: Optional[str] = None
    source_page_id: Optional[int] = None
    source_url: Optional[str] = None
    last_synced_at: Optional[datetime] = None
    is_ready: Optional[bool] = None

    model_config = ConfigDict(from_attributes=True)


class Candidate(BaseModel):
    """1 địa điểm trả về cho truy vấn GPS (dùng chung cho /nearby và /discover)."""

    id: int
    name: str
    description_source: Optional[str] = None
    latitude: float
    longitude: float
    radius: Optional[float] = None  # bán kính KÍCH HOẠT (m) - Gateway so với distance_m
    distance_m: float  # khoảng cách Haversine từ điểm truy vấn tới địa điểm
    source: Optional[str] = None
    source_lang: Optional[str] = None
    source_title: Optional[str] = None
    source_page_id: Optional[int] = None
    source_url: Optional[str] = None
    last_synced_at: Optional[datetime] = None
    is_ready: Optional[bool] = None  # cờ tổng quát, KHÔNG thay cache theo ngôn ngữ


NearbyCandidate = DiscoverCandidate = Candidate  # tên cũ, giữ để tương thích


def make_candidate(loc: Location, distance_m: float) -> dict:
    return Candidate(
        id=loc.id, name=loc.name, description_source=loc.description_source,
        latitude=loc.latitude, longitude=loc.longitude, radius=loc.radius,
        distance_m=distance_m, source=loc.source, source_lang=loc.source_lang,
        source_title=loc.source_title, source_page_id=loc.source_page_id,
        source_url=loc.source_url, last_synced_at=loc.last_synced_at,
        is_ready=loc.is_ready,
    ).model_dump()


# ---------------------------- Tuần 4: narration cache ----------------------------

VALID_LANGS = {"vi", "en", "ja"}
AUDIO_STATUSES = {"text_ready", "audio_ready"}
# Chỉ nhận TÊN FILE mp3 thuần: bắt đầu bằng chữ/số, không có '/', '\\', ':'.
AUDIO_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.mp3$", re.IGNORECASE)


def utcnow() -> datetime:
    """Giờ UTC, không kèm tzinfo (SQLite lưu datetime dạng text không múi giờ)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def narration_ttl() -> timedelta:
    """TTL cache narration, cấu hình bằng NARRATION_TTL_DAYS (mặc định 7 ngày)."""
    try:
        days = float(os.environ.get("NARRATION_TTL_DAYS", "7"))
    except ValueError:
        days = 7.0
    return timedelta(days=days if days > 0 else 7.0)


class NarrationPut(BaseModel):
    """Body của PUT /locations/{id}/narrations/{language} (bản ghi đầy đủ)."""

    # extra="forbid": body có trường lạ (vd audio_url) -> 422. DB chỉ lưu
    # audio_filename, không lưu URL có hostname của container.
    model_config = ConfigDict(extra="forbid")

    requested_lang: Optional[str] = None  # nếu gửi thì phải khớp {language} trên path
    lang: str
    text: str
    source_url: Optional[str] = None
    target_title: Optional[str] = None
    is_fallback: bool = False
    reason: Optional[str] = None
    voice: Optional[str] = None
    text_hash: Optional[str] = None
    audio_filename: Optional[str] = None
    audio_status: str
    expires_at: Optional[datetime] = None

    @field_validator("requested_lang", "lang")
    @classmethod
    def _lang_allowed(cls, v):
        if v is not None and v not in VALID_LANGS:
            raise ValueError(f"Ngôn ngữ phải thuộc {sorted(VALID_LANGS)}")
        return v

    @field_validator("text")
    @classmethod
    def _text_valid(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("text không được để trống")
        if len(v) > 10000:
            raise ValueError("text quá dài (tối đa 10000 ký tự)")
        return v

    @field_validator("audio_status")
    @classmethod
    def _status_valid(cls, v: str) -> str:
        if v not in AUDIO_STATUSES:
            raise ValueError(f"audio_status phải thuộc {sorted(AUDIO_STATUSES)}")
        return v

    @field_validator("audio_filename")
    @classmethod
    def _filename_valid(cls, v):
        if v is None:
            return v
        if ".." in v or not AUDIO_FILENAME_RE.match(v):
            raise ValueError("audio_filename phải là tên file .mp3 thuần (không chứa đường dẫn)")
        return v

    @field_validator("source_url")
    @classmethod
    def _url_valid(cls, v):
        if v is not None and not re.match(r"^https?://\S+$", v):
            raise ValueError("source_url phải là URL http/https")
        return v

    @field_validator("target_title", "reason", "voice", "text_hash")
    @classmethod
    def _not_blank_if_given(cls, v):
        if v is not None and not v.strip():
            raise ValueError("Không được là chuỗi rỗng (hãy dùng null)")
        return v

    @model_validator(mode="after")
    def _cross_checks(self):
        if self.voice and not self.voice.startswith(self.lang + "-"):
            raise ValueError(f"voice '{self.voice}' không khớp lang '{self.lang}'")
        if self.is_fallback and not self.reason:
            raise ValueError("is_fallback=true thì phải có reason")
        if not self.is_fallback and self.reason:
            raise ValueError("is_fallback=false thì reason phải là null")
        if self.audio_status == "audio_ready":
            if not (self.audio_filename and self.voice and self.text_hash):
                raise ValueError("audio_ready cần đủ audio_filename, voice, text_hash")
        elif self.audio_filename is not None:
            raise ValueError("text_ready không được có audio_filename")
        return self


def narration_to_dict(n: NarrationCache) -> dict:
    return {
        "location_id": n.location_id,
        "requested_lang": n.requested_lang,
        "lang": n.lang,
        "text": n.text,
        "source_url": n.source_url,
        "target_title": n.target_title,
        "is_fallback": bool(n.is_fallback),
        "reason": n.reason,
        "voice": n.voice,
        "text_hash": n.text_hash,
        "audio_filename": n.audio_filename,
        "audio_status": n.audio_status,
        "created_at": n.created_at,
        "updated_at": n.updated_at,
        "expires_at": n.expires_at,
    }


def _find_narration(db: Session, location_id: int, requested_lang: str):
    return db.execute(
        select(NarrationCache).where(
            NarrationCache.location_id == location_id,
            NarrationCache.requested_lang == requested_lang,
        )
    ).scalar_one_or_none()


def _recompute_is_ready(db: Session, location: Location) -> bool:
    """is_ready = có ÍT NHẤT 1 narration audio_ready còn hạn (bất kỳ ngôn ngữ nào)."""
    db.flush()  # SessionLocal đang autoflush=False -> flush tay để đếm đúng
    count = db.execute(
        select(func.count())
        .select_from(NarrationCache)
        .where(
            NarrationCache.location_id == location.id,
            NarrationCache.audio_status == "audio_ready",
            NarrationCache.expires_at > utcnow(),
        )
    ).scalar_one()
    location.is_ready = count > 0
    return location.is_ready


def _invalidate_narrations(db: Session, location: Location) -> None:
    """Xóa toàn bộ cache narration của địa điểm và đặt lại is_ready=False."""
    db.execute(delete(NarrationCache).where(NarrationCache.location_id == location.id))
    location.is_ready = False


# ============================================================================
# KHU VỰC 5: FASTAPI APP + ROUTES
# ============================================================================

app = FastAPI(title="content-service")

run_migrations()  # (1) xử lý DB cũ từ tuần 2
Base.metadata.create_all(bind=engine)  # (2) xử lý DB hoàn toàn mới


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/health")
def health_check():
    return {"service": "content-service", "status": "ok"}


# --- 2 route TĨNH dưới đây phải nằm TRƯỚC route động /locations/{location_id},
# nếu không FastAPI sẽ khớp nhầm "nearby"/"discover" vào location_id ---


@app.get("/locations/nearby")
def locations_nearby(
    latitude: float = Query(...),
    longitude: float = Query(...),
    max_distance: float = Query(500, gt=0),
    db: Session = Depends(get_db),
):
    """
    Tìm trong dữ liệu ĐÃ LƯU SẴN (CHỈ đọc SQLite, không bao giờ gọi Wikimedia).
    Trả MỌI địa điểm có GPS trong max_distance, gần nhất trước - không chỉ 1 điểm.
    """
    try:
        validate_coords(latitude, longitude)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    rows = db.execute(
        select(Location).where(Location.latitude.isnot(None), Location.longitude.isnot(None))
    ).scalars().all()

    candidates = []
    for loc in rows:
        d = haversine_m(latitude, longitude, loc.latitude, loc.longitude)
        if d <= max_distance:
            candidates.append(make_candidate(loc, d))
    candidates.sort(key=lambda c: c["distance_m"])
    return {"candidates": candidates}


def _stored_wikimedia_near(db: Session, lat: float, lon: float, radius: float, limit: int) -> list:
    """
    Các bài Wikimedia ĐÃ LƯU trong SQLite nằm trong `radius` quanh điểm truy vấn.
    Chỉ ĐỌC: giữ nguyên metadata cũ (last_synced_at...), không ghi gì.
    """
    rows = db.execute(
        select(Location).where(
            Location.source == "wikimedia",
            Location.source_lang == "vi",
            Location.latitude.isnot(None),
            Location.longitude.isnot(None),
        )
    ).scalars().all()
    found = []
    for loc in rows:
        d = haversine_m(lat, lon, loc.latitude, loc.longitude)
        if d <= radius:
            found.append(make_candidate(loc, d))
    found.sort(key=lambda c: c["distance_m"])
    return found[:limit]


@app.get("/locations/discover")
def locations_discover(
    latitude: float = Query(...),
    longitude: float = Query(...),
    radius: float = Query(500, gt=0),
    limit: int = Query(5, gt=0, le=50),
    db: Session = Depends(get_db),
):
    """
    Tìm bài Wikipedia quanh tọa độ rồi UPSERT vào SQLite.

    served_from cho biết dữ liệu đến từ đâu:
      "wikimedia"    - vừa gọi Wikimedia thành công
      "memory_cache" - cache 60s trong RAM (kể cả ô không có địa điểm)
      "sqlite"       - Wikimedia đang lỗi, phục vụ bản đã lưu trong SQLite
                       (kèm upstream_error để biết dữ liệu có thể cũ)
    Wikimedia lỗi mà SQLite cũng không có gì -> 503 với detail.code ổn định
    (timeout | rate_limited | upstream_error). KHÔNG bao giờ trả danh sách
    rỗng cho một lỗi upstream.
    """
    try:
        validate_coords(latitude, longitude)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        raw_candidates, cache_hit = wikimedia_discover(latitude, longitude, radius, limit)
    except WikimediaError as exc:
        stored = _stored_wikimedia_near(db, latitude, longitude, radius, limit)
        if stored:
            return {
                "candidates": stored,
                "cache_hit": True,
                "served_from": "sqlite",
                "upstream_error": {"code": exc.code, "message": exc.message},
            }
        detail = {"code": exc.code, "message": exc.message}
        headers = {}
        if exc.retry_after is not None:
            detail["retry_after_s"] = exc.retry_after
            headers["Retry-After"] = str(math.ceil(exc.retry_after))
        return JSONResponse(status_code=503, content={"detail": detail}, headers=headers)

    result = []
    for item in raw_candidates:
        loc = _upsert_wikimedia_location(db, item, fetched_now=not cache_hit)
        d = haversine_m(latitude, longitude, loc.latitude, loc.longitude)
        result.append(make_candidate(loc, d))
    result.sort(key=lambda c: c["distance_m"])

    return {
        "candidates": result,
        "cache_hit": cache_hit,
        "served_from": "memory_cache" if cache_hit else "wikimedia",
        "upstream_error": None,
    }


def _find_wikimedia(db: Session, source_page_id: int, source_lang: str = "vi"):
    return db.execute(
        select(Location).where(
            Location.source == "wikimedia",
            Location.source_lang == source_lang,
            Location.source_page_id == source_page_id,
        )
    ).scalar_one_or_none()


def _upsert_wikimedia_location(db: Session, item: dict, fetched_now: bool = True) -> Location:
    """
    Thêm mới hoặc cập nhật 1 record nguồn Wikimedia (chống trùng theo pageid).

    fetched_now=True : dữ liệu VỪA lấy thật từ Wikimedia -> được refresh
                       last_synced_at (và nội dung/tọa độ nếu đổi).
    fetched_now=False: dữ liệu chỉ lấy từ cache 60s -> record đã có thì giữ
                       nguyên, KHÔNG ghi đè last_synced_at (không giả tạo
                       thời gian đồng bộ khi chỉ đọc cache).
    """
    source_lang = "vi"
    existing = _find_wikimedia(db, item["source_page_id"], source_lang)

    if existing:
        if not fetched_now:
            return existing
        content_changed = (
            existing.description_source != item["description_source"]
            or existing.latitude != item["latitude"]
            or existing.longitude != item["longitude"]
        )
        existing.name = item["source_title"]
        existing.description_source = item["description_source"]
        existing.source_title = item["source_title"]
        existing.source_url = item["source_url"]
        existing.latitude = item["latitude"]
        existing.longitude = item["longitude"]
        existing.last_synced_at = item["last_synced_at"]
        if existing.radius is None:
            existing.radius = 80
        if content_changed:
            # Nội dung/tọa độ nguồn đổi -> narration đã cache không còn đúng.
            _invalidate_narrations(db, existing)
        db.commit()
        db.refresh(existing)
        return existing

    new_loc = Location(
        name=item["source_title"], description_vi=item["description_source"],
        target_languages="", description_source=item["description_source"],
        latitude=item["latitude"], longitude=item["longitude"], radius=80,
        source_lang=source_lang, source="wikimedia", source_title=item["source_title"],
        source_page_id=item["source_page_id"], source_url=item["source_url"],
        last_synced_at=item["last_synced_at"], is_ready=False,
    )
    db.add(new_loc)
    try:
        db.commit()
    except IntegrityError:
        # 2 discovery đồng thời cùng insert 1 pageid: request thua cuộc bị
        # unique(source, source_lang, source_page_id) từ chối -> rollback
        # (để session dùng lại được), rồi đọc lại record của request thắng.
        db.rollback()
        winner = _find_wikimedia(db, item["source_page_id"], source_lang)
        if winner is None:  # lỗi ràng buộc khác, không phải trùng khóa -> không nuốt lỗi
            raise
        return winner
    db.refresh(new_loc)
    return new_loc


# --- CRUD cũ (tuần 2) + DELETE mới (tuần 3) ---


@app.post("/locations", response_model=LocationResponse)
def create_location(location: LocationCreate, db: Session = Depends(get_db)):
    new_location = Location(
        name=location.name, description_vi=location.description_vi,
        target_languages=location.target_languages, description_source=location.description_vi,
        latitude=location.latitude, longitude=location.longitude,
        radius=location.radius if location.latitude is not None else None,
        source_lang="vi", source="manual", is_ready=False,
    )
    db.add(new_location)
    db.commit()
    db.refresh(new_location)
    return new_location


@app.get("/locations", response_model=List[LocationResponse])
def get_locations(db: Session = Depends(get_db)):
    return db.execute(select(Location)).scalars().all()


@app.get("/locations/{location_id}", response_model=LocationResponse)
def get_location(location_id: int, db: Session = Depends(get_db)):
    location = db.get(Location, location_id)
    if location is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy địa điểm")
    return location


@app.put("/locations/{location_id}", response_model=LocationResponse)
def update_location(location_id: int, updated: LocationCreate, db: Session = Depends(get_db)):
    location = db.get(Location, location_id)
    if location is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy địa điểm")
    old_content = (location.description_source, location.latitude, location.longitude)
    location.name = updated.name
    location.description_vi = updated.description_vi
    location.target_languages = updated.target_languages
    location.description_source = updated.description_vi
    location.latitude = updated.latitude
    location.longitude = updated.longitude
    location.radius = updated.radius if updated.latitude is not None else None
    if old_content != (location.description_source, location.latitude, location.longitude):
        # Nội dung hoặc tọa độ nguồn đổi -> cache narration cũ không còn đúng.
        # (Chỉ đổi name/target_languages/radius thì giữ nguyên cache.)
        _invalidate_narrations(db, location)
    db.commit()
    db.refresh(location)
    return location


@app.delete("/locations/{location_id}")
def delete_location(location_id: int, db: Session = Depends(get_db)):
    location = db.get(Location, location_id)
    if location is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy địa điểm")
    # Xóa cache narration trước (không để dòng mồ côi trỏ tới location đã xóa).
    db.execute(delete(NarrationCache).where(NarrationCache.location_id == location_id))
    db.delete(location)
    db.commit()
    return {"deleted": True, "id": location_id}


# --- Tuần 4: kho cache narration + trạng thái sẵn sàng ---


def _get_location_or_404(db: Session, location_id: int) -> Location:
    location = db.get(Location, location_id)
    if location is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy địa điểm")
    return location


def _check_language(language: str) -> None:
    if language not in VALID_LANGS:
        raise HTTPException(
            status_code=422, detail=f"language phải thuộc {sorted(VALID_LANGS)}"
        )


@app.get("/locations/{location_id}/narrations/{language}")
def get_narration(location_id: int, language: str, db: Session = Depends(get_db)):
    """Cache hit (còn hạn) -> {cache_hit:true, narration:{...}}; ngược lại miss."""
    _get_location_or_404(db, location_id)
    _check_language(language)
    row = _find_narration(db, location_id, language)
    if row is None or row.expires_at <= utcnow():  # chưa có hoặc đã hết hạn
        return {"cache_hit": False, "narration": None}
    return {"cache_hit": True, "narration": narration_to_dict(row)}


@app.put("/locations/{location_id}/narrations/{language}")
def put_narration(
    location_id: int, language: str, body: NarrationPut, db: Session = Depends(get_db)
):
    """Upsert 1 narration theo (location_id, requested_lang=language)."""
    location = _get_location_or_404(db, location_id)
    _check_language(language)

    if body.requested_lang is not None and body.requested_lang != language:
        raise HTTPException(422, "requested_lang trong body phải khớp {language} trên path")
    if body.is_fallback and body.lang == language:
        raise HTTPException(422, "is_fallback=true nhưng lang trùng requested_lang")
    if not body.is_fallback and body.lang != language:
        raise HTTPException(422, "lang khác requested_lang thì phải is_fallback=true")

    now = utcnow()
    expires_at = body.expires_at
    if expires_at is not None:
        if expires_at.tzinfo is not None:
            expires_at = expires_at.astimezone(timezone.utc).replace(tzinfo=None)
        if expires_at <= now:
            raise HTTPException(422, "expires_at phải ở tương lai")
    else:
        expires_at = now + narration_ttl()

    values = dict(
        lang=body.lang, text=body.text, source_url=body.source_url,
        target_title=body.target_title, is_fallback=body.is_fallback,
        reason=body.reason, voice=body.voice, text_hash=body.text_hash,
        audio_filename=body.audio_filename, audio_status=body.audio_status,
        expires_at=expires_at, updated_at=now,
    )

    def apply(row):
        for key, value in values.items():
            setattr(row, key, value)

    created = False
    row = _find_narration(db, location_id, language)
    try:
        if row is None:
            row = NarrationCache(location_id=location_id, requested_lang=language,
                                 created_at=now, **values)
            db.add(row)
            created = True
        else:
            apply(row)
        _recompute_is_ready(db, location)
        db.commit()
    except IntegrityError:
        # Hai PUT cùng key chạy đồng thời (hoặc location vừa bị xóa).
        db.rollback()
        if db.get(Location, location_id) is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy địa điểm")
        row = _find_narration(db, location_id, language)
        if row is None:
            raise HTTPException(status_code=409, detail="Xung đột khi lưu cache, thử lại")
        created = False
        apply(row)
        _recompute_is_ready(db, db.get(Location, location_id))
        db.commit()

    db.refresh(row)
    return {"created": created, "narration": narration_to_dict(row)}


@app.delete("/locations/{location_id}/narrations/{language}")
def delete_narration(location_id: int, language: str, db: Session = Depends(get_db)):
    """
    Xóa 1 dòng cache narration (B dùng để loại cache hỏng/hết hạn).
    IDEMPOTENT: location có mà key chưa/không còn tồn tại vẫn trả 200 với
    deleted=false (gọi lại bao nhiêu lần kết quả vẫn hợp lệ). Location không
    tồn tại -> 404. Xóa xong tự tính lại is_ready.
    """
    location = _get_location_or_404(db, location_id)
    _check_language(language)
    row = _find_narration(db, location_id, language)
    if row is not None:
        db.delete(row)
    ready = _recompute_is_ready(db, location)  # flush bên trong -> thấy cả lần xóa này
    db.commit()
    return {
        "deleted": row is not None,
        "location_id": location_id,
        "requested_lang": language,
        "is_ready": ready,
    }


@app.patch("/locations/{location_id}/mark-ready")
def mark_ready(location_id: int, db: Session = Depends(get_db)):
    """
    Tính lại is_ready TỪ CACHE (không nhận giá trị từ client). is_ready=true
    chỉ nghĩa là có ít nhất 1 ngôn ngữ đã audio_ready còn hạn.
    """
    location = _get_location_or_404(db, location_id)
    ready = _recompute_is_ready(db, location)
    db.commit()
    languages = db.execute(
        select(NarrationCache.requested_lang)
        .where(
            NarrationCache.location_id == location_id,
            NarrationCache.audio_status == "audio_ready",
            NarrationCache.expires_at > utcnow(),
        )
        .order_by(NarrationCache.requested_lang)
    ).scalars().all()
    return {"id": location_id, "is_ready": ready, "audio_ready_languages": list(languages)}
