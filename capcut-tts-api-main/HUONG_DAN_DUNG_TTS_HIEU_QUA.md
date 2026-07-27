# Hướng dẫn dùng CapCut TTS hiệu quả (đúc kết từ thực chiến)

File này ghi lại cách dùng `capcut_common_task_client.py` để tạo TTS hàng loạt
(ổn định) mà một dự án thật (pipeline dịch + lồng tiếng video) đang dùng. Đọc
README.md trước để hiểu API cơ bản — file này chỉ nói về cách **dùng hiệu quả
trong thực tế**: batch thế nào, xử lý lỗi/bị chặn ra sao, chọn giọng nào.

Không phụ thuộc code của dự án gốc — copy nguyên folder này sang máy/project
khác là dùng được ngay, chỉ cần `pip install requests`.

---

## 1. Luôn gửi theo BATCH, đừng gọi từng câu một

CapCut cho phép gửi **nhiều đoạn text trong 1 request** `/lv/v1/common_task/new`
(một task TTS trả về nhiều file audio, map theo `text`). Gọi từng câu một sẽ:

- Chậm hơn nhiều lần (mỗi request tốn ~5-15s để CapCut tổng hợp + bạn phải poll).
- Dễ bị risk-control để ý vì tần suất request cao bất thường.

Cách làm đúng: gom text thành mảng, gọi 1 lần:

```python
import sys
sys.path.insert(0, "capcut-tts-api-main")
from capcut_common_task_client import (
    BASE as CAPCUT_BASE,
    tts_new_body, query_body, common_query,
    compact_json, base_headers, make_sign_header, DEFAULT_DEVICE,
)

def capcut_tts_batch(texts, voice_type, resource_id, out_paths, device_id, rate="1.0"):
    """Gửi nhiều text trong 1 request, lưu từng mp3 vào out_paths tương ứng (cùng thứ tự)."""
    import requests, json, time
    from copy import deepcopy
    from urllib.parse import urlencode

    session = requests.Session()
    session.trust_env = False  # tránh dính proxy hệ thống linh tinh

    device = deepcopy(DEFAULT_DEVICE)
    device["device_id"] = device_id
    device["tdid"]      = device_id

    babi, body = tts_new_body(texts, voice_type, resource_id, rate, device)
    url = CAPCUT_BASE + "/lv/v1/common_task/new?" + urlencode(common_query(device, babi, include_region=True))
    body_text = compact_json(body)
    headers = base_headers(device, body_text, appid=True)
    lh = {k.lower(): v for k, v in headers.items()}
    headers["sign"] = make_sign_header(url, device["appvr"], lh["device-time"], device["tdid"])

    r = session.post(url, headers=headers, data=body_text.encode(), timeout=30)
    d = r.json()
    if d.get("ret") != "0":
        raise RuntimeError(f"CapCut submit lỗi: {d}")
    task = d["data"]["tasks"][0]
    task_id, token = task["id"], task["token"]

    # Poll cho tới khi task xong — batch càng to càng cần chờ lâu hơn
    max_polls = max(40, int((60 + 3 * len(texts)) / 1.5))
    for _ in range(max_polls):
        time.sleep(1.5)
        body2 = query_body(task_id, token, "sami_text_to_speech")
        url2 = CAPCUT_BASE + "/lv/v1/common_task/query?" + urlencode(common_query(device, None, include_region=False))
        body_text2 = compact_json(body2)
        headers2 = base_headers(device, body_text2, appid=True)
        lh2 = {k.lower(): v for k, v in headers2.items()}
        headers2["sign"] = make_sign_header(url2, device["appvr"], lh2["device-time"], device["tdid"])
        r2 = session.post(url2, headers=headers2, data=body_text2.encode(), timeout=30)
        t2 = r2.json()["data"]["tasks"][0]

        if t2["status"] == "succeed":
            payload = json.loads(t2["payload"])
            by_text = {item["text"]: item["speech_url"] for item in payload["audio_subtitles"]}
            for text, out_path in zip(texts, out_paths):
                url_audio = by_text.get(text)
                if url_audio:
                    out_path.write_bytes(session.get(url_audio, timeout=30).content)
            return
        if t2["status"] == "failed":
            raise RuntimeError(f"CapCut TTS failed: {t2}")
    raise RuntimeError(f"CapCut TTS batch timeout sau {int(max_polls * 1.5)}s")
```

