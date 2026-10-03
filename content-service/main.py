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

(models.py tách riêng - đó là định nghĩa BẢNG trong CSDL, không đổi).
"""

from __future__ import annotations

import math
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

import httpx
from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import Base, Location, SessionLocal, engine, DB_PATH


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
MIGRATION_VERSION = 2


def run_migrations(verbose: bool = True) -> None:
    # Bước 1: sao lưu file DB hiện tại (nếu có) trước khi đổi gì, phòng lỗi.
    if os.path.exists(DB_PATH):
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        backup_path = f"{DB_PATH}.bak-{ts}"
        shutil.copy2(DB_PATH, backup_path)
        if verbose:
            print(f"[migrate] Đã sao lưu DB -> {backup_path}")

    conn = sqlite3.connect(DB_PATH)
    try:
        # Bước 2: bảng để nhớ "version nào đã chạy rồi" - tránh chạy lại.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        already_done = {
            row[0] for row in conn.execute("SELECT version FROM schema_migrations")
        }
        if MIGRATION_VERSION in already_done:
            if verbose:
                print("[migrate] Schema đã cập nhật từ trước, không có gì để làm.")
            return

        # Bước 3: bảng locations có tồn tại chưa? (DB hoàn toàn mới thì
        # chưa có gì - create_all() ở cuối file sẽ tự tạo đủ cột luôn).
        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='locations'"
        ).fetchone()

        if table_exists:
            # Bước 4: với mỗi cột mới, chỉ ALTER TABLE nếu CHƯA có cột đó.
            existing_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(locations)")
            }
            for col_name, col_type in NEW_COLUMNS:
                if col_name not in existing_cols:
                    conn.execute(
                        f"ALTER TABLE locations ADD COLUMN {col_name} {col_type}"
                    )

            # Bước 5: backfill dữ liệu cũ - CHỈ áp dụng cho dòng còn thiếu
            # description_source (tức record từ tuần 2), không đụng tới
            # record nào đã có dữ liệu mới. Không gán tọa độ 0,0.
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

            # Bước 6: chặn trùng Wikimedia bằng unique index.
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_locations_source "
                "ON locations (source, source_lang, source_page_id)"
            )

        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (MIGRATION_VERSION, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        if verbose:
            print(f"[migrate] Hoàn tất version {MIGRATION_VERSION}.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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


class WikimediaError(Exception):
    """code: 'timeout' | 'rate_limited' | 'upstream_error'."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _wiki_user_agent() -> str:
    ua = os.environ.get("WIKIMEDIA_USER_AGENT")
    if ua:
        return ua
    print("[wikimedia] CẢNH BÁO: thiếu WIKIMEDIA_USER_AGENT trong .env.")
    return "content-service-do-an-cnpm/1.0"


def _wiki_request(client: httpx.Client, params: dict) -> dict:
    """Gọi 1 request tới Wikimedia, tự retry tối đa 1 lần nếu 429/5xx."""
    attempts = 0
    while True:
        attempts += 1
        try:
            resp = client.get(
                WIKI_API_URL, params=params, headers={"User-Agent": _wiki_user_agent()}
            )
        except httpx.TimeoutException as exc:
            raise WikimediaError("timeout", "Wikimedia không phản hồi kịp") from exc
        except httpx.RequestError as exc:
            raise WikimediaError("upstream_error", f"Lỗi kết nối: {exc}") from exc

        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            wait_s = min(float(retry_after) if retry_after and retry_after.isdigit() else 1.0, 3.0)
            if attempts <= WIKI_MAX_RETRY:
                time.sleep(wait_s)
                continue
            raise WikimediaError("rate_limited", "Wikimedia trả 429 quá số lần retry")

        if resp.status_code >= 500 and attempts <= WIKI_MAX_RETRY:
            time.sleep(0.5)
            continue
        if resp.status_code >= 400:
            raise WikimediaError("upstream_error", f"HTTP {resp.status_code}")

        try:
            return resp.json()
        except ValueError as exc:
            raise WikimediaError("upstream_error", "Phản hồi không phải JSON") from exc


def wikimedia_discover(lat: float, lon: float, radius: float, limit: int) -> tuple[list[dict], bool]:
    """
    Trả (candidates, cache_hit).
    1. geosearch: tìm pageid các bài gần tọa độ.
    2. extracts: lấy đoạn giới thiệu + url của các pageid đó.
    Bỏ bài thiếu tọa độ hoặc thiếu đoạn giới thiệu. GPS lấy từ chính bài
    viết (geosearch trả về), KHÔNG dùng GPS người dùng truyền vào.
    """
    key = (round(lat, WIKI_GRID_DECIMALS), round(lon, WIKI_GRID_DECIMALS), radius, limit)
    now = time.monotonic()
    cached = _wiki_cache.get(key)
    if cached and cached[1] > now:
        return cached[0], True

    with httpx.Client(timeout=WIKI_TIMEOUT) as client:
        geo_data = _wiki_request(
            client,
            {
                "action": "query", "list": "geosearch",
                "gscoord": f"{lat}|{lon}", "gsradius": int(radius),
                "gsnamespace": 0, "gslimit": limit, "format": "json",
            },
        )
        geo_results = geo_data.get("query", {}).get("geosearch", [])
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
            )
            pages = ext_data.get("query", {}).get("pages", {})

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


