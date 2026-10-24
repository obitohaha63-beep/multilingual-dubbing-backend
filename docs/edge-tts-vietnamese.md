# Sửa dự phòng giọng Việt và kết quả kiểm tra thực

## Bằng chứng và nguyên nhân

Log Render được cung cấp: edge-tts 7.3.1, Hoài Mỹ không trả audio cả ba lần,
`/text-to-speech` 503, Gemini đã tạo văn bản hợp lệ. Code cũ gửi lại ba lần
cùng voice, không thử Nam Minh. Đây là thiếu sót đã sửa; không có bằng chứng cần
thay Gemini, API key Gemini hay bỏ dấu tiếng Việt.

Kiểm tra ngày 24/10/2026 cho thấy lỗi **không xảy ra với mọi request**:

| Nơi thử | Giọng / luồng | Kết quả thực |
| --- | --- | --- |
| Máy phát triển, câu Việt 79 ký tự | Hoài Mỹ, một phiên | NoAudioReceived sau 2.426,1 ms, không có MP3 |
| Máy phát triển, cùng câu | Nam Minh, một phiên | NoAudioReceived sau 2.664,2 ms, không có MP3 |
| Máy phát triển, đối chứng bằng câu Anh | Jenny | MP3 26.208 byte, 956,3 ms |
| Máy phát triển, đối chứng bằng câu Nhật | Nanami | MP3 26.208 byte, 554,7 ms |
| Render `/debug/text-to-speech`, câu Việt mới | Hoài Mỹ | HTTP 200, cache_hit=false, 3,97 giây; MP3 GET 200, 32.688 byte, 5,448 giây |
| Render, câu Anh mới | Jenny | HTTP 200, cache_hit=false; MP3 32.832 byte, 5,472 giây |
| Render, câu Nhật mới | Nanami | HTTP 200, cache_hit=false; MP3 38.448 byte, 6,408 giây |
| Render, GPS của địa điểm ID 1, ngôn ngữ vi | detect → Content `/narrate` → TTS → Supabase | processing ở 5 lượt đầu, audio_ready ở lượt 6; văn bản 493 ký tự, MP3 157.392 byte, 26,232 giây |
| Render, gọi lại cùng địa điểm/ngôn ngữ | Cache | audio_ready, cache_hit=true, lang=vi, URL thuộc Supabase Storage có chữ ký |

Các MP3 thành công được tải và đọc bằng bộ phân tích MPEG, có thời lượng dương.
Không đưa signed URL/token vào log hay tài liệu. Kiểm tra health trên Render:
Gateway, Content và Translate Audio đều báo ok. Thời gian ở bảng là thời gian
đo trong lần thử, không phải cam kết độ trễ.

Endpoint TTS local với policy mới và Edge thật: lần 1 Hoài Mỹ NoAudioReceived
2.609,1 ms; lần 2 Nam Minh NoAudioReceived 2.538,8 ms; lần 3 Nam Minh
NoAudioReceived 2.418,0 ms. Kết quả HTTP 503, retryable=false, **0 MP3 được publish**.
Lần 2 chuyển voice ngay; lần 3 dùng phiên mới sau backoff hữu hạn.

Render đang chạy **bản trước thay đổi này**. Endpoint debug trên bản đó chỉ nhận
text/lang, không cho chọn riêng voice, nên chưa xác minh trực tiếp Nam Minh trong
container Render. Không thể nói "Render chặn cả hai voice". Cũng không thể suy
từ việc voice có trong catalog sang việc mọi request synthesis đều thành công.

