# CapCut Common Task Client

Pure Python command-line client for CapCut common task workflows:

- Text to Speech (TTS)
- Speech to Text / subtitle recognition (STT)
- Audio upload for STT
- Task polling for TTS and STT

This client does not call native libraries, does not load `.dylib` files, does not use C++ helpers, and does not use `ctypes`. Request construction, payload signing, upload signing, and VOD authorization are implemented in Python.

> Use this tool only with accounts, devices, sessions, and media that you are authorized to use.

## Donate

If this project helps your work, you can support development with USDT on TRC20:

```text
TL4sPkfSTVnmneKvvuCfa2wSDnADjxDqYV
```

Network: TRC20

---

## English

### Features

- Builds CapCut `/lv/v1/common_task/new` requests for TTS and STT.
- Uploads local audio/video files to the CapCut text-recognition VOD space before STT.
- Polls `/lv/v1/common_task/query` for task results.
- Generates request body hashes with `x-ss-stub`.
- Generates the common `sign` header used by the captured CapCut flow.
- Generates the TTS inner payload RSA signature in pure Python.
- Generates AWS SigV4 authorization for `ApplyUploadInner` and `CommitUploadInner` in pure Python.
- Supports `--device-json` overrides for device/session values.

### Requirements

- Python 3.9+
- `requests`

Install dependency:

```bash
python3 -m pip install requests
```

### Device Configuration

The script includes a default CapCut desktop device profile in `DEFAULT_DEVICE`. You can override any field by passing a JSON file:

```bash
python3 capcut_common_task_client.py tts-new \
  --device-json device.json \
  --text "Hello world"
```

Example `device.json`:

```json
{
  "device_id": "7647183892936328721",
  "iid": "7647185302080423697",
  "tdid": "7647183892936328721",
  "appvr": "8.7.0",
  "version_name": "8.7.0",
  "version_code": "8.7.0",
  "lan": "vi-VN",
  "loc": "VN",
  "region": "VN"
}
```

### Commands

#### 1. Create a TTS Task

```bash
python3 capcut_common_task_client.py tts-new \
  --text "Hello world"
```

Useful voice options:

```bash
python3 capcut_common_task_client.py tts-new \
  --text "Hello world" \
  --voice BV074_streaming \
  --resource-id 7102355709945188865 \
  --rate 1.0
```

The response contains a task `id` and `token`:

```json
{
  "data": {
    "tasks": [
      {
        "id": "...",
        "status": "queueing",
        "token": "..."
      }
    ]
  }
}
```

#### 2. Query a TTS Task

```bash
python3 capcut_common_task_client.py tts-query \
  --task-id "TASK_ID" \
  --token "TOKEN"
```

#### 3. Upload an Audio/Video File

```bash
python3 capcut_common_task_client.py upload-audio \
  --audio-file 1.mp4
```

Example output:

```json
{
  "vid": "v10639g5000...",
  "md5": "6171f4249ae1561cab6c4e4f1e1d71fa",
  "local_md5": "6171f4249ae1561cab6c4e4f1e1d71fa",
  "duration_ms": 1008,
  "format": "mp3",
  "size": 20160,
  "file_type": "audio",
  "store_uri": "tos-alisg-v-37d494-sg/..."
}
```

#### 4. Create an STT Task from an Uploaded File

Use this when you already have `vid` and `md5` from `upload-audio`:

```bash
python3 capcut_common_task_client.py stt-new \
  --audio-vid "VID_FROM_UPLOAD" \
  --audio-md5 "MD5_FROM_UPLOAD" \
  --duration-ms 1008 \
  --language vi-VN
```

#### 5. Upload and Create STT in One Command

```bash
python3 capcut_common_task_client.py stt-file \
  --audio-file 1.mp4 \
  --language vi-VN
```

The command first uploads the media, then submits the STT task. The response contains a task `id` and `token`.

#### 6. Query an STT Task

```bash
python3 capcut_common_task_client.py stt-query \
  --task-id "TASK_ID" \
  --token "TOKEN"
```

#### 7. Save Response to a File

```bash
python3 capcut_common_task_client.py stt-query \
  --task-id "TASK_ID" \
  --token "TOKEN" \
  --out response.json
```

