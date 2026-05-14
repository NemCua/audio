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
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=check)


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
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-acodec", "libmp3lame",
        "-ac", "1", "-ar", "16000", "-b:a", "32k",
        str(audio_path),
    ])
    size = audio_path.stat().st_size
    if size > 24 * 1024 * 1024:
        tmp = audio_path.with_suffix(".tmp.mp3")
        run([
            "ffmpeg", "-y", "-i", str(audio_path),
            "-acodec", "libmp3lame", "-ac", "1", "-ar", "16000", "-b:a", "16k",
            str(tmp),
        ])
        tmp.replace(audio_path)


def separate_background(video_path: Path, work_dir: Path) -> Path:
    """
    Dùng Demucs tách vocals ra khỏi audio gốc.
    Trả về đường dẫn file background (no_vocals) dạng WAV.
    """
    print("  → Demucs tách nhạc nền (có thể mất 1-3 phút)...")

    # Tách audio gốc chất lượng cao ra WAV trước
    full_audio = work_dir / "full_audio.wav"
    run([
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2",
        str(full_audio),
    ])

    # Chạy Demucs
    demucs_out = work_dir / "demucs_out"
    run([
        "python3", "-m", "demucs",
        "--two-stems", "vocals",
        "-n", DEMUCS_MODEL,
        "--device", "mps",
        "-o", str(demucs_out),
        str(full_audio),
    ], timeout=3600)

    # Tìm file no_vocals output
    # Demucs tạo: demucs_out/{model}/full_audio/no_vocals.wav
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

SYSTEM_PROMPT = """Bạn là dịch giả phụ đề chuyên nghiệp, chuyên dịch phim tu tiên/kiếm hiệp Trung Quốc sang tiếng Việt với văn phong cổ phong tự nhiên.

MỤC TIÊU: Bản dịch có hồn, đậm chất võ hiệp — không dịch máy móc từng chữ. Giữ khí thế, cảm xúc và không khí của từng cảnh.

QUY TẮC BẮT BUỘC:
1. Giữ nguyên số block và timestamp SRT — KHÔNG gộp, KHÔNG tách, KHÔNG bỏ block.
2. Chỉ trả về SRT thuần túy, không giải thích, không markdown.
3. Câu ngắn gọn, súc tích — đúng chất thoại phim kiếm hiệp.

PHONG CÁCH DỊCH:
- Lời thoại hào sảng, mạnh mẽ khi đấu khẩu; trang trọng khi bái sư/lễ nghi.
- Cảnh chiến đấu: dùng từ mạnh, dứt khoát — "chém", "phá", "nghiền nát", "vạn kiếm quy tông".
- Tránh từ hiện đại lạc điệu — không dùng "okay", "được rồi", "vâng ạ" trong bối cảnh cổ đại.
- Thành ngữ Trung → tìm tương đương tiếng Việt cổ phong, không dịch thẳng.

XƯNG HÔ CỔ PHONG (nhất quán theo nhân vật):
- 我/吾 → ta (bề trên/cao nhân), tại hạ (khiêm tốn), tao/ta (thân mật)
- 朕 → trẫm, 本座 → bản tọa, 本王 → bản vương, 在下 → tại hạ
- 你 → ngươi (với kẻ thấp hơn), huynh/đệ/cô nương tùy quan hệ
- 前辈 → tiền bối, 师父 → sư phụ, 师兄 → sư huynh, 师姐 → sư tỷ
- 道友 → đạo hữu, 阁下 → các hạ, 贫道 → bần đạo, 贫僧 → bần tăng

THUẬT NGỮ TU TIÊN (dịch nhất quán):
- 灵石→linh thạch, 灵根→linh căn, 丹田→đan điền, 元神→nguyên thần
- 法宝→pháp bảo, 金丹→kim đan, 元婴→nguyên anh, 渡劫→độ kiếp
- 飞升→phi thăng, 天劫→thiên kiếp, 灵气→linh khí, 神识→thần thức
- 剑气→kiếm khí, 真气→chân khí, 功法→công pháp, 秘境→bí cảnh

HỌ TÊN: phiên âm Hán-Việt (吴→Ngô, 李→Lý, 王→Vương, 张→Trương, 赵→Triệu, 陈→Trần, 林→Lâm)."""


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
        if attempt < max_attempts - 1:
            prompt += f"\n\n[LỖI: cần đúng {len(cues)} block, nhận được {len(translated)}. KHÔNG gộp/bỏ block nào.]"
            time.sleep(3)

    raise RuntimeError(f"Dịch chunk thất bại sau {max_attempts} lần thử (expected {len(cues)} blocks)")


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

async def tts_segment(text: str, voice: str, out_path: Path,
                      beeknoee_key: str | None = None,
                      beeknoee_tts_model: str | None = None,
                      beeknoee_tts_voice: str | None = None):
    """Tạo file TTS cho 1 đoạn text, retry 3 lần nếu thất bại.
    Nếu có beeknoee_key + beeknoee_tts_model → dùng Beeknoee TTS, không thì Edge TTS."""
    clean = re.sub(r"[^\w\sÀ-ɏḀ-ỿ.,!?;:()\-–\"']", " ", text).strip()
    if not clean:
        clean = "."

    if beeknoee_key and beeknoee_tts_model:
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
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ])
    return float(r.stdout.strip())


def stretch_audio(src: Path, dst: Path, ratio: float):
    """Kéo/nén audio theo ratio dùng atempo. ratio > 1 = nhanh hơn."""
    filters = ",".join(atempo_chain(ratio))
    run([
        "ffmpeg", "-y", "-i", str(src),
        "-filter:a", filters,
        str(dst),
    ])


