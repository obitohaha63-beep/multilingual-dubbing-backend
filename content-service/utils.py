"""Hàm tiện ích dùng chung."""

import math


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Tính khoảng cách giữa 2 điểm GPS theo công thức Haversine, đơn vị MÉT.

    Haversine giả định Trái Đất là hình cầu (bán kính trung bình ~6371 km),
    cho sai số rất nhỏ (<0.5%) ở khoảng cách vài km trở xuống -> đủ chính
    xác cho bài toán "có đang đứng gần địa điểm này không" (bán kính
    kích hoạt chỉ 50-500m).
    """
    R = 6_371_000.0  # bán kính Trái Đất, mét

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    c = 2 * math.asin(math.sqrt(a))
    return R * c


def validate_coords(latitude: float, longitude: float) -> None:
    """Raise ValueError nếu tọa độ không hữu hạn hoặc ngoài khoảng hợp lệ."""
    if latitude is None or longitude is None:
        return
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        raise ValueError("Tọa độ phải là số hữu hạn")
    if not (-90 <= latitude <= 90):
        raise ValueError("latitude phải nằm trong khoảng [-90, 90]")
    if not (-180 <= longitude <= 180):
        raise ValueError("longitude phải nằm trong khoảng [-180, 180]")
