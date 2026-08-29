#!/usr/bin/env python3
"""
translate_video.py
Nháº­n video tiáº¿ng Trung tá»« thÆ° má»¥c input/, tá»± Ä‘á»™ng:
  1. TĂ¡ch audio Ä‘áº§y Ä‘á»§ (FFmpeg) + tĂ¡ch vocals/background (Demucs)
  2. STT tiáº¿ng Trung -> SRT (Groq Whisper)
  3. Dá»‹ch Trung -> Viá»‡t (Groq LLM)
  4. TTS tiáº¿ng Viá»‡t Ä‘á»“ng bá»™ theo timestamp (Edge TTS + FFmpeg atempo)
  5. Mix TTS + nháº¡c ná»n gá»‘c -> burn subtitle -> output/

CĂ¡ch dĂ¹ng:
  python3 translate_video.py
  python3 translate_video.py --input path/to/video.mp4
  python3 translate_video.py --key YOUR_GROQ_KEY  (override .env)
"""

import sys, io, os
os.environ.setdefault("PYTHONUTF8", "1")
for _s in ("stdout", "stderr"):
    _stream = getattr(sys, _s)
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if hasattr(_stream, "buffer"):
        setattr(sys, _s, io.TextIOWrapper(
            _stream.buffer, encoding="utf-8", errors="replace", line_buffering=True
        ))

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
BASE_DIR   = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).parent

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

def _resolve_ffprobe_binary() -> str:
    env_value = os.environ.get("FFPROBE_BIN")
    if env_value:
        return env_value

    names = ["ffprobe.exe", "ffprobe"] if sys.platform == "win32" else ["ffprobe"]
    ffmpeg_path = Path(FFMPEG_BIN)
    candidates: list[Path] = []

    if ffmpeg_path.parent != Path("."):
        candidates.extend(ffmpeg_path.parent / name for name in names)

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    return names[0]

FFPROBE_BIN      = _resolve_ffprobe_binary()

def _best_video_codec() -> tuple[list[str], list[str]]:
    """Tráº£ vá» (encode_flags, decode_flags) tá»‘i Æ°u theo platform/GPU."""
    import subprocess as _sp
    if sys.platform == "darwin":
        return ["-c:v", "h264_videotoolbox", "-q:v", "50"], []
    if sys.platform == "win32":
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"], []
    # Windows/Linux: thá»­ NVENC trÆ°á»›c
    _test_input = [FFMPEG_BIN, "-f", "lavfi", "-i", "color=black:size=1280x720:rate=30", "-t", "0.1"]
    try:
        r = _sp.run(
            _test_input + ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "constqp", "-qp", "23", "-f", "null", "-"],
            capture_output=True, timeout=10
        )
        if r.returncode == 0:
            print("[codec] h264_nvenc OK -> dĂ¹ng NVENC (GPU)")
            return ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "constqp", "-qp", "23"], ["-hwaccel", "cuda"]
    except Exception:
        pass
    # Thá»­ Windows Media Foundation (hardware via DirectX/GPU)
    if sys.platform == "win32":
        try:
            r = _sp.run(
                _test_input + ["-c:v", "h264_mf", "-f", "null", "-"],
                capture_output=True, timeout=10
            )
            if r.returncode == 0:
                print("[codec] h264_mf OK -> dĂ¹ng Media Foundation (GPU hardware)")
                return ["-c:v", "h264_mf", "-q:v", "25"], []
        except Exception:
            pass
    print("[codec] fallback -> libx264 (CPU)")
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"], []

_VIDEO_ENC, _VIDEO_DEC = _best_video_codec()
TTS_VOICE        = "vi-VN-HoaiMyNeural"
TTS_SPEED_BOOST  = 1.00   # khĂ´ng tÄƒng tá»‘c máº·c Ä‘á»‹nh, tá»± khá»›p theo window
ATEMPO_MAX       = 2.0    # FFmpeg atempo tá»‘i Ä‘a 2.0x má»—i filter
ATEMPO_MIN       = 0.5
CHUNK_BLOCKS     = 30     # sá»‘ block SRT má»—i láº§n gá»­i dá»‹ch
CONTEXT_BLOCKS   = 5      # sá»‘ block context giá»¯ láº¡i giá»¯a cĂ¡c chunk
PARALLEL_CHUNKS  = 3      # sá»‘ chunk gá»­i song song cĂ¹ng lĂºc
DEMUCS_MODEL     = "htdemucs"   # model Demucs tĂ¡ch vocals
BG_VOLUME        = 0.9    # Ă¢m lÆ°á»£ng nháº¡c ná»n so vá»›i gá»‘c (0.0-1.0)

# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def run(cmd: list[str], timeout: int = 300, check: bool = True, cwd=None) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False, cwd=cwd)
    if check and result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        detail = stderr[-2000:] or stdout[-2000:] or "no ffmpeg output"
        raise RuntimeError(
            f"Command failed with exit {result.returncode}: {' '.join(map(str, cmd))}\n{detail}"
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
    """Parse SRT text -> list of {idx, start, end, start_sec, end_sec, text}"""
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
# STEP 1: Extract audio + tĂ¡ch vocals/background (Demucs)
# ---------------------------------------------------------------------------

def extract_audio_for_stt(video_path: Path, audio_path: Path):
    """TĂ¡ch audio cháº¥t lÆ°á»£ng tháº¥p tá»‘i Æ°u cho Whisper STT (16kHz mono 32kbps)."""
    print("  -> TĂ¡ch audio cho STT (16kHz mono 32kbps)...")
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
    """DĂ¹ng Demucs tĂ¡ch vocals, giá»¯ láº¡i nháº¡c ná»n (no_vocals)."""
    print("  -> TĂ¡ch audio ná»n (Demucs)...")

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
        raise RuntimeError("Demucs khĂ´ng táº¡o ra file no_vocals.wav")

    bg_path = work_dir / "background.wav"
    no_vocals[0].rename(bg_path)
    return bg_path


# ---------------------------------------------------------------------------
# STEP 2: STT via Groq Whisper
# ---------------------------------------------------------------------------

def stt_groq(audio_path: Path, groq_key: str) -> list[dict]:
    print("  -> STT Groq Whisper (zh)...")
    from groq import Groq
    client = Groq(api_key=groq_key)
    with open(audio_path, "rb") as f:
        try:
            resp = client.audio.transcriptions.create(
                file=("audio.mp3", f, "audio/mpeg"),
                model="whisper-large-v3",
                language="zh",
                response_format="verbose_json",
                timestamp_granularities=["word", "segment"],
                temperature=0,
            )
        except TypeError:
            f.seek(0)
            resp = client.audio.transcriptions.create(
                file=("audio.mp3", f, "audio/mpeg"),
                model="whisper-large-v3",
                language="zh",
                response_format="verbose_json",
                temperature=0,
            )
    segments = resp.segments or []
    if not segments:
        raise RuntimeError("Groq STT khong tra ve segments. Kiem tra API key va file audio.")

    words = getattr(resp, "words", None) or []

    def _field(obj, name, default=None):
        if hasattr(obj, name):
            return getattr(obj, name)
        if isinstance(obj, dict):
            return obj.get(name, default)
        return default

    cues = []
    for i, seg in enumerate(segments, 1):
        s = float(_field(seg, "start", 0.0))
        e = float(_field(seg, "end", s))
        t = _field(seg, "text", "") or ""

        seg_words = []
        for w in words:
            ws = _field(w, "start")
            we = _field(w, "end")
            if ws is None or we is None:
                continue
            ws = float(ws)
            we = float(we)
            mid = (ws + we) / 2
            if s - 0.05 <= mid <= e + 0.05:
                seg_words.append((ws, we))
        if seg_words:
            s = min(w[0] for w in seg_words)
            e = max(w[1] for w in seg_words)

        cues.append({
            "idx":       i,
            "start":     sec_to_srt_time(s),
            "end":       sec_to_srt_time(e),
            "start_sec": s,
            "end_sec":   e,
            "text":      t.strip(),
        })
    if cues and cues[0]["start_sec"] <= 0.1 and cues[0]["end_sec"] - cues[0]["start_sec"] > 3.0:
        first = cues[0]
        cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", first["text"]))
        if cjk_chars:
            estimated_duration = max(1.0, min(4.0, cjk_chars / 5.5))
        else:
            estimated_duration = max(1.0, min(4.0, len(first["text"].split()) / 2.5))
        new_start = max(0.0, first["end_sec"] - estimated_duration)
        if new_start > 0.5:
            first["start_sec"] = new_start
            first["start"] = sec_to_srt_time(new_start)
    return cues


# ---------------------------------------------------------------------------
# STEP 3: Translate via Groq LLM
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """Báº¡n lĂ  dá»‹ch giáº£ phá»¥ Ä‘á» tiáº¿ng Viá»‡t chuyĂªn nghiá»‡p, dá»‹ch tá»« Báº¤T Ká»² ngĂ´n ngá»¯ nĂ o sang tiáº¿ng Viá»‡t Ä‘á»ƒ Ä‘á»c thĂ nh tiáº¿ng (TTS).

=== Äá»NH Dáº NG Äáº¦U RA â€” VI PHáº M = SAI HOĂ€N TOĂ€N ===
- Tráº£ vá» Ä‘Ăºng SRT thuáº§n tĂºy, KHĂ”NG markdown, KHĂ”NG giáº£i thĂ­ch.
- Má»—i block Ä‘áº§u vĂ o pháº£i cĂ³ ÄĂNG Má»˜T block Ä‘áº§u ra tÆ°Æ¡ng á»©ng, giá»¯ nguyĂªn sá»‘ thá»© tá»± vĂ  timestamp.
- TUYá»†T Äá»I KHĂ”NG gá»™p 2 block thĂ nh 1, KHĂ”NG bá» block, KHĂ”NG tĂ¡ch 1 block thĂ nh nhiá»u block.
- Náº¿u má»™t cĂ¢u gá»‘c ngáº¯n, dá»‹ch ngáº¯n tÆ°Æ¡ng á»©ng â€” Ä‘á»«ng bĂ¹ thĂªm cho dĂ i.

=== XÆ¯NG HĂ” â€” Báº®T BUá»˜C NHáº¤T QUĂN TOĂ€N Bá»˜ VIDEO ===
- XĂ¡c Ä‘á»‹nh ngĂ´i xÆ°ng tá»« chunk Ä‘áº§u tiĂªn vĂ  GIá»® NGUYĂN Ä‘áº¿n háº¿t, KHĂ”NG tá»± Ă½ Ä‘á»•i giá»¯a chá»«ng.
- Chá»n Má»˜T cáº·p xÆ°ng hĂ´ vĂ  lock cá»©ng cáº£ cáº·p Ä‘Ă³ suá»‘t video:
    thĂ´ng thÆ°á»ng:  tĂ´i â†” báº¡n
    thĂ¢n máº­t:      tao â†” mĂ y
    kĂ­nh trá»ng:    tĂ´i â†” anh / tĂ´i â†” chá»‹
    cá»• trang/vĂµ hiá»‡p: ta â†” ngÆ°Æ¡i
    gia Ä‘Ă¬nh/phim: anh â†” em, chĂº â†” chĂ¡u, v.v.
- NgĂ´i thá»© 2 sá»‘ Ă­t chá»‰ Ä‘Æ°á»£c dĂ¹ng Má»˜T tá»« duy nháº¥t: "báº¡n" HOáº¶C "ngÆ°Æ¡i" HOáº¶C "mĂ y" â€” khĂ´ng trá»™n láº«n dĂ¹ chá»‰ má»™t láº§n.
- Video dáº¡ng "what if / giáº£ sá»­ / náº¿u báº¡n lĂ ..." (narrator nĂ³i vá»›i khĂ¡n giáº£): luĂ´n dĂ¹ng "báº¡n", KHĂ”NG dĂ¹ng cáº­u/mĂ y/ngÆ°Æ¡i dĂ¹ cĂ¢u cĂ³ váº» thĂ¢n máº­t.
- NgĂ´i thá»© nháº¥t sá»‘ nhiá»u: "chĂºng tĂ´i / chĂºng ta / bá»n tao" â€” chá»n má»™t, khĂ´ng Ä‘á»•i.
- Náº¿u ngá»¯ cáº£nh Ä‘Ă£ dá»‹ch cho tháº¥y xÆ°ng hĂ´ cá»¥ thá»ƒ -> báº¯t buá»™c dĂ¹ng Ä‘Ăºng theo.

=== CHáº¤T LÆ¯á»¢NG Dá»CH ===
- Tá»± Ä‘á»™ng nháº­n diá»‡n ngĂ´n ngá»¯ nguá»“n (Anh, Trung, Nháº­t, HĂ n, TĂ¢y Ban Nha, PhĂ¡p...) vĂ  dá»‹ch sang tiáº¿ng Viá»‡t.
- Dá»‹ch tá»± nhiĂªn nhÆ° ngÆ°á»i Viá»‡t nĂ³i chuyá»‡n thá»±c sá»±, khĂ´ng cá»©ng nháº¯c, khĂ´ng dá»‹ch tá»«ng chá»¯.
- CĂ¢u pháº£i Ä‘á»§ chá»§ ngá»¯ vĂ  vá»‹ ngá»¯ Ä‘á»ƒ ngÆ°á»i nghe hiá»ƒu ngay láº§n Ä‘áº§u khi nghe TTS.
- DĂ¹ng tá»« thuáº§n Viá»‡t, háº¡n cháº¿ HĂ¡n Viá»‡t: "cĂ¡ voi sĂ¡t thá»§" khĂ´ng pháº£i "há»• kĂ¬nh", "ná»• tung" khĂ´ng pháº£i "bĂ¹ng phĂ¡t".
- Giá»¯ vÄƒn phong gá»‘c: khoa há»c thĂ¬ rĂµ rĂ ng, hĂ i hÆ°á»›c thĂ¬ thoáº£i mĂ¡i, ká»‹ch tĂ­nh thĂ¬ máº¡nh máº½.
- TĂªn riĂªng vĂ  thuáº­t ngá»¯ khoa há»c: giá»¯ nguyĂªn hoáº·c dĂ¹ng cĂ¡ch phiĂªn Ă¢m phá»• biáº¿n nháº¥t á»Ÿ Viá»‡t Nam.

=== KĂ Tá»° Bá» Cáº¤M (TTS sáº½ Ä‘á»c sai) ===
KhĂ´ng dĂ¹ng: - _ / ( ) ... vĂ  dáº¥u cháº¥m há»i cuá»‘i cĂ¢u thay báº±ng dáº¥u cháº¥m."""


BEEKNOEE_BASE_URL = "https://platform.beeknoee.com/api/v1"
BEEKNOEE_MODEL    = "gemini-2.5-flash-lite"
GROQ_CHAT_MODEL   = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")


def _make_chat_client(groq_key: str, beeknoee_key: str | None):
    """Tráº£ vá» (client, model). Æ¯u tiĂªn Beeknoee náº¿u cĂ³ key."""
    if beeknoee_key:
        from openai import OpenAI
        client = OpenAI(api_key=beeknoee_key, base_url=BEEKNOEE_BASE_URL)
        return client, BEEKNOEE_MODEL
    else:
        from groq import Groq
        return Groq(api_key=groq_key), GROQ_CHAT_MODEL


def translate_chunk(cues: list[dict], context_cues: list[dict],
                    groq_key: str, beeknoee_key: str | None = None) -> list[dict]:
    client, model = _make_chat_client(groq_key, beeknoee_key)

    prompt = ""
    if context_cues:
        ctx_srt = build_srt(context_cues)
        prompt += f"[NGá»® Cáº¢NH ÄĂƒ Dá»CH - chá»‰ tham chiáº¿u xÆ°ng hĂ´/tĂªn, KHĂ”NG dá»‹ch láº¡i]:\n{ctx_srt}\n\n"

    src_srt = build_srt(cues)
    prompt += f"Dá»‹ch ÄĂNG {len(cues)} block sau, Ä‘áº§u ra pháº£i cĂ³ ÄĂNG {len(cues)} block, khĂ´ng hÆ¡n khĂ´ng kĂ©m:\n\n{src_srt}"

    max_attempts = 5
    backoff = 5  # giĂ¢y, tÄƒng dáº§n khi gáº·p 429
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
                print(f"     Rate limit, chá» {wait}s rá»“i thá»­ láº¡i...")
                time.sleep(wait)
                continue
            raise

        raw = resp.choices[0].message.content or ""
        raw = re.sub(r"```\w*\n?", "", raw).strip()
        translated = parse_srt(raw)
        if len(translated) == len(cues):
            for src, tgt in zip(cues, translated):
                tgt["idx"]       = src["idx"]
                tgt["start"]     = src["start"]
                tgt["end"]       = src["end"]
                tgt["start_sec"] = src["start_sec"]
                tgt["end_sec"]   = src["end_sec"]
            return translated
        best_partial = translated
        if attempt < max_attempts - 1:
            prompt += f"\n\n[Lá»–I: cáº§n Ä‘Ăºng {len(cues)} block, nháº­n Ä‘Æ°á»£c {len(translated)}. KHĂ”NG gá»™p/bá» block nĂ o.]"
            time.sleep(3)

    # Fallback: align by SRT index, fill missing with source text
    print(f"     â  DĂ¹ng fallback: cÄƒn chá»‰nh theo index, bá»• sung block thiáº¿u báº±ng text gá»‘c")
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


def _translate_single_shot(cues: list[dict], groq_key: str,
                           beeknoee_key: str | None = None) -> list[dict] | None:
    """NĂ©m toĂ n bá»™ cues vĂ o 1 request. Tráº£ vá» None náº¿u output lá»‡ch."""
    client, model = _make_chat_client(groq_key, beeknoee_key)
    src_srt = build_srt(cues)
    prompt = (f"Dá»‹ch ÄĂNG {len(cues)} block sau, Ä‘áº§u ra pháº£i cĂ³ ÄĂNG {len(cues)} block, "
              f"khĂ´ng hÆ¡n khĂ´ng kĂ©m:\n\n{src_srt}")

    backoff = 5
    for attempt in range(4):
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
                print(f"     [single-shot] Rate limit, chá» {wait}s...")
                time.sleep(wait)
                continue
            print(f"     [single-shot] Lá»—i: {e}")
            return None

        raw = resp.choices[0].message.content or ""
        raw = re.sub(r"```\w*\n?", "", raw).strip()
        translated = parse_srt(raw)
        if len(translated) == len(cues):
            for src, tgt in zip(cues, translated):
                tgt["idx"]       = src["idx"]
                tgt["start"]     = src["start"]
                tgt["end"]       = src["end"]
                tgt["start_sec"] = src["start_sec"]
                tgt["end_sec"]   = src["end_sec"]
            print(f"     [single-shot] OK â€” {len(translated)} blocks")
            return translated

        print(f"     [single-shot] Lá»‡ch block: cáº§n {len(cues)}, nháº­n {len(translated)} â€” fallback chunked")
        return None

    return None


def translate_srt(cues: list[dict], groq_key: str,
                  beeknoee_key: str | None = None,
                  chunk_cb=None) -> list[dict]:
    """
    Dá»‹ch song song PARALLEL_CHUNKS chunk cĂ¹ng lĂºc.
    chunk_cb(done, total, partial_result): gá»i sau má»—i batch xong.
    """
    import concurrent.futures

    provider = "Beeknoee" if beeknoee_key else "Groq"

    print(f"  -> Dich {len(cues)} block SRT ({provider}), chunk={CHUNK_BLOCKS}, parallel={PARALLEL_CHUNKS}...")
    chunks = [cues[i:i + CHUNK_BLOCKS] for i in range(0, len(cues), CHUNK_BLOCKS)]
    total  = len(chunks)

    # Chia thĂ nh cĂ¡c batch song song, má»—i batch PARALLEL_CHUNKS chunk
    # Trong cĂ¹ng batch: cĂ¡c chunk Ä‘á»™c láº­p nhau nĂªn gá»­i song song Ä‘Æ°á»£c.
    # Context cá»§a chunk[i] dĂ¹ng káº¿t quáº£ batch trÆ°á»›c (sequential giá»¯a cĂ¡c batch).
    result: list[dict] = []
    done_count = 0

    for batch_start in range(0, total, PARALLEL_CHUNKS):
        batch_indices = range(batch_start, min(batch_start + PARALLEL_CHUNKS, total))

        # Context cho chunk Ä‘áº§u batch láº¥y tá»« káº¿t quáº£ Ä‘Ă£ cĂ³
        batch_context = result[-CONTEXT_BLOCKS:] if result else []

        def _translate_one(idx):
            # Context: chunk Ä‘áº§u batch dĂ¹ng batch_context, cĂ¡c chunk sau khĂ´ng cĂ³
            # (chĂºng cháº¡y song song nĂªn chÆ°a biáº¿t káº¿t quáº£ nhau)
            ctx = batch_context if idx == batch_start else []
            print(f"     chunk {idx+1}/{total} ({len(chunks[idx])} blocks)...")
            return idx, translate_chunk(chunks[idx], ctx, groq_key, beeknoee_key)

        with concurrent.futures.ThreadPoolExecutor(max_workers=PARALLEL_CHUNKS) as ex:
            futures = {ex.submit(_translate_one, i): i for i in batch_indices}
            batch_results = {}
            for f in concurrent.futures.as_completed(futures):
                idx, translated = f.result()
                batch_results[idx] = translated

        # GhĂ©p theo Ä‘Ăºng thá»© tá»±
        for idx in batch_indices:
            result.extend(batch_results[idx])
            done_count += 1
            if chunk_cb:
                chunk_cb(done_count, total, list(result))

        # Delay ngáº¯n giá»¯a cĂ¡c batch trĂ¡nh burst
        if batch_start + PARALLEL_CHUNKS < total:
            time.sleep(0.3 if beeknoee_key else 1.0)

    return result


# ---------------------------------------------------------------------------
# STEP 4: TTS aligned to SRT timestamps
# ---------------------------------------------------------------------------

CAPCUT_DEVICE_ID = os.environ.get("CAPCUT_DEVICE_ID", "7581502458217252368")
CAPCUT_IID       = os.environ.get("CAPCUT_IID",       "7581504179703056144")

CAPCUT_VOICES_VI = [
    ("Nho Ngot Ngao",       "BV421_vivn_streaming",                   "7252594014782755330"),
    ("Co Gai Hoat Ngon",    "BV074_streaming",                        "7102355709945188865"),
    ("Giong Nu Pho Thong",  "vi_female_huong",                        "7264854897953083905"),
    ("Giong Be",            "BV074_streaming_dsp",                    "7550087831092251920"),
    ("Mai",                 "BV562_streaming",                        "7483736254694035984"),
    ("Gai Moi Lon",         "multi_female_peiqi_uranus_bigtts",       "7637458789033151751"),
    ("Ban Mai",             "multi_female_yangguangnv_uranus_bigtts", "7637456432522218773"),
    ("Ban Tin Nu",          "multi_female_sisi_uranus_bigtts",        "7637455857285860629"),
    ("Thanh Nien Tu Tin",   "BV075_streaming",                        "7102355803792740865"),
    ("Giong Nam Tram",      "multi_male_felipe_uranus_bigtts",        "7637456729696996628"),
    ("Alex Dai De",         "BV560_streaming",                        "7483736167565758992"),
]


def _capcut_session(device_id: str):
    """Táº¡o session + device dict dĂ¹ng chung cho CapCut API."""
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
    """Gá»­i nhiá»u text trong 1 request CapCut TTS, lÆ°u tá»«ng MP3 vĂ o out_paths tÆ°Æ¡ng á»©ng."""
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
        raise RuntimeError(f"CapCut TTS submit lá»—i: {d.get('errmsg')}")
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
            # Map theo field "text" Ä‘á»ƒ trĂ¡nh lá»‡ch thá»© tá»±
            by_text = {item["text"]: item["speech_url"] for item in audio_list}
            for text, out_path in zip(texts, out_paths):
                url = by_text.get(text)
                if not url:
                    # fallback theo index náº¿u text khĂ´ng khá»›p (CapCut cĂ³ thá»ƒ normalize)
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
    """Gá»i CapCut TTS API Ä‘á»“ng bá»™, lÆ°u MP3 vĂ o out_path."""
    capcut_tts_batch([text], voice_type, resource_id, [out_path], device_id)


async def tts_segment(text: str, voice: str, out_path: Path,
                      capcut_device_id: str | None = None,
                      capcut_voice_type: str | None = None,
                      capcut_resource_id: str | None = None,
                      **_ignored):
    """Táº¡o file TTS cho 1 Ä‘oáº¡n text dĂ¹ng CapCut TTS, retry vĂ´ háº¡n + Ä‘á»•i device_id khi bá»‹ block."""
    import random as _random
    clean = re.sub(r"[^\w\s\u00c0-\u024f\u1e00-\u1eff.,!?;:()\"'\u2013-]", " ", text).strip() or "."
    current_did = capcut_device_id
    attempt = 0
    while True:
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, capcut_tts_sync,
                clean, capcut_voice_type, capcut_resource_id, out_path, current_did,
            )
            return
        except Exception as e:
            attempt += 1
            print(f"  â  CapCut TTS láº§n {attempt}: '{clean[:60]}' â€” {e}, Ä‘á»•i device_id vĂ  thá»­ láº¡i...")
            current_did = str(_random.randint(7000000000000000000, 7999999999999999999))
            await asyncio.sleep(min(2 * attempt, 10))


