# Video phân biệt

Trình dựng video ngắn dạng “phân biệt hai khái niệm”.

## Chạy thử

Mở `index.html` trực tiếp bằng trình duyệt, hoặc chạy tại thư mục gốc:

```bash
python3 -m http.server 8080
```

Sau đó mở `http://localhost:8080/allYoutube/videoPhanBiet/`.

Hiện tại một video có thể chứa số lượng cặp phân biệt tùy ý. Mỗi cặp có 2 ảnh, 2 chủ đề, 2 phần giải thích, preview nhân vật hoạt hình và giọng đọc CapCut TTS. Video chạy tuần tự hết một cặp rồi chuyển sang cặp tiếp theo. Phần giải thích được đọc bằng TTS, không hiển thị thành popup trong video. Xuất MP4 sẽ được nối thêm sau.

## Chạy TTS

Từ thư mục gốc, chạy FastAPI dùng chung với công cụ Threads:

```bash
python3 threads/tts_server.py
```

TTS server mặc định chạy tại `http://localhost:8007`. Trong giao diện, chọn giọng rồi bấm **Tạo giọng đọc**. Công cụ tạo tuần tự 5 file cho mỗi cặp: giới thiệu A, giới thiệu B, câu hỏi, giải thích A và giải thích B. Timeline tự dùng thời lượng thật của từng file âm thanh.

Các câu được gửi qua `/tts-batch`, tối đa 50 đoạn trong một request. Nếu video có hơn 50 đoạn, frontend tự chia thành nhiều batch 50.

## AI tạo nội dung

Nhập số cặp từ 1 đến 50 và bấm **AI tạo cặp phân biệt**. Backend đọc `BEEKNOEE_API_KEY2` từ `.env` và gọi model `google/gemini-2.5-flash-lite`; API key không xuất hiện ở frontend.

## Nạp nhanh JSON

Bấm **Nạp JSON** để điền đồng thời hai chủ đề, hai lời giải thích, đường dẫn/URL ảnh và cấu hình TTS. Có thể tải `project-example.json` ngay trên giao diện làm mẫu. Sau khi nạp, chỉ cần chọn hai ảnh nếu JSON chưa có ảnh rồi bấm **Tạo giọng đọc**.

## Nạp JSON

Dùng nút **Nạp JSON** trên giao diện. Có thể tải `project-example.json` làm mẫu. Gốc JSON là một mảng; mỗi phần tử là một cặp phân biệt. Số phần tử chính là số cặp xuất hiện trong video. JSON chỉ chứa nội dung và ảnh, không chứa cấu hình TTS. Trường `imageA` và `imageB` nhận URL, data URL hoặc đường dẫn ảnh tương đối với trang web.

Địa chỉ TTS mặc định là `http://localhost:8007`, giọng mặc định là **Cô Gái Hoạt Ngôn**. Bạn vẫn có thể đổi voice trực tiếp trên giao diện trước khi tạo giọng đọc.

## Bộ ảnh nhân vật

- `assets/character-point-up-left.png`: chỉ lên phía trái.
- `assets/character-point-up-right.png`: chỉ lên phía phải.
- `assets/character-question.png`: tư thế suy nghĩ cùng dấu hỏi.

Ba ảnh sử dụng nền trong suốt và sẵn sàng để ghép vào video.

Khi giới thiệu hoặc giải thích chủ đề A, nhân vật chỉ lên ảnh A. Khi giới thiệu hoặc giải thích chủ đề B, nhân vật chỉ lên ảnh B. Tư thế dấu hỏi chỉ dùng ở đoạn đặt câu hỏi so sánh.
