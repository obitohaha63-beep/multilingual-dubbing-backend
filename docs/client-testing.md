# Hướng Dẫn Kiểm Thử Client (Client Testing Guide)

Tài liệu hướng dẫn kiểm thử các chức năng của Web Client (`gateway-service/client/app.js`) bao gồm Geolocation `watchPosition`, timer định kỳ khi đứng yên, throttle 3s, xử lý quyền và đảm bảo request không chồng chéo (in-flight guard).

---

## 1. Các Tiêu Chí Kiểm Thử (Test Cases)

### Test Case 1: Xin quyền & Khởi tạo `watchPosition`
- **Thao tác**: Nhấn nút "Bắt đầu thuyết minh".
- **Kỳ vọng**:
  - Gọi `navigator.geolocation.watchPosition` với các options: `{ enableHighAccuracy: true, maximumAge: 5000, timeout: 10000 }`.
  - Trạng thái GPS hiển thị: *"Đang xin quyền GPS..."*.
  - Nút "Bắt đầu" bị disable, nút "Dừng GPS" được kích hoạt (enable).
  - Khởi tạo interval timer 1s để giám sát việc gửi dữ liệu.

### Test Case 2: Dừng GPS và Hủy theo dõi (`clearWatch`)
- **Thao tác**: Nhấn nút "Dừng GPS".
- **Kỳ vọng**:
  - `navigator.geolocation.clearWatch(watchId)` được gọi ngay lập tức.
  - Interval timer bị hủy (`clearInterval`).
  - Toàn bộ trạng thái tạm lưu (`lastFix`, `lastSentFix`, `isRequestInFlight`) được reset về giá trị ban đầu.
  - Nút "Bắt đầu" được bật lại, nút "Dừng GPS" bị vô hiệu hóa.

### Test Case 3: Xử lý lỗi quyền truy cập và lỗi định vị
- **Kịch bản**: Giả lập các mã lỗi từ `PositionError`:
  - `PERMISSION_DENIED` (code 1): Hiển thị badge đỏ *"Quyền GPS bị từ chối (Denied)"*.
  - `POSITION_UNAVAILABLE` (code 2): Hiển thị badge đỏ *"Vị trí không khả dụng (Unavailable)"*.
  - `TIMEOUT` (code 3): Hiển thị badge đỏ *"Hết thời gian chờ GPS (Timeout)"*.
- **Kỳ vọng**: Hệ thống không bịa tọa độ, không gửi request ảo lên server, tọa độ hiển thị dạng `--`.

### Test Case 4: Gửi ngay lập tức ở Fix GPS đầu tiên
- **Kịch bản**: Nhận được callback `onGpsSuccess` đầu tiên từ `watchPosition`.
- **Kỳ vọng**:
  - `lastSentFix` lúc này là `null`.
  - Client lập tức kích hoạt gọi `POST /locations/detect`.
  - Cập nhật thời gian gửi và lưu lại `lastSentFix`.

### Test Case 5: Cơ chế di chuyển >= 20m
- **Kịch bản**: 
  - Đã gửi fix tại điểm A (10.7769, 106.7009).
  - 4 giây sau nhận fix tại điểm B (10.7772, 106.7011) — khoảng cách tính bằng Haversine là ~40m (>= 20m) và khoảng cách thời gian >= 3s.
- **Kỳ vọng**: Client gửi request cập nhật mới lên server.

### Test Case 6: Timer định kỳ khi người dùng đứng yên (Keep-alive / Refresh sau 10s)
- **Kịch bản**:
  - Người dùng đứng yên tại 1 vị trí (tọa độ không đổi hoặc dao động < 20m, hoặc `watchPosition` không kích hoạt callback mới do thiết bị đứng yên).
  - Đã qua 10 giây kể từ lần gửi trước.
  - Timer 1s kích hoạt `checkAndSendDetect()`.
- **Kỳ vọng**: Phát hiện `timeSinceLastSent >= 10000ms`, client tự động gửi lại tọa độ hiện tại lên server mà không cần phụ thuộc callback mới từ GPS.

### Test Case 7: Ràng buộc Throttle 3s
- **Kịch bản**:
  - Request vừa gửi lúc T = 0ms.
  - Lúc T = 1500ms, GPS báo vị trí nhảy xa 50m.
- **Kỳ vọng**:
  - `now - lastRequestTime` = 1500ms < 3000ms.
  - Client bỏ qua lượt kiểm tra này, không gửi request để bảo vệ máy chủ khỏi bị spam.
  - Đến khi T >= 3000ms, điều kiện throttle được giải phóng.

### Test Case 8: Không gửi chồng chéo (In-flight Guard)
- **Kịch bản**:
  - Request trước đang gửi (mạng chậm, pending 4 giây).
  - Có tín hiệu GPS mới sau 3.5 giây.
- **Kỳ vọng**:
  - Cờ `isRequestInFlight` vẫn đang là `true`.
  - Client không mở thêm kết nối mới song song, đảm bảo mỗi thời điểm chỉ có tối đa một request đang xử lý.
  - Khi request cũ hoàn thành (`finally`), cờ `isRequestInFlight` chuyển về `false`.

### Test Case 9: Chặn Fix GPS cũ (> 30s)
- **Kịch bản**: Thiết bị mất sóng GPS, `lastFix.timestamp` cách thời điểm hiện tại 35 giây.
- **Kỳ vọng**:
  - Client từ chối gửi request và cảnh báo trên giao diện: *"Fix GPS quá cũ (>30s)"*.

---

## 2. Kịch Bản Test Thực Tế Bằng Trình Duyệt (Manual DevTools Testing)

1. Mở trình duyệt Chrome / Edge trên máy tính, truy cập: `http://localhost:8000/app/`.
2. Mở **Chrome DevTools (F12)** -> Chọn tab **Sensors** (nhấn `Ctrl + Shift + P` -> gõ `Show Sensors`).
3. Trong mục **Location**:
   - Chọn thử các địa điểm mẫu hoặc nhập tọa độ tùy ý (ví dụ: Nhà thờ Đức Bà Sài Gòn: `10.77978, 106.69902`).
4. Nhấn **"Bắt đầu thuyết minh"** trên giao diện:
   - Cho phép quyền vị trí khi trình duyệt hỏi.
   - Quan sát tab **Network**: Request `POST /locations/detect` được gửi ngay lập tức.
   - Kiểm tra UI: Trạng thái hiển thị *"Trong bán kính kích hoạt"*, tên địa điểm Nhà thờ Đức Bà, khoảng cách, mô tả.
5. Thử đổi tọa độ trong tab Sensors sang vị trí cách 30m:
   - Sau tối thiểu 3s, một request mới tự động được gửi đi.
