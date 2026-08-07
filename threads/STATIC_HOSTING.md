# Chạy bản browser-first

Ứng dụng chính trong thư mục này là web tĩnh. Media và xuất video không cần
`media_server.py` hoặc `export_server.py`. Chỉ giọng đọc CapCut gọi một API
TTS nhỏ chạy từ `tts_server.py` hoặc một bản triển khai tương đương.

## Chạy thử trên máy

Từ thư mục `threads/`:

```bash
python3 -m http.server 4173
```

Chạy TTS API bằng `python3 threads/tts_server.py`, sau đó mở
`http://localhost:4173/index.html` bằng Chrome hoặc Edge mới.

## Xuất video

1. Chọn video nền và tạo kịch bản.
2. Bấm **Ghi & xuất WebM**.
3. Trong hộp chia sẻ của trình duyệt, chọn tab đang mở ứng dụng.
4. Bật **Chia sẻ âm thanh tab**.
5. Giữ tab mở đến khi nút tải video xuất hiện.

Chrome/Edge có Region Capture sẽ chỉ ghi khung preview 9:16. Trình duyệt
không hỗ trợ Region Capture sẽ ghi toàn bộ tab và ứng dụng sẽ báo rõ điều đó.

## Host miễn phí

Có thể upload nguyên thư mục `threads/` lên bất kỳ static hosting nào như
Cloudflare Pages, GitHub Pages, Netlify hoặc Vercel. Sau khi mở web, nhập URL
HTTPS công khai của CapCut TTS API trong mục **Giọng đọc TTS**.

## Giới hạn của bản web tĩnh đầu tiên

- CapCut TTS vẫn cần một server API nhỏ và kết nối mạng.
- File xuất là WebM để tránh phụ thuộc FFmpeg phía server.
- URL media cục bộ chỉ sống trong tab hiện tại. Xuất JSON chưa đóng gói file
  media; khi mở lại dự án vẫn cần chọn lại các file đó.
- Thư viện media/meme lưu trên Python server cũ không xuất hiện trong bản tĩnh.