class NearbyCandidate(BaseModel):
    id: int
    name: str
    description_source: Optional[str] = None
    latitude: float
    longitude: float
    radius: Optional[float] = None
    distance_m: float
    source: Optional[str] = None
    source_lang: Optional[str] = None
    source_title: Optional[str] = None
    source_page_id: Optional[int] = None
    source_url: Optional[str] = None
    last_synced_at: Optional[datetime] = None


class DiscoverCandidate(BaseModel):
    id: int
    name: str
    description_source: Optional[str] = None
    latitude: float
    longitude: float
    radius: Optional[float] = None
    source: Optional[str] = None
    source_lang: Optional[str] = None
    source_title: Optional[str] = None
    source_page_id: Optional[int] = None
    source_url: Optional[str] = None
    last_synced_at: Optional[datetime] = None


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
    """Tìm trong dữ liệu ĐÃ LƯU SẴN (không gọi Wikimedia)."""
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
            candidates.append(NearbyCandidate(
                id=loc.id, name=loc.name, description_source=loc.description_source,
                latitude=loc.latitude, longitude=loc.longitude, radius=loc.radius,
                distance_m=d, source=loc.source, source_lang=loc.source_lang,
                source_title=loc.source_title, source_page_id=loc.source_page_id,
                source_url=loc.source_url, last_synced_at=loc.last_synced_at,
            ))
    candidates.sort(key=lambda c: c.distance_m)
    return {"candidates": [c.model_dump() for c in candidates]}


@app.get("/locations/discover")
def locations_discover(
    latitude: float = Query(...),
    longitude: float = Query(...),
    radius: float = Query(500, gt=0),
    limit: int = Query(5, gt=0, le=50),
    db: Session = Depends(get_db),
):
    """Tìm bài Wikipedia quanh tọa độ, UPSERT vào CSDL."""
    try:
        validate_coords(latitude, longitude)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        raw_candidates, cache_hit = wikimedia_discover(latitude, longitude, radius, limit)
    except WikimediaError as exc:
        return JSONResponse(
            status_code=503,
            content={"detail": {"code": exc.code, "message": exc.message}},
        )

    result = []
    for item in raw_candidates:
        loc = _upsert_wikimedia_location(db, item)
        result.append(DiscoverCandidate(
            id=loc.id, name=loc.name, description_source=loc.description_source,
            latitude=loc.latitude, longitude=loc.longitude, radius=loc.radius,
            source=loc.source, source_lang=loc.source_lang, source_title=loc.source_title,
            source_page_id=loc.source_page_id, source_url=loc.source_url,
            last_synced_at=loc.last_synced_at,
        ).model_dump())

    return {"candidates": result, "cache_hit": cache_hit}


def _upsert_wikimedia_location(db: Session, item: dict) -> Location:
    """Thêm mới hoặc cập nhật 1 record nguồn Wikimedia (chống trùng theo pageid)."""
    source_lang = "vi"
    existing = db.execute(
        select(Location).where(
            Location.source == "wikimedia",
            Location.source_lang == source_lang,
            Location.source_page_id == item["source_page_id"],
        )
    ).scalar_one_or_none()

    if existing:
        existing.name = item["source_title"]
        existing.description_source = item["description_source"]
        existing.source_title = item["source_title"]
        existing.source_url = item["source_url"]
        existing.latitude = item["latitude"]
        existing.longitude = item["longitude"]
        existing.last_synced_at = item["last_synced_at"]
        if existing.radius is None:
            existing.radius = 80
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
        # 2 request cùng lúc insert trùng pageid -> rollback, đọc lại record kia.
        db.rollback()
        return db.execute(
            select(Location).where(
                Location.source == "wikimedia",
                Location.source_lang == source_lang,
                Location.source_page_id == item["source_page_id"],
            )
        ).scalar_one()
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
    location.name = updated.name
    location.description_vi = updated.description_vi
    location.target_languages = updated.target_languages
    location.description_source = updated.description_vi
    location.latitude = updated.latitude
    location.longitude = updated.longitude
    location.radius = updated.radius if updated.latitude is not None else None
    db.commit()
    db.refresh(location)
    return location


@app.delete("/locations/{location_id}")
def delete_location(location_id: int, db: Session = Depends(get_db)):
    location = db.get(Location, location_id)
    if location is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy địa điểm")
    db.delete(location)
    db.commit()
    return {"deleted": True, "id": location_id}