def make_silent(duration: float, dst: Path):
    """Tạo file âm thanh im lặng duration giây."""
    run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono",
        "-t", f"{duration:.3f}",
        "-acodec", "libmp3lame", "-b:a", "48k",
        str(dst),
    ])


async def build_tts_track(cues: list[dict], work_dir: Path, video_duration: float,
                          beeknoee_key: str | None = None,
                          beeknoee_tts_model: str | None = None,
                          beeknoee_tts_voice: str | None = None) -> Path:
    """
    Build track TTS đồng bộ:
    - Mỗi cue: TTS → atempo để khớp đúng window [start_sec, end_sec]
    - Tốc độ mặc định tăng 30%, nếu vẫn dài hơn window → tăng thêm
    - Ghép tất cả cue + khoảng lặng giữa chúng thành 1 file mp3
    """
    print(f"  → TTS {len(cues)} đoạn (Edge TTS {TTS_VOICE})...")
    seg_dir = work_dir / "tts_segs"
    seg_dir.mkdir(exist_ok=True)

    files_concat: list[Path] = []   # các file theo thứ tự thời gian
    cursor = 0.0  # vị trí hiện tại trên timeline

    for cue in cues:
        start = cue["start_sec"]
        end   = cue["end_sec"]
        window = end - start        # thời gian dành cho cue này (giây)
        if window <= 0:
            continue

        # Khoảng lặng trước cue này
        gap = start - cursor
        if gap > 0.05:
            silent_path = seg_dir / f"silent_{cue['idx']:04d}.mp3"
            make_silent(gap, silent_path)
            files_concat.append(silent_path)

        text = cue["text"].strip()
        if not text:
            # Cue trống → im lặng bằng window
            silent_path = seg_dir / f"empty_{cue['idx']:04d}.mp3"
            make_silent(window, silent_path)
            files_concat.append(silent_path)
            cursor = end
            continue

        # Tạo TTS gốc
        raw_path = seg_dir / f"raw_{cue['idx']:04d}.mp3"
        await tts_segment(text, TTS_VOICE, raw_path,
                          beeknoee_key=beeknoee_key,
                          beeknoee_tts_model=beeknoee_tts_model,
                          beeknoee_tts_voice=beeknoee_tts_voice)

        raw_dur = get_audio_duration(raw_path)

        if raw_dur <= window:
            # TTS ngắn hơn window → kéo dãn cho vừa khít, tối đa chậm hơn 40%
            ratio = max(raw_dur / window, 0.6)
        else:
            # TTS dài hơn window → tăng tốc vừa đủ để khớp, cap 3x
            ratio = min(raw_dur / window, 3.0)

        stretched_path = seg_dir / f"seg_{cue['idx']:04d}.mp3"
        if abs(ratio - 1.0) < 0.01:
            raw_path.rename(stretched_path)
        else:
            stretch_audio(raw_path, stretched_path, ratio)
            try:
                raw_path.unlink()
            except FileNotFoundError:
                pass

        actual_dur = get_audio_duration(stretched_path)

        # Pad silence chỉ nếu vẫn còn thừa sau khi stretch (do cap 0.6)
        leftover = window - actual_dur
        if leftover > 0.05:
            pad_path = seg_dir / f"pad_{cue['idx']:04d}.mp3"
            make_silent(leftover, pad_path)
            files_concat.append(stretched_path)
            files_concat.append(pad_path)
        else:
            files_concat.append(stretched_path)

        cursor = end

    # Im lặng phần cuối video nếu cần
    tail = video_duration - cursor
    if tail > 0.05:
        tail_path = seg_dir / "tail_silence.mp3"
        make_silent(tail, tail_path)
        files_concat.append(tail_path)

    if not files_concat:
        raise RuntimeError("Không tạo được segment TTS nào.")

    # Ghép tất cả thành 1 file dùng concat demuxer
    concat_list = work_dir / "concat_list.txt"
    concat_list.write_text(
        "\n".join(f"file '{f.resolve()}'" for f in files_concat), encoding="utf-8"
    )
    tts_track = work_dir / "tts_track.mp3"
    run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-acodec", "libmp3lame", "-b:a", "128k",
        str(tts_track),
    ])
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
        "ffmpeg", "-y", "-i", str(video_path),
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
    bg_track: Path,
    srt_path: Path,
    output_path: Path,
):
    print("  → Render video (burn sub + mix TTS + nhạc nền)...")

    srt_escaped = str(srt_path).replace("\\", "/").replace("'", "\\'")

    sub_style = (
        "FontName=Arial Unicode MS,FontSize=22,Bold=1,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
        "BackColour=&H80000000,BorderStyle=4,Outline=2,Shadow=0,"
        "Alignment=2,MarginV=10"
    )

    draw_box = "drawbox=x=0:y=ih-ih*0.15:w=iw:h=ih*0.15:color=black@0.92:t=fill"
    vf = f"{draw_box},subtitles='{srt_escaped}':force_style='{sub_style}'"

    # Mix: background * BG_VOLUME + TTS
    # Input 0 = video, input 1 = background, input 2 = tts
    amix_filter = (
        f"[1:a]volume={BG_VOLUME}[bg];"
        f"[bg][2:a]amix=inputs=2:duration=first:dropout_transition=0[aout]"
    )

    run([
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(bg_track),
        "-i", str(tts_track),
        "-filter_complex", amix_filter,
        "-map", "0:v:0",
        "-map", "[aout]",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(output_path),
    ], timeout=1800)


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
