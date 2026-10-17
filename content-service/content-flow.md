# Luồng dữ liệu của content-service (Tuần 3 → 5)

Content (A, port 8001) là **chủ sở hữu duy nhất của SQLite**. Các service
khác chỉ nói chuyện với nó qua REST.

## 1. Hai loại dữ liệu trong SQLite

| Bảng | Chứa gì | Ai ghi |
|---|---|---|
| `locations` | địa điểm: tên, GPS, `radius`, nguồn Wikimedia, `is_ready` | Content (CRUD, discover) |
| `narration_cache` | text thuyết minh theo ngôn ngữ + tên file MP3 + trạng thái | B ghi qua `PUT`, Content đọc/xóa |

MP3 **không** nằm trong SQLite: file nằm ở volume của B, SQLite chỉ lưu
`audio_filename`.

## 2. Luồng khám phá (Tuần 3, chốt Tuần 5)

```
Gateway ── GET /locations/nearby ──▶ Content ── chỉ đọc SQLite (Haversine) ──▶ candidates
   │ (chưa có candidate nào trong radius kích hoạt)
   └─ GET /locations/discover ─────▶ Content
                                         │
              ┌──────────────────────────┼───────────────────────────────┐
              ▼ cache RAM 60s còn hạn?   ▼ hết hạn/chưa có               │
        trả luôn                    Wikimedia geosearch + extracts        │
   served_from=memory_cache              │ thành công          │ LỖI      │
   (kể cả ô rỗng)                        ▼                     ▼          │
                              upsert vào SQLite        SQLite có bài Wikimedia
                              served_from=wikimedia    đã lưu trong radius?
                                                       ├─ có  → 200, served_from=sqlite,
                                                       │        giữ metadata cũ, kèm upstream_error
                                                       └─ không → 503 detail.code
                                                                  (timeout|rate_limited|upstream_error)
```

- `discovery radius` 500m chỉ để TÌM bài; `activation radius` (cột `radius`, mặc định
  80m) mới quyết định phát. Content chỉ trả `distance_m` và `radius`; Gateway so sánh.
- `/nearby` **không bao giờ** gọi Wikimedia; `/discover` mới gọi (và có cache).
- Lỗi Wikimedia **không bao giờ** biến thành danh sách rỗng: 429 → `rate_limited`,
  không phải "không có địa điểm".
- Cache hit / phục vụ từ SQLite **không** đổi `last_synced_at`.

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

 Cache hỏng / MP3 mất / hết hạn cần loại bỏ:
 6. DELETE /locations/{id}/narrations/{lang}  → idempotent (key không có vẫn 200, deleted=false)
```

Lỗi TTS: dừng ở bước 2, text vẫn được lưu để client đọc bằng giọng của thiết bị.

## 4. Khi nào cache bị xóa / vô hiệu

| Sự kiện | Kết quả |
|---|---|
| quá `expires_at` (mặc định 7 ngày) | `GET` trả `cache_hit=false`; không tính vào `is_ready` |
| `PUT /locations/{id}` đổi nội dung nguồn hoặc tọa độ | xóa mọi narration của địa điểm, `is_ready=false` |
| discover fetch thật, bài wiki đổi nội dung/tọa độ | như trên |
| `DELETE /locations/{id}` | xóa narration của địa điểm (không để dòng mồ côi) |
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

## 6. Ai sở hữu dữ liệu nào (data ownership)

| Dữ liệu | Chủ sở hữu | Nơi lưu | Người khác truy cập bằng |
|---|---|---|---|
| Địa điểm, GPS, radius, nguồn Wikimedia | **Content (A)** | SQLite `locations` | REST của Content |
| Text narration + metadata audio (cache) | **Content (A)** lưu, **B** quyết định nội dung | SQLite `narration_cache` | `GET/PUT/DELETE .../narrations/{lang}` |
| File MP3 | **B** | volume `audio_files/` của B | Gateway proxy `/audio/<filename>` |
| Trạng thái đã phát trong một lần ghé | **Client (C)** | bộ nhớ trình duyệt | — (Content không biết) |

**Vì sao B nói chuyện với Content qua HTTP, không mở chung file SQLite:**
1. **Một nơi duy nhất chịu trách nhiệm tính đúng đắn.** Khóa ngoại, unique, TTL,
   invalidation và tính `is_ready` đều nằm trong code Content. Nếu B ghi thẳng vào file,
   mọi quy tắc đó bị bỏ qua và dữ liệu dễ lệch.
2. **SQLite không hợp để nhiều tiến trình/container cùng ghi.** Hai container cùng mở
   một file qua volume dễ gặp `database is locked`, và khóa file trên volume chia sẻ
   không đáng tin cậy.
3. **Đổi schema/migration không làm vỡ B.** B chỉ phụ thuộc vào contract JSON; Content
   có thể thêm cột hay đổi cách lưu mà B không cần sửa.
4. **Ranh giới rõ ràng, dễ test.** B test bằng cách mock các endpoint của Content.
