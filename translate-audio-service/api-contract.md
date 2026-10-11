# API Contract — translate-audio-service (Tuần 3)

Port: `8002`. Không dùng database. Gọi Wikimedia API để lấy nội dung bản địa hóa.

---

## Tổng quan

`translate-audio-service` cung cấp hai endpoint:

| Endpoint | Vai trò | Tuần |
|---|---|---|
| `POST /localized-content` | Lấy nội dung bản địa hóa Wikipedia (core flow) | Tuần 3 (mới) |
| `POST /translate` | Giả lập dịch văn bản (legacy, tương thích tuần 2) | Tuần 2 (giữ lại) |

**C (gateway-service) chỉ gọi `/localized-content` qua HTTP** — không import module Python của service này.

---

## Khái niệm: Bản địa hóa ≠ Dịch máy

> **Bản địa hóa (localization)**: Lấy bài viết *gốc* do cộng đồng bản ngữ viết trực tiếp trên Wikipedia phiên bản ngôn ngữ đó. Ví dụ: bài "Hanoi" trên **en.wikipedia.org** do người viết tiếng Anh biên soạn — không phải bản dịch tự động.
>
> **Dịch máy (machine translation)**: Phần mềm (Google Translate, DeepL, v.v.) tự chuyển ngữ từ văn bản gốc. Kết quả có thể không tự nhiên, không cập nhật theo ngữ cảnh bản địa, và có thể sai về tên riêng / thuật ngữ địa phương.

Service này dùng **langlinks API của Wikimedia** để tìm bài cùng chủ đề ở wiki ngôn ngữ đích — nghĩa là *luôn lấy bản bản địa hóa thật*, không bao giờ dịch máy.

---

## Allowlist ngôn ngữ

Chỉ chấp nhận mã ngôn ngữ: `vi`, `en`, `ja`.

Hostname Wikipedia (`vi.wikipedia.org`, `en.wikipedia.org`, `ja.wikipedia.org`) được xây dựng **chỉ từ allowlist này** — không bao giờ ghép hostname từ dữ liệu người dùng tùy ý.

---

## Endpoints

### `GET /health`

```json
{"service": "translate-audio-service", "status": "ok"}
```

---

### `POST /localized-content` *(Tuần 3 — Core flow)*

**Body (JSON):**

```json
{
  "source_title":   "Hà Nội",
  "source_lang":    "vi",
  "target_lang":    "en",
  "source_page_id": "12345"
}
```

| Trường | Kiểu | Ghi chú |
|---|---|---|
| `source_title` | string | Tiêu đề bài trên wiki nguồn (dùng để hiển thị, không dùng để tra cứu API) |
| `source_lang` | string | Ngôn ngữ wiki nguồn. Phải nằm trong allowlist `vi,en,ja` |
| `target_lang` | string | Ngôn ngữ wiki đích. Phải nằm trong allowlist `vi,en,ja` |
| `source_page_id` | string | pageid dạng số nguyên (chuỗi) của bài trên wiki nguồn |

Validate lỗi → `422 Unprocessable Entity`.

**Logic xử lý:**

1. Nếu `target_lang == source_lang`: lấy intro extract bài nguồn theo `source_page_id`.
2. Nếu khác ngôn ngữ:
   - Gọi `action=query&prop=langlinks&pageids=<source_page_id>&lllang=<target_lang>&lllimit=max&format=json&formatversion=2` trên wiki nguồn.
   - Lấy title từ langlink trả về (không đoán, không dịch máy).
   - Gọi `action=query&prop=extracts|info&titles=<title>&exintro=1&explaintext=1&inprop=url&redirects=1` trên wiki đích.
   - Dùng title chuẩn hóa và URL do API trả về.

**Response thành công (`found=true`, HTTP 200):**

```json
{
  "found": true,
  "requested_lang": "en",
  "target_lang": "en",
  "lang": "en",
  "target_title": "Hanoi",
  "text": "Hanoi is the capital of Vietnam...",
  "source_url": "https://en.wikipedia.org/wiki/Hanoi",
  "is_fallback": false,
  "reason": null
}
```

| Trường | Ghi chú |
|---|---|
| `found` | `true` nếu tìm được bài và có nội dung |
| `requested_lang` | `target_lang` mà caller yêu cầu |
| `target_lang` | `= requested_lang` (hiện tại không có fallback) |
| `lang` | Ngôn ngữ **thật** của `text` trả về (không gán nhãn sai) |
| `target_title` | Tiêu đề chuẩn hóa do Wikipedia API trả về (sau redirect) |
| `text` | Đoạn intro, whitespace chuẩn hóa, tối đa 1200 ký tự (cắt tại ranh giới câu) |
| `source_url` | URL bài trên wiki đích (do API trả về, không tự ghép) |
| `is_fallback` | Luôn `false` tuần này |
| `reason` | `null` khi `found=true` |

**Response không tìm được (`found=false`, HTTP 200):**

```json
{
  "found": false,
  "requested_lang": "en",
  "target_lang": "en",
  "lang": null,
  "target_title": null,
  "text": null,
  "source_url": null,
  "is_fallback": false,
  "reason": "target_language_unavailable"
}
```

`reason` có thể là:
- `"target_language_unavailable"` — không có langlink sang ngôn ngữ đích, hoặc page không tồn tại
- `"empty_extract"` — tìm được bài nhưng extract rỗng

**Lỗi mạng / upstream (HTTP 503):**

```json
{"detail": {"code": "timeout", "message": "Wikimedia API không phản hồi kịp thời"}}
```

`code` có thể là: `"timeout"` | `"rate_limited"` | `"upstream_error"`.

> **Quan trọng**: Service **không bao giờ** trả `found=false` khi nguyên nhân thật là Wikimedia đang lỗi. Lỗi mạng luôn trả `503`.

---

### `POST /translate` *(LEGACY — Tuần 2, giữ để tương thích)*

> ⚠️ **Deprecated**: Giữ lại để test/code tuần 2 không bị hỏng. **Không dùng trong core flow tuần 3 trở đi.** Không được nâng cấp bằng GoogleTranslator/deep-translator.

**Body:** `{"text": "...", "target_lang": "en"}`

**Response:** `{"translated_text": "[EN] <text gốc>"}` (giả lập)

---

## Wikimedia Client

- User-Agent: lấy từ env `WIKIMEDIA_USER_AGENT` (xem `.env.example`).
- Timeout: `httpx.Timeout(8.0, connect=4.0)`.
- Request tuần tự (không dùng async pool).
- Retry tối đa 1 lần khi gặp 429 (tôn trọng `Retry-After`, tối đa 3s) hoặc 5xx.
- Mọi lỗi mạng → `WikimediaError` → caller trả `503`.

---

## Cài đặt & Chạy

```bash
cd translate-audio-service
pip install -r requirements.txt
# Tạo .env từ .env.example rồi điền WIKIMEDIA_USER_AGENT
uvicorn main:app --host 0.0.0.0 --port 8002 --reload
```

## Test

```bash
cd translate-audio-service
pytest tests/test_translate_audio.py -v
```

Tất cả test dùng mock — **không gọi Wikimedia thật**.