Khoanh vùng từ các phép thử: lỗi phụ thuộc voice/phiên tổng hợp hoặc môi trường
truy cập dịch vụ Edge; không phải mất kết nối toàn bộ trên Render tại thời điểm
kiểm tra. Chưa có bằng chứng để kết luận cụ thể Microsoft rate-limit, chặn IP
hay hỏng backend nào. [Issue của edge-tts](https://github.com/rany2/edge-tts/issues/473)
ghi nhận triệu chứng tương tự; đó là thông tin tham khảo, không phải log của ứng dụng.

## Cơ chế sửa

- Giữ voice ưu tiên theo `TTS_VOICE_VI` hoặc voice được yêu cầu hợp lệ.
- Nếu Hoài Mỹ gặp NoAudioReceived: chuyển ngay sang Nam Minh, không chờ backoff.
  Nếu Nam Minh cũng không có audio: thử Nam Minh thêm một phiên, tổng tối đa ba
  lần. Khi ưu tiên Nam Minh thì thứ tự đảo lại: Nam Minh → Hoài Mỹ → Hoài Mỹ.
- Mỗi lần tạo `edge_tts.Communicate` mới. Nếu catalog chỉ còn một voice hợp lệ,
  không gọi voice đó ba lần cho cùng lỗi no-audio. Không dùng voice Anh đọc thay.
- Catalog Edge được kiểm tra khi cache miss tiếng Việt: timeout 2 giây, cache
  danh sách một giờ, cache lỗi một phút. Voice không được liệt kê bị bỏ qua. Nếu
  không lấy được catalog, vẫn thử các voice cấu hình để sự cố endpoint catalog
  không làm mất hoàn toàn synthesis. Danh sách hợp lệ không bảo đảm audio.
- Giữ Unicode, dấu và dấu câu; chỉ chuẩn hóa khoảng trắng như trước. Input rỗng,
  sai kiểu, chỉ có ký tự vô hình hoặc control/surrogate không hợp lệ bị từ chối
  trước khi gọi Edge. Không xóa dấu hoặc cắt lời thuyết minh.
- `Communicate.save()` thu toàn bộ stream. File tạm chỉ được rename thành MP3 sau
  khi Mutagen đọc được MPEG frames, thời lượng dương, không sketchy và kích thước
  128 byte–2 MB. ID3 header đơn lẻ/rỗng/dữ liệu hỏng không được publish. Lỗi hoặc
  cancellation dọn file tạm; chỉ thành công mới có URL audio.
- Log Uvicorn ghi voice, version, attempt, elapsed ms, độ dài/hash và category:
  no_audio, timeout, websocket, network, http, invalid_audio, fixed_error.
  HTTP status ghi khi có; không tự bịa HTTP status cho NoAudioReceived. Không log
  text, exception message hoặc WebSocket URL vì URL có thể chứa token.
- Response lỗi trả `cause`, `retryable`, danh sách `attempts` và thông báo voice
  đã thử. No-audio hết lượt là lỗi dừng, không để polling gọi lại vô hạn.
  Hàng đợi bận/timeout/mạng/HTTP tạm thời giữ khả năng retry hữu hạn hiện có.

## Content, Supabase và cache

Content trước đây từ chối mọi voice khác voice yêu cầu. Bản sửa chấp nhận hai
voice Việt đúng allowlist và vẫn đối chiếu text_hash, requested_voice. Voice Anh
không được chấp nhận làm fallback cho Việt. Không thay code compose Gemini.

Tên MP3 local chứa hash của text, ngôn ngữ và **voice thực tế**. Khóa job Supabase
giữ voice yêu cầu để lần polling sau tìm lại đúng job, không biên soạn lại Gemini.
Voice thực tế được checkpoint trong JSON có sẵn:
`location_snapshot._tts = {voice, requested_voice}`. Không thêm bảng/cột/migration.
Response Content dùng voice thực tế và thêm requested_voice/voice_fallback;
`is_fallback` của ngôn ngữ không bị đổi nghĩa. Row cũ chưa có metadata vẫn dùng
voice cũ. Restart không làm mất thông tin Nam Minh đã tạo MP3.

Văn bản/hash, upload, kiểm tra Storage và ghi ready tiếp tục theo luồng cũ. Khi
TTS thất bại, giữ final_text; không gọi Gemini lại và không ghi ready giả.

## Kiểm tra sau deploy

Build lại image để cài thêm Mutagen. Giữ edge-tts 7.3.1 và TTS_VOICE_VI hiện có.
Không thay Gateway, giao diện, Gemini hoặc cấu hình Supabase.

Trong Render Shell, chạy:

```sh
cd /app/translate-audio-service
python probe_edge_tts.py --controls --api-url http://127.0.0.1:8002
```

Lệnh này kiểm tra catalog, thử riêng mỗi voice Việt một lần, thử Anh/Nhật bằng
ngôn ngữ tương ứng làm đối chứng, rồi gọi endpoint TTS với cơ chế fallback thật.
Nó không gọi Gemini hoặc sửa job. Mã thoát khác 0 nếu không tạo được MP3 Việt.
File kiểm tra được dọn sau khi xác minh.

Kiểm tra luồng đầy đủ bằng địa điểm thật:

```sh
cd /app/content-service
python diagnose_narration.py --location-id 1 --language vi
```

Job lỗi cũ vẫn có thể đang ở trạng thái failed, dù Edge đã phục hồi. Sau khi
kiểm tra nguyên nhân và deploy bản sửa, reset **đúng job** trong Supabase để tiếp
tục từ final_text đã lưu; không xóa văn bản/đổi CONTENT_VERSION:

```sql
update public.narrations
set status='pending', attempts=0, retry_at=null, error_code=null,
    lease_token=null, lease_until=null, updated_at=timezone('utc', now())
where id='JOB_ID_THUC_TE' and language='vi' and status='failed'
  and error_code in ('tts_no_audio', 'tts_unavailable', 'tts_failed');
```

Không reset hàng loạt hoặc lặp lệnh khi cả hai voice còn lỗi. Nếu cả hai vẫn
NoAudioReceived trên Render trong khi đối chứng Anh/Nhật thành công, ghi đúng
"không nhận audio từ các voice Việt trong phép thử này", giữ final_text và báo
503. Không kết luận bị chặn IP nếu không có bằng chứng.

Nếu cần nguồn TTS Việt ổn định hơn, có thể tích hợp Azure Speech chính thức
ngay trong Translate Audio Service, vẫn giữ ba service. Microsoft liệt kê cả
[Hoài Mỹ và Nam Minh cho tiếng Việt](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts).
Phương án đó cần tài khoản/key/region và kiểm tra quota/chi phí, chưa được tự
động thêm hoặc kích hoạt trong bản sửa này.

## Kiểm thử và file thay đổi

Đã chạy: Content 105 test đạt, Gateway/Admin 52 test đạt, TTS 70 test đạt —
tổng 227 test Python đạt; 12 test JavaScript đạt. Một test PostgreSQL thật bỏ
qua vì chưa có TEST_POSTGRES_URL. Kiểm tra cú pháp và git diff --check đạt.

- `translate-audio-service/tts.py`: policy voice, catalog, validation MP3/input và log.
- `translate-audio-service/main.py`: metadata voice/attempts, lỗi an toàn và queue timeout.
- `translate-audio-service/requirements.txt`: thêm Mutagen, giữ nguyên edge-tts.
- `translate-audio-service/probe_edge_tts.py`: chẩn đoán thật trên Render.
- `content-service/narration_clients.py`: chỉ sửa hàm speech để nhận fallback Việt.
- `content-service/narration_pipeline.py`: lưu/trả voice thực tế bằng JSON checkpoint sẵn có.
- Hai file test TTS/pipeline và fixture MP3 thật: test ưu tiên cả hai voice,
  H→N thành công, H→N→N hết lượt, giữ dấu, cache, catalog lỗi/thiếu voice,
  MP3 hỏng, phân loại transport, không log token, dọn partial file và queue bận.
- Hai test tích hợp gọi route Content `/narrate` qua route TTS `/text-to-speech`
  thật trong ASGI, dùng SQLite/MP3 parser thật và chỉ giả lập provider/Storage;
  kiểm tra upload/cache/restart và không gọi lại Gemini khi TTS thất bại.

Các test giả lập xác minh cơ chế dự phòng, không chứng minh Nam Minh đã phát
thành công trên Edge thật. Bảng kết quả thực ở đầu tài liệu tách rõ điều đó.
