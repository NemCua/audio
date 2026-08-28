# Checklist làm sạch máy Windows để test bản Dich Video như máy mới

Mục tiêu: giả lập một máy Windows chưa từng cài app, chưa có sẵn ffmpeg/ffprobe/Python dependency để kiểm tra bản installer có thật sự đóng gói đủ hay không.

> Làm theo checklist này trên máy Windows test trước khi cài lại bản mới.

## 1. Gỡ app Dich Video

Vào:

```text
Settings → Apps → Installed apps
```

Tìm và gỡ:

```text
Dich Video
```

Nếu có nhiều bản cũ, gỡ hết.

## 2. Xóa thư mục app còn sót lại

Mở File Explorer, dán từng đường dẫn dưới đây vào thanh địa chỉ, nếu thư mục tồn tại thì xóa:

```text
%LOCALAPPDATA%\Programs\Dich Video
%LOCALAPPDATA%\dich-video
%APPDATA%\dich-video
%APPDATA%\Dich Video
```

Nếu app từng cài vào Program Files, kiểm tra thêm:

```text
C:\Program Files\Dich Video
C:\Program Files (x86)\Dich Video
```

## 3. Xóa dữ liệu runtime nếu từng chạy bản dev/bản giải nén

Tìm trong những nơi từng để app hoặc source code cũ và xóa các thư mục/file runtime nếu có:

```text
input
output
workdir
tmp
logs
translate_server.log
content_server.log
```

Không cần xóa video gốc của bạn, chỉ xóa thư mục/file do app tạo ra.

## 4. Kiểm tra máy có đang “ăn ké” ffmpeg/ffprobe không

Mở Command Prompt mới, chạy:

```bat
where ffmpeg
where ffprobe
```

Nếu cả hai báo kiểu:

```text
INFO: Could not find files for the given pattern(s).
```

thì máy đang khá sạch.

Nếu nó hiện đường dẫn, nghĩa là máy đã có ffmpeg/ffprobe trong PATH. Để test như máy mới, nên tạm thời bỏ các đường dẫn đó khỏi PATH:

```text
Settings → System → About → Advanced system settings → Environment Variables
```

Kiểm tra cả:

```text
User variables → Path
System variables → Path
```

Xóa hoặc tạm gỡ các dòng liên quan:

```text
ffmpeg
ffprobe
ImageMagick
Python Scripts nếu chỉ dùng để app chạy ké
```

Sau khi sửa PATH, đóng hết Command Prompt rồi mở lại và chạy lại:

```bat
where ffmpeg
where ffprobe
```

## 5. Kiểm tra Python không phải điều kiện bắt buộc

Bản app đóng gói đúng thì người dùng cuối không cần tự cài Python.

Có thể kiểm tra:

```bat
where python
where py
```

Không bắt buộc phải xóa Python nếu máy đang dùng việc khác. Nhưng nếu muốn test cực sạch, dùng máy ảo hoặc Windows Sandbox sẽ chuẩn hơn.

## 6. Restart máy

Restart Windows để chắc chắn:

- app cũ không còn process ngầm;
- PATH mới được áp dụng;
- port `8005` không còn server cũ giữ.

Sau khi restart, có thể kiểm tra port:

```bat
netstat -ano | findstr :8005
```

Nếu không hiện gì là ổn.

## 7. Cài bản installer mới

Cài lại file `.exe` mới build từ GitHub Actions hoặc từ release mới.

Sau khi mở app, nếu app chạy được thì vào trình duyệt trên máy đó mở:

```text
http://127.0.0.1:8005/api/diagnostics
```

Kết quả tốt cần có dạng:

```json
{
  "ok": true,
  "ffmpeg": {
    "exists": true
  },
  "ffprobe": {
    "exists": true
  },
  "static_translate": {
    "exists": true
  }
}
```

Quan trọng nhất là:

```text
ffmpeg.exists = true
ffprobe.exists = true
static_translate.exists = true
```

## 8. Test lỗi Internal Server Error

Test theo thứ tự:

1. Mở app.
2. Upload một video ngắn.
3. Nếu lỗi `Internal Server Error`, mở:

```text
http://127.0.0.1:8005/api/diagnostics
```

4. Chụp màn hình kết quả diagnostics.
5. Nếu app có cửa sổ lỗi server, chụp luôn phần log.

## 9. Cách sạch nhất nếu muốn test như máy mới 100%

Nếu vẫn nghi máy test còn thư viện cũ, dùng một trong các cách này:

- Windows Sandbox.
- Máy ảo Windows mới.
- Một user Windows mới tinh.
- Máy Windows chưa từng cài app/ffmpeg/Python.

Windows Sandbox là chuẩn nhất vì mỗi lần đóng Sandbox là mất sạch trạng thái, giống máy mới.

## Ghi chú kỹ thuật

Bản app đúng cần tự mang theo ít nhất:

```text
translate_server.exe
ffmpeg.exe
ffprobe.exe
static_translate/
capcut-tts-api-main/
```

Không nên phụ thuộc vào:

```text
ffmpeg có sẵn trong PATH
ffprobe có sẵn trong PATH
Python cài sẵn trên máy người dùng
thư viện pip cài sẵn trên máy người dùng
```

Nếu một máy chạy được còn máy khác lỗi, nguyên nhân thường là máy chạy được đang có sẵn dependency trong PATH nên app “ăn ké” mà mình không biết.
