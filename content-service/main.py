"""
content-service - service quản lý dữ liệu địa điểm (locations).
Tuần 1: chỉ có /health.
Tuần 2: thêm CSDL SQLite (SQLAlchemy) + 4 endpoint CRUD cho locations.
"""

from typing import List

from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from models import Base, Location, SessionLocal, engine

app = FastAPI(title="content-service")

# Lệnh này bảo SQLAlchemy: hãy tạo tất cả các bảng đã khai báo trong
# models.py (ở đây là bảng "locations") nếu bảng đó CHƯA tồn tại trong
# file content.db. Nếu bảng đã có rồi thì bỏ qua, không xóa dữ liệu cũ.
Base.metadata.create_all(bind=engine)


def get_db():
    """
    Dependency (hàm phụ trợ) cấp 1 session CSDL cho mỗi request.
    FastAPI sẽ tự gọi hàm này trước khi chạy endpoint, rồi tự đóng
    session lại sau khi endpoint xử lý xong (nhờ cú pháp yield),
    dù request thành công hay bị lỗi.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------- Pydantic schemas: định nghĩa "hình dạng" dữ liệu vào/ra ----------
# Khác với Location (model SQLAlchemy - đại diện cho BẢNG trong CSDL),
# các class dưới đây (kế thừa BaseModel) đại diện cho dữ liệu JSON mà
# client gửi lên / server trả về. FastAPI dùng chúng để tự validate
# (kiểm tra) dữ liệu và tự sinh tài liệu Swagger.


class LocationCreate(BaseModel):
    """Dữ liệu client gửi lên khi tạo mới 1 địa điểm (không có id)."""

    name: str
    description_vi: str
    target_languages: str  # ví dụ: "en,ja,ko"


class LocationResponse(BaseModel):
    """Dữ liệu server trả về cho client (có thêm id)."""

    id: int
    name: str
    description_vi: str
    target_languages: str

    class Config:
        # Cho phép Pydantic đọc trực tiếp từ object SQLAlchemy (Location)
        # thay vì chỉ đọc từ dict thông thường.
        from_attributes = True


# ---------------------------------- Routes ----------------------------------


@app.get("/health")
def health_check():
    """Endpoint kiểm tra tình trạng service."""
    return {"service": "content-service", "status": "ok"}


@app.post("/locations", response_model=LocationResponse)
def create_location(location: LocationCreate, db: Session = Depends(get_db)):
    """
    Tạo mới 1 địa điểm.
    - FastAPI tự parse JSON gửi lên thành object LocationCreate (đã validate).
    - Ta tạo 1 object Location (SQLAlchemy) từ dữ liệu đó, add + commit vào CSDL.
    """
    new_location = Location(
        name=location.name,
        description_vi=location.description_vi,
        target_languages=location.target_languages,
    )
    db.add(new_location)  # đánh dấu "sẽ thêm dòng này"
    db.commit()  # thực sự ghi xuống CSDL
    db.refresh(new_location)  # lấy lại id vừa được CSDL tự sinh ra
    return new_location


@app.get("/locations", response_model=List[LocationResponse])
def get_locations(db: Session = Depends(get_db)):
    """Lấy danh sách toàn bộ địa điểm."""
    return db.query(Location).all()


@app.get("/locations/{location_id}", response_model=LocationResponse)
def get_location(location_id: int, db: Session = Depends(get_db)):
    """Lấy thông tin 1 địa điểm theo id."""
    location = db.query(Location).filter(Location.id == location_id).first()
    if location is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy địa điểm")
    return location


@app.put("/locations/{location_id}", response_model=LocationResponse)
def update_location(
    location_id: int, updated: LocationCreate, db: Session = Depends(get_db)
):
    """Cập nhật toàn bộ thông tin của 1 địa điểm theo id."""
    location = db.query(Location).filter(Location.id == location_id).first()
    if location is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy địa điểm")

    location.name = updated.name
    location.description_vi = updated.description_vi
    location.target_languages = updated.target_languages

    db.commit()
    db.refresh(location)
    return location
