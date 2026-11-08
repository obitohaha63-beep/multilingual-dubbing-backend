"""
Bộ lọc và xếp hạng địa điểm tham quan du lịch (POI - Point of Interest) cho Gateway Service.
Dùng để phân loại ứng viên và kiểm tra độ tin cậy của địa điểm trước khi kích hoạt thuyết minh.

Loại trừ:
- Quốc gia, chính thể, nhà nước lịch sử, thuộc địa, chế độ chính trị.
- Đơn vị hành chính cấp tỉnh / thành phố / quận / huyện / xã diện rộng.
- Tổ chức, ủy ban, cơ quan, đảng phái, phong trào.
- Sự kiện, chiến tranh, chiến dịch, hiệp định, trận đánh.
- Con người, nhân vật lịch sử, tiểu sử cá nhân.

Ưu tiên (Scoring):
- Cảnh quan thiên nhiên: hồ, sông, suối, núi, đảo, bãi biển, vịnh...
- Di tích lịch sử - văn hóa: đền, chùa, tháp, đình, miếu, lăng, nhà thờ, di tích...
- Điểm tham quan, công trình kiến trúc: bảo tàng, nhà hát, quảng trường, công viên, chợ, bưu điện...
"""
import re
from typing import Dict, List, Optional, Tuple, Any

TITLE_EXCLUDE_PATTERNS = [
    r"Việt Nam Dân chủ Cộng hòa",
    r"Cộng hòa Xã hội chủ nghĩa Việt Nam",
    r"Cộng hòa Miền Nam Việt Nam",
    r"Việt Nam Cộng hòa",
    r"Liên bang Đông Dương",
    r"Đông Dương thuộc Pháp",
    r"Đại La\b",
    r"Đại Cồ Việt\b",
    r"Đại Việt\b",
    r"Vương quốc\b",
    r"Đế chế\b",
    r"Đế quốc\b",
    r"^Việt Nam$",
    r"^Hà Nội$",
    r"\(quận\)",
    r"\(huyện\)",
    r"\(phường\)",
    r"\(tỉnh\)",
    r"\(thị xã\)",
    r"^Quận\s+",
    r"^Huyện\s+",
    r"^Phường\s+",
    r"^Tỉnh\s+",
    r"Ủy ban\b",
    r"Hội đồng\b",
    r"Bộ Chính trị\b",
    r"Mặt trận Tổ quốc\b",
    r"Đảng Cộng sản\b",
    r"Hiệp hội\b",
    r"^Chiến dịch\b",
    r"^Trận\s+",
    r"^Chiến tranh\b",
    r"^Khởi nghĩa\b",
    r"^Hiệp định\b",
    r"^Hội nghị\b",
    r"^Cuộc vây hãm\b",
]

EXTRACT_EXCLUDE_PATTERNS = [
    r"là một nhà nước",
    r"là một quốc gia",
    r"là quốc gia\b",
    r"là một chính thể",
    r"là chính thể\b",
    r"là một chế độ",
    r"là một thuộc địa",
    r"thuộc địa của",
    r"nhà nước từng tồn tại",
    r"là thủ đô của nước",
    r"là thủ đô của",
    r"là thành phố trực thuộc trung ương",
    r"là một thành phố trực thuộc",
    r"là một quận",
    r"là một phường",
    r"là một huyện",
    r"là một tỉnh",
    r"là một xã\b",
    r"là đơn vị hành chính",
    r"là một đơn vị hành chính",
    r"là một ủy ban",
    r"là một tổ chức",
    r"là một cơ quan",
    r"là một liên minh",
    r"là một phong trào",
    r"là một hiệp hội",
    r"là một cuộc chiến",
    r"là một trận đánh",
    r"là một chiến dịch",
    r"là một hiệp định",
    r"là một cuộc khởi nghĩa",
    r"nổ ra vào năm",
    r"diễn ra từ năm",
    r"sinh ngày \d+",
    r"sinh năm \d+",
    r"\(\d{4}\s*[-–]\s*\d{4}\)",
    r"là một chính trị gia",
    r"là một tướng lĩnh",
    r"là một danh tướng",
    r"là một nhà cách mạng",
    r"là một vị vua",
    r"là một nhà văn",
    r"là một nhà thơ",
    r"là một đại biểu",
]

CATEGORY_EXCLUDE_PATTERNS = [
    r"Sinh năm\b",
    r"Mất năm\b",
    r"Nhân vật\b",
    r"Người Hà Nội\b",
    r"Người Việt Nam\b",
    r"Quốc gia\b",
    r"Chính trị Việt Nam\b",
    r"Lịch sử Việt Nam\b",
    r"Chiến tranh\b",
    r"Chiến dịch\b",
    r"Trận đánh\b",
]

