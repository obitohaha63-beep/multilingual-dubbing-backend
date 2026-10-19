Hướng dẫn khởi động và kiểm thử hệ thống

1\. Chuẩn bị môi trường

Bước 1: Mở đúng thư mục dự án

Mở Docker Desktop và đợi Docker khởi động hoàn toàn.

Mở thư mục chứa dự án bằng File Explorer.

Trong thư mục dự án, nhấp vào thanh địa chỉ, nhập powershell.exe rồi nhấn Enter.



Thao tác này mở PowerShell mặc định và trỏ trực tiếp đến thư mục dự án.

: Tạo file cấu hình .env

File .env.example là file mẫu chứa các biến cấu hình. Em cần tạo một bản .env để ứng dụng sử dụng.



Trong PowerShell, chạy:





powershell

Copy-Item .env.example .env



Sau đó mở file .env bằng vscode, tìm dòng WIKIMEDIA\_USER\_AGENT=multilingual-dubbing-backend/1.0 (contact: your-email@example.com), thay your-email@example.com bằng gmail của bạn. sau đó lưu lại.

Bước 2: Khởi động 3 dịch vụ



Trong PowerShell, chạy lệnh sau:



docker-compose up --build -d





Ý nghĩa các tham số:



up: Tạo và khởi chạy các container.

\--build: Xây dựng lại image nếu cần.

\-d: Chạy nền, không chiếm cửa sổ PowerShell.



Sau đó, kiểm tra trạng thái các container:



docker-compose ps





Kết quả mong đợi là bảng danh sách các container cùng trạng thái hoạt động của chúng.



2\. Mở Swagger để kiểm thử API



Swagger là giao diện web giúp gửi yêu cầu đến API mà không cần tự viết chương trình.



Mở Chrome và lần lượt truy cập các địa chỉ sau.



2.1. Dịch vụ chính



Chức năng: Phát hiện địa điểm và điều phối luồng thuyết minh.



URL: http://localhost:8000/docs



2.2. Content Service



Chức năng: Quản lý địa điểm.



URL: http://localhost:8001/docs



2.3. Translate-Audio Service



Chức năng: Dịch nội dung và tạo âm thanh.



URL: http://localhost:8002/docs



3\. Kiểm thử chức năng chính

Bước 1: Tạo địa điểm kiểm thử



Ta cần tạo một địa điểm để hệ thống có dữ liệu đối chiếu khi nhận tọa độ GPS.



Trong PowerShell, chạy nguyên khối lệnh sau:



$body = @{

&#x20;   name = "Chợ Bến Thành"

&#x20;   description\_vi = "Chợ Bến Thành là chợ lâu đời ở Quận 1, TP.HCM."

&#x20;   target\_languages = @("vi", "en", "ja")

&#x20;   latitude = 10.7725

&#x20;   longitude = 106.6980

&#x20;   radius = 80

} | ConvertTo-Json -Depth 5



$utf8Body = \[System.Text.Encoding]::UTF8.GetBytes($body)



Invoke-RestMethod `

&#x20;   -Method Post `

&#x20;   -Uri "http://localhost:8001/locations" `

&#x20;   -ContentType "application/json; charset=utf-8" `

&#x20;   -Body $utf8Body



Kết quả mong đợi



API trả về thông tin địa điểm vừa tạo. Ví dụ minh họa:



{

&#x20; "id": 1,

&#x20; "name": "Chợ Bến Thành"

}



Bước 2: Kiểm thử chức năng phát hiện địa điểm



Đây là bước quan trọng nhất vì nó kiểm tra luồng nghiệp vụ cốt lõi.



Giả sử điện thoại đang ở Chợ Bến Thành. Ta gửi tọa độ gần như trùng với tọa độ địa điểm đã tạo.



Trong PowerShell, chạy:



$det = @{

&#x20;   latitude = 10.7725

&#x20;   longitude = 106.6980

&#x20;   language = "en"

&#x20;   accuracy\_m = 10

} | ConvertTo-Json



Invoke-RestMethod `

&#x20;   -Method Post `

&#x20;   -Uri "http://localhost:8000/locations/detect" `

&#x20;   -ContentType "application/json" `

