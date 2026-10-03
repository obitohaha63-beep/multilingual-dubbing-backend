"""
content-service - port 8001.
Tuần 1: /health.
Tuần 2: CRUD /locations (SQLite).
Tuần 3:
  - Thêm DELETE /locations/{id}.
  - Location có thêm GPS + metadata nguồn Wikimedia.
  - /locations/nearby: tìm trong các địa điểm ĐÃ LƯU gần 1 tọa độ.
  - /locations/discover: tìm bài Wikipedia gần 1 tọa độ, LƯU lại vào CSDL.
  - Migration tự chạy khi service khởi động (an toàn, không mất dữ liệu cũ).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import wikimedia_client
from migrate import run_migrations
from models import Base, Location, SessionLocal, engine
from utils import haversine_m, validate_coords

app = FastAPI(title="content-service")

# 1) Chạy migration TRƯỚC (xử lý DB cũ từ tuần 2: thêm cột, backfill).
run_migrations()
# 2) create_all() xử lý trường hợp DB hoàn toàn mới (chưa có bảng nào) -
#    với DB đã có bảng "locations" rồi thì lệnh này không làm gì cả.
Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============================= Pydantic schemas =============================


class LocationCreate(BaseModel):
    """Dữ liệu tạo/sửa 1 địa điểm THỦ CÔNG (source='manual')."""

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
    def _radius_positive(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v <= 0:
            raise ValueError("radius phải > 0")
        return v

    @field_validator("longitude")
    @classmethod
    def _coords_valid(cls, v, info):
        lat = info.data.get("latitude")
        lon = v
        if lat is not None or lon is not None:
            try:
                validate_coords(lat, lon)
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
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


class NearbyResponse(BaseModel):
    candidates: List[NearbyCandidate]


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


class DiscoverResponse(BaseModel):
    candidates: List[DiscoverCandidate]
    cache_hit: bool


# ================================== /health ==================================


@app.get("/health")
def health_check():
    return {"service": "content-service", "status": "ok"}


# ====================== /locations/nearby + /locations/discover =====================
# QUAN TRỌNG: 2 route "tĩnh" này phải khai báo TRƯỚC route động
# /locations/{location_id}. FastAPI/Starlette khớp route theo đúng thứ tự
# đăng ký; nếu để /{location_id} lên trước, request tới /locations/nearby
# sẽ bị khớp nhầm vào đó (location_id="nearby"), rồi FastAPI báo lỗi 422
# vì "nearby" không convert được sang int.


@app.get("/locations/nearby", response_model=NearbyResponse)
def locations_nearby(
    latitude: float = Query(...),
    longitude: float = Query(...),
    max_distance: float = Query(500, gt=0),
    db: Session = Depends(get_db),
):
    """
    Tìm trong các địa điểm ĐÃ CÓ SẴN trong CSDL (CSDL của content-service,
    không gọi Wikimedia) những địa điểm có GPS và cách tọa độ truyền vào
    không quá max_distance (mét). Sắp xếp theo khoảng cách tăng dần.

    Lưu ý: đây CHỈ là danh sách ứng viên - gateway-service (thành viên C)
    mới là nơi quyết định có "kích hoạt" (phát audio) hay không, dựa vào
    bán kính riêng (radius) của từng địa điểm.
    """
    try:
        validate_coords(latitude, longitude)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    locations = (
        db.execute(
            select(Location).where(
                Location.latitude.isnot(None), Location.longitude.isnot(None)
            )
        )
        .scalars()
        .all()
    )

    candidates = []
    for loc in locations:
        distance = haversine_m(latitude, longitude, loc.latitude, loc.longitude)
        if distance <= max_distance:
            candidates.append(
                NearbyCandidate(
                    id=loc.id,
                    name=loc.name,
                    description_source=loc.description_source,
                    latitude=loc.latitude,
                    longitude=loc.longitude,
                    radius=loc.radius,
                    distance_m=distance,
                    source=loc.source,
                    source_lang=loc.source_lang,
                    source_title=loc.source_title,
                    source_page_id=loc.source_page_id,
                    source_url=loc.source_url,
                    last_synced_at=loc.last_synced_at,
                )
            )

    candidates.sort(key=lambda c: c.distance_m)
    return NearbyResponse(candidates=candidates)


@app.get("/locations/discover")
def locations_discover(
    latitude: float = Query(...),
    longitude: float = Query(...),
    radius: float = Query(500, gt=0),
    limit: int = Query(5, gt=0, le=50),
    db: Session = Depends(get_db),
):
    """
    Tìm bài viết Wikipedia (tiếng Việt) gần tọa độ truyền vào (qua
    wikimedia_client.discover), rồi UPSERT (thêm mới hoặc cập nhật nếu đã
    có) vào CSDL với source="wikimedia". Có cache 60s để tránh spam API
    Wikimedia khi client gọi lại cùng khu vực nhiều lần liên tiếp.

    Trả {candidates, cache_hit}. Có candidate KHÔNG đồng nghĩa là đã nằm
    trong bán kính kích hoạt (activation) 80m - đây chỉ là bước "khám phá"
    với bán kính tìm kiếm rộng hơn (mặc định 500m).
    """
    try:
        validate_coords(latitude, longitude)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        raw_candidates, cache_hit = wikimedia_client.discover(
            latitude, longitude, radius, limit
        )
    except wikimedia_client.WikimediaError as exc:
        return JSONResponse(
            status_code=503,
            content={"detail": {"code": exc.code, "message": exc.message}},
        )

    result_candidates = []
    for item in raw_candidates:
        loc = _upsert_wikimedia_location(db, item)
        result_candidates.append(
            DiscoverCandidate(
                id=loc.id,
                name=loc.name,
                description_source=loc.description_source,
                latitude=loc.latitude,
                longitude=loc.longitude,
                radius=loc.radius,
                source=loc.source,
                source_lang=loc.source_lang,
                source_title=loc.source_title,
                source_page_id=loc.source_page_id,
                source_url=loc.source_url,
                last_synced_at=loc.last_synced_at,
            )
        )

    return DiscoverResponse(candidates=result_candidates, cache_hit=cache_hit)


def _upsert_wikimedia_location(db: Session, item: dict) -> Location:
    """
    Thêm mới hoặc cập nhật 1 Location nguồn Wikimedia.
    Dựa vào unique(source, source_lang, source_page_id) để biết bài này
    đã từng lưu chưa. Nếu 2 request chạy gần như đồng thời cùng insert 1
    bài mới -> bắt IntegrityError, đọc lại record đã có (do request kia
    vừa tạo) rồi update thay vì insert, tránh crash.
    """
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
        name=item["source_title"],
        description_vi=item["description_source"],  # chưa dịch, tạm dùng bản gốc
        target_languages="",
        description_source=item["description_source"],
        latitude=item["latitude"],
        longitude=item["longitude"],
        radius=80,
        source_lang=source_lang,
        source="wikimedia",
        source_title=item["source_title"],
        source_page_id=item["source_page_id"],
        source_url=item["source_url"],
        last_synced_at=item["last_synced_at"],
        is_ready=False,
    )
    db.add(new_loc)
    try:
        db.commit()
    except IntegrityError:
        # Race condition: request khác vừa insert cùng pageid trước ta.
        db.rollback()
        existing = db.execute(
            select(Location).where(
                Location.source == "wikimedia",
                Location.source_lang == source_lang,
                Location.source_page_id == item["source_page_id"],
            )
        ).scalar_one()
        return existing
    db.refresh(new_loc)
    return new_loc


# ================================ CRUD /locations ================================


@app.post("/locations", response_model=LocationResponse)
def create_location(location: LocationCreate, db: Session = Depends(get_db)):
    new_location = Location(
        name=location.name,
        description_vi=location.description_vi,
        target_languages=location.target_languages,
        description_source=location.description_vi,
        latitude=location.latitude,
        longitude=location.longitude,
        radius=location.radius if location.latitude is not None else None,
        source_lang="vi",
        source="manual",
        is_ready=False,
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
def update_location(
    location_id: int, updated: LocationCreate, db: Session = Depends(get_db)
):
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