POI_STRONG_PATTERNS = [
    r"\bhồ\b", r"\blake\b", r"\bsông\b", r"\bsuối\b", r"\bnúi\b", r"\bđảo\b", r"\bvịnh\b", r"\bbãi biển\b",
    r"\bđền\b", r"\bchùa\b", r"\btháp\b", r"\bđình\b", r"\bmiếu\b", r"\blăng\b",
    r"\bnhà thờ\b", r"\bchính tòa\b", r"\btu viện\b", r"\bphủ\b", r"\bdinh\b", r"\bcung điện\b", r"\bthành cổ\b",
    r"\bbảo tàng\b", r"\bnhà hát\b", r"\bquảng trường\b", r"\bcông viên\b", r"\bvườn hoa\b",
    r"\bvườn bách thảo\b", r"\bchợ\b", r"\bphố cổ\b", r"\bbưu điện\b", r"\bcầu\b", r"\bdi tích\b",
    r"\bkhu du lịch\b", r"\bkỳ đài\b", r"\bcột cờ\b",
]

POI_MEDIUM_PATTERNS = [
    r"\btòa nhà\b", r"\btrung tâm thương mại\b", r"\btrung tâm văn hóa\b",
    r"\bcông trình\b", r"\bkhu phố\b", r"\bphố\b", r"\bđường\b",
]


def is_valid_travel_poi(title: str, extract: str = "", categories: Optional[List[str]] = None) -> Tuple[bool, str, int]:
    """
    Xác minh một ứng viên có phải là địa điểm tham quan / POI hợp lệ hay không.
    Trả về: (is_valid: bool, reason: str, poi_score: int)
    """
    clean_title = (title or "").strip()
    clean_extract = (extract or "").strip().lower()
    categories = categories or []

    if not clean_title:
        return False, "empty_title", 0

    for pat in TITLE_EXCLUDE_PATTERNS:
        if re.search(pat, clean_title, re.IGNORECASE):
            return False, f"title_excluded_{pat}", -9999

    for cat in categories:
        cat_str = cat.get("title", "") if isinstance(cat, dict) else str(cat)
        for pat in CATEGORY_EXCLUDE_PATTERNS:
            if re.search(pat, cat_str, re.IGNORECASE):
                return False, f"category_excluded_{pat}", -9999

    for pat in EXTRACT_EXCLUDE_PATTERNS:
        if re.search(pat, clean_extract, re.IGNORECASE):
            return False, f"extract_excluded_{pat}", -9999

    score = 10
    title_lower = clean_title.lower()

    has_strong_title = any(re.search(pat, title_lower, re.IGNORECASE) for pat in POI_STRONG_PATTERNS)
    if has_strong_title:
        score += 100
    elif any(re.search(pat, clean_extract, re.IGNORECASE) for pat in POI_STRONG_PATTERNS):
        score += 40

    has_medium_title = any(re.search(pat, title_lower, re.IGNORECASE) for pat in POI_MEDIUM_PATTERNS)
    if has_medium_title:
        score += 50
    elif any(re.search(pat, clean_extract, re.IGNORECASE) for pat in POI_MEDIUM_PATTERNS):
        score += 20

    return True, "valid_poi", score


def rank_candidates(candidates: List[Dict[str, Any]], user_lat: float, user_lon: float) -> List[Dict[str, Any]]:
    """
    Lọc các ứng viên không hợp lệ và xếp hạng ứng viên theo độ phù hợp kết hợp khoảng cách.
    """
    valid_list = []
    for c in candidates:
        title = c.get("name") or c.get("source_title") or ""
        extract = c.get("description_source") or c.get("description_vi") or ""
        categories = c.get("categories") or []

        is_valid, reason, poi_score = is_valid_travel_poi(title, extract, categories)
        if not is_valid:
            continue

        item = dict(c)
        item["poi_score"] = poi_score
        dist = item.get("distance_m")
        if dist is None and "latitude" in item and "longitude" in item and item["latitude"] is not None and item["longitude"] is not None:
            from math import radians, cos, sin, asin, sqrt
            lat1, lon1, lat2, lon2 = map(radians, [user_lat, user_lon, item["latitude"], item["longitude"]])
            dlon = lon2 - lon1
            dlat = lat2 - lat1
            a = sin(dlat / 2)**2 + cos(lat1) * cos(lat2) * sin(dlon / 2)**2
            c_val = 2 * asin(sqrt(a))
            dist = 6371000 * c_val
            item["distance_m"] = round(dist, 1)

        dist_val = dist if dist is not None else 9999.0
        item["rank_score"] = round(poi_score - (dist_val * 0.1), 2)
        valid_list.append(item)

    valid_list.sort(key=lambda x: (x.get("rank_score", 0), -(x.get("distance_m") or 9999)), reverse=True)
    return valid_list
