#!/usr/bin/env python3
"""
translate_video.py
Nhận video tiếng Trung từ thư mục input/, tự động:
  1. Tách audio đầy đủ (FFmpeg) + tách vocals/background (Demucs)
  2. STT tiếng Trung → SRT (Groq Whisper)
  3. Dịch Trung → Việt (Groq LLM)
  4. TTS tiếng Việt đồng bộ theo timestamp (Edge TTS + FFmpeg atempo)
  5. Mix TTS + nhạc nền gốc → burn subtitle → output/

Cách dùng:
  python3 translate_video.py
  python3 translate_video.py --input path/to/video.mp4
  python3 translate_video.py --key YOUR_GROQ_KEY  (override .env)
"""

import argparse
import asyncio
import os
import re
import subprocess
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
BASE_DIR   = Path(__file__).parent

# Load .env
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())
INPUT_DIR  = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
WORK_DIR   = BASE_DIR / "workdir"

FFMPEG_BIN       = os.environ.get("FFMPEG_BIN", "ffmpeg")
TTS_VOICE        = "vi-VN-HoaiMyNeural"
TTS_SPEED_BOOST  = 1.00   # không tăng tốc mặc định, tự khớp theo window
ATEMPO_MAX       = 2.0    # FFmpeg atempo tối đa 2.0x mỗi filter
ATEMPO_MIN       = 0.5
CHUNK_BLOCKS     = 20     # số block SRT mỗi lần gửi dịch
CONTEXT_BLOCKS   = 4      # số block context giữ lại giữa các chunk
DEMUCS_MODEL     = "htdemucs"   # model Demucs tách vocals
BG_VOLUME        = 0.9    # âm lượng nhạc nền so với gốc (0.0-1.0)

# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def run(cmd: list[str], timeout: int = 300, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, result.args,
            output=result.stdout,
            stderr=result.stderr,
        )
    return result


