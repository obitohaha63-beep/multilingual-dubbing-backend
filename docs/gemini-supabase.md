# GPS Audio Guide: Gemini và Supabase

Phần mới dùng các hàm thông thường, chia theo trách nhiệm để dễ đọc và trình bày
trong đồ án. Vẫn có đúng ba service, giao diện GPS và Dashboard cũ.

## Các file đã thay đổi

| File | Mục đích |
| --- | --- |
| `content-service/main.py` | Endpoint điều phối mới, nạp địa điểm, trạng thái chờ, đóng HTTP pool khi dừng, bảo toàn địa điểm đã có thuyết minh. |
| `content-service/models.py` | Kết nối PostgreSQL và model `Narration`, vẫn hỗ trợ SQLite cho test/CRUD cũ. |
| `content-service/narration_store.py` | Nhận lease nguyên tử, ghi checkpoint có kiểm tra token, phục hồi MP3 mất. |
| `content-service/narration_clients.py` | Làm sạch nguồn, gọi Gemini/TTS/Storage, timeout và retry, kiểm tra kết quả. |
| `content-service/narration_pipeline.py` | Luồng tạo tuần tự, cache hit, giới hạn task, phiên bản và giọng đọc. |
| `content-service/import_sqlite.py` | Chép địa điểm cũ sang PostgreSQL, giữ ID, dừng khi trùng ID có dữ liệu khác. |
| `content-service/requirements.txt` | Thêm driver Psycopg cho PostgreSQL. |
| `gateway-service/main.py` | Gọi Content thay cho TTS khi thuyết minh, giữ signed URL, chuyển trạng thái chờ. |
| `gateway-service/client/app.js` | Hiển thị đang xử lý, không đọc nhầm mô tả nguồn, hiển thị văn bản cuối cùng. |
| `translate-audio-service/main.py` | Endpoint `/narrate` cũ chỉ chuyển tiếp, giới hạn TTS đồng thời, nhận voice. |
| `translate-audio-service/tts.py` | Xác thực voice, đưa voice đã chọn vào cache key và Edge TTS. |
| `translate-audio-service/requirements.txt` | Đồng bộ FastAPI hiện hành với các service khác, bỏ phụ thuộc httpx2 không được code gọi trực tiếp. |
| `render.yaml`, `render.Dockerfile`, `supervisord.conf`, `.env.example` | Cấu hình và chú thích triển khai Supabase/Gemini, giữ ba process. |
| `docs/supabase-schema.sql` | Schema, unique/check constraints, index, RLS và bucket private. |
| `content-service/test_narration_pipeline.py`, `test_postgres_claim.py` | Kiểm tra luồng mới và test PostgreSQL thật tùy chọn. |
| `gateway-service/test_gateway_service.py`, `tests/test_client_logic.test.js` | Kiểm tra signed URL và việc chờ kết quả. |
| `translate-audio-service/tests/test_translate_audio.py` | Giữ test Edge TTS/localization; thay test điều phối cũ bằng test chuyển tiếp. |
| `tests/run_checks.py`, `tests/load_narration.py` | Chạy kiểm thử riêng từng service và thử tải mô phỏng. |
| `docs/load-results.json`, `docs/gemini-supabase.md`, `README.md`, `.gitignore` | Kết quả đo, hướng dẫn bàn giao, liên kết tài liệu và bỏ qua dữ liệu test tạm. |

## Luồng hoạt động

1. Gateway giữ nguyên việc tìm địa điểm theo GPS, rồi gọi `POST Content /narrate`
   với `location_id`, `target_lang`, `voice` (có thể bỏ qua để dùng giọng mặc định).
2. Content tìm bản thuyết minh trong PostgreSQL của Supabase theo địa điểm,
   ngôn ngữ, giọng đọc và phiên bản. Với bản `ready`, kiểm tra metadata MP3 trên
   Storage rồi ký URL có hạn một giờ. Không gọi Wikimedia, Gemini hay TTS.
3. Khi thiếu dữ liệu, Content nhận quyền tạo bằng thao tác nguyên tử trong database.
   Trả HTTP 202 ngay sau bước này, không chờ toàn bộ quá trình tạo âm thanh.
