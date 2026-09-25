"""
File này định nghĩa cách content-service kết nối với CSDL (SQLite)
và cấu trúc bảng dữ liệu "locations" bằng SQLAlchemy (ORM).

ORM (Object-Relational Mapping) nghĩa là: ta viết 1 class Python (Location),
SQLAlchemy sẽ tự chuyển class đó thành 1 bảng SQL tương ứng, và tự sinh câu
lệnh SQL (INSERT/SELECT/UPDATE/DELETE) khi ta gọi các hàm Python bình thường
-- không cần tự viết SQL thủ công.
"""

from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import declarative_base, sessionmaker

# Đường dẫn tới file CSDL SQLite. SQLite lưu toàn bộ dữ liệu vào 1 file
# duy nhất (content.db) ngay trong thư mục chạy service - không cần cài
# server CSDL riêng, rất phù hợp để học và demo.
SQLALCHEMY_DATABASE_URL = "sqlite:///./content.db"

# "engine" là đối tượng đại diện cho kết nối tới CSDL.
# connect_args={"check_same_thread": False} là yêu cầu bắt buộc của SQLite
# khi dùng cùng 1 kết nối cho nhiều request chạy đồng thời (FastAPI có thể
# xử lý nhiều request cùng lúc).
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)

# SessionLocal là "nhà máy" tạo ra các session làm việc với CSDL.
# Mỗi request tới API sẽ mở 1 session riêng, dùng xong thì đóng lại.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base là class gốc mà mọi model (bảng) trong project phải kế thừa từ nó.
# SQLAlchemy dựa vào Base để biết những class nào cần tạo bảng.
Base = declarative_base()


class Location(Base):
    """
    Model đại diện cho 1 địa điểm cần dịch + tạo audio.
    Mỗi thuộc tính (attribute) dưới đây tương ứng với 1 cột trong bảng "locations".
    """

    __tablename__ = "locations"

    # id: khóa chính (primary key), SQLAlchemy tự tăng dần (1, 2, 3, ...)
    id = Column(Integer, primary_key=True, index=True)

    # name: tên địa điểm, ví dụ "Chợ Bến Thành"
    name = Column(String, nullable=False)

    # description_vi: mô tả gốc bằng tiếng Việt, sẽ được dịch sang các
    # ngôn ngữ khác ở các tuần sau (translate-audio-service sẽ dùng)
    description_vi = Column(String, nullable=False)

    # target_languages: danh sách ngôn ngữ cần dịch, lưu dạng chuỗi
    # phân tách bởi dấu phẩy, ví dụ "en,ja,ko" (SQLite không có kiểu
    # "list" nên ta lưu tạm dạng string, khi cần dùng thì .split(","))
    target_languages = Column(String, nullable=False)
