# Điều tra `gemini_invalid_text` và xác minh trên Render

## Kết luận từ source, giới hạn của bằng chứng

`content-service/narration_clients.py::check_text` là nơi tạo
`gemini_invalid_text`. Trước bản sửa, nó từ chối chuỗi dưới 20 hoặc trên 1.200
ký tự, Markdown, HTML, URL và chuỗi không có chữ cái. `compose` chỉ gọi hàm này
sau HTTP thành công, candidate có `finishReason=STOP` và ghép các text part
không phải `thought`. Vì vậy mã này **không tự chứng minh API key sai, model sai
hay lỗi Edge TTS**. Cần log phản hồi của đúng job để xác định điều kiện thực tế.

Các lỗi xác nhận được trong code cũ:

- Đầu ra ngắn nhưng có nghĩa (đặc biệt tiếng Nhật) bị loại bởi ngưỡng 20 ký tự.
- Không có candidates bị quy thành blocked ngay cả khi không có blockReason.
- Chuỗi nguồn bị cắt âm thầm ở 12.000 ký tự; kiểu dữ liệu và control characters
  chưa được kiểm tra đầy đủ trước khi gọi Gemini.
- Mọi job thất bại được thử lại qua polling, kể cả định dạng đầu ra cố định.
- Retry HTTP TTS chồng lên retry bên trong Edge; timeout HTTP 35 giây ngắn hơn
  ngân sách 3 × 25 giây + backoff của Edge trên Render, có thể bỏ phiên đang tạo
  âm thanh và gửi lại yêu cầu.
- Log thiếu token, độ dài đầu ra, lý do kiểm tra thất bại và chi tiết JSON lỗi.

Chưa có URL/quyền quản trị Render, log gốc hoặc khóa Gemini/Supabase trong môi
trường làm việc này. Chưa xác định được điều kiện cụ thể đã gây lỗi production,
chưa xác nhận biến môi trường thực tế trên Render và chưa deploy bản sửa.

## Truy vết dữ liệu

1. `content-service/main.py::wikimedia_discover` lấy địa điểm Wikipedia;
   `_upsert_wikimedia_location` lưu page ID, title, source language, URL và
   description. `/locations/nearby` có thể trả dữ liệu đã lưu/cache.
2. `gateway-service/main.py` tìm địa điểm trong phạm vi GPS, gọi Content
   `POST /narrate` bằng `location_id`, `target_lang`, `voice`. `/narrate` cũ ở
   Translate Audio chỉ chuyển tiếp; `/localized-content` không thuộc luồng mới.
3. `content-service/main.py::generate_narration` đọc snapshot từ DB; kiểm tra
   PostgreSQL và cấu hình Gemini/Storage. Thiếu cấu hình trả 503 trước khi gọi AI.
4. `narration_pipeline.py::request` tìm cache theo địa điểm/ngôn ngữ/voice/version;
   `narration_store.py::claim` nhận lease nguyên tử. Job mới trả 202, chưa phải
   kết quả thành công. Lỗi cố định đã lưu trả trạng thái error/503.
5. `NarrationClients.source` gọi **lại** `https://{vi|en|ja}.wikipedia.org/w/api.php`
   với `prop=extracts|info`, `exintro=1`, `explaintext=1`, `inprop=url`,
   `formatversion=2`, theo page ID hoặc tên bài chính xác. Vì đây là lần gọi khác,
   nearby 200 không chứng minh bước này thành công.
6. `clean_source` làm sạch markup/tham chiếu và khoảng trắng, kiểm tra kiểu,
   control characters, nguồn rỗng/quá dài; không cắt phần vượt giới hạn.
   Pipeline lưu `source_text` và `source_url` làm checkpoint.
7. `compose` kiểm tra lại nguồn đã lưu, gửi `systemInstruction.parts[].text` và
   `contents=[{role:user,parts:[{text:source}]}]`. Model giữ nguyên mặc định và
   `render.yaml`: **gemini-3.5-flash-lite**. Endpoint:
   `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`.
   Key đọc trực tiếp từ `GEMINI_API_KEY` môi trường, gửi qua header
   `x-goog-api-key`, không nằm trong URL. Không có model fallback tự động.