4. Content lấy đoạn giới thiệu Wikipedia theo page ID; địa điểm nhập tay dùng tên
   chính xác. Làm sạch HTML, chú thích, phần tham khảo rồi lưu checkpoint nguồn.
   Nguồn thiếu/rỗng: trả trạng thái lỗi, không bịa thêm nội dung.
5. Gemini viết lời thuyết minh bằng ngôn ngữ yêu cầu. Kiểm tra kết thúc thành công,
   văn bản thuần, độ dài 20–1200 ký tự; chuẩn hóa khoảng trắng một lần rồi lưu.
6. Content gửi chính văn bản đã lưu đến `POST Translate Audio /text-to-speech`.
   Edge TTS tạo MP3; Content kiểm tra voice và hash của văn bản trả về.
7. Content tải MP3 tối đa 2 MB từ API nội bộ, kiểm tra header MP3, upload lên
   bucket riêng tư. Chỉ sau khi Storage xác nhận file tồn tại mới ghi `ready`.
8. Giao diện kiểm tra lại qua detect khoảng 10 giây/lần khi đang theo dõi GPS và
   có fix hợp lệ. `processing`/`error` không kích hoạt đọc mô tả nguồn.
   Sau khi dừng GPS, công việc đã nhận vẫn có thể hoàn tất; lần truy cập sau dùng lại.

MP3 hoàn tất phát thẳng từ Supabase, không đi qua Gateway. `/audio` và API debug
cũ vẫn hoạt động. `/narrate` cũ của Translate Audio chỉ chuyển tiếp về Content;
`/localized-content` là API tương thích cũ, không tham gia luồng mới.

## Database và chống trùng

Chạy [supabase-schema.sql](supabase-schema.sql) trong SQL Editor của Supabase
**trước khi deploy**. Script dành cho schema mới của tính năng này, có thể chạy
lại. Nếu dự án Supabase đã có bảng cùng tên với cấu trúc khác, cần đối chiếu trước;
`CREATE TABLE IF NOT EXISTS` không tự nâng cấp các cột khác cấu trúc.

- `locations`: giữ cấu trúc hiện tại, chuyển từ SQLite sang PostgreSQL khi có
  `DATABASE_URL`. ID, GPS, tên, nội dung và URL nguồn đều được lưu lâu dài.
- `narrations`: mỗi bản/phiên bản một dòng, chứa snapshot địa điểm, nguồn đã làm
  sạch, văn bản Gemini, hash, đường dẫn MP3, trạng thái, lease và thời gian retry.
  Không lưu bytes MP3 trong PostgreSQL. Không cần bảng jobs riêng vì chính dòng
  narration đã lưu tiến độ công việc.
- `narration_cache`: chỉ giữ để tương thích API cũ. TTL của bảng cũ không tác động
  tới bảng `narrations` mới.

UNIQUE `(location_id, language, voice, content_version)` và `INSERT ON CONFLICT`
ngăn tạo hai dòng. `UPDATE ... WHERE ... RETURNING` chỉ cho một worker chuyển
pending/failed/processing hết hạn sang processing. Transaction kết thúc trước
khi gọi API ngoài. Không dựa vào khóa RAM để chống trùng giữa các instance.

Lease 240 giây; tổng thời gian một lượt tạo tối đa 180 giây. Checkpoint cần đúng
token và lease còn hạn; worker cũ không ghi đè được worker mới. Thời gian dùng UTC.
Giới hạn mặc định hai job đồng thời **mỗi process Content**, hai lượt TTS mỗi
process Translate Audio. Chạy một worker/service theo cấu hình Render hiện tại.
Nếu nhân số instance, tổng số job có thể tăng theo số instance; khóa theo bản
thuyết minh vẫn có hiệu lực toàn database.

Retry HTTP tối đa ba lần khi lỗi mạng/429/5xx, backoff 0,5 và 1 giây; lỗi 4xx khác
không retry. Job có tối đa ba lượt tạo, chờ 10 rồi 20 giây giữa các lượt. Request
polling tiếp theo thực hiện retry khi đến hạn. Không có worker chạy nền riêng.
Restart sau khi lấy lease: chờ lease hết hạn rồi request mới tiếp tục checkpoint.
Nếu upload đã xong nhưng chưa ghi ready, đường dẫn đã lưu giúp tìm lại MP3.
Nếu TTS đã được checkpoint và file tạm còn tồn tại, upload retry không gọi TTS lại.
Nếu file tạm mất sau restart, chỉ tạo lại TTS từ văn bản Gemini đã lưu.
Kết quả API ngoài thành công nhưng chưa kịp checkpoint khi process chết vẫn có
thể phải gọi lại API đó; không cam kết exactly-once với nhà cung cấp bên ngoài.

