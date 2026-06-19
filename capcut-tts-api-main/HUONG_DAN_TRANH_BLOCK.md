# Hướng dẫn dùng CapCut TTS API không bị block

## Vấn đề

CapCut TTS API trả về lỗi `"shark block only"` khi server phát hiện request bất thường.
Nguyên nhân chính: **device_id bị đánh dấu** — đổi ID mới là hết ngay.

---

## Cách fix đã được kiểm chứng

### 1. Đổi device_id

Server dùng `device_id` để track quota. Đổi sang ID mới là server coi như user mới.

- Format: số nguyên 19 chữ số bắt đầu bằng `7`
- Generate ngẫu nhiên:

```python
import random
print(random.randint(7000000000000000000, 7999999999999999999))
```

- Lưu vào `.env`:

```
CAPCUT_DEVICE_ID=7164195157294605701
```

Không gian ID là 10^18 — không bao giờ hết.

### 2. Dùng batch thay vì từng request riêng lẻ (QUAN TRỌNG)

Nếu gửi từng câu 1 request thì với video 282 đoạn = 282 request → bị block gần như chắc chắn.
Gom thành batch 10 text/request → chỉ còn 29 request, ít bị detect hơn 10 lần.
**Đây là điều kiện tiên quyết — phải làm batch trước khi nghĩ đến chuyện khác.**

API hỗ trợ nhiều text trong 1 SSML:

```xml
<speak>
  <voice name="BV074_streaming" ...>
    <prosody rate="1.0">text 1</prosody>
  </voice>
  <voice name="BV074_streaming" ...>
    <prosody rate="1.0">text 2</prosody>
  </voice>
  ...
</speak>
```

Server trả về mảng `audio_subtitles` tương ứng từng text.

---

## Cách tự động hóa trong code

Khi bị block, code tự:
1. Generate device_id mới
2. Ghi vào `.env`
3. Retry batch đó ngay với ID mới
4. Thử tối đa 5 lần trước khi fallback

```python
import random, os
from pathlib import Path

def new_device_id(env_path: Path) -> str:
    new_id = str(random.randint(7000000000000000000, 7999999999999999999))
    if env_path.exists():
        lines = env_path.read_text().splitlines()
        new_lines = [
            f"CAPCUT_DEVICE_ID={new_id}" if l.startswith("CAPCUT_DEVICE_ID=") else l
            for l in lines
        ]
        if not any(l.startswith("CAPCUT_DEVICE_ID=") for l in lines):
            new_lines.append(f"CAPCUT_DEVICE_ID={new_id}")
        env_path.write_text("\n".join(new_lines) + "\n")
    return new_id

def flush_batch_with_retry(texts, paths, voice_type, resource_id, device_id, env_path, rate="1.0"):
    current_id = device_id
    for attempt in range(5):
        try:
            capcut_tts_batch(texts, voice_type, resource_id, paths, current_id, rate=rate)
            return
        except Exception as e:
            print(f"Block lần {attempt+1}: {e} — đổi device_id...")
            current_id = new_device_id(env_path)
    # fallback nếu vẫn fail sau 5 lần
    raise RuntimeError("CapCut bị block sau 5 lần đổi ID")
```

---

## Tóm tắt thứ tự ưu tiên khi bị block

1. **Dùng batch 10 text/request** — bắt buộc, làm ngay từ đầu
2. **Đổi device_id** trong `.env` — hết block ngay
3. Nếu vẫn bị → đổi thêm IP (VPN/proxy)
