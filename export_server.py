"""Server ghép & xuất video cho dự án threads/ (mục 5.17 CONTEXT.md).

Nhận kịch bản (steps[] đã tính _start/_end từ index.html) + đường dẫn video
nền/nhạc nền (đã upload sẵn qua media_server.py), dùng Playwright chụp từng
"quãng ổn định" của khung stack Threads thành ảnh PNG nền trong suốt, rồi
dùng FFmpeg overlay các ảnh đó + audio (TTS/nhạc nền/hiệu ứng) lên video nền
theo đúng mốc thời gian, xuất ra 1 file .mp4.

Chạy: python3 threads/export_server.py (cổng 8011).
"""
import asyncio
import json
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from playwright.async_api import async_playwright
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
EXPORT_RENDER_URL_DIR = BASE_DIR  # export_render.html nằm cùng thư mục
EXPORTS_DIR = BASE_DIR / "exports"
EXPORTS_DIR.mkdir(exist_ok=True)
TTS_AUDIO_DIR = BASE_DIR / "tts_audio"

FFMPEG_BIN = "ffmpeg"
FFPROBE_BIN = "ffprobe"

# export_render.html cần được serve qua HTTP (không phải file://) để fetch
# được ảnh URL trong thẻ (imgUrl) và tránh mixed-content — xem mục 5.9.
# Tự phục vụ static files ngay trong server này (mount bên dưới, đường dẫn
# /static/) — không cần chạy thêm `python3 -m http.server` riêng nữa.
RENDER_BASE_URL = "http://localhost:8011/static"

TARGET_W = 1080
TARGET_H = 1920

app = FastAPI(title="Threads Video Export Service")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR)), name="static")


class ExportRequest(BaseModel):
    steps: list[dict]  # để nguyên dict (không ép qua ExportStep) — cần giữ mọi field để render_shared.js dùng
    bgVideoUrl: str
    bgAudioUrl: str | None = None
    bgAudioVolume: float = 0.5
    blurIdentity: bool = True  # khớp trạng thái checkbox "Làm mờ tên & avatar" ở preview lúc bấm Xuất


jobs: dict[str, dict] = {}  # job_id -> {status, progress, message, downloadUrl}


def run(cmd: list[str], **kwargs):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)


