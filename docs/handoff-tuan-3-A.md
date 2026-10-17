# Handoff Tuần 3 — content-service (Thành viên A)

## 1. Đã làm gì

- Giữ nguyên `GET /health` và 4 endpoint CRUD cũ của tuần 2 (port 8001
  không đổi).
- Thêm `DELETE /locations/{id}` (404 nếu id không tồn tại).
- Mở rộng model `Location`: thêm GPS (`latitude`, `longitude`, `radius`)
  và metadata nguồn Wikimedia (`source`, `source_lang`, `source_title`,
  `source_page_id`, `source_url`, `last_synced_at`, `is_ready`,
  `description_source`).
- Viết migration có version, chạy tự động khi start service, an toàn khi
  chạy lặp lại, tự sao lưu DB trước khi đổi schema, KHÔNG xóa dữ liệu cũ,
  KHÔNG gán tọa độ `0,0` cho record thiếu GPS.
- Thêm `GET /locations/nearby`: tìm trong dữ liệu đã lưu, tính khoảng
  cách bằng Haversine.
- Thêm `GET /locations/discover`: gọi Wikimedia geosearch + extracts, có
  cache 60 giây, upsert kết quả vào CSDL, xử lý lỗi upstream (timeout /
  rate limit / lỗi khác) trả về 503 có `detail.code`.
- `docs/api-contract.md` mô tả đầy đủ contract.
- 15 test (`tests/test_content_service.py`, chạy `pytest -v`, tất cả PASS):
  migration idempotent + giữ dữ liệu cũ, CRUD cũ, DELETE, validate tọa độ,
  thứ tự route nearby/discover không bị `/{id}` bắt nhầm, Haversine đúng,
  discover có kết quả / rỗng / trùng pageid / timeout / rate-limited (mock
  Wikimedia, KHÔNG gọi mạng thật khi chạy test).

## 2. File đã sửa / thêm mới

```
content-service/
├── main.py              (sửa: TOÀN BỘ logic tuần 3 gộp vào đây - migration,
│                          Haversine, Wikimedia client, CRUD, DELETE,
│                          nearby, discover - chia rõ 5 khu vực bằng comment)
├── models.py             (sửa: thêm cột GPS + metadata nguồn trong Location)
├── requirements.txt       (sửa: + httpx, pytest)
├── .env.example            (mới)
├── docs/
│   ├── api-contract.md    (mới)
│   └── handoff-tuan-3-A.md (file này)
└── tests/
    └── test_content_service.py (mới)
```

Chỉ còn **2 file code** (`main.py`, `models.py`) thay vì tách thành 5 file
nhỏ - dễ đọc tuần tự từ trên xuống dưới hơn khi mới học, đổi lại `main.py`
dài hơn (~450 dòng). `main.py` chia làm 5 khu vực đánh số rõ bằng comment
lớn (MIGRATION → HÀM TIỆN ÍCH → WIKIMEDIA → SCHEMA → ROUTES) - đọc theo
đúng thứ tự này là hiểu được toàn bộ luồng.

Dockerfile: không đổi (vẫn port 8001, cách build/run như cũ).

## 3. Lệnh migration và test đã chạy thật

```bash
# Migration (tự chạy khi service start, nhưng cũng chạy tay được):
python migrate.py
# -> tạo content.db.bak-<timestamp> nếu đã có content.db, thêm cột mới,
#    backfill dữ liệu cũ, tạo unique index. Chạy lại lần 2 -> không lỗi,
#    in ra "Không có gì mới để chạy".

# Cài thư viện:
pip install -r requirements.txt

# Chạy service:
python -m uvicorn main:app --port 8001

# Chạy test:
python -m pytest tests/ -v
```

Kết quả thật đã chạy (trên máy tạo code): **15 passed**, bao gồm 1 test
dựng 1 DB giả lập đúng schema tuần 2 (4 cột, có sẵn 1 dòng dữ liệu), chạy
`run_migrations()` 2 lần, xác nhận dòng dữ liệu cũ giữ nguyên `id`/`name`/
`description_vi`, các cột mới được backfill đúng (`description_source`
= `description_vi`, `source="manual"`, `radius=80`, `is_ready=false`,
`latitude` vẫn `NULL`).