def srt_time_to_sec(ts: str) -> float:
    h, m, rest = ts.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def sec_to_srt_time(sec: float) -> str:
    sec = max(0.0, sec)
    h   = int(sec // 3600)
    m   = int((sec % 3600) // 60)
    s   = int(sec % 60)
    ms  = round((sec % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(text: str) -> list[dict]:
    """Parse SRT text → list of {idx, start, end, start_sec, end_sec, text}"""
    blocks = re.split(r"\n\s*\n", text.strip())
    cues = []
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        ts_match = re.match(
            r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})", lines[1]
        )
        if not ts_match:
            continue
        start, end = ts_match.group(1), ts_match.group(2)
        cues.append({
            "idx":       int(lines[0].strip()),
            "start":     start,
            "end":       end,
            "start_sec": srt_time_to_sec(start),
            "end_sec":   srt_time_to_sec(end),
            "text":      " ".join(l.strip() for l in lines[2:] if l.strip()),
        })
    return cues


def build_srt(cues: list[dict]) -> str:
    parts = []
    for i, c in enumerate(cues, 1):
        parts.append(f"{i}\n{c['start']} --> {c['end']}\n{c['text']}")
    return "\n\n".join(parts) + "\n\n"


def atempo_chain(ratio: float) -> list[str]:
    """Build chained atempo filters for ratios outside [0.5, 2.0]"""
    filters = []
    r = ratio
    while r > ATEMPO_MAX:
        filters.append(f"atempo={ATEMPO_MAX}")
        r /= ATEMPO_MAX
    while r < ATEMPO_MIN:
        filters.append(f"atempo={ATEMPO_MIN}")
        r /= ATEMPO_MIN
    filters.append(f"atempo={r:.6f}")
    return filters


# ---------------------------------------------------------------------------
# STEP 1: Extract audio + tách vocals/background (Demucs)
# ---------------------------------------------------------------------------

def extract_audio_for_stt(video_path: Path, audio_path: Path):
    """Tách audio chất lượng thấp tối ưu cho Whisper STT (16kHz mono 32kbps)."""
    print("  → Tách audio cho STT (16kHz mono 32kbps)...")
    run([
        FFMPEG_BIN, "-y", "-i", str(video_path),
        "-vn", "-acodec", "libmp3lame",
        "-ac", "1", "-ar", "16000", "-b:a", "32k",
        str(audio_path),
    ])
    size = audio_path.stat().st_size
    if size > 24 * 1024 * 1024:
        tmp = audio_path.with_suffix(".tmp.mp3")
        run([
            FFMPEG_BIN, "-y", "-i", str(audio_path),
            "-acodec", "libmp3lame", "-ac", "1", "-ar", "16000", "-b:a", "16k",
            str(tmp),
        ])
        tmp.replace(audio_path)


def separate_background(video_path: Path, work_dir: Path) -> Path:
    """Dùng Demucs tách vocals, giữ lại nhạc nền (no_vocals)."""
    print("  → Tách audio nền (Demucs)...")

    full_audio = work_dir / "full_audio.wav"
    run([
        FFMPEG_BIN, "-y", "-i", str(video_path),
        "-vn", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2",
        str(full_audio),
    ])

    import platform, torch
    if torch.cuda.is_available():
        device = "cuda"
    elif platform.system() == "Darwin" and torch.backends.mps.is_available():
        device = "mps"
    else:
        device = "cpu"

    demucs_out = work_dir / "demucs_out"
    run([
        "python3", "-m", "demucs",
        "--two-stems", "vocals",
        "-n", DEMUCS_MODEL,
        "--device", device,
        "-o", str(demucs_out),
        str(full_audio),
    ], timeout=3600)

    no_vocals = list(demucs_out.rglob("no_vocals.wav"))
    if not no_vocals:
        raise RuntimeError("Demucs không tạo ra file no_vocals.wav")

    bg_path = work_dir / "background.wav"
    no_vocals[0].rename(bg_path)
    return bg_path


# ---------------------------------------------------------------------------
# STEP 2: STT via Groq Whisper
# ---------------------------------------------------------------------------

def stt_groq(audio_path: Path, groq_key: str) -> list[dict]:
    print("  → STT Groq Whisper (zh)...")
    from groq import Groq
    client = Groq(api_key=groq_key)
    with open(audio_path, "rb") as f:
        resp = client.audio.transcriptions.create(
            file=("audio.mp3", f, "audio/mpeg"),
            model="whisper-large-v3",
            language="zh",
            response_format="verbose_json",
        )
    segments = resp.segments or []
    if not segments:
        raise RuntimeError("Groq STT không trả về segments. Kiểm tra API key và file audio.")

    cues = []
    for i, seg in enumerate(segments, 1):
        s = seg.start if hasattr(seg, "start") else seg["start"]
        e = seg.end   if hasattr(seg, "end")   else seg["end"]
        t = seg.text  if hasattr(seg, "text")  else seg["text"]
        cues.append({
            "idx":       i,
            "start":     sec_to_srt_time(s),
            "end":       sec_to_srt_time(e),
            "start_sec": s,
            "end_sec":   e,
            "text":      t.strip(),
        })
    return cues


# ---------------------------------------------------------------------------
# STEP 3: Translate via Groq LLM
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """Bạn là dịch giả phụ đề chuyên nghiệp, dịch từ tiếng Trung sang tiếng Việt.

QUY TẮC BẮT BUỘC:
1. Giữ nguyên số block và timestamp SRT — KHÔNG gộp, KHÔNG tách, KHÔNG bỏ block.
2. Chỉ trả về SRT thuần túy, không giải thích, không markdown.
3. Dịch đúng ngữ cảnh, tự nhiên — không dịch máy móc từng chữ.
4. Giữ nguyên văn phong của video: khoa học thì giữ giọng khoa học, hài hước thì giữ hài hước, kịch tính thì giữ kịch tính."""


BEEKNOEE_BASE_URL = "https://platform.beeknoee.com/api/v1"
BEEKNOEE_MODEL    = "deepseek/deepseek-chat-v3.1"


def _make_chat_client(groq_key: str, beeknoee_key: str | None):
    """Trả về (client, model). Ưu tiên Beeknoee nếu có key."""
    if beeknoee_key:
        from openai import OpenAI
        client = OpenAI(api_key=beeknoee_key, base_url=BEEKNOEE_BASE_URL)
        return client, BEEKNOEE_MODEL
    else:
        from groq import Groq
        return Groq(api_key=groq_key), "llama-3.3-70b-versatile"


def translate_chunk(cues: list[dict], context_cues: list[dict],
                    groq_key: str, beeknoee_key: str | None = None) -> list[dict]:
    client, model = _make_chat_client(groq_key, beeknoee_key)

    prompt = ""
    if context_cues:
        ctx_srt = build_srt(context_cues)
        prompt += f"[NGỮ CẢNH ĐÃ DỊCH - chỉ tham chiếu xưng hô/tên, KHÔNG dịch lại]:\n{ctx_srt}\n\n"

    src_srt = build_srt(cues)
    prompt += f"[DỊCH {len(cues)} BLOCK SRT SAU, giữ nguyên index và timestamp]:\n{src_srt}"

    max_attempts = 5
    backoff = 5  # giây, tăng dần khi gặp 429
    for attempt in range(max_attempts):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
                temperature=0.05,
            )
        except Exception as e:
            err = str(e)
            if "429" in err or "rate_limit" in err or "too_many_requests" in err:
                wait = backoff * (2 ** attempt)
                print(f"     Rate limit, chờ {wait}s rồi thử lại...")
                time.sleep(wait)
                continue
            raise

        raw = resp.choices[0].message.content or ""
        raw = re.sub(r"```\w*\n?", "", raw).strip()
        translated = parse_srt(raw)
        if len(translated) == len(cues):
            for src, tgt in zip(cues, translated):
                tgt["start"]     = src["start"]
                tgt["end"]       = src["end"]
                tgt["start_sec"] = src["start_sec"]
                tgt["end_sec"]   = src["end_sec"]
            return translated
        best_partial = translated
        if attempt < max_attempts - 1:
            prompt += f"\n\n[LỖI: cần đúng {len(cues)} block, nhận được {len(translated)}. KHÔNG gộp/bỏ block nào.]"
            time.sleep(3)

    # Fallback: align by SRT index, fill missing with source text
    print(f"     ⚠ Dùng fallback: căn chỉnh theo index, bổ sung block thiếu bằng text gốc")
    by_idx = {t["idx"]: t for t in best_partial}
    result = []
    for i, src in enumerate(cues, 1):
        tgt = by_idx.get(i) or by_idx.get(src["idx"])
        if tgt:
            tgt = dict(tgt)
        else:
            tgt = dict(src)
            tgt["text"] = src["text"]
        tgt["start"]     = src["start"]
        tgt["end"]       = src["end"]
        tgt["start_sec"] = src["start_sec"]
        tgt["end_sec"]   = src["end_sec"]
        tgt["idx"]       = src["idx"]
        result.append(tgt)
    return result


def translate_srt(cues: list[dict], groq_key: str,
                  beeknoee_key: str | None = None,
                  chunk_cb=None) -> list[dict]:
    """
    chunk_cb(done, total, partial_result): gọi sau mỗi chunk xong.
    """
    provider = "Beeknoee" if beeknoee_key else "Groq"
    print(f"  → Dịch {len(cues)} block SRT ({provider})...")
    chunks = [cues[i:i + CHUNK_BLOCKS] for i in range(0, len(cues), CHUNK_BLOCKS)]
    result = []
    prev_translated: list[dict] = []

    for i, chunk in enumerate(chunks):
        print(f"     chunk {i+1}/{len(chunks)} ({len(chunk)} blocks)...")
        context = prev_translated[-CONTEXT_BLOCKS:]
        translated_chunk = translate_chunk(chunk, context, groq_key, beeknoee_key)
        result.extend(translated_chunk)
        prev_translated = translated_chunk
        if chunk_cb:
            chunk_cb(i + 1, len(chunks), list(result))
        if i < len(chunks) - 1:
            time.sleep(0.5 if beeknoee_key else 1.5)

    return result


# ---------------------------------------------------------------------------
# STEP 4: TTS aligned to SRT timestamps
# ---------------------------------------------------------------------------

CAPCUT_DEVICE_ID = os.environ.get("CAPCUT_DEVICE_ID", "7581502458217252368")
CAPCUT_IID       = os.environ.get("CAPCUT_IID",       "7581504179703056144")

CAPCUT_VOICES_VI = [
    ("Nhỏ Ngọt Ngào",       "BV421_vivn_streaming",                   "7252594014782755330"),
    ("Cô Gái Hoạt Ngôn",    "BV074_streaming",                        "7102355709945188865"),
    ("Giọng Nữ Phổ Thông",  "vi_female_huong",                        "7264854897953083905"),
    ("Giọng Bé",             "BV074_streaming_dsp",                    "7550087831092251920"),
    ("Mai",                  "BV562_streaming",                        "7483736254694035984"),
    ("Giọng Gái Mới Lớn",   "multi_female_peiqi_uranus_bigtts",       "7637458789033151751"),
    ("Ban Mai",              "multi_female_yangguangnv_uranus_bigtts", "7637456432522218773"),
    ("Bản Tin Nữ",           "multi_female_sisi_uranus_bigtts",        "7637455857285860629"),
    ("Thanh Niên Tự Tin",   "BV075_streaming",                        "7102355803792740865"),
    ("Giọng Nam Trầm",      "multi_male_felipe_uranus_bigtts",        "7637456729696996628"),
    ("Alex Đại Đế",          "BV560_streaming",                        "7483736167565758992"),
]


def _capcut_session(device_id: str):
    """Tạo session + device dict dùng chung cho CapCut API."""
    import sys
    sys.path.insert(0, str(BASE_DIR / "capcut-tts-api-main"))
    from capcut_common_task_client import DEFAULT_DEVICE
    import requests as _req
    from copy import deepcopy
    _session = _req.Session()
    _session.trust_env = False
    device = deepcopy(DEFAULT_DEVICE)
    device["device_id"] = device_id
    device["iid"]       = os.environ.get("CAPCUT_IID", CAPCUT_IID)
    device["tdid"]      = device_id
    return _session, device


def capcut_tts_batch(texts: list[str], voice_type: str, resource_id: str,
                     out_paths: list[Path], device_id: str, rate: str = "1.0"):
    """Gửi nhiều text trong 1 request CapCut TTS, lưu từng MP3 vào out_paths tương ứng."""
    import sys
    sys.path.insert(0, str(BASE_DIR / "capcut-tts-api-main"))
    from capcut_common_task_client import (
        BASE as CAPCUT_BASE,
        tts_new_body, query_body, common_query,
        compact_json, base_headers, make_sign_header,
    )
    from urllib.parse import urlencode
    import json as _json

    _session, device = _capcut_session(device_id)

    babi, body = tts_new_body(texts, voice_type, resource_id, rate, device)
    url = CAPCUT_BASE + "/lv/v1/common_task/new?" + urlencode(common_query(device, babi, include_region=True))
    body_text = compact_json(body)
    headers = base_headers(device, body_text, appid=True)
    lh = {k.lower(): v for k, v in headers.items()}
    headers["sign"] = make_sign_header(url, device["appvr"], lh["device-time"], device["tdid"])
    r = _session.post(url, headers=headers, data=body_text.encode(), timeout=30)
    d = r.json()
    if d.get("ret") != "0":
        raise RuntimeError(f"CapCut TTS submit lỗi: {d.get('errmsg')}")
    task = d["data"]["tasks"][0]
    task_id, token = task["id"], task["token"]

    for _ in range(40):
        time.sleep(1.5)
        body2 = query_body(task_id, token, "sami_text_to_speech")
        url2 = CAPCUT_BASE + "/lv/v1/common_task/query?" + urlencode(common_query(device, None, include_region=False))
        body_text2 = compact_json(body2)
        headers2 = base_headers(device, body_text2, appid=True)
        lh2 = {k.lower(): v for k, v in headers2.items()}
        headers2["sign"] = make_sign_header(url2, device["appvr"], lh2["device-time"], device["tdid"])
        r2 = _session.post(url2, headers=headers2, data=body_text2.encode(), timeout=30)
        d2 = r2.json()
        t2 = d2["data"]["tasks"][0]
        if t2["status"] == "succeed":
            payload = _json.loads(t2["payload"])
            audio_list = payload["audio_subtitles"]
            # Map theo field "text" để tránh lệch thứ tự
            by_text = {item["text"]: item["speech_url"] for item in audio_list}
            for text, out_path in zip(texts, out_paths):
                url = by_text.get(text)
                if not url:
                    # fallback theo index nếu text không khớp (CapCut có thể normalize)
                    idx = texts.index(text)
                    url = audio_list[idx]["speech_url"] if idx < len(audio_list) else None
                if url:
                    out_path.write_bytes(_session.get(url, timeout=30).content)
                else:
                    make_silent(1.0, out_path)
            return
        if t2["status"] == "failed":
            raise RuntimeError(f"CapCut TTS batch failed")
    raise RuntimeError("CapCut TTS batch timeout sau 60s")


def capcut_tts_sync(text: str, voice_type: str, resource_id: str, out_path: Path, device_id: str):
    """Gọi CapCut TTS API đồng bộ, lưu MP3 vào out_path."""
    capcut_tts_batch([text], voice_type, resource_id, [out_path], device_id)


async def tts_segment(text: str, voice: str, out_path: Path,
                      beeknoee_key: str | None = None,
                      beeknoee_tts_model: str | None = None,
                      beeknoee_tts_voice: str | None = None,
                      capcut_device_id: str | None = None,
                      capcut_voice_type: str | None = None,
                      capcut_resource_id: str | None = None):
    """Tạo file TTS cho 1 đoạn text, retry 3 lần nếu thất bại.
    Ưu tiên: CapCut TTS → Beeknoee TTS → Edge TTS."""
    clean = re.sub(r"[^\w\sÀ-ɏḀ-ỿ.,!?;:()\-–\"']", " ", text).strip()
    if not clean:
        clean = "."

    if capcut_device_id and capcut_voice_type and capcut_resource_id:
        for attempt in range(3):
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None, capcut_tts_sync,
                    clean, capcut_voice_type, capcut_resource_id, out_path, capcut_device_id,
                )
                return
            except Exception as e:
                if attempt == 2:
                    print(f"  ⚠ CapCut TTS thất bại, fallback Edge TTS: '{clean[:60]}' — {e}")
                    import edge_tts as _edge
                    try:
                        communicate = _edge.Communicate(clean, voice)
                        await communicate.save(str(out_path))
                    except Exception:
                        make_silent(1.0, out_path)
                else:
                    await asyncio.sleep(2)
    elif beeknoee_key and beeknoee_tts_model:
        from openai import OpenAI
        bee_voice = beeknoee_tts_voice or "vi"
        for attempt in range(3):
            try:
                def _call():
                    client = OpenAI(api_key=beeknoee_key, base_url=BEEKNOEE_BASE_URL)
                    resp = client.audio.speech.create(
                        model=beeknoee_tts_model,
                        voice=bee_voice,
                        input=clean,
                        response_format="mp3",
                    )
                    out_path.write_bytes(resp.content)
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, _call)
                return
            except Exception as e:
                if attempt == 2:
                    print(f"  ⚠ Beeknoee TTS fallback silent: '{clean[:60]}' — {e}")
                    make_silent(1.0, out_path)
                else:
                    await asyncio.sleep(1)
    else:
        import edge_tts
        for attempt in range(3):
            try:
                communicate = edge_tts.Communicate(clean, voice)
                await communicate.save(str(out_path))
                return
            except Exception as e:
                if attempt == 2:
                    print(f"  ⚠ TTS fallback silent: '{clean[:60]}' — {e}")
                    make_silent(1.0, out_path)
                else:
                    await asyncio.sleep(1)