8. Đọc JSON thật, promptFeedback, finishReason; ghép các text part có thể đọc,
   bỏ phần thought. Phân biệt `gemini_blocked`, `gemini_empty_text`,
   `gemini_incomplete`, `gemini_invalid_response` và `gemini_invalid_text`.
   Chuỗi ngắn có chữ được chấp nhận; vẫn giữ trần 1.200 ký tự của TTS. Không cắt
   đầu ra, không xóa nội dung có nghĩa để ép qua kiểm tra.
9. Văn bản hợp lệ được lưu nguyên bản sau chuẩn hóa khoảng trắng cùng SHA256
   vào bảng `narrations` của PostgreSQL Supabase. Edge nhận đúng văn bản/hash/voice.
   MP3 thật được kiểm tra header và upload Supabase Storage; chỉ khi xác nhận file
   tồn tại mới ghi ready. Cache hit giữ nguyên logic ký URL và không gọi lại AI.

## Log và retry

Log `[Gemini]` là JSON theo event: `configuration`, `source_ready`, `request`,
`response`, `validated`, `input_rejected`, `output_rejected`, `api_error`,
`job_failed`. Tìm theo job_id để nối các bước; source_ready có page ID và độ dài.

Ghi model/endpoint, key_present (boolean), HTTP status, input/output length,
usageMetadata (nếu provider trả token), modelVersion/responseId, promptFeedback,
blockReason, finish reasons, thời gian phản hồi và JSON error từ Gemini.
`validation=too_long: ...` xác định chính xác lỗi độ dài; không suy đoán dựa
riêng vào `gemini_invalid_text`. Dữ liệu JSON sai cấu trúc ghi loại lỗi parser.
Log đi qua logger Uvicorn để hiện INFO trên Render. Không ghi prompt, văn bản
đầu ra, headers hay giá trị khóa; thông tin lỗi được che các secret đã biết,
key/token/authorization và mẫu Google API key.

Gemini retry tối đa 3 lần/request cho timeout/lỗi mạng, 429, 500/502/503/504,
backoff hữu hạn. Job transient có tối đa 3 lượt theo logic cũ (tối đa 9 HTTP
Gemini calls nếu cả ba lượt đều lỗi tạm thời); không bắt đầu lại Gemini khi
đã lưu final_text. Các lỗi đầu vào/JSON/blocked/empty/format/auth/model dừng
ngay, không được polling nhận lease lại, kể cả sau restart.

Content gửi TTS một lần/lượt job, timeout 90 giây. Edge retry tối đa 3 lần cho
NoAudioReceived sau validation, timeout, lỗi kết nối và HTTP tạm thời. Lỗi cố định
khác không retry. NoAudioReceived sau 3 lần được truyền kèm cause/retryable=false;
job dừng và giữ final_text, không gọi lại Gemini/Edge qua polling.

Prompt đặt mục tiêu có khoảng dự phòng dưới 1.200 ký tự, không đổi model/token
budget tùy tiện. Kết nối HTTP và checkpoint/cache được tái sử dụng như cũ.
Giao diện kiểm tra mỗi 3 giây khi generation đang pending/processing, vẫn giữ
10 giây ở trạng thái khác và tối đa một request đang chờ. Không cam kết thời gian
end-to-end khi upstream lỗi hay Render cold start. Chọn Việt/Anh/Nhật đổi nhãn,
trạng thái và thuộc tính truy cập giao diện tương ứng; không dịch máy hay thay
đổi nội dung Wikipedia/văn bản đã biên soạn trong trình duyệt.

## Xác minh trên Render sau khi deploy

Giữ nguyên ba process và các biến hiện có. Không đưa key vào câu lệnh hoặc gửi
key vào chat. Trong Render Shell:

```sh
cd /app/content-service
python diagnose_narration.py
python diagnose_narration.py --location-id 12 --language vi
```

Thay 12 bằng ID thực từ nearby. Lệnh đầu đọc **môi trường của container** và GET
metadata đúng model đang cấu hình: cần HTTP 200, `generateContent` trong methods.
Lệnh sau tạo/tiếp tục job thật theo logic hiện có, kiểm tra signed MP3 bằng GET
và header thực. Mã thoát khác 0 khi thất bại; không in URL có token hay văn bản.
Đây là gọi thật, có thể tiêu thụ quota. Chạy lại cùng tham số để xác nhận cache
hit; thử en/ja rồi restart và xác nhận cache vẫn dùng được.

