"""
Định nghĩa kết nối CSDL (SQLite) và model Location.

Tuần 2: id, name, description_vi, target_languages.
Tuần 3: bổ sung dữ liệu địa lý (GPS) + metadata nguồn Wikimedia, để phục vụ
2 endpoint mới /locations/nearby (tìm trong các địa điểm đã lưu) và
/locations/discover (tìm bài viết Wikimedia quanh 1 tọa độ rồi lưu lại).
"""

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Float,
    Boolean,
    DateTime,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, sessionmaker

DB_PATH = "content.db"
SQLALCHEMY_DATABASE_URL = f"sqlite:///./{DB_PATH}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Location(Base):
    """
    1 địa điểm. Có 2 "nguồn" (source):
      - "manual": người dùng tự nhập (CRUD tuần 2) -> có thể thiếu GPS.
      - "wikimedia": lấy từ Wikipedia qua /locations/discover -> luôn có
        source_page_id, source_title, source_url, bắt buộc có GPS.
    """

    __tablename__ = "locations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)

    # Giữ lại để tương thích ngược với tuần 2 (translate-audio-service của B
    # vẫn đang đọc field này).
    description_vi = Column(String, nullable=False)
    target_languages = Column(String, nullable=False)

    # ----- Tuần 3: nội dung mô tả theo nguồn gốc -----
    # Với record cũ (manual): description_source = description_vi (backfill).
    # Với record Wikimedia: description_source = đoạn giới thiệu lấy từ bài viết.
    description_source = Column(String, nullable=True)

    # ----- Tuần 3: tọa độ + bán kính kích hoạt audio (mét) -----
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    radius = Column(Float, nullable=True)  # mét, mặc định 80

    # ----- Tuần 3: metadata nguồn -----
    source_lang = Column(String, nullable=True)  # vd: "vi"
    source = Column(String, nullable=True)  # "manual" | "wikimedia"
    source_title = Column(String, nullable=True)  # tiêu đề CHÍNH XÁC trên wiki
    source_page_id = Column(Integer, nullable=True)  # pageid Wikipedia
    source_url = Column(String, nullable=True)
    last_synced_at = Column(DateTime, nullable=True)  # lần cuối đồng bộ (UTC)
    is_ready = Column(Boolean, nullable=True, default=False)

    __table_args__ = (
        # Chặn trùng: 1 bài Wikipedia (theo pageid + ngôn ngữ) chỉ được lưu
        # 1 lần. Record manual có source_page_id = NULL nên không bị chặn
        # bởi ràng buộc này (SQL coi NULL != NULL, nhiều NULL vẫn hợp lệ).
        UniqueConstraint(
            "source", "source_lang", "source_page_id", name="ux_locations_source"
        ),
    )
