# Đặc Tả Kiểm Thử Client Audio Player (tests/test_client_spec.md)

Tài liệu này quy định chi tiết các kịch bản kiểm thử (Test Specifications) cho chức năng phát âm thanh thuyết minh đa ngôn ngữ trên Web Client (`gateway-service/client/`).

---

## Danh Sách Kịch Bản Kiểm Thử Cốt Lõi

| Mã test | Tên kịch bản | Thao tác thực hiện | Kết quả mong đợi |
|---|---|---|---|
| **TC-01** | **Client Play Success (Phát MP3 thành công)** | 1. Mở giao diện `/app/`.<br>2. Bật *Kiểm tra Audio Player (Debug Mode)*.<br>3. Chọn ngôn ngữ `vi` và bấm `▶ Tạo & Phát MP3`. | - API `/debug/text-to-speech` trả về HTTP 200 kèm `audio_url: /audio/<hash>.mp3`.<br>- Thẻ `<audio>` tải stream MP3 cùng origin từ Gateway.<br>- Huy hiệu trạng thái âm thanh đổi thành `Đang phát MP3` (màu xanh).<br>- Nút `⏹ Dừng phát` và `🔁 Phát lại` được kích hoạt.<br>- Âm thanh phát rõ ràng qua loa/tai nghe.<br>- Khi phát xong: trạng thái chuyển thành `Đã phát xong`. |
| **TC-02** | **Autoplay Blocked (Trình duyệt chặn tự động phát)** | 1. Mở `/app/` trên trình duyệt di động (hoặc giả lập chặn autoplay trong Chrome: Settings -> Sound -> Don't allow sites to play sound).<br>2. Bấm `Bắt đầu thuyết minh` rồi đứng yên đợi kích hoạt phát âm thanh ngầm.<br>3. `audio.play()` bị reject với lỗi `NotAllowedError`. | - Trình duyệt **KHÔNG** xem đây là file âm thanh hỏng.<br>- **KHÔNG** kích hoạt fallback sang SpeechSynthesis một cách nhầm lẫn.<br>- Huy hiệu trạng thái hiển thị `Trình duyệt chặn tự động phát (Chạm để nghe)` (màu vàng).<br>- Hộp thoại cảnh báo xuất hiện nút nổi bật: `🔊 Cho phép phát âm thanh`.<br>- Khi người dùng chạm vào nút này: `audio.play()` được gọi lại dưới user gesture hợp lệ và âm thanh phát bình thường; hộp thoại cảnh báo tự động biến mất. |
| **TC-03** | **MP3 Fail -> Speech Synthesis Fallback** | 1. Mở `/app/`.<br>2. Trong Debug Panel, bấm nút `🧪 Test MP3 404 (Fallback Speech)`.<br>*(URL truyền vào là file không tồn tại: `/audio/nonexistent_test.mp3`)*. | - Gateway trả về HTTP 404 `File âm thanh không tồn tại`.<br>- Sự kiện `audio.onerror` kích hoạt.<br>- Client tự động bắt lỗi và chuyển giao sang `fallbackToSpeech(text, lang)`.<br>- Client khởi tạo `SpeechSynthesisUtterance` với mã ngôn ngữ chuẩn BCP-47 (`vi-VN`, `en-US`, `ja-JP`).<br>- Nguồn âm thanh hiển thị `Giọng đọc trình duyệt (<voice_name>)`.<br>- Huy hiệu hiển thị `Đang phát giọng đọc trình duyệt` (màu tím xanh).<br>- Giọng đọc trình duyệt cất tiếng đọc văn bản mô tả. |
| **TC-04** | **Speech Không Có Voice (No Voice Available)** | 1. Mở `/app/`.<br>2. Trong Debug Panel, bấm nút `🧪 Test Fallback Không có Voice`.<br>*(Ngôn ngữ chỉ định là mã không tồn tại trên hệ máy: `xx-unknown-language`)*. | - Client gọi `findMatchingVoice("xx-unknown-language")` và trả về `null`.<br>- Client **KHÔNG** làm crash hay đơ giao diện.<br>- Không hứa hẹn hỗ trợ mọi ngôn ngữ khi hệ điều hành máy khách thiếu gói voice TTS.<br>- Nguồn âm hiển thị: `Chỉ hiển thị văn bản (Trình duyệt chưa có voice)`.<br>- Huy hiệu trạng thái: `Không có giọng đọc cho ngôn ngữ này trong trình duyệt` (màu vàng).<br>- Hiển thị khung cảnh báo hướng dẫn đọc văn bản trực tiếp kèm nút `🔄 Thử lại phát âm thanh` để người dùng có thể thử lại sau khi cài gói ngôn ngữ. |
| **TC-05** | **Dừng Phát (Stop Narration)** | 1. Khi âm thanh đang phát (bằng MP3 hoặc SpeechSynthesis), bấm nút `⏹ Dừng phát`. | - Thao tác `audioElement.pause()`, reset `currentTime = 0`, xóa `src`.<br>- Đồng thời gọi `window.speechSynthesis.cancel()`.<br>- Âm thanh lập tức tắt hoàn toàn.<br>- Huy hiệu trạng thái chuyển thành `Đã dừng phát` (màu trung tính).<br>- Nút `⏹ Dừng phát` bị vô hiệu hóa (disabled).<br>- Khi bấm `Dừng GPS`, luồng phát âm thanh cũng tự động được dừng đồng bộ. |

---

## Chi Tiết Các Kịch Bản Bổ Sung

### TC-06: Tắt Tiếng / Bật Tiếng (Mute & Unmute)
- **Thao tác**: Trong khi âm thanh MP3 đang phát, bấm nút `🔇 Tắt tiếng`.
- **Kết quả**:
  - `audioElement.muted = true`. Âm lượng về 0 ngay lập tức mà thanh tiến trình vẫn tiếp tục chạy.
  - Nhãn nút đổi thành `🔊 Bật tiếng`.
  - Bấm lại `🔊 Bật tiếng` -> `audioElement.muted = false`, âm thanh tiếp tục phát với âm lượng ban đầu, nhãn nút chuyển lại thành `🔇 Tắt tiếng`.

### TC-07: Phát Lại (Replay)
- **Thao tác**: Sau khi một bài thuyết minh đã phát xong hoặc đang dừng, bấm nút `🔁 Phát lại`.
- **Kết quả**:
  - Gọi lại `playNarration(currentNarration)`.
  - Âm thanh phát lại trọn vẹn từ đầu.

### TC-08: Đơn Nhất Luồng Âm Thanh (Single Narration Exclusivity)
- **Thao tác**: Trong khi narration A đang phát, có tín hiệu địa điểm mới kích hoạt hoặc bấm phát narration B trong Debug Panel.
- **Kết quả**:
  - Âm thanh của narration A lập tức bị dừng/hủy bỏ.
  - Narration B bắt đầu phát độc lập.
  - Tuyệt đối **không có hiện tượng 2 âm thanh bị nói đè/chồng chéo lên nhau**.