&#x20;   -Body $det



Em đang mô phỏng điều gì?

latitude, longitude: Vị trí hiện tại của người dùng.

language = "en": Người dùng chọn tiếng Anh.

accuracy\_m = 10: GPS báo độ chính xác khoảng 10 mét.



Dịch vụ chính nhận tọa độ, tìm địa điểm phù hợp, sau đó xử lý nội dung thuyết minh và yêu cầu tạo audio.



Đọc kết quả trả về



Theo đặc tả kiểm thử, kết quả thành công dự kiến có các thông tin tương tự:



{

&#x20; "detected": true,

&#x20; "audio\_url": "/audio/example.mp3",

&#x20; "narration\_status": "audio\_ready"

}





Đây là dữ liệu minh họa; phản hồi thực tế phụ thuộc vào cách triển khai API.



Bước 3: Mở và nghe file audio



Sau khi API detect trả về audio\_url, cần kiểm tra xem file có thực sự phát được hay không.



Ví dụ, nếu kết quả trả về là:



/audio/example.mp3





Mở trình duyệt và truy cập:



http://localhost:8000/audio/example.mp3



Lưu ý: Thay example.mp3 bằng đúng tên file mà API trả về.



Kết quả mong đợi

Trình duyệt mở được file âm thanh.

Âm thanh phát được, không phải file rỗng hoặc lỗi định dạng.

Nội dung nghe được phù hợp với địa điểm.

Ngôn ngữ thực tế được ghi nhận đúng, kể cả khi đang sử dụng nội dung dự phòng.



Nếu trình duyệt báo 404 Not Found, hãy kiểm tra lại đường dẫn và xác minh file audio đã được tạo hay chưa.



Bước 4: Kiểm thử qua giao diện web



Em cũng có thể mở giao diện web tại:



http://localhost:8000/app/



Sau đó thực hiện các bước:



Chọn ngôn ngữ.

Nhấn nút Bắt đầu.

Cho phép trình duyệt truy cập vị trí.

Kiểm tra hệ thống có phát hiện địa điểm và phát âm thanh hay không.

Giả lập GPS bằng Chrome DevTools



Nếu đang demo trên máy tính, em có thể giả lập GPS bằng Chrome DevTools:



Nhấn F12.

Chọn menu ba chấm trong DevTools.

Chọn More tools → Sensors.

Tìm phần Location và nhập tọa độ phù hợp nếu phiên bản Chrome hỗ trợ nhập tọa độ tùy chỉnh.



Lưu ý: Tọa độ gửi từ giao diện có thể khác với tọa độ trong lệnh PowerShell. Nếu đang ở ngoài bán kính kích hoạt, hệ thống có thể không phát hiện địa điểm.



4\. Kiểm thử các trường hợp biên



Ở bước này, em không chỉ kiểm tra khi mọi thứ hoạt động tốt mà còn kiểm tra khi dữ liệu sai, người dùng ở sai vị trí hoặc dịch vụ phụ gặp lỗi.



Trong kiểm thử phần mềm, đây là cách xác minh hệ thống có xử lý đúng các tình huống bất thường hay không.



Ca 1: Người dùng ở quá xa địa điểm



Gửi tọa độ gần điểm (0, 0):



$det = @{

&#x20;   latitude = 0

&#x20;   longitude = 0

&#x20;   language = "en"

&#x20;   accuracy\_m = 10

} | ConvertTo-Json



Invoke-RestMethod `

&#x20;   -Method Post `

&#x20;   -Uri "http://localhost:8000/locations/detect" `

&#x20;   -ContentType "application/json" `

&#x20;   -Body $det





Kết quả mong đợi: Hệ thống không phát hiện địa điểm, với reason = "no\_place\_found" nếu đúng theo quy ước phản hồi của dự án.



Ý nghĩa: Hệ thống không được tự ý chọn Chợ Bến Thành khi người dùng đang ở rất xa.



Ca 2: Ở trong vùng tìm kiếm nhưng ngoài bán kính kích hoạt



Ta muốn mô phỏng người dùng cách Chợ Bến Thành khoảng 200–250 mét.



Giữ nguyên vĩ độ và tăng kinh độ một lượng nhỏ.



