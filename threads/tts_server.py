"""TTS server riêng cho dự án threads/ (short video Threads).

Đây LÀ một service tách biệt, KHÔNG phải một phần của content_video.py /
translate_video.py — chỉ tái sử dụng phần hạ tầng gọi CapCut TTS đã được kiểm
chứng ổn định ở gốc repo (capcut_tts_batch, xoay device_id khi bị block) thay
vì viết lại từ đầu. Xem threads/CONTEXT.md mục "CHƯA làm" để biết lý do và
ràng buộc (không đụng vào 2 file kia, chỉ import hàm dùng chung).

Chạy: python3 threads/tts_server.py  (mặc định cổng 8007, các server khác
trong repo dùng 8004/8005/8006 nên tránh trùng).
"""
import os
import json
import random
import re
import sys
import uuid
import threading
import time
import asyncio
import subprocess
import unicodedata
import contextvars
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent.parent  # gốc repo, nơi có translate_video.py
load_dotenv(BASE_DIR / ".env")
sys.path.insert(0, str(BASE_DIR))

import translate_video as tv  # noqa: E402  (tái dùng capcut_tts_batch + danh sách giọng VN)
try:
    from account_api import consume_tts_quota, current_identity, finish_ai_script_credit, reserve_ai_script_credit, tts_identity
except ImportError:
    # Cho phép chạy dạng module từ repo root.
    from threads.account_api import consume_tts_quota, current_identity, finish_ai_script_credit, reserve_ai_script_credit, tts_identity

AUDIO_DIR = Path(os.environ.get("TTS_AUDIO_DIR", Path(__file__).resolve().parent / "tts_audio"))
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_PHANBIET_DIR = BASE_DIR / "allYoutube" / "videoPhanBiet"
EXPORT_DIR = VIDEO_PHANBIET_DIR / "exports"
ENABLE_LEGACY_TOOLS = os.getenv("ENABLE_LEGACY_TOOLS", "0").lower() in ("1", "true", "yes")
if ENABLE_LEGACY_TOOLS:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = os.environ.get("FFPROBE_BIN", "ffprobe")
TTS_FILE_TTL_SECONDS = max(300, int(os.environ.get("TTS_FILE_TTL_SECONDS", "3600")))

app = FastAPI(title="Threads TTS Service")
AI_REQUEST_LOCK = threading.Lock()
export_jobs: dict[str, dict] = {}
_request_id_ctx = contextvars.ContextVar("tts_request_id", default="-")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=os.getenv(
        "CORS_ORIGIN_REGEX",
        r"^(null|https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?)$",
    ),
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Authorization", "Content-Type", "X-Admin-Key"],
)
if ENABLE_LEGACY_TOOLS:
    app.mount("/videoPhanBiet", StaticFiles(directory=str(VIDEO_PHANBIET_DIR)), name="videoPhanBiet")


def _tts_log(event: str, **fields) -> None:
    """Một dòng JSON cho mỗi mốc để tra trọn luồng theo request_id."""
    record = {
        "event": event,
        "request_id": _request_id_ctx.get(),
        "ts": round(time.time(), 3),
        **fields,
    }
    print("TTS_TRACE " + json.dumps(record, ensure_ascii=False, default=str), flush=True)


@app.middleware("http")
async def trace_requests(request: Request, call_next):
    request_id = request.headers.get("x-request-id", "").strip()[:80] or uuid.uuid4().hex[:12]
    token = _request_id_ctx.set(request_id)
    started = time.perf_counter()
    client = request.client.host if request.client else "unknown"
    if request.url.path != "/health":
        _tts_log("request_start", method=request.method, path=request.url.path, client=client)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        if request.url.path != "/health":
            _tts_log("request_end", method=request.method, path=request.url.path,
                     status=response.status_code,
                     elapsed_ms=round((time.perf_counter() - started) * 1000, 1))
        return response
    except Exception as exc:
        _tts_log("request_exception", method=request.method, path=request.url.path,
                 elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
                 error_type=type(exc).__name__, error=str(exc)[:1000])
        raise
    finally:
        _request_id_ctx.reset(token)


@app.get("/health", include_in_schema=False)
def health():
    """Health check nhẹ để Render đánh thức service mà không chạm database/CapCut."""
    return {"ok": True, "service": "threads-api"}


