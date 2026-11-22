"""Smoke test buoc "Tao video": burn phu de tieng Viet + tron TTS bang ffmpeg di kem app.

Chay tren may build Windows (bundled python + ffmpeg):
    python tools/smoke_render.py <thu_muc_tam>
Thoat code != 0 neu render loi hoac file ra rong / khong co video+audio.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

work = Path(sys.argv[1] if len(sys.argv) > 1 else "smoke_render_tmp").resolve()
work.mkdir(parents=True, exist_ok=True)

import translate_video as tv  # noqa: E402  (PYTHONPATH tro toi thu muc resources/python)

ffmpeg = tv.FFMPEG_BIN
ffprobe = os.environ.get("FFPROBE_BIN") or "ffprobe"
print("FFMPEG_BIN =", ffmpeg)

video = work / "in.mp4"
tts = work / "tts_track.mp3"
srt = work / "captions_vi.srt"
out = work / "video_vi.mp4"

subprocess.run([ffmpeg, "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
                "-t", "4", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", str(video)], check=True)
subprocess.run([ffmpeg, "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=44100",
                "-t", "3", "-c:a", "libmp3lame", str(tts)], check=True)
srt.write_text(
    "1\n00:00:00,000 --> 00:00:02,000\nXin chào mọi người, đây là phụ đề thử\n\n"
    "2\n00:00:02,000 --> 00:00:04,000\nKiểm tra dấu: ả ã ạ ố ừ ữ đ\n",
    encoding="utf-8",
)

tv.render_video(video, tts, srt, out, original_audio=video, original_volume=0.25)

if not out.exists() or out.stat().st_size < 10_000:
    print("Render khong tao ra file hop le:", out, out.stat().st_size if out.exists() else "missing")
    sys.exit(1)

probe = subprocess.run([ffprobe, "-v", "error", "-show_entries", "stream=codec_type",
                        "-of", "json", str(out)], capture_output=True, text=True, check=True)
types = sorted(s["codec_type"] for s in json.loads(probe.stdout).get("streams", []))
print("Output streams:", types, "size:", out.stat().st_size)
if "video" not in types or "audio" not in types:
    print("File ra thieu video hoac audio")
    sys.exit(1)
print("SMOKE RENDER OK")