**Kích thước batch khuyến nghị: 50 đoạn/request.** Batch lớn hơn vẫn hoạt động
nhưng thời gian tổng hợp phía CapCut kéo dài, tăng rủi ro timeout khi mạng
không ổn định. Nếu bạn có hàng trăm/nghìn câu, chia thành nhiều batch 50 và
nghỉ 1-2 giây giữa các batch (`time.sleep(1.5)`), đừng bắn liên tục.

**Map kết quả theo `text`, không theo thứ tự index** — CapCut có thể trả về
audio không đúng thứ tự bạn gửi lên. Dùng dict `{text: speech_url}` để tra
cứu đúng, chỉ fallback về index nếu text không khớp (do CapCut tự ý chuẩn
hoá khoảng trắng/ký tự lạ).

## 2. Làm sạch text trước khi gửi

CapCut TTS đôi khi lỗi hoặc đọc sai với ký tự đặc biệt. Lọc trước khi gửi,
chỉ giữ chữ, số, dấu tiếng Việt và dấu câu cơ bản:

```python
import re
clean = re.sub(r"[^\w\sÀ-ɏḀ-ỿ.,!?;:()\-–\"']", " ", text).strip() or "."
```

Câu rỗng sau khi lọc thì thay bằng `"."` để không gửi request với text rỗng
(CapCut có thể trả lỗi hoặc audio vô nghĩa).

## 3. Phân biệt 2 loại lỗi — đừng đổi `device_id` cho MỌI lỗi

Đây là điểm quan trọng nhất để tránh vừa tốn thời gian vừa dễ bị chặn thật.

**Lỗi tạm thời (transient)** — do server CapCut nội bộ reset/quá tải, KHÔNG
phải bạn bị chặn. Chỉ cần retry, không cần đổi gì:

```python
_TRANSIENT_MARKERS = (
    "connection reset by peer", "httpcode=499", "httpcode=502",
    "httpcode=503", "httpcode=504", "timeout", "timed out",
    "read timed out", "connection aborted", "remote end closed",
)

def _is_transient(err_text: str) -> bool:
    return any(m in err_text.lower() for m in _TRANSIENT_MARKERS)
```

Gặp lỗi transient → đợi ngắn (`min(3 * lần_lỗi_liên_tiếp, 15)` giây) rồi gọi
lại với **cùng `device_id`**.

**Lỗi có vẻ bị chặn thật** (risk control, permission denied, rate limit theo
device...) — mọi lỗi KHÔNG khớp marker transient ở trên. Lúc này mới:

1. Đổi sang `device_id` ngẫu nhiên mới.
2. Nếu có sẵn cơ chế đổi IP (VPN), đổi luôn — CapCut risk-control thường khoá
   theo cặp (device_id, IP).
3. Đợi lâu hơn (`min(5 * lần_lỗi, 60)` giây) trước khi thử lại.

```python
import random
def new_device_id() -> str:
    return str(random.randint(7000000000000000000, 7999999999999999999))
```

`device_id` là số 19 chữ số bắt đầu bằng 7 (giống dải ID thật CapCut cấp cho
thiết bị desktop) — số ngẫu nhiên trong dải này thường được server chấp nhận
như một thiết bị "mới toanh" chưa bị đánh dấu.

**Luôn đặt giới hạn số lần thử lại tối đa** (ví dụ 30 lần) rồi `raise` hẳn ra
ngoài — retry vô hạn không giới hạn thời gian nghỉ đã từng khiến một job chạy
tới hơn 40.000 lần lặp mà không dừng, ghi log tràn ổ đĩa. Đừng lặp lại lỗi đó.

## 4. Ghi `device_id` mới vào `.env` để dùng lại

Sau khi đổi `device_id` thành công (vượt qua được lỗi chặn), lưu lại luôn để
lần chạy sau dùng thẳng device đã "sạch", đỡ phải đổi lại từ đầu:

```python
def save_device_id(new_id: str, env_path):
    if not env_path.exists():
        return
    lines = env_path.read_text().splitlines()
    new_lines = [f"CAPCUT_DEVICE_ID={new_id}" if l.startswith("CAPCUT_DEVICE_ID=") else l
                 for l in lines]
    if not any(l.startswith("CAPCUT_DEVICE_ID=") for l in lines):
        new_lines.append(f"CAPCUT_DEVICE_ID={new_id}")
    env_path.write_text("\n".join(new_lines) + "\n")
```