def probe_duration(path: Path) -> float:
    r = subprocess.run(
        [FFPROBE_BIN, "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return float(r.stdout.strip() or "0")


def has_audio_stream(path: Path) -> bool:
    """Kiểm tra file có audio track không — dùng cho meme dạng VIDEO có sẵn
    tiếng (mục 5.27 CONTEXT.md): trước đây build_ffmpeg_cmd() chỉ lấy video
    stream ([{idx}:v]) của input meme động, bỏ qua hoàn toàn audio track của
    chính file đó — người dùng dùng video mp4 làm meme (thay vì gif+audio
    rời) thì mất tiếng khi xuất. Gif không có audio nên luôn trả False mà
    không cần probe (đỡ 1 lệnh subprocess thừa cho trường hợp phổ biến)."""
    if path.suffix.lower() == ".gif":
        return False
    r = subprocess.run(
        [FFPROBE_BIN, "-v", "quiet", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return bool(r.stdout.strip())


def compute_breakpoints(steps: list[dict], total_duration: float) -> list[float]:
    """Mọi mốc _start/_end của step post/comment/reply/image (KHÔNG gồm
    audio — nó không hiện hình, không ảnh hưởng stack) — mỗi đoạn giữa 2 mốc
    liền kề là 1 "quãng ổn định" của computeSceneAt(), chỉ cần render 1 ảnh."""
    points = {0.0, total_duration}
    for s in steps:
        if s.get("type") in ("post", "comment", "reply", "image"):
            points.add(round(s["_start"], 6))
            points.add(round(s["_end"], 6))
    return sorted(points)


async def render_stack_images(job_id: str, steps: list[dict], breakpoints: list[float], job_dir: Path,
                               on_progress, blur_identity: bool = True):
    """Dùng Playwright mở export_render.html 1 lần, chụp ảnh cho từng quãng
    ổn định — trả về list[dict] mỗi phần tử:
      {t0, t1, img_path, dynamic_meme} trong đó dynamic_meme (nếu
    có) = {media_url, x_ratio, y_ratio, w_ratio, h_ratio} để export_server.py
    overlay TRỰC TIẾP file video/gif gốc bằng FFmpeg (không chụp PNG tĩnh —
    xem export_render.html:isDynamicMeme/measureMemeBox, mục 5.17 CONTEXT.md
    'meme video/gif chạy động thật'). Khi renderStackAt() phát hiện meme động,
    nó tự ẩn #meme-overlay trước khi trả kết quả — nên #preview-frame chụp ở
    đây KHÔNG dính meme nữa dù has_meme=true, tránh chụp trùng 2 lớp."""
    segments = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": TARGET_W + 40, "height": TARGET_H + 40})
        blur_param = "1" if blur_identity else "0"
        await page.goto(f"{RENDER_BASE_URL}/export_render.html?w={TARGET_W}&blur={blur_param}")
        await page.wait_for_load_state("networkidle")
        await page.evaluate("setSteps", steps)

        n = len(breakpoints) - 1
        for i in range(n):
            t0, t1 = breakpoints[i], breakpoints[i + 1]
            if t1 - t0 < 1e-6:
                continue
            mid = (t0 + t1) / 2
            result = await page.evaluate("renderStackAt", mid)
            stack_ids = result.get("stackIds", [])
            has_meme = result.get("hasMeme", False)
            is_dynamic = result.get("dynamicMeme", False)
            meme_box = result.get("memeBox")
            meme_media_url = result.get("memeMediaUrl")

            dynamic_meme = None
            if has_meme and is_dynamic and meme_box:
                dynamic_meme = {
                    "media_url": meme_media_url,
                    "x_ratio": meme_box["xRatio"], "y_ratio": meme_box["yRatio"],
                    "w_ratio": meme_box["wRatio"], "h_ratio": meme_box["hRatio"],
                }

            # ảnh tĩnh cần chụp: có card (stack_ids) HOẶC có meme TĨNH đứng
            # riêng (has_meme mà không phải dynamic — dynamic đã tự ẩn ở JS)
            needs_static_shot = bool(stack_ids) or (has_meme and not is_dynamic)
            img_path = None
            if needs_static_shot:
                img_path = job_dir / f"stack_{i:04d}.png"
                await page.locator("#preview-frame").screenshot(path=str(img_path), omit_background=True)

            segments.append({
                "t0": t0, "t1": t1, "img_path": img_path, "dynamic_meme": dynamic_meme,
            })
            on_progress(i + 1, n)
        await browser.close()
    return segments


def build_ffmpeg_cmd(job_id: str, bg_video_path: Path, bg_audio_path: Path | None, bg_audio_volume: float,
                      segments: list, total_duration: float, out_path: Path, audio_tracks: list[tuple[str, float, float]]):
    """audio_tracks: list of (file_path, start_offset_seconds, volume) — TTS + hiệu ứng, mỗi cái adelay theo start.
    segments: list[dict] từ render_stack_images() — mỗi phần tử {t0, t1, img_path, dynamic_meme}."""
    inputs = []
    filter_parts = []

    # input 0: video nền, loop nếu ngắn hơn tổng thời lượng
    inputs += ["-stream_loop", "-1", "-i", str(bg_video_path)]
    filter_parts.append(
        f"[0:v]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_W}:{TARGET_H},setsar=1,trim=duration={total_duration},setpts=PTS-STARTPTS[bg]"
    )

    last_label = "bg"
    idx = 1
    for seg in segments:
        t0, t1, img_path = seg["t0"], seg["t1"], seg["img_path"]
        if not img_path:
            continue
        inputs += ["-loop", "1", "-t", f"{t1 - t0:.3f}", "-i", str(img_path)]
        new_label = f"v{idx}"
        filter_parts.append(
            f"[{last_label}][{idx}:v]overlay=0:0:enable='between(t,{t0:.3f},{t1:.3f})'[{new_label}]"
        )
        last_label = new_label
        idx += 1

    # Meme video/gif ĐỘNG: overlay file gốc (không phải PNG tĩnh) tại đúng vị
    # trí/kích thước đo được ở export_render.html (measureMemeBox — tỉ lệ so
    # với #preview-frame, quy đổi ra pixel TARGET_W/TARGET_H ở đây). Loop input
    # bằng -stream_loop -1 vì file gốc có thể ngắn hơn quãng [t0,t1) cần hiện
    # (giống hệt cách xử lý video nền) — riêng ảnh GIF ffmpeg cần -ignore_loop 0
    # để nhận đúng số vòng lặp thay vì chỉ phát 1 lần.
    for seg in segments:
        dm = seg.get("dynamic_meme")
        if not dm or not dm.get("_local_path"):
            continue
        t0, t1 = seg["t0"], seg["t1"]
        dur = t1 - t0
        x_px = round(dm["x_ratio"] * TARGET_W)
        y_px = round(dm["y_ratio"] * TARGET_H)
        w_px = max(2, round(dm["w_ratio"] * TARGET_W))
        h_px = max(2, round(dm["h_ratio"] * TARGET_H))
        # kích thước chẵn — libx264/scale filter yêu cầu width/height chẵn
        w_px += w_px % 2
        h_px += h_px % 2
        is_gif = dm["_local_path"].lower().endswith(".gif")
        loop_flags = ["-ignore_loop", "0"] if is_gif else ["-stream_loop", "-1"]
        # -itsoffset {t0} — BẮT BUỘC, không chỉ là tối ưu. Không có nó, input
        # gif/video bắt đầu đọc PTS=0 ngay từ đầu lệnh ffmpeg, nhưng overlay
        # 'enable=between(t,t0,t1)' chỉ ẨN/HIỆN kết quả — không hề dịch PTS
        # của input overlay. Track chính chạy tới t=t0 thì input gif (chỉ có
        # đúng '-t {dur}' giây dữ liệu tính từ PTS=0) đã hết sạch từ lâu,
        # FFmpeg giữ nguyên frame CUỐI CÙNG available → overlay đứng hình y
        # hệt 1 ảnh tĩnh (bug thật đã gặp, xem qua nhiều lớp test: so khung
        # hình bằng mắt phát hiện MD5 khác nhau chỉ do nén H.264, không phải
        # chuyển động thật — ffprobe/duration không lộ được bug này, phải
        # crop đúng vùng meme rồi xếp lưới nhiều khung mới thấy). itsoffset
        # dịch input để nó "bắt đầu" đúng lúc t0 của track chính, khớp pha.
        inputs += loop_flags + ["-itsoffset", f"{t0:.3f}", "-t", f"{dur:.3f}", "-i", dm["_local_path"]]
        new_label = f"v{idx}"
        filter_parts.append(
            f"[{idx}:v]scale={w_px}:{h_px}[m{idx}];"
            f"[{last_label}][m{idx}]overlay={x_px}:{y_px}:enable='between(t,{t0:.3f},{t1:.3f})'[{new_label}]"
        )
        last_label = new_label
        idx += 1

    # audio: TTS/hiệu ứng qua adelay, nhạc nền qua stream_loop riêng, amix gộp
    audio_input_labels = []
    for (afile, astart, avol) in audio_tracks:
        inputs += ["-i", afile]
        lbl = f"a{idx}"
        ms = int(round(astart * 1000))
        filter_parts.append(f"[{idx}:a]adelay={ms}|{ms},volume={avol}[{lbl}]")
        audio_input_labels.append(lbl)
        idx += 1

    if bg_audio_path:
        bg_audio_idx = idx
        inputs += ["-stream_loop", "-1", "-i", str(bg_audio_path)]
        filter_parts.append(f"[{bg_audio_idx}:a]atrim=duration={total_duration},volume={bg_audio_volume}[abg]")
        audio_input_labels.append("abg")
        idx += 1

    audio_out = "aout"
    if audio_input_labels:
        joined = "".join(f"[{l}]" for l in audio_input_labels)
        # normalize=0 — BẮT BUỘC, không phải tối ưu (mục 5.34 CONTEXT.md).
        # amix mặc định normalize=1 tự chia biên độ output theo SỐ INPUT khai
        # báo (inputs=N), không phải theo số track thực sự phát tiếng cùng lúc
        # — kịch bản nhiều step TTS/audio (mỗi step = 1 input riêng dù chỉ 1-2
        # cái "sống" cùng lúc nhờ adelay đặt đúng lúc, hiếm khi chồng nhau thật)
        # bị chia âm lượng rất mạnh dù thực tế không cần, gây tiếng nhỏ hẳn so
        # với preview (người dùng phải bật max loa mới nghe). Tắt normalize giữ
        # nguyên biên độ gốc từng track; alimiter chặn clip an toàn cho trường
        # hợp hiếm gặp nhiều track cùng lúc cộng dồn vượt full-scale.
        filter_parts.append(
            f"{joined}amix=inputs={len(audio_input_labels)}:duration=longest:"
            f"dropout_transition=0:normalize=0,alimiter=limit=0.97[{audio_out}]"
        )
    else:
        filter_parts.append(f"anullsrc=channel_layout=stereo:sample_rate=44100[{audio_out}]")

    filter_complex = ";".join(filter_parts)
    # KHÔNG dùng -shortest: video track ([last_label]) luôn đủ total_duration
    # (bg đã trim=duration=total_duration), nhưng audio track (amix) có thể
    # NGẮN HƠN total_duration nếu track audio cuối cùng (theo adelay) kết
    # thúc sớm — vd không có nhạc nền, audio hiệu ứng cuối kịch bản chỉ dài
    # 1-2s. -shortest khi đó cắt CẢ VIDEO theo audio ngắn hơn, làm mất hẳn
    # đoạn cuối kịch bản (bug thật đã gặp: video xuất bị cụt trước khi tới
    # đoạn meme cuối). -t {total_duration} ở dưới đã đủ để giới hạn đúng độ
    # dài — audio ngắn hơn tự động im lặng ở phần dư, không cần -shortest.
    cmd = [FFMPEG_BIN, "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", f"[{last_label}]",
        "-map", f"[{audio_out}]",
        "-t", f"{total_duration:.3f}",
        "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-progress", "pipe:1",
        str(out_path),
    ]
    return cmd


def update_job(job_id, **kwargs):
    jobs[job_id].update(kwargs)


async def run_export_job(job_id: str, req: ExportRequest):
    job_dir = EXPORTS_DIR / job_id
    job_dir.mkdir(exist_ok=True)
    try:
        steps = req.steps
        # Audio SONG SONG (parallel=true) không chiếm chỗ track chính — bám
        # theo step khác, không ảnh hưởng tổng thời lượng (giống mục 5.13
        # bên index.html: isParallelAudio()). Audio TUẦN TỰ (mặc định) vẫn
        # tính vào track chính như post/comment — bug đã gặp: trước đây loại
        # trừ MỌI step audio khỏi phép tính, khiến audio tuần tự đứng cuối
        # kịch bản bị cắt cụt mất, tổng thời lượng tính thiếu.
        total_duration = max(
            [s["_end"] + (s.get("gap") or 0) for s in steps
             if not (s.get("type") == "audio" and s.get("parallel"))] or [0]
        )
        if total_duration <= 0:
            raise RuntimeError("Kịch bản rỗng hoặc không có step nào có thời lượng")

        update_job(job_id, status="rendering_cards", progress=0, message="Đang chụp ảnh các thẻ Threads...")
        breakpoints = compute_breakpoints(steps, total_duration)

        def on_progress(done, total):
            pct = int(done / max(total, 1) * 60)  # chụp ảnh chiếm 0-60% progress
            update_job(job_id, progress=pct, message=f"Đang chụp thẻ {done}/{total}...")

        segments = await render_stack_images(job_id, steps, breakpoints, job_dir, on_progress, req.blurIdentity)

        # thu thập audio track: TTS mỗi step (đọc thẳng file local threads/tts_audio/),
        # audio hiệu ứng mỗi step audio
        audio_tracks = []
        for s in steps:
            if s.get("type") in ("post", "comment", "reply", "image") and s.get("audioUrl"):
                # Nhiều đoạn giọng đọc nối tiếp trong CÙNG 1 step (mục "nhiều
                # đoạn TTS") — đoạn #1 giữ nguyên field cũ (audioUrl/ttsDuration),
                # các đoạn thêm nằm trong extraTtsSegments[] theo đúng thứ tự
                # phát. Cộng dồn offset qua từng đoạn giống hệt nguyên lý cũ
                # "khối 2 của reply bám sau khối 1" (offset = _start + tổng
                # duration các đoạn trước nó), giờ tổng quát hoá cho N đoạn
                # thay vì hard-code đúng 2 khối.
                clips = [{"audioUrl": s["audioUrl"], "duration": s.get("ttsDuration") or 0}]
                clips.extend(s.get("extraTtsSegments") or [])
                acc = 0.0
                for clip in clips:
                    url = clip.get("audioUrl")
                    if url:
                        fname = url.rstrip("/").split("/")[-1]
                        fpath = TTS_AUDIO_DIR / fname
                        if fpath.exists():
                            audio_tracks.append((str(fpath), s["_start"] + acc, 1.0))
                    acc += clip.get("duration") or 0
            elif s.get("type") == "audio" and s.get("mediaUrl"):
                local_path = _resolve_local_media(s["mediaUrl"])
                if local_path:
                    audio_tracks.append((str(local_path), s["_start"], 1.0))
            elif s.get("type") == "image" and s.get("mediaUrl") and s.get("mediaIsVideo"):
                # Ảnh dạng VIDEO (không phải gif) có thể tự mang sẵn audio
                # track (vd clip có tiếng cười/nhạc nền riêng) — bug đã gặp:
                # build_ffmpeg_cmd() chỉ overlay VIDEO stream của input động
                # ([{idx}:v]), hoàn toàn bỏ qua audio track của chính file đó,
                # khiến người dùng dùng thẳng 1 mp4 có sẵn tiếng làm ảnh động
                # thì mất tiếng khi xuất. Dùng đúng s["_start"] của STEP GỐC
                # (không phải t0 của segment — 1 step có thể bị chia thành
                # nhiều segment nếu chồng lấp thời gian với step khác) để
                # không lặp audio nhiều lần.
                local_path = _resolve_local_media(s["mediaUrl"])
                if local_path and has_audio_stream(local_path):
                    audio_tracks.append((str(local_path), s["_start"], 1.0))

        # overlay động (step 'image' dạng video/gif): resolve mediaUrl -> file
        # thật trên đĩa (giống mọi media khác qua media_server.py) — nếu không
        # resolve được (file bị xoá, server media không chạy...), bỏ qua đoạn
        # đó thay vì làm hỏng cả job.
        for seg in segments:
            dm = seg.get("dynamic_meme")
            if dm:
                local_path = _resolve_local_media(dm["media_url"])
                dm["_local_path"] = str(local_path) if local_path else None

        update_job(job_id, status="downloading_assets", progress=62, message="Đang tải video/nhạc nền...")
        bg_video_path = _resolve_local_media(req.bgVideoUrl)
        if not bg_video_path or not bg_video_path.exists():
            raise RuntimeError(f"Không tìm thấy file video nền: {req.bgVideoUrl}")
        bg_audio_path = _resolve_local_media(req.bgAudioUrl) if req.bgAudioUrl else None

        update_job(job_id, status="encoding", progress=65, message="Đang ghép FFmpeg...")
        out_path = job_dir / "output.mp4"
        cmd = build_ffmpeg_cmd(job_id, bg_video_path, bg_audio_path, req.bgAudioVolume, segments, total_duration, out_path, audio_tracks)

        cmd_log_path = job_dir / "ffmpeg_cmd.txt"
        cmd_log_path.write_text(" ".join(cmd))

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for line in proc.stdout:
            m = re.search(r"out_time_ms=(\d+)", line)
            if m:
                done_s = int(m.group(1)) / 1_000_000
                pct = 65 + int(min(done_s / total_duration, 1.0) * 35)
                update_job(job_id, progress=pct, message=f"Đang encode... {done_s:.1f}s/{total_duration:.1f}s")
        stderr_out = proc.stderr.read()
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg lỗi (mã {proc.returncode}): {stderr_out[-2000:]}")

        update_job(job_id, status="done", progress=100, message="Hoàn tất!", downloadUrl=f"/export-download/{job_id}")
    except Exception as e:
        update_job(job_id, status="error", message=str(e))


# Khớp media_server.py:LIBRARY_DIRS — category URL "meme" không trùng tên
# thư mục vật lý "gif" (bug thật đã gặp: _resolve_local_media từng đoán
# đường dẫn = BASE_DIR/<category>/<file>, ra threads/meme/ không tồn tại,
# trong khi file thật nằm threads/gif/ — khiến resolve luôn thất bại âm
# thầm, chỉ lộ ra khi thêm meme động vì audio hiệu ứng dùng category "audio"
# trùng tên thư mục nên tình cờ không lộ bug).
LIBRARY_DIR_MAP = {
    "audio": BASE_DIR / "audio",
    "meme": BASE_DIR / "gif",
    "background": BASE_DIR / "background",
    "bgaudio": BASE_DIR / "audiobackground",
}


MEMEREADY_DIR = BASE_DIR / "memeready"


def _resolve_local_media(url: str | None) -> Path | None:
    """Chuyển URL http://localhost:PORT/media/xxx, /library-file/..., hoặc
    /memeready-file/{id}/{filename} thành đường dẫn file thật trên đĩa
    (media_server.py serve từ threads/media/, threads/audio/, threads/gif/,
    threads/memeready/{id}/) — tránh phải re-fetch qua HTTP khi file đã nằm
    sẵn trên cùng máy chạy export_server.py.

    Bug thật đã gặp (mục 5.27 CONTEXT.md): thiếu hẳn nhánh /memeready-file/
    — khi người dùng dùng 1 bộ "meme sẵn" (mục 5.19, thư mục riêng
    threads/memeready/<id>/ do media_server.py:save_memeready() tạo), URL
    trả về không khớp 2 dạng /media/ hoặc /library-file/ nào cả, hàm này
    luôn trả None. Hậu quả kép: (1) audio track của meme video mất tiếng
    (mục 5.27), (2) NGHIÊM TRỌNG HƠN — dòng resolve dynamic_meme._local_path
    cũng gặp cùng lỗi, khiến overlay VIDEO của meme sẵn dạng động bị bỏ qua
    hoàn toàn (build_ffmpeg_cmd() continue khi thiếu _local_path) — nghĩa
    là hình ảnh của meme sẵn động lẽ ra CŨNG không hiện trong video xuất,
    không chỉ riêng thiếu tiếng."""
    if not url:
        return None
    if "/media/" in url:
        fname = url.split("/media/")[-1]
        p = BASE_DIR / "media" / fname
        if p.exists():
            return p
    if "/library-file/" in url:
        rest = url.split("/library-file/")[-1]  # "meme/ten-file.gif" hoặc "audio/ten-file.mp3"
        category, _, fname = rest.partition("/")
        lib_dir = LIBRARY_DIR_MAP.get(category)
        if lib_dir:
            p = lib_dir / fname
            if p.exists():
                return p
    if "/memeready-file/" in url:
        rest = url.split("/memeready-file/")[-1]  # "{id}/{filename}"
        item_id, _, fname = rest.partition("/")
        p = MEMEREADY_DIR / item_id / fname
        if p.exists() and MEMEREADY_DIR in p.resolve().parents:
            return p
    return None


@app.post("/export")
async def export_video(req: ExportRequest):
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {"status": "queued", "progress": 0, "message": "Đang chờ...", "downloadUrl": None}
    asyncio.create_task(run_export_job(job_id, req))
    return {"job_id": job_id}


@app.get("/export-status/{job_id}")
def export_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Không tìm thấy job")
    return job


@app.get("/export-download/{job_id}")
def export_download(job_id: str):
    out_path = EXPORTS_DIR / job_id / "output.mp4"
    if not out_path.exists():
        raise HTTPException(404, "File chưa sẵn sàng hoặc job không tồn tại")
    return FileResponse(out_path, media_type="video/mp4", filename=f"threads-video-{job_id}.mp4")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8011)