Nếu job cũ đã failed do lỗi cố định, nó không tự chạy lại. Sau khi đọc log và sửa
nguyên nhân, có thể reset **đúng một job** để kiểm chứng (giữ checkpoint hợp lệ):

```sql
update public.narrations
set status='pending', attempts=0, retry_at=null,
    lease_token=null, lease_until=null, error_code=null,
    updated_at=timezone('utc', now())
where id='JOB_ID_THUC_TE' and status='failed';
```

Nếu chính source_text lưu trước đó sai, phải kiểm tra/sửa nguồn hoặc xóa riêng
checkpoint nguồn của đúng job trước khi thử lại. Không reset hàng loạt, không đổi
CONTENT_VERSION chỉ để che lỗi. So sánh log request/response/output_rejected của
job mới với job lỗi; chỉ kết luận nguyên nhân production sau khi có bằng chứng.

## File đã sửa

| File | Thay đổi |
| --- | --- |
| `content-service/narration_clients.py` | Kiểm tra nguồn/đầu ra, chẩn đoán an toàn, parsing Gemini và một lượt HTTP TTS. |
| `content-service/narration_pipeline.py` | Trả error ngay và không lên lịch retry cho lỗi cố định. |
| `content-service/narration_store.py` | Chỉ claim lại job có mã lỗi tạm thời, có hiệu lực qua restart. |
| `content-service/diagnose_narration.py` (mới) | Kiểm tra model/key môi trường và chạy luồng thật trong Render Shell. |
| `translate-audio-service/tts.py`, `main.py` | Retry theo loại lỗi, trả cause/retryable của TTS. |
| `gateway-service/client/app.js`, `index.html`, `i18n.js` (mới) | Poll 3 giây khi đang tạo và đồng bộ giao diện Việt/Anh/Nhật, giữ CSS. |
| `content-service/test_narration_pipeline.py`, `translate-audio-service/tests/test_translate_audio.py` | Test hồi quy validation/parsing/retry/log và lỗi TTS. |
| `tests/test_client_i18n.test.js` (mới), `tests/run_checks.py` | Test ngôn ngữ/polling và đưa vào bộ kiểm tra hiện có. |
| `docs/gemini-supabase.md`, tài liệu này (mới) | Cập nhật model/cơ chế retry và hướng dẫn xác minh. |

## Kiểm thử đã thực hiện

Kết quả các bộ đã chạy: Content CRUD/cache 53, pipeline 50, Gateway/Admin 52,
TTS 50 — tổng 205 test Python đạt; 12 test JavaScript đạt. Một test PostgreSQL
thật bỏ qua do thiếu TEST_POSTGRES_URL. Kiểm tra cú pháp và git diff --check đạt.

Kiểm thử SQLite thật + HTTP giả lập cho toàn luồng, với MP3 fixture có header:
cache/restart/lease/concurrency, nguồn sai kiểu/rỗng/control/quá dài, multipart
và thought-only, blocked/empty/malformed/STOP/MAX_TOKENS, HTTP lỗi và timeout,
che key trong log, không retry lỗi cố định, giữ final_text sau lỗi TTS và xác
nhận Storage trước ready. PostgreSQL thật bỏ qua khi thiếu TEST_POSTGRES_URL.

Đã kiểm tra giao diện trên Microsoft Edge headless ở viewport 390×844: chuyển
en → ja → vi, đúng heading/label/html lang, không tràn ngang, không lỗi JS và
giữ nguyên văn bản thuyết minh. Không đổi CSS/bố cục.

Thử upstream thật từ máy phát triển ngày 24/10/2026: không lấy được nguồn Wikimedia;
Edge TTS 7.3.1 với văn bản ngắn tiếng Việt hợp lệ vẫn trả NoAudioReceived cả ba
lần, dừng sau khoảng 10,62 giây, không có MP3. Điều này không chứng minh tình
trạng mạng của Render; chưa kiểm thử Gemini/Supabase thật vì thiếu cấu hình.

Tài liệu chính thức đã đối chiếu:
[Gemini generateContent](https://ai.google.dev/api/generate-content),
[danh sách model](https://ai.google.dev/gemini-api/docs/models).