#### 8. Preview a Request Without Calling the API

```bash
python3 capcut_common_task_client.py stt-new \
  --audio-vid "VID_FROM_UPLOAD" \
  --audio-md5 "MD5_FROM_UPLOAD" \
  --duration-ms 1000 \
  --language vi-VN \
  --dry-run
```

### How It Works

#### TTS Flow

1. Build SSML from `--text`, `--voice`, `--resource-id`, and `--rate`.
2. Create the inner TTS payload.
3. Generate the payload `sign` using RSA PKCS#1 v1.5 in pure Python.
4. Wrap the payload in a CapCut common task body.
5. Generate `x-ss-stub`, `x-khronos`, `device-time`, and request `sign`.
6. POST to `/lv/v1/common_task/new`.
7. Poll `/lv/v1/common_task/query` with the returned task `id` and `token`.

#### STT File Flow

1. Call `/lv/v1/upload_sign` to obtain temporary VOD credentials.
2. Sign `ApplyUploadInner` with AWS SigV4.
3. Upload the media bytes to the returned VOD upload host.
4. Finish the upload with the part CRC32.
5. Sign and call `CommitUploadInner` to receive the media `vid`, `md5`, and duration.
6. Submit `/lv/v1/common_task/new` with `req_key=cc_audio_subtitle_asr`.
7. Poll `/lv/v1/common_task/query` until the task succeeds.

### Where Are the Subtitles?

STT query responses store subtitles inside:

```text
data.tasks[0].payload
```

`payload` is itself a JSON string. Parse it, then read:

```text
payload.utterances[].text
payload.utterances[].start_time
payload.utterances[].end_time
payload.utterances[].words[]
```

Quick extractor:

```bash
python3 - <<'PY'
import json

data = json.load(open("response.json", encoding="utf-8"))
payload = json.loads(data["data"]["tasks"][0]["payload"])

for item in payload.get("utterances", []):
    print(f'[{item["start_time"]}ms -> {item["end_time"]}ms] {item["text"]}')
PY
```

### Notes

- `upload-audio` accepts media files such as `.mp3`, `.m4a`, and `.mp4` when CapCut's upload service can parse the media.
- `duration_ms` is read from the upload commit result when using `stt-file`.
- The removed device-generation flow is intentionally not part of this client. Device identity should be configured explicitly through `DEFAULT_DEVICE` or `--device-json`.

---

## Tiếng Việt

### Donate / Ủng hộ

Nếu project hữu ích cho công việc của bạn, có thể ủng hộ bằng USDT mạng TRC20:

```text
TL4sPkfSTVnmneKvvuCfa2wSDnADjxDqYV
```

Network: TRC20

### Tính năng

- Tạo request CapCut `/lv/v1/common_task/new` cho TTS và STT.
- Upload file audio/video local lên VOD space dùng cho nhận diện phụ đề.
- Query `/lv/v1/common_task/query` để lấy kết quả task.
- Tạo `x-ss-stub` bằng MD5 của body.
- Tạo header `sign` theo flow CapCut đã phân tích.
- Tạo chữ ký RSA cho payload TTS bằng Python thuần.
- Tạo AWS SigV4 cho `ApplyUploadInner` và `CommitUploadInner` bằng Python thuần.
- Hỗ trợ override cấu hình thiết bị/session bằng `--device-json`.

### Yêu cầu

- Python 3.9+
- `requests`

Cài dependency:

```bash
python3 -m pip install requests
```

### Cấu hình thiết bị

Script có sẵn profile thiết bị CapCut desktop trong `DEFAULT_DEVICE`. Có thể override bằng file JSON:

```bash
python3 capcut_common_task_client.py tts-new \
  --device-json device.json \
  --text "Xin chào"
```

Ví dụ `device.json`:

```json
{
  "device_id": "7647183892936328721",
  "iid": "7647185302080423697",
  "tdid": "7647183892936328721",
  "appvr": "8.7.0",
  "version_name": "8.7.0",
  "version_code": "8.7.0",
  "lan": "vi-VN",
  "loc": "VN",
  "region": "VN"
}
```

### Cách dùng

#### 1. Tạo task TTS

```bash
python3 capcut_common_task_client.py tts-new \
  --text "Xin chào"
```

