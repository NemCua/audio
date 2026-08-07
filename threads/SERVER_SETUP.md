# Threads Desktop API

Server mới gộp account/credit và CapCut TTS vào cùng một process. Nó dùng lại
`DATABASE_URL` Neon hiện tại và bảng `users` cũ; migration chỉ thêm bảng/cột,
không xóa dữ liệu.

## Chạy local

```bash
python3 threads/unified_server.py
```

Mặc định server chạy cổng `8007`:

- `POST /auth/register`, `/auth/login`, `/auth/refresh`, `/auth/logout`
- `GET /auth/me`, `/auth/credits/ledger`
- `POST /auth/exports/reserve`, `/consume`, `/refund`
- `POST /tts`, `/tts-batch`, `/tts-mixed-batch`
- `GET /voices`, `/voices-en`, `/audio/{filename}`
- Swagger: `/docs`

## Development và production

Local UI cũ có thể chạy với `REQUIRE_TTS_AUTH=0`. Production phải đặt
`REQUIRE_TTS_AUTH=1`; khi đó các endpoint tạo TTS yêu cầu access token.

Access token sống ngắn. Refresh token được hash trong database, xoay sau mỗi
lần dùng và có thể revoke bằng logout. Không lưu refresh token dạng rõ trong
database.

## Credit export

App gọi `/auth/exports/reserve` với một `idempotency_key` duy nhất trước khi
export. Credit được giữ/trừ nguyên tử. Khi xong gọi `/consume`; khi FFmpeg lỗi
gọi `/refund`. Reservation hết hạn được hoàn tự động trước lần reserve kế tiếp.

`/auth/admin/credits` dùng header `X-Admin-Key` để cộng credit thủ công hoặc từ
payment webhook trong giai đoạn sau. Mọi biến động đều ghi vào `credit_ledger`.