Sau ba lượt lỗi, xem `error_code` trong Supabase. Sửa cấu hình/nguyên nhân trước,
rồi quản trị viên có thể thử lại đúng job bằng SQL sau (thay ID thực tế):

```sql
update public.narrations
set status = 'pending', attempts = 0, retry_at = null,
    lease_token = null, lease_until = null, error_code = null,
    updated_at = timezone('utc', now())
where id = 'ID_CAN_THU_LAI' and status = 'failed';
```

Không xóa các phiên bản cũ do hết hạn cache. Thay `CONTENT_VERSION` khi muốn
biên soạn lại; thay nguồn, tên/GPS hoặc cấu hình Gemini/TTS cũng tạo khóa phiên
bản mới. `last_synced_at` và `is_ready` không tạo version mới. Xóa địa điểm đã có
thuyết minh trả 409 để tránh mất dữ liệu; cần chính sách lưu trữ riêng trước khi xóa.

## Cấu hình Supabase và Render

1. Tạo project Supabase. Chạy SQL migration; bucket mặc định `narration-audio`
   được tạo **private**, chỉ nhận `audio/mpeg`, tối đa 2 MB. Nếu bucket đã có,
   kiểm tra lại Public = OFF và gỡ các policy public cũ. Nếu đổi tên bucket trong
   biến môi trường, tự tạo bucket cùng giới hạn tương ứng.
2. Trong Supabase Connect, sao chép PostgreSQL Session pooler connection string
   vào `DATABASE_URL`, dùng driver `postgresql+psycopg://`, giữ `sslmode=require`.
   URL-encode mật khẩu nếu có ký tự đặc biệt. Psycopg tắt prepared statements để
   tương thích pooler. Pool trong Content tối đa năm kết nối/process.
3. Cấu hình `SUPABASE_URL`, `SUPABASE_SECRET_KEY` (backend secret key hoặc JWT
   service_role cũ), `SUPABASE_AUDIO_BUCKET`, `GEMINI_API_KEY`, `GEMINI_MODEL`.
   Mẫu dùng `gemini-3.8-flash`; có thể đổi sang model có quyền sử dụng trong
   project. Giá trị mẫu không bảo đảm quota.
4. Các tùy chọn: `CONTENT_VERSION=v1`, `NARRATION_TARGET_CHARS=900` (100–1200),
   `NARRATION_MAX_CONCURRENT=2`, `TTS_MAX_CONCURRENT=2`,
   `TTS_CONFIG_VERSION=v1`, `TTS_TIMEOUT_SECONDS=25`,
   `TTS_VOICE_VI/EN/JA`, `TTS_ALLOWED_VOICES_VI/EN/JA` (danh sách cách nhau dấu phẩy).
   Cấu hình giọng phải giống nhau ở Content và Translate Audio. Giữ biến Gateway,
   Wikimedia và Admin đang dùng. Danh sách mẫu nằm trong `.env.example`.
5. Chỉ Content sử dụng Gemini/Supabase. Không đưa các key vào HTML/JS, response
   hay log. Không tạo policy đọc/ghi database cho anon/authenticated. Backend kết
   nối bằng tài khoản PostgreSQL riêng có quyền cần thiết hoặc tài khoản từ Connect.
   RLS đang chặn Data API phía trình duyệt; Storage URL được ký bởi backend.
6. Nếu có địa điểm SQLite cũ cần giữ, chạy công cụ `import_sqlite.py` trước khi
   chuyển ứng dụng. Công cụ chỉ thêm địa điểm vào DB Supabase, không đánh dấu MP3
   cũ là ready. Dữ liệu nguồn SQLite được giữ nguyên. Các narration mới sẽ được
   tạo theo luồng mới khi yêu cầu. Không dùng lại ID cho địa điểm khác.
7. Deploy bằng `render.Dockerfile`/`render.yaml`, vẫn chạy ba process qua Supervisor.
   Kiểm tra `/services-health`, mở `/app/`, bật GPS và chờ `audio_ready`.
   Gọi lại cùng địa điểm/ngôn ngữ để xác nhận cache hit. Restart Render và thử lại.