Tuỳ chỉnh giọng đọc:

```bash
python3 capcut_common_task_client.py tts-new \
  --text "Xin chào" \
  --voice BV074_streaming \
  --resource-id 7102355709945188865 \
  --rate 1.0
```

Response sẽ có `id` và `token` của task.

#### 2. Query task TTS

```bash
python3 capcut_common_task_client.py tts-query \
  --task-id "TASK_ID" \
  --token "TOKEN"
```

#### 3. Upload file audio/video

```bash
python3 capcut_common_task_client.py upload-audio \
  --audio-file 1.mp4
```

Kết quả trả về gồm `vid`, `md5`, `duration_ms`, `format`, `size`, `file_type`, và `store_uri`.

#### 4. Tạo task STT từ file đã upload

Khi đã có `vid` và `md5`:

```bash
python3 capcut_common_task_client.py stt-new \
  --audio-vid "VID_FROM_UPLOAD" \
  --audio-md5 "MD5_FROM_UPLOAD" \
  --duration-ms 1008 \
  --language vi-VN
```

#### 5. Upload rồi tạo STT bằng một lệnh

```bash
python3 capcut_common_task_client.py stt-file \
  --audio-file 1.mp4 \
  --language vi-VN
```

Lệnh này upload file trước, lấy `vid/md5/duration_ms`, rồi tự submit task STT.

#### 6. Query task STT

```bash
python3 capcut_common_task_client.py stt-query \
  --task-id "TASK_ID" \
  --token "TOKEN"
```

#### 7. Lưu response ra file

```bash
python3 capcut_common_task_client.py stt-query \
  --task-id "TASK_ID" \
  --token "TOKEN" \
  --out response.json
```

#### 8. Xem request mà không gọi API

```bash
python3 capcut_common_task_client.py stt-new \
  --audio-vid "VID_FROM_UPLOAD" \
  --audio-md5 "MD5_FROM_UPLOAD" \
  --duration-ms 1000 \
  --language vi-VN \
  --dry-run
```

### Cách thức hoạt động

#### Flow TTS

1. Tạo SSML từ `--text`, `--voice`, `--resource-id`, và `--rate`.
2. Tạo payload TTS bên trong.
3. Ký payload bằng RSA PKCS#1 v1.5 thuần Python.
4. Đóng payload vào body common task.
5. Tạo `x-ss-stub`, `x-khronos`, `device-time`, và header `sign`.
6. POST tới `/lv/v1/common_task/new`.
7. Query `/lv/v1/common_task/query` bằng `task_id` và `token`.

#### Flow STT từ file

1. Gọi `/lv/v1/upload_sign` để lấy credential VOD tạm thời.
2. Ký `ApplyUploadInner` bằng AWS SigV4.
3. Upload bytes của file lên VOD upload host.
4. Finish upload bằng CRC32 của part.
5. Ký và gọi `CommitUploadInner` để lấy `vid`, `md5`, và duration.
6. Submit `/lv/v1/common_task/new` với `req_key=cc_audio_subtitle_asr`.
7. Query `/lv/v1/common_task/query` đến khi task thành công.

### Phụ đề nằm ở đâu?

Response STT chứa phụ đề trong:

```text
data.tasks[0].payload
```

`payload` là JSON string. Parse string này rồi đọc:

```text
payload.utterances[].text
payload.utterances[].start_time
payload.utterances[].end_time
payload.utterances[].words[]
```

Trích phụ đề nhanh:

```bash
python3 - <<'PY'
import json

data = json.load(open("response.json", encoding="utf-8"))
payload = json.loads(data["data"]["tasks"][0]["payload"])

for item in payload.get("utterances", []):
    print(f'[{item["start_time"]}ms -> {item["end_time"]}ms] {item["text"]}')
PY
```

### Ghi chú

- `upload-audio` có thể dùng với `.mp3`, `.m4a`, `.mp4` nếu dịch vụ upload của CapCut đọc được media.
- Với `stt-file`, `duration_ms` được lấy tự động từ kết quả commit upload.
- Flow tạo thiết bị tự động đã bị loại bỏ. Cấu hình thiết bị nên được khai báo rõ bằng `DEFAULT_DEVICE` hoặc `--device-json`.