def get_audio_duration(path: Path) -> float:
    r = run([
        FFPROBE_BIN, "-v", "error",
        "-show_entries", "stream=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ])
    # Láº¥y max cá»§a táº¥t cáº£ stream (video + audio) Ä‘á»ƒ trĂ¡nh thiáº¿u náº¿u 1 stream ngáº¯n hÆ¡n
    vals = [float(x) for x in r.stdout.strip().splitlines() if x.strip()]
    return max(vals) if vals else 0.0


def stretch_audio(src: Path, dst: Path, ratio: float):
    """KĂ©o/nĂ©n audio theo ratio dĂ¹ng atempo. ratio > 1 = nhanh hÆ¡n."""
    filters = ",".join(atempo_chain(ratio))
    extra = ["-ar", "44100", "-ac", "1"] if dst.suffix.lower() == ".wav" else []
    run([
        FFMPEG_BIN, "-y", "-i", str(src),
        "-filter:a", filters,
        *extra,
        str(dst),
    ])


def make_silent(duration: float, dst: Path):
    """Táº¡o file Ă¢m thanh im láº·ng duration giĂ¢y. DĂ¹ng WAV náº¿u dst.suffix=.wav."""
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
                          capcut_rate: str = "1.0",
                          speed_ratio: float = 1.0,
                          progress_cb=None,
                          log_cb=None) -> Path:
    """
    Build track TTS Ä‘á»“ng bá»™:
    - Má»—i cue: TTS -> atempo Ä‘á»ƒ khá»›p Ä‘Ăºng window [start_sec, end_sec]
    - GhĂ©p táº¥t cáº£ cue + khoáº£ng láº·ng giá»¯a chĂºng thĂ nh 1 file mp3
    - Æ¯u tiĂªn: CapCut TTS -> Beeknoee TTS -> Edge TTS
    """
    def _log(msg: str):
        print(msg, flush=True)
        if log_cb:
            log_cb(msg)

    if capcut_device_id and capcut_voice_type:
        label = f"CapCut TTS batch ({capcut_voice_type})"
    else:
        label = "CapCut TTS (thiáº¿u device_id)"
    _log(f"  -> TTS {len(cues)} Ä‘oáº¡n ({label})...")
    seg_dir = work_dir / "tts_segs"
    seg_dir.mkdir(exist_ok=True)

    # --- Pre-fetch CapCut batch -------------------------------------------
    # Gom cue co text, gui theo batch 50 doan/request thay vi tung cai.
    raw_paths: dict[int, Path] = {}   # idx -> raw mp3 path
    total_cues = len([c for c in cues if c["text"].strip()])

    if capcut_device_id and capcut_voice_type and capcut_resource_id:
        BATCH = 50
        pending_idxs  : list[int]  = []
        pending_texts : list[str]  = []
        pending_paths : list[Path] = []
        done_cues     : int        = 0

        current_device_id = capcut_device_id

        def _new_device_id() -> str:
            import random
            new_id = str(random.randint(7000000000000000000, 7999999999999999999))
            # Cáº­p nháº­t .env Ä‘á»ƒ láº§n sau dĂ¹ng luĂ´n
            env_path = BASE_DIR / ".env"
            if env_path.exists():
                lines = env_path.read_text().splitlines()
                new_lines = [f"CAPCUT_DEVICE_ID={new_id}" if l.startswith("CAPCUT_DEVICE_ID=") else l for l in lines]
                if not any(l.startswith("CAPCUT_DEVICE_ID=") for l in lines):
                    new_lines.append(f"CAPCUT_DEVICE_ID={new_id}")
                env_path.write_text("\n".join(new_lines) + "\n")
            print(f"  -> Äá»•i device_id má»›i: {new_id}")
            return new_id

        def _flush_batch():
            nonlocal current_device_id, done_cues
            if not pending_texts:
                return
            _log(f"CapCut batch {pending_idxs[0]}â€“{pending_idxs[-1]} ({len(pending_texts)} Ä‘oáº¡n)...")
            if progress_cb:
                progress_cb(done_cues, total_cues)
            attempt = 0
            while True:
                try:
                    capcut_tts_batch(pending_texts, capcut_voice_type, capcut_resource_id,
                                     pending_paths, current_device_id, rate=capcut_rate)
                    done_cues += len(pending_texts)
                    if progress_cb:
                        progress_cb(done_cues, total_cues)
                    _log(f"âœ“ Batch xong â€” {done_cues}/{total_cues} Ä‘oáº¡n")
                    return
                except Exception as e:
                    if isinstance(e, ModuleNotFoundError):
                        raise RuntimeError(f"Missing Python dependency for CapCut TTS: {e.name}") from e
                    attempt += 1
                    msg = f"â  CapCut batch lá»—i láº§n {attempt}: {str(e)[:80]} â€” Ä‘á»•i device_id..."
                    _log(msg)
                    if progress_cb:
                        progress_cb(done_cues, total_cues)
                    current_device_id = _new_device_id()
                    time.sleep(min(2 * attempt, 10))

        for cue in cues:
            text = cue["text"].strip()
            if not text:
                continue
            clean = re.sub(r"[^\w\s\u00c0-\u024f\u1e00-\u1eff.,!?;:()\"'\u2013-]", " ", text).strip() or "."
            raw_path = seg_dir / f"raw_{cue['idx']:04d}.mp3"
            raw_paths[cue["idx"]] = raw_path
            pending_idxs.append(cue["idx"])
            pending_texts.append(clean)
            pending_paths.append(raw_path)
            if len(pending_texts) >= BATCH:
                _flush_batch()
                pending_idxs.clear(); pending_texts.clear(); pending_paths.clear()
                await asyncio.sleep(capcut_delay)
        _flush_batch()  # flush batch cuá»‘i


    # DĂ¹ng adelay Ä‘á»ƒ pin tá»«ng segment vĂ o timestamp tuyá»‡t Ä‘á»‘i start_sec
    # -> khĂ´ng cĂ³ sai sá»‘ tĂ­ch lÅ©y dĂ¹ trim/stretch cĂ³ lá»‡ch nhá»
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

        # Láº¥y raw TTS
        raw_path = raw_paths.get(cue["idx"]) or seg_dir / f"raw_{cue['idx']:04d}.mp3"
        if not raw_path.exists():
            await tts_segment(text, TTS_VOICE, raw_path,
                              capcut_device_id=capcut_device_id,
                              capcut_voice_type=capcut_voice_type,
                              capcut_resource_id=capcut_resource_id)

        # Convert sang WAV + apply speed_ratio Ä‘á»“ng Ä‘á»u (atempo)
        seg_wav = seg_dir / f"seg_{cue['idx']:04d}.wav"
        if speed_ratio and abs(speed_ratio - 1.0) > 0.01:
            tmp_mp3 = seg_dir / f"spd_{cue['idx']:04d}.mp3"
            stretch_audio(raw_path, tmp_mp3, speed_ratio)
            run([FFMPEG_BIN, "-y", "-i", str(tmp_mp3),
                 "-ar", str(WAV_SR), "-ac", "1", str(seg_wav)])
            try:
                tmp_mp3.unlink()
            except FileNotFoundError:
                pass
        else:
            run([FFMPEG_BIN, "-y", "-i", str(raw_path),
                 "-ar", str(WAV_SR), "-ac", "1", str(seg_wav)])
        try:
            raw_path.unlink()
        except FileNotFoundError:
            pass

        segments.append((start, end, seg_wav))

    if not segments:
        raise RuntimeError("KhĂ´ng táº¡o Ä‘Æ°á»£c segment TTS nĂ o.")

    # DĂ¹ng FFmpeg filter_complex vá»›i adelay Ä‘á»ƒ pin má»—i segment vĂ o start_sec tuyá»‡t Ä‘á»‘i
    # Giá»›i háº¡n 200 inputs/filter_complex cá»§a FFmpeg -> chia batch náº¿u cáº§n
    ADELAY_BATCH = 150

    def _mix_batch(batch: list[tuple[float, float, Path]], total_dur: float, out_wav: Path):
        """Mix má»™t batch segments báº±ng adelay+amix -> 1 WAV."""
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
        # Chia thĂ nh nhiá»u batch, má»—i batch mix riĂªng rá»“i amix láº¡i
        batch_wavs: list[Path] = []
        for bi in range(0, len(segments), ADELAY_BATCH):
            batch = segments[bi:bi + ADELAY_BATCH]
            bwav = work_dir / f"batch_{bi:04d}.wav"
            _mix_batch(batch, total_dur, bwav)
            batch_wavs.append(bwav)

        # Final amix cĂ¡c batch
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
# STEP 5 (optional): KĂ©o dĂ£n video cháº­m láº¡i
# ---------------------------------------------------------------------------

def slowdown_video(video_path: Path, out_path: Path, factor: float) -> Path:
    """
    KĂ©o dĂ£n video factor láº§n (vd factor=1.2 -> cháº­m hÆ¡n 20%).
    setpts=factor*PTS cho video, atempo=1/factor cho audio gá»‘c.
    """
    print(f"  -> KĂ©o dĂ£n video {factor}x (cháº­m láº¡i {(factor-1)*100:.0f}%)...")
    atempo = 1.0 / factor
    af_filters = ",".join(atempo_chain(atempo))
    run([
        FFMPEG_BIN, "-y", "-i", str(video_path),
        "-vf", f"setpts={factor:.4f}*PTS",
        "-af", af_filters,
        *_VIDEO_ENC,
        "-c:a", "aac", "-b:a", "192k",
        str(out_path),
    ], timeout=1800)
    return out_path


def scale_cues(cues: list[dict], factor: float) -> list[dict]:
    """NhĂ¢n táº¥t cáº£ timestamp trong SRT theo factor."""
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
    subtitle_backdrop: bool = True,
    logo: Path | None = None,
    logo_pos: str = "topright",
    logo_size: int = 100,
    logo_tab: str = "none",
    logo_text: str = "",
    logo_fontsize: int = 28,
    logo_color: str = "#ffffff",
    watermark: str = "nem_vietsub",
    render_preset: str = "veryfast",
):
    print("  -> Render video (burn sub + mix TTS + nháº¡c ná»n)...")

    # On Windows, FFmpeg subtitles filter cannot parse drive-letter paths (C:\...).
    # Fix: run ffmpeg with cwd=work_dir and use just the filename (relative).
    _ffmpeg_cwd = str(Path(srt_path).parent) if sys.platform == "win32" else None
    _srt_name = Path(srt_path).name if sys.platform == "win32" else str(Path(srt_path).resolve())
    srt_escaped = (
        _srt_name
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

    # Watermark bounce DVD-style
    wm_text = watermark.replace("_", "\\_").replace("'", "\\'").replace(":", "\\:") if watermark.strip() else ""
    _wm_font = (
        "C\\\\:/Windows/Fonts/arial.ttf" if sys.platform == "win32"
        else "/System/Library/Fonts/Helvetica.ttc"
    )
    wm_filter = (
        f"drawtext=text='{wm_text}':fontsize=24:fontcolor=white@0.5:"
        f"fontfile={_wm_font}:"
        f"x=abs(mod(t*73\\,2*(w-tw))-(w-tw)):"
        f"y=abs(mod(t*51\\,2*(h-th))-(h-th))"
    ) if wm_text else ""

    # XĂ¢y danh sĂ¡ch inputs
    inputs = ["-i", str(video_path)]
    inputs += ["-i", str(tts_track)]   # idx 1
    tts_idx = 1

    audio_parts  = [f"[{tts_idx}:a]volume={tts_volume}[tts]"]
    audio_labels = ["[tts]"]

    if bg_music:
        bg_idx = len(inputs) // 2
        inputs += ["-stream_loop", "-1", "-i", str(bg_music)]
        audio_parts.append(f"[{bg_idx}:a]volume={bg_volume}[bg]")
        audio_labels.append("[bg]")

    if original_audio:
        orig_idx = len(inputs) // 2
        inputs += ["-i", str(original_audio)]
        audio_parts.append(f"[{orig_idx}:a]volume={original_volume}[orig]")
        audio_labels.append("[orig]")

    pos_map = {
        "topleft":     ("10", "10"),
        "topright":    ("W-w-10", "10"),
        "bottomleft":  ("10", "H-h-10"),
        "bottomright": ("W-w-10", "H-h-10"),
    }
    px, py = pos_map.get(logo_pos, ("W-w-10", "10"))

    vf_chain = f"subtitles={srt_escaped}:force_style='{sub_style}'"
    if wm_filter:
        vf_chain += f",{wm_filter}"

    if subtitle_backdrop:
        base_vf = (
            f"[0:v]split[vmain][vblur];"
            f"[vblur]crop=iw:ih*0.18:0:ih*0.82,boxblur=8:2[blurred];"
            f"[vmain][blurred]overlay=0:H*0.82,{vf_chain}"
        )
    else:
        base_vf = f"[0:v]{vf_chain}"

    _has_img_logo  = logo_tab == "img"  and logo and Path(logo).exists()
    _has_text_logo = logo_tab == "text" and logo_text.strip()

    if _has_img_logo:
        logo_idx = len(inputs) // 2
        inputs += ["-i", str(logo)]
        vf_base = (
            f"{base_vf}[vbase];"
            f"[{logo_idx}:v]scale={logo_size}:-1[logo];"
            f"[vbase][logo]overlay={px}:{py}[vout]"
        )
    elif _has_text_logo:
        # hex color #rrggbb -> ffmpeg fontcolor
        txt = logo_text.strip().replace("'", "\\'").replace(":", "\\:")
        fc  = logo_color.lstrip("#")
        fc  = f"0x{fc}ff" if len(fc) == 6 else "white"
        _logo_font = (
            "C\\\\:/Windows/Fonts/arial.ttf" if sys.platform == "win32"
            else "/System/Library/Fonts/Helvetica.ttc"
        )
        vf_base = (
            f"{base_vf},"
            f"drawtext=text='{txt}':fontsize={logo_fontsize}:fontcolor={fc}:"
            f"fontfile={_logo_font}:"
            f"x={px}:y={py}:box=1:boxcolor=black@0.4:boxborderw=4[vout]"
        )
    else:
        vf_base = f"{base_vf}[vout]"

    if len(audio_labels) == 1:
        # Pad silence after the last TTS cue so -shortest follows the source
        # video duration instead of cutting the exported video at the TTS end.
        filter_complex = f"{vf_base};[{tts_idx}:a]volume={tts_volume},alimiter=limit=0.95,apad[aout]"
    else:
        n = len(audio_labels)
        filter_complex = (
            f"{vf_base};"
            + ";".join(audio_parts)
            + f";{''.join(audio_labels)}amix=inputs={n}:duration=longest:dropout_transition=0,alimiter=limit=0.95,apad[aout]"
        )

    video_enc = list(_VIDEO_ENC)
    if "-preset" in video_enc and "-c:v" in video_enc and "libx264" in video_enc:
        preset_i = video_enc.index("-preset") + 1
        video_enc[preset_i] = render_preset if render_preset in {"ultrafast", "superfast", "veryfast", "fast", "medium"} else "veryfast"

    cmd = [
        FFMPEG_BIN, "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "[aout]",
        *video_enc,
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(output_path),
    ]

    try:
        run(cmd, timeout=1800, cwd=_ffmpeg_cwd)
    except subprocess.CalledProcessError as e:
        print("=== FFmpeg stderr ===")
        print(e.stderr[-3000:] if e.stderr else "(no stderr)")
        raise RuntimeError(f"FFmpeg lá»—i (exit {e.returncode}):\n{e.stderr[-1000:] if e.stderr else ''}") from e


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def find_input_video(input_dir: Path) -> Path:
    for ext in ("*.mp4", "*.mov", "*.mkv", "*.avi", "*.webm"):
        files = sorted(input_dir.glob(ext))
        if files:
            return files[0]
    raise FileNotFoundError(f"KhĂ´ng tĂ¬m tháº¥y video nĂ o trong {input_dir}")


def main():
    parser = argparse.ArgumentParser(description="Dá»‹ch video tiáº¿ng Trung -> tiáº¿ng Viá»‡t")
    parser.add_argument("--key",   default=None,  help="Groq API key (máº·c Ä‘á»‹nh: Ä‘á»c tá»« .env)")
    parser.add_argument("--input", default=None,  help="ÄÆ°á»ng dáº«n video (máº·c Ä‘á»‹nh: láº¥y tá»« input/)")
    parser.add_argument("--slow",  type=float, default=1.0,
                        help="KĂ©o dĂ£n video cháº­m láº¡i X láº§n trÆ°á»›c khi xá»­ lĂ½ (vd: --slow 1.2)")
    args = parser.parse_args()

    groq_key = args.key or os.environ.get("GROQ_API_KEY", "")
    if not groq_key or groq_key == "your_groq_key_here":
        print("Lá»—i: chÆ°a cĂ³ Groq API key. Äiá»n vĂ o .env hoáº·c dĂ¹ng --key YOUR_KEY")
        raise SystemExit(1)

    # TĂ¬m video input
    if args.input:
        video_path = Path(args.input).expanduser().resolve()
    else:
        video_path = find_input_video(INPUT_DIR)
    print(f"\n[VIDEO] {video_path.name}")

    # Chuáº©n bá»‹ thÆ° má»¥c
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
        # 1. KĂ©o dĂ£n video náº¿u cĂ³ --slow
        if slow_factor != 1.0:
            print(f"\n[0/6] KĂ©o dĂ£n video {slow_factor}x")
            slowed_path = work_dir / "slowed.mp4"
            video_path  = slowdown_video(video_path, slowed_path, slow_factor)

        # 1. TĂ¡ch audio
        print("\n[1/6] TĂ¡ch audio")
        extract_audio_for_stt(video_path, audio_stt_path)

        # 2. Demucs tĂ¡ch nháº¡c ná»n
        print("\n[2/6] TĂ¡ch nháº¡c ná»n (Demucs)")
        bg_track = separate_background(video_path, work_dir)

        # 3. STT
        print("\n[3/6] Speech-to-Text (Groq Whisper)")
        zh_cues = stt_groq(audio_stt_path, groq_key)
        srt_zh_path.write_text(build_srt(zh_cues), encoding="utf-8")
        print(f"       {len(zh_cues)} segments")

        # 4. Dá»‹ch
        print("\n[4/6] Dá»‹ch Trung -> Viá»‡t (Groq LLM)")
        vi_cues = translate_srt(zh_cues, groq_key)
        srt_vi_path.write_text(build_srt(vi_cues), encoding="utf-8")

        # 5. TTS
        print("\n[5/6] TTS tiáº¿ng Viá»‡t + Ä‘á»“ng bá»™ thá»i gian")
        video_dur = get_audio_duration(video_path)
        tts_track = asyncio.run(build_tts_track(vi_cues, work_dir, video_dur))

        # 6. Render
        print("\n[6/6] Render video cuá»‘i")
        render_video(video_path, tts_track, bg_track, srt_vi_path, output_path)

        print(f"\nâœ“ Xong! Output: {output_path}")

    finally:
        # Dá»n workdir
        import shutil
        try:
            shutil.rmtree(work_dir)
        except Exception:
            pass


if __name__ == "__main__":
    main()