## 5. Chọn giọng — danh sách voice tiếng Việt đã test hoạt động tốt

Mỗi giọng cần cả `voice_type` lẫn `resource_id` tương ứng (thiếu 1 trong 2
là lỗi). Danh sách dưới đây đã dùng ổn định trong sản xuất:

| Tên gợi nhớ | `voice_type` | `resource_id` |
|---|---|---|
| Nhỏ Ngọt Ngào | `BV421_vivn_streaming` | `7252594014782755330` |
| Cô Gái Hoạt Ngôn | `BV074_streaming` | `7102355709945188865` |
| Giọng Nữ Phổ Thông | `vi_female_huong` | `7264854897953083905` |
| Giọng Bé | `BV074_streaming_dsp` | `7550087831092251920` |
| Mai | `BV562_streaming` | `7483736254694035984` |
| Giọng Gái Mới Lớn | `multi_female_peiqi_uranus_bigtts` | `7637458789033151751` |
| Ban Mai | `multi_female_yangguangnv_uranus_bigtts` | `7637456432522218773` |
| Bản Tin Nữ | `multi_female_sisi_uranus_bigtts` | `7637455857285860629` |
| Thanh Niên Tự Tin | `BV075_streaming` | `7102355803792740865` |
| Giọng Nam Trầm | `multi_male_felipe_uranus_bigtts` | `7637456729696996628` |
| Alex Đại Đế | `BV560_streaming` | `7483736167565758992` |

Xem thêm voice khác trong `Voice.json` (danh sách đầy đủ CapCut hỗ trợ, nhiều
ngôn ngữ) nếu cần giọng ngoài tiếng Việt.

`rate` (tốc độ đọc): `"1.0"` là tốc độ chuẩn. Test thực tế cho thấy `"1.2"`
(nhanh hơn 20%) nghe vẫn tự nhiên và phù hợp video ngắn (TikTok/Shorts) —
đọc nhanh hơn giúp video đỡ dài mà không bị "gấp gáp" khó nghe. Trên `1.3`
bắt đầu nghe hơi vội.

## 6. Timeout hợp lý theo độ dài batch

Đừng dùng 1 timeout cố định cho mọi batch — batch càng nhiều câu, CapCut càng
mất nhiều thời gian tổng hợp xong tất cả trước khi trả `status: succeed`:

```python
max_polls = max(40, int((60 + 3 * len(texts)) / 1.5))
# poll mỗi 1.5s → chờ tối thiểu 60s, cộng thêm ~3s cho mỗi câu trong batch
```

Với batch 50 câu: tối thiểu chờ `(60 + 150) / 1.5 ≈ 140` lần poll × 1.5s
`≈ 210s` (3.5 phút) trước khi coi là timeout thật.

## 7. Khi task `failed`, in chi tiết lỗi thay vì chỉ "failed"

CapCut trả nhiều field lỗi khác nhau tuỳ tình huống — in hết ra để biết
nguyên nhân thật (hết quota, text vi phạm content policy, risk control...):

```python
if t2["status"] == "failed":
    err_detail = {k: t2.get(k) for k in
                  ("status", "err_code", "err_msg", "error", "message", "fail_reason")
                  if t2.get(k) is not None}
    raise RuntimeError(f"CapCut TTS failed — chi tiết: {err_detail or t2}")
```

## 8. Tóm tắt quy trình khuyến nghị cho 1 job TTS hàng loạt

1. Gom toàn bộ câu cần đọc thành danh sách text đã làm sạch (mục 2).
2. Chia thành batch 50 câu.
3. Với mỗi batch: gọi `capcut_tts_batch`, bọc trong vòng lặp retry
   (mục 3) tối đa ~30 lần, phân biệt lỗi tạm thời/lỗi bị chặn.
4. Giữa 2 batch: nghỉ 1–2 giây, không bắn liên tục.
5. Nếu đổi `device_id` để vượt chặn, lưu lại vào `.env` (mục 4).
6. Ghép các file mp3 kết quả theo đúng thứ tự gốc (không theo thứ tự
   CapCut trả về task) bằng `ffmpeg -f concat`.

Xem thêm `HUONG_DAN_TRANH_BLOCK.md` trong cùng thư mục để hiểu sâu hơn về
cách CapCut phát hiện & chặn thiết bị, và cách hạn chế bị chặn ngay từ đầu.