## 4. Biến môi trường

Xem `.env.example`. Cần set `WIKIMEDIA_USER_AGENT` trước khi gọi
`/locations/discover` thật (không bắt buộc để chạy `/health` hay CRUD).
Nếu thiếu, service vẫn chạy nhưng in cảnh báo và dùng User-Agent mặc
định tạm thời — nên set đầy đủ trước khi demo/deploy thật.

## 5. Luồng dữ liệu Wikimedia → SQLite → nearby (giải thích cho giảng viên)

1. Client (app di động) gửi tọa độ GPS hiện tại lên `gateway-service`.
2. Gateway gọi `GET /locations/discover?latitude=...&longitude=...` của
   content-service (bán kính tìm kiếm rộng, mặc định 500m).
3. content-service kiểm tra cache nội bộ (theo ô tọa độ ~100m, 60 giây):
   - Có cache còn hạn -> trả luôn, `cache_hit=true`, không gọi Wikimedia.
   - Không có -> gọi Wikimedia API 2 lần: `geosearch` (tìm bài viết gần
     tọa độ) rồi `extracts` (lấy đoạn giới thiệu text thuần của các bài
     đó), ghép lại, bỏ bài thiếu tọa độ hoặc thiếu nội dung giới thiệu.
4. Với mỗi bài hợp lệ: UPSERT vào bảng `locations` — nếu đã từng lưu
   (trùng `source_page_id`) thì cập nhật nội dung/GPS mới nhất, chưa có
   thì tạo record mới với `source="wikimedia"`, `radius=80` (bán kính
   kích hoạt mặc định).
5. Kết quả trả về client gồm các `id` nội bộ của content-service — từ
   đây về sau B (dịch + audio) và C (gateway) chỉ cần dùng `id` này,
   không cần quan tâm lại tới Wikipedia nữa.
6. Khi client di chuyển và gửi GPS liên tục, gateway gọi
   `GET /locations/nearby?latitude=...&longitude=...&max_distance=...`
   (bán kính hẹp hơn, thường = activation radius của từng location) để
   biết đang đứng gần địa điểm nào trong số đã lưu, từ đó quyết định
   kích hoạt phát audio.

## 6. Sẵn sàng cho B và C

**Cho B (translate-audio-service):**
- Field vẫn dùng được như tuần 2: `description_vi`, `target_languages`.
- Field mới hữu ích: `description_source` (với record Wikimedia, đây là
  nội dung GỐC tiếng Việt lấy từ Wikipedia — B có thể dịch field này thay
  vì `description_vi` cho các record `source="wikimedia"`, vì `description_vi`
  của các record này hiện đang tạm set giống `description_source`, không
  phải do người dùng tự viết).
- `is_ready`: đề xuất B/C dùng cờ này để đánh dấu "đã dịch + tạo audio
  xong" sau khi B xử lý xong 1 record — hiện content-service mặc định
  `false` khi tạo mới.

**Cho C (gateway-service):**
- Dùng `/locations/discover` khi cần "khám phá" địa điểm mới quanh vị
  trí người dùng (ít gọi, có cache).
- Dùng `/locations/nearby` khi cần polling liên tục theo GPS thực time để
  quyết định kích hoạt — đây là lệnh gọi nội bộ (SQLite), nhanh, không
  đụng Wikimedia, có thể gọi thường xuyên.
- `distance_m` trong `/nearby` là khoảng cách Haversine thực tế (mét),
  C tự so với `radius` của từng candidate để quyết định activation,
  content-service không tự quyết định việc này.

## 7. Đề xuất commit

Đề xuất message (chưa commit/push — chờ xác nhận):

```
feat(content-service): tuan 3 - GPS, Wikimedia discover/nearby, migration, DELETE
```

Phạm vi: chỉ `content-service/` (đúng quy ước mỗi thành viên add riêng
thư mục của mình).
