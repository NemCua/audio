# Hướng dẫn cài đặt

## Yêu cầu hệ thống
- macOS (Apple Silicon hoặc Intel) hoặc Linux
- Python 3.11+
- FFmpeg (bản đầy đủ có libmp3lame)
- 4GB RAM trở lên

---

## 1. Cài FFmpeg

### macOS
```bash
brew install ffmpeg-full
```
Sau khi cài, lấy đường dẫn:
```bash
which ffmpeg
# Ví dụ: /opt/homebrew/opt/ffmpeg-full/bin/ffmpeg
```

### Linux (Ubuntu/Debian)
```bash
sudo apt update && sudo apt install -y ffmpeg
which ffmpeg
# /usr/bin/ffmpeg
```

---

## 2. Clone project
```bash
git clone <repo-url>
cd test-audio
```

---

## 3. Cài Python dependencies
```bash
pip3 install fastapi uvicorn[standard] groq openai edge-tts httpx \
             python-dotenv python-jose[cryptography] bcrypt psycopg2-binary \
             python-multipart gradio pandas numpy Pillow
```

---

## 4. Tạo file .env
Tạo file `.env` trong thư mục gốc với nội dung:
```env
GROQ_API_KEY=gsk_...
AUTH_SECRET_KEY=<random 64 ký tự hex>
DATABASE_URL=postgresql://user:pass@host/dbname?sslmode=require
BEEKNOEE_API_KEY=sk-bee-...
FFMPEG_BIN=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg
CAPCUT_DEVICE_ID=<số ngẫu nhiên 19 chữ số>
NGROK_AUTHTOKEN=<token từ dashboard.ngrok.com>
```

Tạo AUTH_SECRET_KEY:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Tạo CAPCUT_DEVICE_ID:
```bash
python3 -c "import random; print(random.randint(7000000000000000000, 7999999999999999999))"
```

---

## 5. Tạo database (Neon.tech - miễn phí)
1. Vào https://neon.tech → tạo project
2. Copy connection string dán vào `DATABASE_URL` trong `.env`
3. Bảng được tạo tự động khi chạy server lần đầu

---

## 6. Chạy server

### Tab 1 — Auth server (port 8006)
```bash
python auth_server.py
```

### Tab 2 — Translate server (port 8005)
```bash
python translate_server.py
```

### Tab 3 — Gradio UI (port 8003, tùy chọn)
```bash
python app.py
```

### Tab 4 — Tunnel ra ngoài (chọn 1 trong 2)
```bash
# Ngrok (khuyến nghị)
ngrok config add-authtoken <NGROK_AUTHTOKEN>
ngrok http 8005

# Hoặc Cloudflare
cloudflared tunnel --url http://localhost:8005
```

---

## 7. Truy cập
- Local: http://localhost:8005
- Ngrok: URL hiển thị trong terminal ngrok
- Gradio: http://localhost:8003

---

## Lưu ý quan trọng
- File `.env` **tuyệt đối không commit** lên git
- Job dịch lưu trong RAM — restart server là mất job đang chạy
- Mỗi lần restart ngrok URL sẽ thay đổi
- CapCut TTS gọi API ra ngoài internet, cần kết nối tốt