Lưu ý: Kinh độ của Chợ Bến Thành phải khoảng 106.6980, không phải 10.6980. Vì vậy, hãy sử dụng tọa độ đúng sau:



$det = @{

&#x20;   latitude = 10.7725

&#x20;   longitude = 106.7002

&#x20;   language = "en"

&#x20;   accuracy\_m = 10

} | ConvertTo-Json



Invoke-RestMethod `

&#x20;   -Method Post `

&#x20;   -Uri "http://localhost:8000/locations/detect" `

&#x20;   -ContentType "application/json" `

&#x20;   -Body $det





Kinh độ đã tăng khoảng 0.0022 độ, tương đương khoảng 240 mét theo hướng đông tại vĩ độ này.



Đây là khoảng cách nằm ngoài bán kính kích hoạt 80 mét nhưng có thể vẫn nằm trong vùng tìm kiếm 500 mét, tùy cách triển khai.



Kết quả mong đợi: Không kích hoạt thuyết minh, với reason = "outside\_activation\_radius" nếu hệ thống phân biệt vùng tìm kiếm và vùng kích hoạt như đặc tả.



Nếu nhận được lý do khác, em cần xem lại cách hệ thống xác định địa điểm gần nhất và các ngưỡng khoảng cách trong code.



Ca 3: Gửi ngôn ngữ không hợp lệ



Thử gửi language = "fr":



$det = @{

&#x20;   latitude = 10.7725

&#x20;   longitude = 106.6980

&#x20;   language = "fr"

&#x20;   accuracy\_m = 10

} | ConvertTo-Json



Invoke-RestMethod `

&#x20;   -Method Post `

&#x20;   -Uri "http://localhost:8000/locations/detect" `

&#x20;   -ContentType "application/json" `

&#x20;   -Body $det





Kết quả mong đợi theo đặc tả: HTTP 422 Unprocessable Entity.



Ý nghĩa: API từ chối dữ liệu không hợp lệ, thay vì âm thầm xử lý một ngôn ngữ không được hỗ trợ.



Lưu ý: Mã HTTP 422 chỉ là kết quả mong đợi nếu mô hình đầu vào thực sự giới hạn danh sách ngôn ngữ. Nếu API chấp nhận mọi chuỗi, cần kiểm tra lại code hoặc đặc tả.



Ca 4: Tọa độ vượt giới hạn



Thử gửi vĩ độ bằng 200:



$det = @{

&#x20;   latitude = 200

&#x20;   longitude = 106.6980

&#x20;   language = "en"

&#x20;   accuracy\_m = 10

} | ConvertTo-Json



Invoke-RestMethod `

&#x20;   -Method Post `

&#x20;   -Uri "http://localhost:8000/locations/detect" `

&#x20;   -ContentType "application/json" `

&#x20;   -Body $det





Vĩ độ hợp lệ trên Trái Đất nằm trong khoảng từ -90 đến 90.



Kết quả mong đợi: HTTP 422. Nếu API chấp nhận vĩ độ 200, cần kiểm tra lại các ràng buộc dữ liệu.



Ca 5: Kiểm tra cache



Cache là cơ chế lưu kết quả đã xử lý để tránh thực hiện lại công việc tốn thời gian.



Thực hiện:



Gửi yêu cầu detect với tọa độ Chợ Bến Thành.

Gửi lại chính xác yêu cầu đó lần thứ hai.

Xem log của dịch vụ tạo audio:

docker-compose logs --tail=100 translate-audio-service





Kết quả mong đợi: Nếu hệ thống có cache và yêu cầu thứ hai khớp khóa cache, log có thể xuất hiện Cache HIT.



Điều này cho thấy hệ thống có thể tái sử dụng kết quả cũ. Thời gian xử lý lần hai thường ngắn hơn, nhưng thời gian nhanh hơn không đủ để chứng minh cache hoạt động.



Ca 6: Dừng dịch vụ tạo audio



Ca này kiểm tra khả năng xử lý khi một dịch vụ phụ gặp sự cố.



Đầu tiên, dừng dịch vụ:



docker-compose stop translate-audio-service





Sau đó, gửi lại yêu cầu detect với tọa độ hợp lệ.



