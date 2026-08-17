# Deploy Threads API lên VPS Ubuntu

Stack production gồm hai container:

- `api`: FastAPI account/credit + CapCut/Edge TTS, chạy non-root.
- `caddy`: reverse proxy và tự cấp/gia hạn HTTPS Let's Encrypt.

Video được export bằng FFmpeg trên máy người dùng. VPS không render video và
không chứa Electron hay thư viện media của app.

## 1. Chuẩn bị DNS

Tạo một bản ghi `A`, ví dụ `api.example.com`, trỏ về IPv4 của VPS. Chờ DNS
phân giải đúng trước khi chạy Caddy. Cổng `80` và `443` phải truy cập được từ
Internet.

## 2. Cài Docker trên Ubuntu 24.04

Đăng nhập SSH bằng tài khoản có quyền sudo rồi chạy:

```bash
sudo apt update
sudo apt install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
```

Đăng xuất SSH và đăng nhập lại để nhóm `docker` có hiệu lực.

## 3. Clone và cấu hình

```bash
git clone https://github.com/NemCua/audio.git
cd audio
git checkout threads_all_browser
cp .env.vps.example .env.vps
nano .env.vps
```

Thay toàn bộ giá trị `REPLACE_*`, `DATABASE_URL` và `DOMAIN`. Sinh hai secret
khác nhau bằng:

```bash
openssl rand -hex 32
```

Không commit hoặc gửi file `.env.vps` cho người khác.

## 4. Mở firewall và chạy

Nếu VPS dùng UFW:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 443/udp
sudo ufw enable
```

Build và chạy dịch vụ:

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f --tail=100
```

Kiểm tra public:

```bash
curl https://api.example.com/health
```

Kết quả đúng:

```json
{"ok":true,"service":"threads-api"}
```

Không mở trực tiếp cổng `8007`; chỉ Caddy được public qua `80/443`.

## 5. Cập nhật phiên bản mới

```bash
git pull --ff-only
docker compose up -d --build
docker image prune -f
```

## Vận hành

```bash
# Trạng thái và health
docker compose ps

# Log API
docker compose logs -f --tail=200 api

# Khởi động lại API
docker compose restart api

# Dừng toàn bộ (không xóa volume)
docker compose down
```

Không chạy `docker compose down -v` nếu chưa chủ động muốn xóa volume file TTS
và dữ liệu chứng thư HTTPS của Caddy.

## Nối app desktop sang VPS

Trước khi phát hành app mới, thay URL Render cũ bằng domain VPS trong ba chỗ:

- `threads/index.html`: `REMOTE_TTS_SERVER_URL`
- `threads/auth_client.js`: URL fallback
- `electron/main.js`: allowlist trong `allowedApiBase()`

Sau đó tăng version Electron, build và tạo GitHub Release mới. URL API phải là
`https://...`; không dùng IP HTTP trong bản production.