Chạy local bằng Compose vẫn cần cấu hình Supabase cho luồng mới. Khi không có
`DATABASE_URL`, SQLite chỉ phục vụ CRUD/test cũ, `/narrate` mới trả 503 rõ ràng.
Compose dùng chung env_file như trước; các secret chỉ được code Content đọc.
Trên Render, các process cùng container thừa hưởng môi trường chung như thiết kế
Supervisor hiện tại. `DB_PATH` và `AUDIO_DIR` là dữ liệu tạm; analytics Dashboard
vẫn dùng SQLite cũ nên **số liệu analytics có thể mất sau redeploy**. Địa điểm,
thuyết minh và MP3 mới không phụ thuộc các đường dẫn tạm này.

## Kiểm thử và giới hạn

Kết quả đã chạy trên máy phát triển: **174 test Python đạt**, **10 test JavaScript
đạt**, **1 test PostgreSQL thật bỏ qua** vì chưa có `TEST_POSTGRES_URL`.
Trong đó Content: 53 test cũ và 22 test luồng mới; Gateway/Admin: 52; TTS: 47.
Các cảnh báo deprecation từ thư viện kiểm thử không làm test thất bại.
Kiểm tra cú pháp Python và `git diff --check` cũng đã qua.

Chạy `python tests/run_checks.py` trong môi trường có requirements của cả ba
service và Node.js. Bộ kiểm thử chạy riêng từng service tránh trùng module `main`.
Tests dùng file SQLite thật và giả lập API ngoài; kiểm tra cache, concurrency qua
hai bộ điều phối/thread, ngôn ngữ/giọng/version riêng, lỗi từng API, retry tiếp tục
checkpoint, token cũ không ghi đè, MP3 mất và tái sử dụng sau khởi tạo pipeline mới.

`test_postgres_claim.py` kiểm tra 20 kết nối với PostgreSQL thật khi đặt
`TEST_POSTGRES_URL` trỏ tới **database test**, không dùng production. Nó tạo/xóa
một schema ngẫu nhiên riêng. Không có cấu hình thì báo skip.

Chạy `python tests/load_narration.py` để đo 5, 10, 20 người dùng mô phỏng trong
ba tình huống: cache sẵn, cùng một bản mới, nhiều bản mới khác nhau. Kết quả ghi
trong [load-results.json](load-results.json): thời gian phản hồi đầu tiên, thời
gian hoàn tất, RAM tiến trình, CPU, lỗi, cache và số lần gọi mỗi API.
Đây là microbenchmark cục bộ với SQLite và API giả lập không có độ trễ; **không
phải tải trên Render/Supabase/Edge thực**. CPU trên các lượt rất ngắn có sai số
lớn do độ phân giải bộ đếm; RAM là toàn bộ tiến trình kiểm thử. Chưa đo tài nguyên
của ba service thật. Không suy ra số người dùng Render Free có thể phục vụ.

Kiểm tra văn bản tự động xác nhận định dạng/độ dài/kết thúc, chưa chứng minh được
mọi câu Gemini viết đều đúng dữ kiện hoặc đúng ngôn ngữ. Prompt yêu cầu chỉ dùng
nguồn và không suy đoán; cần nghe/đọc rà soát mẫu ở cả ba ngôn ngữ khi nghiệm thu.
Kiểm tra cache MP3 dùng metadata kích thước và file upload được kiểm tra header,
không tải lại toàn bộ file mỗi cache hit để giải mã kiểm tra âm thanh.

Các việc cần kiểm chứng trên tài khoản thật: migration PostgreSQL, quyền bucket,
quota Gemini, truy cập Edge TTS, phát/seek trên điện thoại, phục hồi sau redeploy,
và tải cả ba service tăng dần 5/10/20 với Render metrics.

Tài liệu API dùng khi triển khai:
[Gemini generateContent](https://ai.google.dev/api/generate-content),
[Gemini models](https://ai.google.dev/gemini-api/docs/models),
[Supabase Storage API](https://supabase.github.io/storage/),
[Storage client chính thức](https://github.com/supabase/storage-js/blob/main/src/packages/StorageFileApi.ts).