Kết quả mong đợi theo đặc tả: Hệ thống vẫn có thể xác định địa điểm (detected = true), nhưng thông tin thuyết minh báo lỗi như narrate\_error hoặc narrate\_unavailable.



Lưu ý: Kết quả chính xác phụ thuộc vào cách dịch vụ chính xử lý lỗi và timeout. Nếu yêu cầu bị treo hoặc trả lỗi HTTP, em cần ghi lại kết quả thực tế thay vì kết luận ngay rằng hệ thống đạt yêu cầu.



Khởi động lại dịch vụ



Sau khi thử nghiệm, khởi động lại dịch vụ và kiểm tra trạng thái:



docker-compose start translate-audio-service

docker-compose ps





Đợi dịch vụ chạy ổn định rồi mới làm các ca tiếp theo.



Ca 7: Truy cập file audio không tồn tại



Mở PowerShell và chạy:



Invoke-WebRequest `

&#x20;   -Uri "http://localhost:8000/audio/khong\_co.mp3"





Kết quả mong đợi: HTTP 404 Not Found.



Nếu PowerShell hiển thị lỗi màu đỏ thì chưa chắc là lỗi ngoài dự kiến. Với ca này, HTTP 404 chính là kết quả kiểm thử thành công vì file không tồn tại.



Ca 8: Kiểm thử Path Traversal



Path Traversal là kiểu tấn công khai thác đường dẫn nhằm tìm cách truy cập ra ngoài thư mục được phép.



Thử gọi:



Invoke-WebRequest `

&#x20;   -Uri "http://localhost:8000/audio/..%2Fx.mp3"





Theo đặc tả kiểm thử, kết quả mong đợi là HTTP 422.



Tuy nhiên, tùy cách framework và bộ định tuyến xử lý đường dẫn, kết quả thực tế có thể khác. Em hãy ghi nhận mã HTTP nhận được và đối chiếu với quy tắc bảo vệ đường dẫn trong code.



Ca 9: Kiểm thử API tạo audio trực tiếp



Trước đó, em đã kiểm tra audio thông qua dịch vụ chính. Bây giờ, ta kiểm tra riêng Translate-Audio Service để khoanh vùng lỗi.



Chạy:



$body = @{

&#x20;   text = "Xin chào"

&#x20;   lang = "vi"

} | ConvertTo-Json



Invoke-RestMethod `

&#x20;   -Method Post `

&#x20;   -Uri "http://localhost:8002/text-to-speech" `

&#x20;   -ContentType "application/json" `

&#x20;   -Body $body





Kết quả mong đợi theo đặc tả: API trả về thông tin có audio\_url hoặc đường dẫn audio tương ứng.



Nếu gọi trực tiếp thành công nhưng luồng detect không tạo được audio, lỗi có thể nằm ở cách dịch vụ chính gọi dịch vụ phụ, truyền dữ liệu hoặc xử lý phản hồi.

Nếu gọi trực tiếp cũng thất bại, hãy kiểm tra log của Translate-Audio Service:

docker-compose logs --tail=100 translate-audio-service



5\. Chụp bằng chứng kiểm thử



Sau khi kiểm thử xong, em nên lưu ít nhất ba ảnh chụp màn hình để làm bằng chứng.



Ảnh 1 — Hệ thống hoạt động



Chụp lệnh docker-compose ps và kết quả /services-health.



Ảnh 2 — Kết quả phát hiện địa điểm



Chụp phản hồi JSON có các thông tin:



detected

audio\_url

narration\_status

Thông tin fallback nếu có

Ảnh 3 — Audio phát được



Chụp trình phát âm thanh khi mở đúng đường dẫn mà API trả về.



6\. Dọn dẹp sau khi kiểm thử



Khi đã lưu đầy đủ bằng chứng, chạy:



docker-compose down





Lệnh này dừng và gỡ các container cùng network do Docker Compose quản lý.



Thông thường, lệnh không xóa các volume được giữ lại nếu không sử dụng thêm tùy chọn -v.



Lưu ý: Nếu em còn cần demo ngay thì chưa cần chạy lệnh này; có thể để các dịch vụ tiếp tục hoạt động.

