# Luồng dữ liệu của content-service (Tuần 3 + 4 + 5)

Content (A, port 8001) là **chủ sở hữu duy nhất của SQLite**. Các service
khác chỉ nói chuyện với nó qua REST.

## 1. Hai loại dữ liệu trong SQLite

| Bảng | Chứa gì | Ai ghi |
|---|---|---|
| `locations` | địa điểm: tên, GPS, `radius`, nguồn Wikimedia, `is_ready` | Content (CRUD, discover) |
| `narration_cache` | text thuyết minh theo ngôn ngữ + tên file MP3 + trạng thái | B ghi qua `PUT`, Content đọc/xóa |

MP3 **không** nằm trong SQLite: file nằm ở volume của B, SQLite chỉ lưu
`audio_filename`.

## 2. Luồng khám phá (Tuần 3)

```
Gateway ── GET /locations/nearby ──▶ Content ── SQLite (Haversine) ──▶ candidates
   │ (chưa có điểm kích hoạt)
   └─ GET /locations/discover ─────▶ Content ── cache 60s? ──hit──▶ dùng lại
                                         │ miss
                                         ▼
                                  Wikimedia geosearch + extracts
                                         │
                                         ▼
                              upsert vào locations (chống trùng theo pageid)
```

- `discovery radius` 500m chỉ để TÌM bài; `activation radius` (cột `radius`,
  mặc định 80m) mới quyết định phát. Content chỉ trả `distance_m`; Gateway so sánh.
- Cache hit của discovery **không** đổi `last_synced_at`.
- Wikimedia lỗi (429/timeout/5xx): nếu SQLite đã có bài Wikimedia trong bán kính
  thì trả dữ liệu cũ kèm `stale=true`; nếu không có thì `503` + `detail.code`.
  Không bao giờ biến lỗi upstream thành `candidates: []`. `/nearby` không bao
  giờ gọi Wikimedia.

## 3. Luồng narration (Tuần 4)

```
B nhận yêu cầu (location_id, target_lang)
 1. GET  /locations/{id}/narrations/{lang}
      ├─ cache_hit=true, audio_ready → B kiểm tra file MP3 → trả ngay
      ├─ cache_hit=true, text_ready  → B dùng lại text, chỉ thử TTS lại
      └─ cache_hit=false             → B lấy nội dung (Wikimedia / fallback)
 2. PUT  /locations/{id}/narrations/{lang}   (audio_status = text_ready)
 3. B chạy Edge TTS, lưu MP3 vào volume của B
 4. PUT  /locations/{id}/narrations/{lang}   (audio_status = audio_ready + audio_filename...)
 5. PATCH /locations/{id}/mark-ready         → Content tính lại is_ready từ cache
```

Lỗi TTS: dừng ở bước 2, text vẫn được lưu để client đọc bằng giọng của thiết bị.

## 4. Khi nào cache bị xóa / vô hiệu

| Sự kiện | Kết quả |
|---|---|
| quá `expires_at` (mặc định 7 ngày) | `GET` trả `cache_hit=false`; không tính vào `is_ready` |
| `PUT /locations/{id}` đổi nội dung nguồn hoặc tọa độ | xóa mọi narration của địa điểm, `is_ready=false` |
| discover fetch thật, bài wiki đổi nội dung/tọa độ | như trên |
| `DELETE /locations/{id}` | xóa narration của địa điểm (không để dòng mồ côi) |
| `DELETE /locations/{id}/narrations/{lang}` | B chủ động loại 1 cache hỏng; `is_ready` tính lại |
| chỉ đổi `name` / `target_languages` / `radius` | giữ cache |

## 5. Ba loại "cache" cần phân biệt

1. **Cache discovery** (trong RAM của Content, 60 giây): tránh gọi Wikimedia
   liên tục khi người dùng đứng yên. Mất khi restart service.
2. **Cache narration** (bảng `narration_cache`, 7 ngày): text + metadata audio
   đã dịch/tạo, **bền vững qua restart**, chia sẻ cho mọi người dùng cùng
   ngôn ngữ. Đây là "cache nội dung".
3. **Cache phát lại ở client** (trạng thái chống phát lặp trong trình duyệt):
   chỉ nhớ "lần ghé này đã phát `location_id + requested_lang` chưa". Content
   không biết và không quản lý cái này.

`is_ready` **không** thay thế cache theo ngôn ngữ: `is_ready=true` chỉ nói
"có ít nhất 1 ngôn ngữ đã có MP3". Muốn biết ngôn ngữ cụ thể đã sẵn sàng chưa
phải `GET .../narrations/{language}`.

## 6. Vì sao B gọi Content qua HTTP, không dùng chung file SQLite

- **Một chủ sở hữu**: chỉ Content mở `content.db`, nên chỉ Content giữ khóa
  ngoại, TTL, unique và invalidation. B/C ghi thẳng file thì có thể làm lệch
  `is_ready`, ghi dòng mồ côi hoặc bỏ qua validate `audio_filename`.
- **Không khóa chéo**: SQLite là 1 file, nhiều process ghi cùng lúc dễ `database is locked`.
- **Tách service**: mỗi service chạy trong container riêng; đổi schema chỉ sửa
  Content, B/C chỉ cần hợp đồng REST.