def get_audio_duration(path: Path) -> float:
    r = run([
        "ffprobe", "-v", "error",
        "-show_entries", "stream=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ])
    # Lấy max của tất cả stream (video + audio) để tránh thiếu nếu 1 stream ngắn hơn
    vals = [float(x) for x in r.stdout.strip().splitlines() if x.strip()]
    return max(vals) if vals else 0.0


def stretch_audio(src: Path, dst: Path, ratio: float):
    """Kéo/nén audio theo ratio dùng atempo. ratio > 1 = nhanh hơn."""
    filters = ",".join(atempo_chain(ratio))
    extra = ["-ar", "44100", "-ac", "1"] if dst.suffix.lower() == ".wav" else []
    run([
        FFMPEG_BIN, "-y", "-i", str(src),
        "-filter:a", filters,
        *extra,
        str(dst),
    ])


def make_silent(duration: float, dst: Path):
    """Tạo file âm thanh im lặng duration giây. Dùng WAV nếu dst.suffix=.wav."""
    ext = dst.suffix.lower()
    if ext == ".wav":
        run([
            FFMPEG_BIN, "-y",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
            "-t", f"{duration:.6f}",
            "-ar", "44100", "-ac", "1",
            str(dst),
        ])
    else:
        run([
            FFMPEG_BIN, "-y",
            "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
            "-t", f"{duration:.3f}",
            "-acodec", "libmp3lame", "-b:a", "48k",
            str(dst),
        ])


async def build_tts_track(cues: list[dict], work_dir: Path, video_duration: float,
                          beeknoee_key: str | None = None,
                          beeknoee_tts_model: str | None = None,
                          beeknoee_tts_voice: str | None = None,
                          capcut_device_id: str | None = None,
                          capcut_voice_type: str | None = None,
                          capcut_resource_id: str | None = None,
                          capcut_delay: float = 1.5,
                          capcut_rate: str = "1.0") -> Path:
    """
    Build track TTS đồng bộ:
    - Mỗi cue: TTS → atempo để khớp đúng window [start_sec, end_sec]
    - Ghép tất cả cue + khoảng lặng giữa chúng thành 1 file mp3
    - Ưu tiên: CapCut TTS → Beeknoee TTS → Edge TTS
    """
    if capcut_device_id and capcut_voice_type:
        label = f"CapCut TTS batch ({capcut_voice_type})"
    elif beeknoee_tts_model:
        label = f"Beeknoee ({beeknoee_tts_model})"
    else:
        label = f"Edge TTS ({TTS_VOICE})"
    print(f"  → TTS {len(cues)} đoạn ({label})...")
    seg_dir = work_dir / "tts_segs"
    seg_dir.mkdir(exist_ok=True)

    # --- Pre-fetch CapCut batch -------------------------------------------
    # Gom tất cả cue có text, gửi theo batch 10 đoạn/request thay vì từng cái
    raw_paths: dict[int, Path] = {}   # idx → raw mp3 path
    if capcut_device_id and capcut_voice_type and capcut_resource_id:
        BATCH = 10
        pending_idxs  : list[int]  = []
        pending_texts : list[str]  = []
        pending_paths : list[Path] = []

        current_device_id = capcut_device_id

        def _new_device_id() -> str:
            import random
            new_id = str(random.randint(7000000000000000000, 7999999999999999999))
            # Cập nhật .env để lần sau dùng luôn
            env_path = BASE_DIR / ".env"
            if env_path.exists():
                lines = env_path.read_text().splitlines()
                new_lines = [f"CAPCUT_DEVICE_ID={new_id}" if l.startswith("CAPCUT_DEVICE_ID=") else l for l in lines]
                if not any(l.startswith("CAPCUT_DEVICE_ID=") for l in lines):
                    new_lines.append(f"CAPCUT_DEVICE_ID={new_id}")
                env_path.write_text("\n".join(new_lines) + "\n")
            print(f"  → Đổi device_id mới: {new_id}")
            return new_id

        def _flush_batch():
            nonlocal current_device_id
            if not pending_texts:
                return
            print(f"    CapCut batch {pending_idxs[0]}–{pending_idxs[-1]} ({len(pending_texts)} đoạn)...")
            for attempt in range(5):
                try:
                    capcut_tts_batch(pending_texts, capcut_voice_type, capcut_resource_id,
                                     pending_paths, current_device_id, rate=capcut_rate)
                    return
                except Exception as e:
                    if attempt >= 4:
                        print(f"  ⚠ CapCut batch thất bại sau 5 lần, fallback Edge TTS: {e}")
                        for text_fb, p in zip(pending_texts, pending_paths):
                            if not p.exists():
                                try:
                                    import subprocess as _sp
                                    _sp.run([
                                        "python3", "-c",
                                        f"import asyncio, edge_tts; asyncio.run(edge_tts.Communicate({repr(text_fb)}, {repr(TTS_VOICE)}).save({repr(str(p))}))"
                                    ], timeout=30)
                                except Exception:
                                    make_silent(1.0, p)
                    else:
                        print(f"  ⚠ CapCut batch lỗi (lần {attempt+1}): {e} — đổi device_id và thử lại...")
                        current_device_id = _new_device_id()
                        time.sleep(2)

        for cue in cues:
            text = cue["text"].strip()
            if not text:
                continue
            clean = re.sub(r"[^\w\sÀ-ɏḀ-ỿ.,!?;:()\-–\"']", " ", text).strip() or "."
            raw_path = seg_dir / f"raw_{cue['idx']:04d}.mp3"
            raw_paths[cue["idx"]] = raw_path
            pending_idxs.append(cue["idx"])
            pending_texts.append(clean)
            pending_paths.append(raw_path)
            if len(pending_texts) >= BATCH:
                _flush_batch()
                pending_idxs.clear(); pending_texts.clear(); pending_paths.clear()
                await asyncio.sleep(capcut_delay)
        _flush_batch()  # flush batch cuối

    # --- Pre-fetch Edge TTS song song cho các cue chưa có raw (không dùng CapCut) ---
    elif not beeknoee_key:
        edge_tasks = []
        for cue in cues:
            text = cue["text"].strip()
            if not text:
                continue
            clean = re.sub(r"[^\w\sÀ-ɏḀ-ỿ.,!?;:()\-–\"']", " ", text).strip() or "."
            raw_path = seg_dir / f"raw_{cue['idx']:04d}.mp3"
            raw_paths[cue["idx"]] = raw_path
            edge_tasks.append((clean, raw_path))

        async def _edge_one(txt, path):
            import edge_tts as _et
            for attempt in range(3):
                try:
                    await _et.Communicate(txt, TTS_VOICE).save(str(path))
                    return
                except Exception:
                    if attempt == 2:
                        make_silent(1.0, path)
                    else:
                        await asyncio.sleep(1)

        EDGE_CONCURRENT = 10
        for i in range(0, len(edge_tasks), EDGE_CONCURRENT):
            chunk = edge_tasks[i:i + EDGE_CONCURRENT]
            print(f"  Edge TTS song song {i+1}–{i+len(chunk)}/{len(edge_tasks)}...")
            await asyncio.gather(*[_edge_one(t, p) for t, p in chunk])

    # Dùng adelay để pin từng segment vào timestamp tuyệt đối start_sec
    # → không có sai số tích lũy dù trim/stretch có lệch nhỏ
    WAV_SR = 44100

    # segments: list of (start_sec, end_sec, wav_path)
    segments: list[tuple[float, float, Path]] = []

    for cue in cues:
        start = cue["start_sec"]
        end   = cue["end_sec"]
        window = end - start
        if window <= 0:
            continue

        text = cue["text"].strip()
        if not text:
            continue

        # Lấy raw TTS
        raw_path = raw_paths.get(cue["idx"]) or seg_dir / f"raw_{cue['idx']:04d}.mp3"
        if not raw_path.exists():
            await tts_segment(text, TTS_VOICE, raw_path,
                              beeknoee_key=beeknoee_key,
                              beeknoee_tts_model=beeknoee_tts_model,
                              beeknoee_tts_voice=beeknoee_tts_voice,
                              capcut_device_id=None,
                              capcut_voice_type=capcut_voice_type,
                              capcut_resource_id=capcut_resource_id)

        # Convert sang WAV, không stretch/trim — tốc độ cố định từ rate CapCut
        seg_wav = seg_dir / f"seg_{cue['idx']:04d}.wav"
        run([FFMPEG_BIN, "-y", "-i", str(raw_path),
             "-ar", str(WAV_SR), "-ac", "1", str(seg_wav)])
        try:
            raw_path.unlink()
        except FileNotFoundError:
            pass

        segments.append((start, end, seg_wav))

    if not segments:
        raise RuntimeError("Không tạo được segment TTS nào.")

    # Dùng FFmpeg filter_complex với adelay để pin mỗi segment vào start_sec tuyệt đối
    # Giới hạn 200 inputs/filter_complex của FFmpeg → chia batch nếu cần
    ADELAY_BATCH = 150

    def _mix_batch(batch: list[tuple[float, float, Path]], total_dur: float, out_wav: Path):
        """Mix một batch segments bằng adelay+amix → 1 WAV."""
        inputs = []
        filter_parts = []
        labels = []
        for i, (st, _, wav) in enumerate(batch):
            inputs += ["-i", str(wav)]
            delay_ms = int(st * 1000)
            filter_parts.append(f"[{i}]adelay={delay_ms}|{delay_ms}[d{i}]")
            labels.append(f"[d{i}]")
        mix_label = "".join(labels) + f"amix=inputs={len(batch)}:normalize=0[out]"
        filter_complex = ";".join(filter_parts) + ";" + mix_label
        run([
            FFMPEG_BIN, "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-ar", str(WAV_SR), "-ac", "1",
            "-t", f"{total_dur:.6f}",
            str(out_wav),
        ])

    total_dur = video_duration + 2.0

    if len(segments) <= ADELAY_BATCH:
        tts_track_wav = work_dir / "tts_track.wav"
        _mix_batch(segments, total_dur, tts_track_wav)
    else:
        # Chia thành nhiều batch, mỗi batch mix riêng rồi amix lại
        batch_wavs: list[Path] = []
        for bi in range(0, len(segments), ADELAY_BATCH):
            batch = segments[bi:bi + ADELAY_BATCH]
            bwav = work_dir / f"batch_{bi:04d}.wav"
            _mix_batch(batch, total_dur, bwav)
            batch_wavs.append(bwav)

        # Final amix các batch
        tts_track_wav = work_dir / "tts_track.wav"
        inputs = []
        labels = []
        for i, bw in enumerate(batch_wavs):
            inputs += ["-i", str(bw)]
            labels.append(f"[{i}]")
        fc = "".join(labels) + f"amix=inputs={len(batch_wavs)}:normalize=0[out]"
        run([
            FFMPEG_BIN, "-y",
            *inputs,
            "-filter_complex", fc,
            "-map", "[out]",
            "-ar", str(WAV_SR), "-ac", "1",
            "-t", f"{total_dur:.6f}",
            str(tts_track_wav),
        ])
        for bw in batch_wavs:
            try:
                bw.unlink()
            except FileNotFoundError:
                pass

    tts_track = work_dir / "tts_track.mp3"
    run([
        FFMPEG_BIN, "-y", "-i", str(tts_track_wav),
        "-acodec", "libmp3lame", "-b:a", "128k",
        str(tts_track),
    ])
    try:
        tts_track_wav.unlink()
    except FileNotFoundError:
        pass
    return tts_track


# ---------------------------------------------------------------------------
# STEP 5 (optional): Kéo dãn video chậm lại
# ---------------------------------------------------------------------------

def slowdown_video(video_path: Path, out_path: Path, factor: float) -> Path:
    """
    Kéo dãn video factor lần (vd factor=1.2 → chậm hơn 20%).
    setpts=factor*PTS cho video, atempo=1/factor cho audio gốc.
    """
    print(f"  → Kéo dãn video {factor}x (chậm lại {(factor-1)*100:.0f}%)...")
    atempo = 1.0 / factor
    af_filters = ",".join(atempo_chain(atempo))
    run([
        FFMPEG_BIN, "-y", "-i", str(video_path),
        "-vf", f"setpts={factor:.4f}*PTS",
        "-af", af_filters,
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        str(out_path),
    ], timeout=1800)
    return out_path


def scale_cues(cues: list[dict], factor: float) -> list[dict]:
    """Nhân tất cả timestamp trong SRT theo factor."""
    scaled = []
    for c in cues:
        s = c["start_sec"] * factor
        e = c["end_sec"]   * factor
        scaled.append({**c, "start": sec_to_srt_time(s), "end": sec_to_srt_time(e),
                        "start_sec": s, "end_sec": e})
    return scaled


# ---------------------------------------------------------------------------
# STEP 6: Render final video
# ---------------------------------------------------------------------------

def render_video(
    video_path: Path,
    tts_track: Path,
    srt_path: Path,
    output_path: Path,
    bg_music: Path | None = None,
    bg_volume: float = 0.3,
    tts_volume: float = 1.8,
    original_audio: Path | None = None,
    original_volume: float = 0.3,
):
    print("  → Render video (burn sub + mix TTS + nhạc nền)...")

    srt_escaped = (
        str(Path(srt_path).resolve())
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
    )

    sub_style = (
        "FontName=Arial Unicode MS,FontSize=22,Bold=1,"
        "PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,"
        "BorderStyle=1,Outline=2,Shadow=0,"
        "Alignment=2,MarginV=22"
    )

    # Watermark bounce DVD-style: bật cả ngang lẫn dọc
    watermark_text = "nem\\_vietsub"
    watermark = (
        f"drawtext=text='{watermark_text}':fontsize=24:fontcolor=white@0.5:"
        f"fontfile=/System/Library/Fonts/Helvetica.ttc:"
        f"x=abs(mod(t*73\\,2*(w-tw))-(w-tw)):"
        f"y=abs(mod(t*51\\,2*(h-th))-(h-th))"
    )

    vf = f"[0:v]crop=iw:ih*0.15:0:ih*0.85,boxblur=10:5[blurred];[0:v][blurred]overlay=0:H*0.85,subtitles={srt_escaped}:force_style='{sub_style}',{watermark}[vout]"

    # Xây danh sách inputs và audio filter linh hoạt
    inputs = ["-i", str(video_path)]
    audio_parts = []
    audio_labels = []

    # TTS luôn có
    inputs += ["-i", str(tts_track)]
    tts_idx = 1
    audio_parts.append(f"[{tts_idx}:a]volume={tts_volume}[tts]")
    audio_labels.append("[tts]")

    # Nhạc nền upload (loop)
    if bg_music:
        bg_idx = len(inputs) // 2
        inputs += ["-stream_loop", "-1", "-i", str(bg_music)]
        audio_parts.append(f"[{bg_idx}:a]volume={bg_volume}[bg]")
        audio_labels.append("[bg]")

    # Audio gốc đã tách vocals (no_vocals từ Demucs)
    if original_audio:
        orig_idx = len(inputs) // 2
        inputs += ["-i", str(original_audio)]
        audio_parts.append(f"[{orig_idx}:a]volume={original_volume}[orig]")
        audio_labels.append("[orig]")

    if len(audio_labels) == 1:
        filter_complex = f"{vf};{audio_parts[0].replace('[tts]', '[aout]').replace('volume={tts_volume}', f'volume={tts_volume}')}"
        # đơn giản hơn: không cần amix
        filter_complex = f"{vf};[{tts_idx}:a]volume={tts_volume}[aout]"
    else:
        n = len(audio_labels)
        filter_complex = (
            f"{vf};"
            + ";".join(audio_parts)
            + f";{''.join(audio_labels)}amix=inputs={n}:duration=first:dropout_transition=0[aout]"
        )

    cmd = [
        FFMPEG_BIN, "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "[aout]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(output_path),
    ]

    try:
        run(cmd, timeout=1800)
    except subprocess.CalledProcessError as e:
        print("=== FFmpeg stderr ===")
        print(e.stderr[-3000:] if e.stderr else "(no stderr)")
        raise RuntimeError(f"FFmpeg lỗi (exit {e.returncode}):\n{e.stderr[-1000:] if e.stderr else ''}") from e


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def find_input_video(input_dir: Path) -> Path:
    for ext in ("*.mp4", "*.mov", "*.mkv", "*.avi", "*.webm"):
        files = sorted(input_dir.glob(ext))
        if files:
            return files[0]
    raise FileNotFoundError(f"Không tìm thấy video nào trong {input_dir}")


def main():
    parser = argparse.ArgumentParser(description="Dịch video tiếng Trung → tiếng Việt")
    parser.add_argument("--key",   default=None,  help="Groq API key (mặc định: đọc từ .env)")
    parser.add_argument("--input", default=None,  help="Đường dẫn video (mặc định: lấy từ input/)")
    parser.add_argument("--slow",  type=float, default=1.0,
                        help="Kéo dãn video chậm lại X lần trước khi xử lý (vd: --slow 1.2)")
    args = parser.parse_args()

    groq_key = args.key or os.environ.get("GROQ_API_KEY", "")
    if not groq_key or groq_key == "your_groq_key_here":
        print("Lỗi: chưa có Groq API key. Điền vào .env hoặc dùng --key YOUR_KEY")
        raise SystemExit(1)

    # Tìm video input
    if args.input:
        video_path = Path(args.input).expanduser().resolve()
    else:
        video_path = find_input_video(INPUT_DIR)
    print(f"\n[VIDEO] {video_path.name}")

    # Chuẩn bị thư mục
    OUTPUT_DIR.mkdir(exist_ok=True)
    job_id   = f"job_{int(time.time())}"
    work_dir = WORK_DIR / job_id
    work_dir.mkdir(parents=True, exist_ok=True)

    audio_stt_path = work_dir / "audio_stt.mp3"
    srt_zh_path    = work_dir / "captions_zh.srt"
    srt_vi_path    = work_dir / "captions_vi.srt"
    slow_factor    = args.slow
    suffix         = f"_slow{slow_factor}x" if slow_factor != 1.0 else ""
    output_path    = OUTPUT_DIR / f"{video_path.stem}_vi{suffix}.mp4"

    try:
        # 1. Kéo dãn video nếu có --slow
        if slow_factor != 1.0:
            print(f"\n[0/6] Kéo dãn video {slow_factor}x")
            slowed_path = work_dir / "slowed.mp4"
            video_path  = slowdown_video(video_path, slowed_path, slow_factor)

        # 1. Tách audio
        print("\n[1/6] Tách audio")
        extract_audio_for_stt(video_path, audio_stt_path)

        # 2. Demucs tách nhạc nền
        print("\n[2/6] Tách nhạc nền (Demucs)")
        bg_track = separate_background(video_path, work_dir)

        # 3. STT
        print("\n[3/6] Speech-to-Text (Groq Whisper)")
        zh_cues = stt_groq(audio_stt_path, groq_key)
        srt_zh_path.write_text(build_srt(zh_cues), encoding="utf-8")
        print(f"       {len(zh_cues)} segments")

        # 4. Dịch
        print("\n[4/6] Dịch Trung → Việt (Groq LLM)")
        vi_cues = translate_srt(zh_cues, groq_key)
        srt_vi_path.write_text(build_srt(vi_cues), encoding="utf-8")

        # 5. TTS
        print("\n[5/6] TTS tiếng Việt + đồng bộ thời gian")
        video_dur = get_audio_duration(video_path)
        tts_track = asyncio.run(build_tts_track(vi_cues, work_dir, video_dur))

        # 6. Render
        print("\n[6/6] Render video cuối")
        render_video(video_path, tts_track, bg_track, srt_vi_path, output_path)

        print(f"\n✓ Xong! Output: {output_path}")

    finally:
        # Dọn workdir
        import shutil
        try:
            shutil.rmtree(work_dir)
        except Exception:
            pass


if __name__ == "__main__":
    main()