def _cleanup_expired_tts_files() -> None:
    """TTS là file chuyển tiếp; bản desktop tải về máy user ngay sau khi tạo."""
    cutoff = time.time() - TTS_FILE_TTL_SECONDS
    for path in AUDIO_DIR.glob("*.mp3"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def _require_legacy_tools() -> None:
    if not ENABLE_LEGACY_TOOLS:
        raise HTTPException(404, "Chức năng này không được bật trên production")


class TTSRequest(BaseModel):
    text: str
    voice_type: str | None = None   # vd "BV074_streaming" — bỏ trống dùng mặc định
    resource_id: str | None = None
    rate: str = "1.0"
    speed: float = 1.0
    # Hiệu ứng "vang" (mục nhiều đoạn TTS trong index.html) — người dùng chọn
    # Hiệu ứng vang phòng nhẹ, đồng thời tăng âm lượng đầu ra 30%.
    # Limiter ở cuối giữ các đỉnh lớn không bị clipping/rè. Áp dụng cho TOÀN BỘ 1 đoạn
    # TTS (không tách riêng từng từ — CapCut không trả timestamp từng từ để
    # cắt chính xác).
    echo: bool = False


class TTSBatchRequest(BaseModel):
    texts: list[str]
    voice_type: str | None = None
    resource_id: str | None = None
    rate: str = "1.0"
    speed: float = 1.0
    echo: bool = False


class ProjectTTSItem(BaseModel):
    text: str
    voice_type: str | None = None
    resource_id: str | None = None
    speed: float = 1.0
    echo: bool = False


class ProjectTTSBatchRequest(BaseModel):
    items: list[ProjectTTSItem]


class MixedTTSItem(BaseModel):
    text: str
    englishPhrases: list[str] = []


class MixedTTSBatchRequest(BaseModel):
    items: list[MixedTTSItem]
    vi_voice_type: str | None = None
    vi_resource_id: str | None = None
    en_voice_type: str | None = None
    en_resource_id: str | None = None
    rate: str = "1.0"


class GenerateComparisonsRequest(BaseModel):
    count: int
    theme: str = ""


class GenerateThreadsScriptRequest(BaseModel):
    prompt: str
    platform: str = "auto"
    length: str = "medium"
    allow_echo: bool = True
    idempotency_key: str


AI_FEMALE_VOICES = ("Nhỏ Ngọt Ngào", "Giọng Nữ Phổ Thông", "Giọng Bé", "Cô Gái Hoạt Ngôn", "Mai")
AI_MALE_VOICES = ("Thanh Niên Tự Tin", "Alex Đại Đế", "Nam Minh")
AI_ALLOWED_VOICES = set(AI_FEMALE_VOICES + AI_MALE_VOICES)


def _sanitize_ai_tts_text(value: object, visible_text: str = "") -> str:
    """TTS chỉ giữ chữ/số, khoảng trắng, dấu phẩy và dấu chấm.

    Dấu hỏi và các ký tự đặc biệt bị loại khỏi lời đọc. Xử lý từng dòng để
    không làm mất ranh giới progressive reveal.
    """
    source = str(value or "")
    lines = []
    source_lines = source.splitlines()
    visible_lines = str(visible_text or "").splitlines()
    for line_index, raw_line in enumerate(source_lines):
        kept = []
        for char in raw_line:
            category = unicodedata.category(char)
            if char in ",." or char.isspace() or category[0] in ("L", "N"):
                kept.append(char)
            else:
                kept.append(" ")
        cleaned_line = re.sub(r"\s+", " ", "".join(kept)).strip()
        if len(source_lines) == len(visible_lines) and re.fullmatch(r"\?+", visible_lines[line_index].strip()):
            cleaned_line = "hỏi chấm"
        lines.append(cleaned_line)
    if re.fullmatch(r"\?+", str(visible_text or "").strip()):
        return "hỏi chấm"
    return "\n".join(line for line in lines if line).strip()


def _validate_ai_question_reading(text: str, tts_text: str, label: str) -> None:
    visible_parts = [part.strip() for part in text.splitlines() if part.strip()]
    spoken_parts = [part.strip() for part in tts_text.splitlines() if part.strip()]
    if len(visible_parts) != len(spoken_parts):
        return  # Luật progressive sẽ báo lỗi rõ hơn ở bước sau.
    for index, visible in enumerate(visible_parts):
        if re.fullmatch(r"\?+", visible) and spoken_parts[index].lower() != "hỏi chấm":
            raise ValueError(f"{label}: đoạn chỉ có dấu ? phải có ttsText là hỏi chấm")


def _extract_json_object(raw: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(raw or "").strip(), flags=re.IGNORECASE)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start: raise ValueError("AI không trả JSON object")
        value = json.loads(cleaned[start:end + 1])
    if not isinstance(value, dict): raise ValueError("Kịch bản AI phải là JSON object")
    return value


def _clean_ai_script(value: dict, allow_echo: bool) -> dict:
    raw_cards = value.get("cards")
    if not isinstance(raw_cards, list) or not raw_cards or len(raw_cards) > 40:
        raise ValueError("AI phải trả từ 1 đến 40 card")
    content_units = sum(1 if not isinstance(c, dict) or c.get("type") != "mess" else len(c.get("messages") or []) for c in raw_cards)
    cards, echo_budget = [], max(1, content_units // 5)
    echo_used = 0
    for card_index, raw in enumerate(raw_cards):
        if not isinstance(raw, dict): raise ValueError(f"Card {card_index + 1} không hợp lệ")
        kind = str(raw.get("type") or "").lower()
        if kind not in ("post", "comment", "reply", "mess"):
            raise ValueError(f"AI tạo loại card bị cấm: {kind or '?'}")
        if kind == "mess":
            messages = raw.get("messages")
            if not isinstance(messages, list) or not messages or len(messages) > 60:
                raise ValueError("Hội thoại phải có 1-60 tin nhắn")
            mine_gender = str(raw.get("mineGender") or "male").lower()
            theirs_gender = str(raw.get("theirsGender") or "female").lower()
            if mine_gender not in ("male", "female") or theirs_gender not in ("male", "female"):
                raise ValueError("Giới tính Messenger chỉ nhận male/female")
            mine_voice = str(raw.get("mineVoice") or "")
            theirs_voice = str(raw.get("theirsVoice") or "")
            if mine_voice not in (AI_MALE_VOICES if mine_gender == "male" else AI_FEMALE_VOICES): raise ValueError("Giọng Tôi không khớp giới tính")
            if theirs_voice not in (AI_MALE_VOICES if theirs_gender == "male" else AI_FEMALE_VOICES): raise ValueError("Giọng Đối phương không khớp giới tính")
            cleaned_messages = []
            for message in messages:
                side, text = str(message.get("side") or ""), str(message.get("text") or "").strip()
                tts_text = _sanitize_ai_tts_text(message.get("ttsText"), text)
                if side not in ("mine", "theirs") or not text or not tts_text or len(text) > 1200 or len(tts_text) > 1800: raise ValueError("Tin nhắn AI thiếu text/ttsText hợp lệ")
                _validate_ai_question_reading(text, tts_text, "Tin nhắn AI")
                echo = bool(message.get("echo")) and allow_echo and echo_used < echo_budget
                if echo: echo_used += 1
                cleaned_messages.append({"side": side, "text": text, "ttsText": tts_text, "echo": echo})
            cards.append({"type":"mess","partnerName":str(raw.get("partnerName") or "Đối phương")[:80],"platform":str(raw.get("platform") or "messenger"),"mineGender":mine_gender,"theirsGender":theirs_gender,"mineVoice":mine_voice,"theirsVoice":theirs_voice,"messages":cleaned_messages})
            continue
        text = str(raw.get("text") or "").strip()
        tts_text = _sanitize_ai_tts_text(raw.get("ttsText"), text)
        if not text or not tts_text or len(text) > 8000 or len(tts_text) > 12000: raise ValueError(f"Card {kind} thiếu text/ttsText")
        _validate_ai_question_reading(text, tts_text, f"Card {kind}")
        voice = str(raw.get("voice") or "")
        if voice not in AI_ALLOWED_VOICES: raise ValueError(f"Giọng card {kind} không thuộc whitelist")
        echo = bool(raw.get("echo")) and allow_echo and echo_used < echo_budget
        if echo: echo_used += 1
        platform = "threads" if kind == "post" else str(raw.get("platform") or "threads").lower()
        if platform not in ("threads", "tiktok"): platform = "threads"
        progressive = bool(raw.get("progressiveReveal")) and "\n" in text
        if progressive:
            visible_parts = [part.strip() for part in text.splitlines() if part.strip()]
            spoken_parts = [part.strip() for part in tts_text.splitlines() if part.strip()]
            if len(visible_parts) != len(spoken_parts):
                raise ValueError(f"Card {kind} có số đoạn text và ttsText không khớp")
        # Ranh giới cụm nội dung là luật của renderer, không giao AI quyết:
        # Post/Comment mở cụm mới; Reply nối tiếp Comment ngay trước nó.
        cards.append({"type":kind,"text":text,"ttsText":tts_text,"platform":platform,"voice":voice,"echo":echo,"progressiveReveal":progressive,"clearBefore":kind in ("post", "comment")})
    return {"title": str(value.get("title") or "Kịch bản AI")[:120], "cards": cards}


class ExportTrack(BaseModel):
    comparisonIndex: int
    scene: str
    audioUrl: str
    duration: float


class ExportPhanBietRequest(BaseModel):
    comparisons: list[dict]
    tracks: list[ExportTrack]


def _probe_duration(path: Path) -> float:
    r = subprocess.run(
        [FFPROBE_BIN, "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return float(r.stdout.strip() or "0")


def _voice_json_by_lang(lang_prefix: str) -> list[dict]:
    voice_path = BASE_DIR / "capcut-tts-api-main" / "Voice.json"
    if not voice_path.exists():
        return []
    voices = json.loads(voice_path.read_text(encoding="utf-8"))
    return [
        {
            "label": str(item.get("display_name") or item.get("voice_type")).strip(),
            "voice_type": item.get("voice_type"),
            "resource_id": item.get("resource_id"),
            "engine": "edge" if str(item.get("voice_type", "")).lower().endswith("neural") else "capcut",
        }
        for item in voices
        if str(item.get("lang", "")).lower().startswith(lang_prefix.lower())
        and item.get("voice_type")
        and item.get("resource_id")
    ]


def _is_edge_voice(voice_type: str | None) -> bool:
    return str(voice_type or "").lower().endswith("neural")


async def _edge_tts_files(texts: list[str], voice_type: str, paths: list[Path]) -> None:
    import edge_tts

    async def _save_one(text: str, path: Path) -> None:
        last_error = None
        for attempt in range(5):
            try:
                path.unlink(missing_ok=True)
                await edge_tts.Communicate(text, voice_type).save(str(path))
                if path.exists() and path.stat().st_size > 0:
                    return
                raise RuntimeError("Edge TTS trả về file rỗng")
            except Exception as exc:
                last_error = exc
                if attempt < 4:
                    await asyncio.sleep(2 * (attempt + 1))
        raise RuntimeError(f"Edge TTS lỗi sau 5 lần thử: {last_error}")

    # Edge không hỗ trợ batch nhiều câu trong một task như CapCut. Dù API
    # nội bộ nhận một mảng để frontend dùng chung luồng, mỗi câu vẫn được
    # gửi thành một request RIÊNG và chạy tuần tự để tránh rate-limit/block.
    for index, (text, path) in enumerate(zip(texts, paths)):
        await _save_one(text, path)
        if index < len(texts) - 1:
            await asyncio.sleep(1)


def _split_mixed_text(text: str, english_phrases: list[str]) -> list[tuple[str, str]]:
    clean_text = " ".join((text or "").split())
    phrases = sorted({" ".join(p.split()) for p in english_phrases if p and p.strip()}, key=len, reverse=True)
    if not clean_text or not phrases:
        return [("vi", clean_text)] if clean_text else []

    pattern = re.compile("|".join(re.escape(p) for p in phrases), re.IGNORECASE)
    pieces: list[tuple[str, str]] = []
    cursor = 0
    for match in pattern.finditer(clean_text):
        if match.start() > cursor:
            pieces.append(("vi", clean_text[cursor:match.start()].strip()))
        pieces.append(("en", match.group(0).strip()))
        cursor = match.end()
    if cursor < len(clean_text):
        pieces.append(("vi", clean_text[cursor:].strip()))
    return [(lang, part) for lang, part in pieces if part]


def _concat_audio(parts: list[Path], out_path: Path) -> None:
    if len(parts) == 1:
        parts[0].replace(out_path)
        return
    list_path = out_path.with_suffix(".concat.txt")
    list_path.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    subprocess.run(
        [FFMPEG_BIN, "-y", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(out_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    list_path.unlink(missing_ok=True)


def _apply_echo(path: Path) -> None:
    """Ghi đè MP3 với vang phòng nhẹ và gain đầu ra +30%.

    Bốn phản xạ 38/79/127/181ms không cách đều, decay thấp và giảm nhanh nên
    hòa vào giọng như không gian phòng thay vì nghe thành các bản sao lặp lại.
    Limiter tránh vỡ tiếng sau gain.
    """
    tmp_out = path.with_suffix(".echo.mp3")
    # BUG đã gặp lúc verify: không chỉ định -b:a, FFmpeg tự chọn bitrate mp3
    # mặc định rất thấp (~33kbps thay vì 160kbps gốc CapCut trả về) — nghe
    # rè/mất chất lượng rõ rệt dù hiệu ứng echo đúng. Ép lại đúng 160k để
    # khớp chất lượng gốc.
    subprocess.run(
        [
            FFMPEG_BIN, "-y", "-i", str(path),
            "-af", "aecho=0.88:0.88:38|79|127|181:0.34|0.24|0.15|0.09,volume=2.0,alimiter=limit=0.97:level=false",
            "-b:a", "160k", str(tmp_out),
        ],
        capture_output=True, check=True,
    )
    tmp_out.replace(path)


def _apply_speed(path: Path, speed: float) -> None:
    """Tăng tốc file đã sinh bằng FFmpeg; không gửi speed lên nhà cung cấp TTS."""
    speed = max(1.0, min(float(speed or 1.0), 2.0))
    if abs(speed - 1.0) < 0.001:
        return
    tmp_out = path.with_suffix(".speed.mp3")
    subprocess.run(
        [FFMPEG_BIN, "-y", "-i", str(path), "-filter:a", f"atempo={speed:.3f}",
         "-b:a", "160k", str(tmp_out)],
        capture_output=True, text=True, check=True,
    )
    tmp_out.replace(path)


@app.get("/voices")
def list_voices():
    voices = _voice_json_by_lang("vi")
    if voices:
        return voices
    return [
        {"label": label, "voice_type": voice_type, "resource_id": resource_id}
        for label, voice_type, resource_id in tv.CAPCUT_VOICES_VI
    ]


@app.get("/voices-en")
def list_english_voices():
    voices = _voice_json_by_lang("en")
    if voices:
        return voices
    return [
        {"label": "English", "voice_type": "BV510_streaming", "resource_id": "7081169180120060418"},
        {"label": "American Female", "voice_type": "BV029_streaming", "resource_id": "6898240012404396546"},
    ]


@app.post("/tts")
def generate_tts(req: TTSRequest, _identity: dict = Depends(tts_identity)):
    _cleanup_expired_tts_files()
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "text rỗng")
    if len(text) > int(os.getenv("TTS_MAX_CHARS", "5000")):
        raise HTTPException(400, "Nội dung TTS quá dài")
    consume_tts_quota(_identity, len(text))

    voice_type = req.voice_type or tv.CAPCUT_VOICES_VI[1][1]
    resource_id = req.resource_id or tv.CAPCUT_VOICES_VI[1][2]
    device_id = os.environ.get("CAPCUT_DEVICE_ID")
    if not device_id:
        raise HTTPException(500, "Thiếu CAPCUT_DEVICE_ID trong .env")

    out_name = f"{uuid.uuid4().hex}.mp3"
    out_path = AUDIO_DIR / out_name

    if _is_edge_voice(voice_type):
        try:
            asyncio.run(_edge_tts_files([text], voice_type, [out_path]))
            _apply_speed(out_path, req.speed)
            if req.echo:
                _apply_echo(out_path)
            duration = _probe_duration(out_path)
            if duration <= 0:
                raise RuntimeError("File Edge TTS trả về rỗng/0 giây")
            return {"audio_url": f"/audio/{out_name}", "duration": duration}
        except Exception as e:
            out_path.unlink(missing_ok=True)
            raise HTTPException(502, f"Edge TTS lỗi: {e}")

    current_did = device_id
    last_err = None
    for attempt in range(5):
        try:
            tv.capcut_tts_batch([text], voice_type, resource_id, [out_path], current_did, rate=req.rate)
            _apply_speed(out_path, req.speed)
            if req.echo:
                _apply_echo(out_path)
            duration = _probe_duration(out_path)
            if duration <= 0:
                raise RuntimeError("File TTS trả về rỗng/0 giây")
            return {"audio_url": f"/audio/{out_name}", "duration": duration}
        except Exception as e:
            last_err = e
            current_did = str(random.randint(7000000000000000000, 7999999999999999999))
    raise HTTPException(502, f"CapCut TTS lỗi sau 5 lần đổi device_id: {last_err}")


@app.post("/tts-batch")
def generate_tts_batch(req: TTSBatchRequest, _identity: dict = Depends(tts_identity)):
    batch_started = time.perf_counter()
    _cleanup_expired_tts_files()
    """Tạo tối đa 50 đoạn trong đúng một task CapCut để giảm số request."""
    texts = [text.strip() for text in req.texts]
    if not texts or any(not text for text in texts):
        raise HTTPException(400, "texts phải là mảng các chuỗi không rỗng")
    if len(texts) > 50:
        raise HTTPException(400, "Mỗi batch tối đa 50 đoạn")
    total_chars = sum(map(len, texts))
    if total_chars > int(os.getenv("TTS_BATCH_MAX_CHARS", "20000")):
        raise HTTPException(400, "Tổng nội dung TTS batch quá dài")
    if not _identity.get("_quota_consumed"):
        consume_tts_quota(_identity, total_chars)

    voice_type = req.voice_type or tv.CAPCUT_VOICES_VI[1][1]
    resource_id = req.resource_id or tv.CAPCUT_VOICES_VI[1][2]
    device_id = os.environ.get("CAPCUT_DEVICE_ID")
    if not device_id:
        raise HTTPException(500, "Thiếu CAPCUT_DEVICE_ID trong .env")

    names = [f"{uuid.uuid4().hex}.mp3" for _ in texts]
    paths = [AUDIO_DIR / name for name in names]
    _tts_log("batch_start", engine="edge" if _is_edge_voice(voice_type) else "capcut",
             voice_type=voice_type, resource_id=resource_id, count=len(texts),
             chars=total_chars, speed=req.speed, echo=req.echo)
    if _is_edge_voice(voice_type):
        try:
            provider_started = time.perf_counter()
            asyncio.run(_edge_tts_files(texts, voice_type, paths))
            _tts_log("provider_success", engine="edge", voice_type=voice_type,
                     count=len(texts), elapsed_ms=round((time.perf_counter() - provider_started) * 1000, 1))
            effects_started = time.perf_counter()
            for path in paths:
                _apply_speed(path, req.speed)
            if req.echo:
                for path in paths:
                    _apply_echo(path)
            items = []
            for name, path in zip(names, paths):
                duration = _probe_duration(path)
                if duration <= 0:
                    raise RuntimeError(f"File Edge TTS {name} rỗng/0 giây")
                items.append({"audio_url": f"/audio/{name}", "duration": duration})
            _tts_log("batch_success", engine="edge", voice_type=voice_type, count=len(items),
                     effects_ms=round((time.perf_counter() - effects_started) * 1000, 1),
                     elapsed_ms=round((time.perf_counter() - batch_started) * 1000, 1))
            return {"items": items}
        except Exception as e:
            _tts_log("batch_error", engine="edge", voice_type=voice_type,
                     elapsed_ms=round((time.perf_counter() - batch_started) * 1000, 1),
                     error_type=type(e).__name__, error=str(e)[:1000])
            for path in paths:
                path.unlink(missing_ok=True)
            raise HTTPException(502, f"Edge TTS batch lỗi: {e}")
    current_did = device_id
    last_err = None
    for _attempt in range(5):
        attempt = _attempt + 1
        attempt_started = time.perf_counter()
        _tts_log("provider_attempt_start", engine="capcut", voice_type=voice_type,
                 count=len(texts), attempt=attempt, max_attempts=5,
                 device_id_suffix=current_did[-6:])
        try:
            tv.capcut_tts_batch(texts, voice_type, resource_id, paths, current_did, rate=req.rate)
            _tts_log("provider_success", engine="capcut", voice_type=voice_type,
                     count=len(texts), attempt=attempt,
                     elapsed_ms=round((time.perf_counter() - attempt_started) * 1000, 1))
            effects_started = time.perf_counter()
            for path in paths:
                _apply_speed(path, req.speed)
            if req.echo:
                for path in paths:
                    _apply_echo(path)
            items = []
            for name, path in zip(names, paths):
                duration = _probe_duration(path)
                if duration <= 0:
                    raise RuntimeError(f"File TTS {name} rỗng/0 giây")
                items.append({"audio_url": f"/audio/{name}", "duration": duration})
            _tts_log("batch_success", engine="capcut", voice_type=voice_type, count=len(items),
                     attempt=attempt,
                     effects_ms=round((time.perf_counter() - effects_started) * 1000, 1),
                     elapsed_ms=round((time.perf_counter() - batch_started) * 1000, 1))
            return {"items": items}
        except Exception as exc:
            last_err = exc
            _tts_log("provider_attempt_error", engine="capcut", voice_type=voice_type,
                     count=len(texts), attempt=attempt,
                     elapsed_ms=round((time.perf_counter() - attempt_started) * 1000, 1),
                     error_type=type(exc).__name__, error=str(exc)[:1000])
            current_did = str(random.randint(7000000000000000000, 7999999999999999999))
    for path in paths:
        path.unlink(missing_ok=True)
    _tts_log("batch_error", engine="capcut", voice_type=voice_type, count=len(texts),
             elapsed_ms=round((time.perf_counter() - batch_started) * 1000, 1),
             error_type=type(last_err).__name__ if last_err else "unknown",
             error=str(last_err)[:1000])
    raise HTTPException(502, f"CapCut TTS batch lỗi sau 5 lần đổi device_id: {last_err}")


@app.post("/tts-project-batch")
def generate_project_tts_batch(req: ProjectTTSBatchRequest, identity: dict = Depends(tts_identity)):
    """Nhận toàn bộ TTS còn thiếu của project trong một HTTP request.

    Server tự gom các đoạn cùng giọng/tốc độ/echo rồi chia task CapCut tối đa
    50 đoạn. Kết quả luôn trả theo đúng thứ tự items mà desktop đã gửi.
    """
    _cleanup_expired_tts_files()
    if not req.items or len(req.items) > 2000:
        raise HTTPException(400, "items phải có từ 1 đến 2000 đoạn")
    if any(not item.text.strip() for item in req.items):
        raise HTTPException(400, "Mọi item TTS phải có text không rỗng")
    total_chars = sum(len(item.text) for item in req.items)
    if total_chars > int(os.getenv("TTS_PROJECT_MAX_CHARS", "200000")):
        raise HTTPException(400, "Tổng nội dung TTS project quá dài")
    consume_tts_quota(identity, total_chars)
    batch_identity = {**identity, "_quota_consumed": True}

    grouped: dict[tuple, list[tuple[int, ProjectTTSItem]]] = {}
    for index, item in enumerate(req.items):
        key = (item.voice_type, item.resource_id, round(item.speed, 4), item.echo)
        grouped.setdefault(key, []).append((index, item))

    project_started = time.perf_counter()
    _tts_log("project_start", items=len(req.items), chars=total_chars, groups=len(grouped),
             user_id=identity.get("id"))

    ordered_results: list[dict | None] = [None] * len(req.items)
    total_groups = len(grouped)
    for group_index, ((voice_type, resource_id, speed, echo), indexed_items) in enumerate(grouped.items(), 1):
        _tts_log("project_group_start", group_index=group_index, total_groups=total_groups,
                 voice_type=voice_type, resource_id=resource_id, speed=speed, echo=echo,
                 items=len(indexed_items), chunks=(len(indexed_items) + 49) // 50)
        for start in range(0, len(indexed_items), 50):
            chunk = indexed_items[start:start + 50]
            chunk_started = time.perf_counter()
            chunk_index = start // 50 + 1
            _tts_log("project_chunk_start", group_index=group_index, chunk_index=chunk_index,
                     voice_type=voice_type, items=len(chunk),
                     chars=sum(len(item.text.strip()) for _, item in chunk))
            result = generate_tts_batch(TTSBatchRequest(
                texts=[item.text.strip() for _, item in chunk],
                voice_type=voice_type,
                resource_id=resource_id,
                speed=speed,
                echo=echo,
            ), batch_identity)
            for (original_index, _), generated in zip(chunk, result["items"]):
                ordered_results[original_index] = generated
            _tts_log("project_chunk_success", group_index=group_index, chunk_index=chunk_index,
                     voice_type=voice_type, items=len(chunk),
                     elapsed_ms=round((time.perf_counter() - chunk_started) * 1000, 1))

    if any(item is None for item in ordered_results):
        _tts_log("project_error", reason="missing_results", items=len(req.items))
        raise HTTPException(500, "TTS project batch trả về thiếu kết quả")
    _tts_log("project_success", items=len(ordered_results), groups=len(grouped),
             elapsed_ms=round((time.perf_counter() - project_started) * 1000, 1))
    return {"items": ordered_results}


@app.post("/tts-mixed-batch")
def generate_tts_mixed_batch(req: MixedTTSBatchRequest, _identity: dict = Depends(tts_identity)):
    _cleanup_expired_tts_files()
    if not req.items or len(req.items) > 50:
        raise HTTPException(400, "items phải có từ 1 đến 50 đoạn")
    total_chars = sum(len(item.text or "") for item in req.items)
    if total_chars > int(os.getenv("TTS_BATCH_MAX_CHARS", "20000")):
        raise HTTPException(400, "Tổng nội dung mixed TTS quá dài")
    consume_tts_quota(_identity, total_chars)

    vi_voice_type = req.vi_voice_type or tv.CAPCUT_VOICES_VI[1][1]
    vi_resource_id = req.vi_resource_id or tv.CAPCUT_VOICES_VI[1][2]
    en_fallback = list_english_voices()[0]
    en_voice_type = req.en_voice_type or en_fallback["voice_type"]
    en_resource_id = req.en_resource_id or en_fallback["resource_id"]
    device_id = os.environ.get("CAPCUT_DEVICE_ID")
    if not device_id:
        raise HTTPException(500, "Thiếu CAPCUT_DEVICE_ID trong .env")

    split_items = [_split_mixed_text(item.text, item.englishPhrases) for item in req.items]
    flat_parts = [(item_index, part_index, lang, text)
                  for item_index, parts in enumerate(split_items)
                  for part_index, (lang, text) in enumerate(parts)]
    if not flat_parts:
        raise HTTPException(400, "Không có nội dung TTS")

    part_paths: dict[tuple[int, int], Path] = {}
    current_device_id = device_id
    last_err = None
    for _attempt in range(5):
        try:
            part_paths.clear()
            for lang in ("vi", "en"):
                batch = [(item_index, part_index, text)
                         for item_index, part_index, part_lang, text in flat_parts
                         if part_lang == lang]
                if not batch:
                    continue
                voice_type = vi_voice_type if lang == "vi" else en_voice_type
                resource_id = vi_resource_id if lang == "vi" else en_resource_id
                for start in range(0, len(batch), 50):
                    chunk = batch[start:start + 50]
                    paths = [AUDIO_DIR / f"{uuid.uuid4().hex}.mp3" for _ in chunk]
                    texts = [text for _item_index, _part_index, text in chunk]
                    tv.capcut_tts_batch(texts, voice_type, resource_id, paths, current_device_id, rate=req.rate)
                    for (item_index, part_index, _text), path in zip(chunk, paths):
                        part_paths[(item_index, part_index)] = path

            items = []
            for item_index, parts in enumerate(split_items):
                out_name = f"{uuid.uuid4().hex}.mp3"
                out_path = AUDIO_DIR / out_name
                ordered_paths = [part_paths[(item_index, part_index)] for part_index in range(len(parts))]
                _concat_audio(ordered_paths, out_path)
                duration = _probe_duration(out_path)
                if duration <= 0:
                    raise RuntimeError(f"File mixed TTS {out_name} rỗng/0 giây")
                items.append({"audio_url": f"/audio/{out_name}", "duration": duration})
            return {"items": items}
        except Exception as exc:
            last_err = exc
            current_device_id = str(random.randint(7000000000000000000, 7999999999999999999))
    raise HTTPException(502, f"CapCut mixed TTS lỗi sau 5 lần đổi device_id: {last_err}")


@app.post("/generate-comparisons")
def generate_comparisons(req: GenerateComparisonsRequest, _identity: dict = Depends(tts_identity)):
    _require_legacy_tools()
    if req.count < 1 or req.count > 50:
        raise HTTPException(400, "Số cặp phải từ 1 đến 50")
    api_key = os.environ.get("BEEKNOEE_API_KEY2", "").strip()
    if not api_key:
        raise HTTPException(500, "Thiếu BEEKNOEE_API_KEY2 trong .env")

    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=tv.BEEKNOEE_BASE_URL)
    theme = req.theme.strip()
    theme_instruction = (
        f"Chủ đề chung người dùng muốn: {theme}." if theme
        else "Tự chọn các cặp khái niệm rất gần nhau về nghĩa, hình thức hoặc ngữ cảnh sử dụng để người xem dễ nhầm."
    )
    prompt = f"""Tạo đúng {req.count} cặp khái niệm để làm video giáo dục ngắn dạng “gần giống nhau nhưng khác nhau”.
{theme_instruction}

Yêu cầu:
- Mỗi cặp phải là hai khái niệm gần nhau, thường bị nhầm lẫn, nhưng khác nhau thật sự ở bản chất hoặc sắc thái.
- Ưu tiên các cặp có tính “fact” hoặc kiến thức đời sống, ngôn ngữ, tâm lý, xã hội, thói quen, cảm xúc, không chọn những cặp quá xa nhau.
- Mỗi cặp phải hữu ích, dễ hiểu, không lặp ý với cặp khác.
- topicA và topicB ngắn gọn, ưu tiên 1 đến 4 từ.
- explanationA và explanationB là câu giải thích tiếng Việt tự nhiên, chính xác, khoảng 15 đến 30 từ, tự chứa tên khái niệm để TTS đọc trực tiếp.
- imageA và imageB để chuỗi rỗng.
- Không tạo nội dung nguy hiểm, thù ghét hoặc khẳng định y khoa/pháp lý thiếu căn cứ.

Trả về duy nhất JSON array hợp lệ, không markdown, đúng cấu trúc:
[
  {{
    "topicA": "...",
    "topicB": "...",
    "explanationA": "...",
    "explanationB": "...",
    "imageA": "",
    "imageB": ""
  }}
]
Mảng phải có đúng {req.count} phần tử."""

    try:
        with AI_REQUEST_LOCK:
            last_error = None
            raw = ""
            for attempt in range(5):
                try:
                    response = client.chat.completions.create(
                        model="google/gemini-2.5-flash-lite",
                        messages=[
                            {"role": "system", "content": "Bạn là biên tập viên nội dung giáo dục tiếng Việt. Luôn trả về JSON thuần hợp lệ."},
                            {"role": "user", "content": prompt},
                        ],
                        temperature=0.7,
                        max_tokens=12000,
                    )
                    raw = (response.choices[0].message.content or "").strip()
                    break
                except Exception as exc:
                    last_error = exc
                    message = str(exc).lower()
                    if "concurrent_limit_exceeded" not in message and "too many concurrent requests" not in message:
                        raise
                    time.sleep(1.5 * (attempt + 1))
            else:
                raise last_error or RuntimeError("AI request failed")

        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            start, end = raw.find("["), raw.rfind("]")
            if start < 0 or end <= start:
                raise
            data = json.loads(raw[start:end + 1])
        if not isinstance(data, list) or len(data) != req.count:
            raise ValueError(f"AI trả {len(data) if isinstance(data, list) else 0}/{req.count} cặp")
        required = ("topicA", "topicB", "explanationA", "explanationB")
        cleaned = []
        for index, item in enumerate(data):
            if not isinstance(item, dict) or any(not str(item.get(key, "")).strip() for key in required):
                raise ValueError(f"Cặp {index + 1} thiếu trường bắt buộc")
            cleaned.append({
                "topicA": str(item["topicA"]).strip(),
                "topicB": str(item["topicB"]).strip(),
                "explanationA": str(item["explanationA"]).strip(),
                "explanationB": str(item["explanationB"]).strip(),
                "imageA": "",
                "imageB": "",
            })
        return {"comparisons": cleaned, "model": "google/gemini-2.5-flash-lite"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Beeknoee AI lỗi: {exc}")


@app.post("/ai/generate-script")
def generate_threads_script(req: GenerateThreadsScriptRequest, identity: dict = Depends(tts_identity)):
    user_prompt = req.prompt.strip()
    if len(user_prompt) < 3 or len(user_prompt) > 50_000: raise HTTPException(400, "Yêu cầu AI phải dài từ 3 đến 50.000 ký tự")
    if req.platform not in ("auto", "threads", "tiktok"): raise HTTPException(400, "Nền tảng AI không hợp lệ")
    if req.length not in ("short", "medium", "long"): raise HTTPException(400, "Độ dài AI không hợp lệ")
    api_key = (os.environ.get("BEEKNOEE_API_KEY") or os.environ.get("BEEKNOEE_API_KEY2") or "").strip()
    if not api_key: raise HTTPException(500, "Backend chưa cấu hình BEEKNOEE_API_KEY")
    reservation = reserve_ai_script_credit(identity, req.idempotency_key, req.length, user_prompt)
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=tv.BEEKNOEE_BASE_URL)
        system_prompt = f"""Bạn là biên kịch video mạng xã hội Việt Nam cho ứng dụng Threads Video Maker.
Chỉ trả JSON thuần hợp lệ, không markdown, không giải thích.

PHẠM VI TUYỆT ĐỐI:
- Chỉ được tạo 4 loại card: post, comment, reply, mess.
- CẤM tạo ảnh, video, sticker, meme, âm thanh hoặc gợi ý file media.
- Có thể dùng Threads hoặc TikTok cho comment/reply. Post là Threads.
- progressiveReveal=true chỉ khi một post/comment/reply dài có nhiều đoạn chủ ý; giữ ký tự xuống hàng trong text.
- Tôn trọng chính xác số lượng/loại card người dùng yêu cầu (ví dụ "một post duy nhất" thì chỉ trả đúng một post, không tự thêm comment/reply).
- Luật clearBefore cố định: post=true, comment=true, reply=false. Comment mở cụm mới; các reply sau nó phải cộng dồn và không xóa comment/reply trước.

TEXT HIỂN THỊ VÀ TEXT ĐỌC:
- Mọi card/câu tin nhắn bắt buộc có cả text và ttsText.
- text là chữ người xem nhìn thấy: viết tự nhiên như người Việt nhắn mạng xã hội, có thể rút gọn hợp ngữ cảnh như "tao"→"t", "mày"→"m", "rồi"→"r", "không"→"k/ko", "được"→"đc". Không rút gọn đến mức khó hiểu và không áp dụng máy móc cho nội dung trang trọng.
- ttsText là câu để máy đọc: phải mở rộng toàn bộ chữ viết tắt về tiếng Việt đầy đủ (t→tao, m→mày, r→rồi, k/ko→không, đc→được theo đúng ngữ cảnh).
- Trong ttsText, từ/cụm tiếng Anh phải đổi sang phiên âm cách người Việt đọc, không để nguyên chính tả tiếng Anh. Ví dụ: "deadline"→"đét lai", "crush"→"cờ rớt", "sorry"→"so ri", "AI"→"ây ai".
- ttsText CHỈ được chứa chữ, số, khoảng trắng, dấu phẩy và dấu chấm. Cấm mọi ký tự đặc biệt khác như @ # $ % ^ & * ( ) ` ~ _ - + / \\ | = và dấu nháy, hai chấm, chấm phẩy, chấm than.
- Dấu ? trong câu bình thường KHÔNG được đọc và không xuất hiện trong ttsText. Ví dụ text "m đi đâu?" thì ttsText kết thúc ở chữ "đâu".
- Trường hợp đặc biệt: nếu toàn bộ một câu/đoạn text chỉ gồm một hoặc nhiều dấu hỏi ("?", "??", "???"), ttsText của riêng đoạn đó phải là đúng hai chữ "hỏi chấm".
- text vẫn được phép giữ từ tiếng Anh đúng chính tả để giống bài đăng/tin nhắn thật.
- Nếu progressiveReveal=true, số đoạn xuống hàng trong text và ttsText phải bằng nhau, mỗi đoạn nhìn thấy khớp đúng đoạn được đọc.

ECHO / HIỆU ỨNG VANG:
- echo là hiệu ứng nhấn gây hài, chỉ dùng ở đúng câu punchline, plot twist hoặc cú bẻ lái thật sự gây cười.
- Mặc định echo=false. Không dùng cho câu dẫn, câu bình thường, câu buồn/nghiêm túc; tuyệt đối không lạm dụng.
- Nếu toàn kịch bản không có đoạn gây cười rõ ràng thì tất cả echo=false.
- Người dùng cho phép echo: {str(req.allow_echo).lower()}.

GIỌNG ĐƯỢC PHÉP (phải chép đúng nhãn):
- Nữ: {', '.join(AI_FEMALE_VOICES)}.
- Nam: {', '.join(AI_MALE_VOICES)}.
- post/comment/reply: chọn tự nhiên một giọng bất kỳ trong whitelist, không cần suy luận giới tính.
- mess: tự quyết định mineGender/theirsGender là male hoặc female, chọn mineVoice/theirsVoice đúng nhóm và giữ mỗi bên một giọng xuyên suốt hội thoại.

SCHEMA DUY NHẤT:
{{"title":"...","cards":[
  {{"type":"post","text":"...","ttsText":"...","voice":"Mai","echo":false,"progressiveReveal":false,"clearBefore":true}},
  {{"type":"comment","platform":"threads|tiktok","text":"...","ttsText":"...","voice":"Nam Minh","echo":false,"progressiveReveal":false,"clearBefore":true}},
  {{"type":"reply","platform":"threads|tiktok","text":"...","ttsText":"...","voice":"Giọng Bé","echo":false,"progressiveReveal":false,"clearBefore":false}},
  {{"type":"mess","platform":"messenger","partnerName":"...","mineGender":"male|female","theirsGender":"male|female","mineVoice":"...","theirsVoice":"...","messages":[{{"side":"mine|theirs","text":"...","ttsText":"...","echo":false}}]}}
]}}

Viết tự nhiên, có mở đầu/diễn biến/kết thúc đúng prompt. Không thêm field ngoài schema."""
        length_hint = {"short":"ngắn gọn, khoảng 1-5 card hoặc 5-10 tin nhắn", "medium":"vừa phải, khoảng 4-12 card hoặc 12-24 tin nhắn", "long":"dài và nhiều diễn biến, khoảng 12-30 card hoặc 32-48 tin nhắn"}[req.length]
        platform_hint = "tự chọn Threads/TikTok phù hợp" if req.platform == "auto" else f"ưu tiên nền tảng {req.platform}"
        messages = [{"role":"system","content":system_prompt},{"role":"user","content":f"Yêu cầu: {user_prompt}\nĐộ dài: {length_hint}.\nNền tảng: {platform_hint}."}]
        script = None
        last_ai_error = None
        with AI_REQUEST_LOCK:
            for attempt in range(3):
                response = client.chat.completions.create(
                    model=os.environ.get("BEEKNOEE_SCRIPT_MODEL", "gemini-2.5-flash-lite"),
                    messages=messages,
                    temperature=0.65 if attempt == 0 else 0.15,
                    max_tokens=12000,
                )
                raw = response.choices[0].message.content or ""
                try:
                    script = _clean_ai_script(_extract_json_object(raw), req.allow_echo)
                    break
                except (ValueError, json.JSONDecodeError) as exc:
                    last_ai_error = exc
                    if attempt >= 2: break
                    # Cho model tự sửa chính output vừa trả. Không tự chắp vá
                    # bằng regex vì dễ tạo JSON hợp lệ cú pháp nhưng sai nội dung.
                    messages.extend([
                        {"role":"assistant","content":raw[:24000]},
                        {"role":"user","content":f"JSON trên không hợp lệ: {exc}. Hãy trả lại TOÀN BỘ JSON đã sửa, giữ nguyên ý và đúng schema. Chỉ JSON thuần, kiểm tra kỹ dấu phẩy, dấu ngoặc và escape dấu nháy trong text."},
                    ])
        if script is None: raise ValueError(f"AI không sửa được JSON sau 3 lần: {last_ai_error}")
        payment = finish_ai_script_credit(identity, str(reservation["id"]), True)
        return {"script": script, "cost": reservation["cost"], "balance": payment["balance"], "model": os.environ.get("BEEKNOEE_SCRIPT_MODEL", "gemini-2.5-flash-lite")}
    except HTTPException:
        finish_ai_script_credit(identity, str(reservation["id"]), False)
        raise
    except Exception as exc:
        finish_ai_script_credit(identity, str(reservation["id"]), False)
        raise HTTPException(502, f"Beeknoee tạo kịch bản lỗi: {exc}")


def _export_timeline(tracks: list[ExportTrack]) -> list[dict]:
    cursor = 0.0
    segments = []
    for index, track in enumerate(tracks):
        duration = max(0.1, float(track.duration) + 0.2)
        segment = {
            "index": index,
            "comparisonIndex": track.comparisonIndex,
            "scene": track.scene,
            "start": cursor,
            "end": cursor + duration,
            "duration": duration,
            "audioUrl": track.audioUrl,
        }
        segments.append(segment)
        cursor = segment["end"]
    return segments


def _audio_path_from_url(audio_url: str) -> Path:
    filename = audio_url.rstrip("/").split("/")[-1]
    path = AUDIO_DIR / filename
    if not path.exists():
        raise RuntimeError(f"Không tìm thấy audio: {filename}")
    return path


def _set_export_job(job_id: str, **kwargs) -> None:
    export_jobs[job_id].update(kwargs)


async def _render_phanbiet_frames(job_id: str, comparisons: list[dict], segments: list[dict], job_dir: Path) -> list[Path]:
    from playwright.async_api import async_playwright

    frame_paths = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1080, "height": 1920}, device_scale_factor=1)
        await page.goto("http://localhost:8007/videoPhanBiet/export_render.html")
        await page.wait_for_load_state("networkidle")
        await page.evaluate("setExportProject", {"comparisons": comparisons, "segments": segments})
        for index, _segment in enumerate(segments):
            await page.evaluate("renderExportSegment", index)
            frame_path = job_dir / f"frame_{index:04d}.png"
            await page.locator("#video-stage").screenshot(path=str(frame_path), omit_background=False)
            frame_paths.append(frame_path)
            pct = 5 + int(((index + 1) / max(len(segments), 1)) * 50)
            _set_export_job(job_id, progress=pct, message=f"Đang chụp cảnh {index + 1}/{len(segments)}...")
        await browser.close()
    return frame_paths


def _build_phanbiet_ffmpeg(frame_paths: list[Path], segments: list[dict], out_path: Path) -> list[str]:
    inputs = []
    filters = []
    video_labels = []
    for index, (frame_path, segment) in enumerate(zip(frame_paths, segments)):
        duration = segment["duration"]
        inputs += ["-loop", "1", "-t", f"{duration:.3f}", "-i", str(frame_path)]
        label = f"v{index}"
        filters.append(f"[{index}:v]scale=1080:1920,setsar=1,format=yuv420p[{label}]")
        video_labels.append(label)

    audio_labels = []
    audio_start_index = len(frame_paths)
    for offset, segment in enumerate(segments):
        audio_path = _audio_path_from_url(segment["audioUrl"])
        input_index = audio_start_index + offset
        inputs += ["-i", str(audio_path)]
        label = f"a{offset}"
        ms = int(round(segment["start"] * 1000))
        filters.append(f"[{input_index}:a]adelay={ms}|{ms},volume=1.0[{label}]")
        audio_labels.append(label)

    filters.append("".join(f"[{label}]" for label in video_labels) + f"concat=n={len(video_labels)}:v=1:a=0[vout]")
    if audio_labels:
        filters.append(
            "".join(f"[{label}]" for label in audio_labels)
            + f"amix=inputs={len(audio_labels)}:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.97[aout]"
        )
    else:
        filters.append("anullsrc=channel_layout=stereo:sample_rate=44100[aout]")

    total_duration = segments[-1]["end"] if segments else 0.0
    return [FFMPEG_BIN, "-y"] + inputs + [
        "-filter_complex", ";".join(filters),
        "-map", "[vout]",
        "-map", "[aout]",
        "-t", f"{total_duration:.3f}",
        "-c:v", "libx264",
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-progress", "pipe:1",
        str(out_path),
    ]


async def _run_phanbiet_export(job_id: str, req: ExportPhanBietRequest) -> None:
    job_dir = EXPORT_DIR / job_id
    job_dir.mkdir(exist_ok=True)
    try:
        if not req.comparisons:
            raise RuntimeError("Chưa có cặp phân biệt nào")
        expected_tracks = len(req.comparisons) * 5
        if len(req.tracks) != expected_tracks:
            raise RuntimeError("Cần tạo TTS đầy đủ trước khi xuất video")

        segments = _export_timeline(req.tracks)
        _set_export_job(job_id, status="rendering", progress=5, message="Đang dựng ảnh từng cảnh...")
        frame_paths = await _render_phanbiet_frames(job_id, req.comparisons, segments, job_dir)

        _set_export_job(job_id, status="encoding", progress=58, message="Đang ghép MP4 bằng FFmpeg...")
        out_path = job_dir / "output.mp4"
        cmd = _build_phanbiet_ffmpeg(frame_paths, segments, out_path)
        (job_dir / "ffmpeg_cmd.txt").write_text(" ".join(cmd), encoding="utf-8")

        total_duration = segments[-1]["end"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for line in proc.stdout:
            match = re.search(r"out_time_ms=(\d+)", line)
            if match:
                done_s = int(match.group(1)) / 1_000_000
                pct = 58 + int(min(done_s / max(total_duration, 0.1), 1) * 40)
                _set_export_job(job_id, progress=pct, message=f"Đang encode... {done_s:.1f}s/{total_duration:.1f}s")
        stderr_out = proc.stderr.read()
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg lỗi: {stderr_out[-1600:]}")
        _set_export_job(job_id, status="done", progress=100, message="Hoàn tất!", downloadUrl=f"/export-phanbiet-download/{job_id}")
    except Exception as exc:
        _set_export_job(job_id, status="error", message=str(exc))


@app.post("/export-phanbiet")
async def export_phanbiet(req: ExportPhanBietRequest, identity: dict = Depends(tts_identity)):
    _require_legacy_tools()
    job_id = uuid.uuid4().hex[:12]
    export_jobs[job_id] = {"userId": identity["id"], "status": "queued", "progress": 0, "message": "Đang chờ...", "downloadUrl": None}
    asyncio.create_task(_run_phanbiet_export(job_id, req))
    return {"job_id": job_id}


@app.get("/export-phanbiet-status/{job_id}")
def export_phanbiet_status(job_id: str, identity: dict = Depends(tts_identity)):
    _require_legacy_tools()
    job = export_jobs.get(job_id)
    if not job or job.get("userId") != identity["id"]:
        raise HTTPException(404, "Không tìm thấy job export")
    return job


@app.get("/export-phanbiet-download/{job_id}")
def export_phanbiet_download(job_id: str, identity: dict = Depends(tts_identity)):
    _require_legacy_tools()
    job = export_jobs.get(job_id)
    if not job or job.get("userId") != identity["id"]:
        raise HTTPException(404, "Không tìm thấy job export")
    out_path = EXPORT_DIR / job_id / "output.mp4"
    if not out_path.exists():
        raise HTTPException(404, "File chưa sẵn sàng")
    return FileResponse(out_path, media_type="video/mp4", filename=f"video-phan-biet-{job_id}.mp4")


@app.get("/audio/{filename}")
def get_audio(filename: str, _identity: dict = Depends(current_identity)):
    """Tải file cần đăng nhập nhưng không tính vào rate limit tạo TTS."""
    if not re.fullmatch(r"[a-f0-9]{32}\.mp3", filename):
        raise HTTPException(404, "Không tìm thấy file audio")
    path = AUDIO_DIR / filename
    if not path.exists():
        raise HTTPException(404, "Không tìm thấy file audio")
    return FileResponse(path, media_type="audio/mpeg", filename=filename)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8007)
