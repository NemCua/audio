"""Server upload media riêng cho dự án threads/ (meme video/gif/ảnh, âm thanh
hiệu ứng) — tách biệt với tts_server.py (khác mục đích: cái này chỉ lưu file
người dùng kéo-thả, không gọi TTS/CapCut gì cả).

Lý do có server này: người dùng chạy threads/index.html qua Live Server (hay
bất kỳ http server nào khác file://) — lúc đó đường dẫn file:// KHÔNG load
được do mixed-content (xem threads/CONTEXT.md mục 5.9). Giải pháp: kéo-thả
file vào app, JS tự upload lên đây, server lưu vào threads/media/ và trả về
URL http:// dùng được từ mọi cách mở app (Live Server, python -m http.server,
hay cả file:// trực tiếp).

Chạy: python3 threads/media_server.py (cổng 8010 — 8004/8005/8006 các server
gốc repo đã dùng, 8007 là tts_server.py, 8008/8009 dự phòng).
"""
import json
import re
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask

FFPROBE_BIN = "ffprobe"
FFMPEG_BIN = "ffmpeg"
BASE_DIR = Path(__file__).resolve().parent
STICKER_DIR = BASE_DIR / "stickers"


def _probe_duration(path: Path) -> float | None:
    """Đo độ dài thật (giây) của gif/video/audio bằng ffprobe (đã dùng chung
    export_server.py) — trình duyệt KHÔNG có API đọc trực tiếp duration của
    file gif tĩnh (khác video có sẵn video.duration), nên phải đo phía
    server. Trả None nếu file không phải media có thời lượng (ảnh tĩnh
    jpg/png) hoặc ffprobe lỗi — không raise, để không chặn upload/thư viện
    chỉ vì đo duration thất bại."""
    try:
        r = subprocess.run(
            [FFPROBE_BIN, "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        val = r.stdout.strip()
        return round(float(val), 2) if val else None
    except Exception:
        return None

MEDIA_DIR = Path(__file__).resolve().parent / "media"
MEDIA_DIR.mkdir(exist_ok=True)
VIDEO_TOOL_TMP_DIR = Path(__file__).resolve().parent / ".video_tool_tmp"
VIDEO_TOOL_TMP_DIR.mkdir(exist_ok=True)

# File mp3 TTS sinh ra bởi tts_server.py (cổng 8007, threads/tts_audio/) — nằm
# CÙNG máy/CÙNG thư mục threads/ với server này nên đọc thẳng file local được,
# không cần gọi HTTP sang 8007. Dùng khi đóng gói snapshot step vào memeready/
# (mục "Meme sẵn v2" — step post/comment/reply/image có audioUrl trỏ dạng
# http://localhost:8007/audio/xxx.mp3 cần copy file thật vào bộ, vì tts_audio/
# là thư mục cache có thể bị dọn bất kỳ lúc nào — mục 5.31 CONTEXT.md).
TTS_AUDIO_DIR = Path(__file__).resolve().parent / "tts_audio"

# "Meme sẵn" (mục 5.19 CONTEXT.md) — cụm gif/video + âm thanh đi kèm, đóng
# gói lại 1 lần từ modal soạn step meme rồi tái dùng nhiều lần sau (đỡ phải
# tự chọn lại audio mỗi lần dùng cùng 1 meme). Mỗi bộ = 1 thư mục con riêng
# threads/memeready/<id>/ chứa gif/video gốc + audio gốc (copy, không phải
# symlink — để không phụ thuộc file nguồn media/library có còn tồn tại hay
# không) + meta.json (label hiển thị, tên file gốc, có/không có audio).
MEMEREADY_DIR = Path(__file__).resolve().parent / "memeready"
MEMEREADY_DIR.mkdir(exist_ok=True)

# Thư mục "cache dùng 1 lần" — làm nặng máy theo thời gian nhưng KHÔNG phải
# thư viện tái dùng: media/ (file upload tạm qua dropzone/video-process),
# exports/ (video .mp4 đã ghép xong, mỗi lần xuất 1 thư mục con — mục 6 TODO
# CONTEXT.md), tts_audio/ (mp3 TTS sinh ra mỗi lần soạn step). KHÔNG gồm
# memeready/ (đóng gói tái dùng), audio/ + gif/ (thư viện gốc), avatarthreads/
# (ảnh avatar giả cố định) — những thứ này người dùng chủ động giữ lại để
# dùng cho dự án sau, xoá nhầm là mất thật.
CACHE_DIRS = {
    "media": MEDIA_DIR,
    "exports": BASE_DIR / "exports",
    "tts_audio": BASE_DIR / "tts_audio",
}

# Thư viện file có sẵn (người dùng tự copy vào, không qua upload) — mỗi
# category trỏ 1 thư mục cố định trong threads/. "audio" = âm thanh hiệu
# ứng sẵn có (threads/audio/, vd vine-boom.mp3, bonk.mp3...); "meme" = ảnh
# gif/video meme sẵn có (threads/gif/, vd john-jonah-jameson-lol.gif);
# "image" = ảnh asset đi kèm app (threads/image/) để đóng gói cùng bản tải về.
LIBRARY_DIRS = {
    "audio": Path(__file__).resolve().parent / "audio",
    "meme": Path(__file__).resolve().parent / "gif",
    "image": Path(__file__).resolve().parent / "image",
    # "background"/"bgaudio" — video nền (gameplay)/nhạc nền có sẵn, chọn
    # nhanh trong sidebar mục 1/2 thay vì phải chọn file từ máy mỗi lần.
    "background": Path(__file__).resolve().parent / "background",
    "bgaudio": Path(__file__).resolve().parent / "audiobackground",
}

# Mô tả (label + description) cho TỪNG FILE trong thư viện — khác memeready
# (mục 5.19/5.24, mỗi bộ là 1 thư mục riêng có metadata gắn liền file), đây
# là file THƯ VIỆN GỐC dùng chung nhiều nơi, không muốn đổi cấu trúc thư mục
# audio/ hay gif/ hiện có (người dùng tự thả file vào bằng tay, không qua
# app) — nên lưu metadata TÁCH RIÊNG trong 1 file JSON PHẲNG, map tên file
# -> {label, description}, đặt cạnh mỗi thư mục thư viện. File audio/gif nào
# chưa có entry ở đây vẫn hiện bình thường trong thư viện, chỉ thiếu mô tả
# (không bắt buộc). Mục đích mô tả GIỐNG HỆT memeready (mục 5.24): không
# hiển thị gì đặc biệt cho người dùng ngoài tooltip — để COPY-PASTE gửi cho
# AI ngoài app khi nhờ chọn âm thanh/meme phù hợp ngữ cảnh.
LIBRARY_META_FILES = {
    "audio": Path(__file__).resolve().parent / "audio" / "_meta.json",
    "meme": Path(__file__).resolve().parent / "gif" / "_meta.json",
    "image": Path(__file__).resolve().parent / "image" / "_meta.json",
    "background": Path(__file__).resolve().parent / "background" / "_meta.json",
    "bgaudio": Path(__file__).resolve().parent / "audiobackground" / "_meta.json",
}


def _load_library_meta(category: str) -> dict:
    meta_path = LIBRARY_META_FILES.get(category)
    if not meta_path or not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text())
    except Exception:
        return {}


def _save_library_meta(category: str, meta: dict):
    meta_path = LIBRARY_META_FILES.get(category)
    if not meta_path:
        raise HTTPException(404, f"Không có thư viện '{category}'")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

app = FastAPI(title="Threads Media Upload Service")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
# Serve các trang HTML phụ trợ (video_tool.html — trang cắt/crop video, mục
# 5.26 CONTEXT.md) ngay trong chính server này, mở tại
# http://localhost:8010/static/video_tool.html — copy đúng pattern
# export_server.py đã dùng cho export_render.html (không cần bật thêm
# http server rời nào, bài học từ bug ERR_CONNECTION_REFUSED mục 5.17).
app.mount("/static", StaticFiles(directory=str(BASE_DIR)), name="static")


def _safe_filename(name: str) -> str:
    name = Path(name).name  # bỏ mọi phần thư mục nếu trình duyệt gửi kèm
    stem = Path(name).stem
    suffix = Path(name).suffix
    stem = re.sub(r"[^\w\-]", "_", stem)[:80] or "file"
    return f"{stem}_{uuid.uuid4().hex[:8]}{suffix}"


@app.post("/upload")
async def upload(file: UploadFile):
    if not file.filename:
        raise HTTPException(400, "Thiếu tên file")
    out_name = _safe_filename(file.filename)
    out_path = MEDIA_DIR / out_name
    content = await file.read()
    out_path.write_bytes(content)
    is_video = bool(re.search(r"\.(mp4|mov|webm|m4v)$", out_name, re.IGNORECASE))
    return {
        "url": f"/media/{out_name}",
        "filename": out_name,
        "originalName": file.filename,
        "isVideo": is_video,
        "size": len(content),
        "duration": _probe_duration(out_path),
    }


@app.post("/video-tool-upload")
async def video_tool_upload(file: UploadFile):
    if not file.filename:
        raise HTTPException(400, "Thiếu tên file")
    out_name = _safe_filename(file.filename)
    out_path = VIDEO_TOOL_TMP_DIR / out_name
    content = await file.read()
    out_path.write_bytes(content)
    is_video = bool(re.search(r"\.(mp4|mov|webm|m4v)$", out_name, re.IGNORECASE))
    return {
        "filename": out_name,
        "originalName": file.filename,
        "isVideo": is_video,
        "size": len(content),
        "duration": _probe_duration(out_path),
    }


@app.get("/media/{filename}")
def get_media(filename: str):
    path = MEDIA_DIR / filename
    if not path.exists() or MEDIA_DIR not in path.resolve().parents:
        raise HTTPException(404, "Không tìm thấy file")
    return FileResponse(path)


@app.get("/video-tool-file/{filename}")
def get_video_tool_file(filename: str):
    path = VIDEO_TOOL_TMP_DIR / Path(filename).name
    if not path.exists() or VIDEO_TOOL_TMP_DIR not in path.resolve().parents:
        raise HTTPException(404, "Không tìm thấy file")
    return FileResponse(path, filename=path.name, background=BackgroundTask(path.unlink, missing_ok=True))


@app.get("/library/{category}")
def list_library(category: str):
    lib_dir = LIBRARY_DIRS.get(category)
    if lib_dir is None:
        raise HTTPException(404, f"Không có thư viện '{category}'")
    if not lib_dir.exists():
        return []
    # "_meta.json" (không bắt đầu bằng dấu chấm) là file metadata riêng của
    # chính thư viện này — loại trừ tường minh, không chỉ dựa vào quy ước
    # "bỏ file ẩn" (file ẩn dấu chấm là quy ước KHÁC, không liên quan).
    meta = _load_library_meta(category)
    files = sorted(
        p for p in lib_dir.iterdir()
        if p.is_file() and not p.name.startswith(".") and p.name != "_meta.json"
    )
    return [
        {
            "filename": p.name,
            "url": f"/library-file/{category}/{p.name}",
            "isVideo": bool(re.search(r"\.(mp4|mov|webm|m4v)$", p.name, re.IGNORECASE)),
            "size": p.stat().st_size,
            "duration": _probe_duration(p),
            "label": meta.get(p.name, {}).get("label") or p.stem,
            "description": meta.get(p.name, {}).get("description", ""),
        }
        for p in files
    ]


@app.post("/library/{category}")
async def add_library_file(category: str, file: UploadFile):
    lib_dir = LIBRARY_DIRS.get(category)
    if lib_dir is None:
        raise HTTPException(404, f"Không có thư viện '{category}'")
    if not file.filename:
        raise HTTPException(400, "Thiếu tên file")
    lib_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_filename(file.filename)
    target = lib_dir / safe_name
    content = await file.read()
    target.write_bytes(content)
    return {
        "filename": target.name,
        "url": f"/library-file/{category}/{target.name}",
        "desktopPath": str(target),
        "duration": _probe_duration(target),
    }


@app.get("/library-file/{category}/{filename}")
def get_library_file(category: str, filename: str):
    lib_dir = LIBRARY_DIRS.get(category)
    if lib_dir is None:
        raise HTTPException(404, f"Không có thư viện '{category}'")
    path = lib_dir / filename
    if not path.exists() or lib_dir not in path.resolve().parents:
        raise HTTPException(404, "Không tìm thấy file")
    return FileResponse(path)


@app.get("/stickers")
def list_sticker_packs():
    """Thư viện sticker đóng sẵn cho bản web local (Electron dùng IPC)."""
    if not STICKER_DIR.exists():
        return []
    packs = []
    allowed = re.compile(r"\.(png|jpe?g|webp|gif)$", re.IGNORECASE)
    for directory in sorted(STICKER_DIR.iterdir()):
        if not directory.is_dir():
            continue
        meta = {}
        try:
            meta = json.loads((directory / "_pack.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        stickers = [
            {
                "filename": path.name,
                "url": f"/sticker-file/{directory.name}/{path.name}",
                "desktopPath": str(path),
                "isVideo": False,
            }
            for path in sorted(directory.iterdir())
            if path.is_file() and allowed.search(path.name)
        ]
        packs.append({"id": directory.name, "name": meta.get("name") or directory.name,
                      "userOwned": False, "stickers": stickers})
    return packs


@app.get("/sticker-file/{pack_id}/{filename}")
def get_sticker_file(pack_id: str, filename: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", pack_id) or Path(filename).name != filename:
        raise HTTPException(400, "Đường dẫn sticker không hợp lệ")
    path = STICKER_DIR / pack_id / filename
    if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        raise HTTPException(404, "Không tìm thấy sticker")
    return FileResponse(path)


class LibraryItemUpdateRequest(BaseModel):
    label: str | None = None
    description: str | None = None


@app.patch("/library/{category}/{filename}")
def update_library_item(category: str, filename: str, req: LibraryItemUpdateRequest):
    """Sửa label/mô tả cho 1 file trong thư viện (audio hoặc gif) — CHỈ ghi
    vào _meta.json, không đụng file thật. Cho phép sửa từng phần (field nào
    None thì giữ nguyên giá trị cũ)."""
    lib_dir = LIBRARY_DIRS.get(category)
    if lib_dir is None:
        raise HTTPException(404, f"Không có thư viện '{category}'")
    path = lib_dir / filename
    if not path.exists() or lib_dir not in path.resolve().parents:
        raise HTTPException(404, "Không tìm thấy file")
    meta = _load_library_meta(category)
    entry = meta.get(filename, {})
    if req.label is not None:
        entry["label"] = req.label.strip() or path.stem
    if req.description is not None:
        entry["description"] = req.description.strip()
    meta[filename] = entry
    _save_library_meta(category, meta)
    return {"filename": filename, **entry}


@app.delete("/library/{category}/{filename}")
def delete_library_item(category: str, filename: str):
    """Xoá HẲN file khỏi thư viện (khác memeready — đây là file gốc, xoá là
    xoá thật, không phải bản copy). Dọn luôn entry trong _meta.json nếu có,
    tránh rác tích luỹ theo thời gian."""
    lib_dir = LIBRARY_DIRS.get(category)
    if lib_dir is None:
        raise HTTPException(404, f"Không có thư viện '{category}'")
    path = lib_dir / filename
    if not path.exists() or lib_dir not in path.resolve().parents:
        raise HTTPException(404, "Không tìm thấy file")
    path.unlink()
    meta = _load_library_meta(category)
    if filename in meta:
        del meta[filename]
        _save_library_meta(category, meta)
    return {"ok": True}


@app.get("/cache-size")
def cache_size():
    """Tổng dung lượng (bytes) của 3 thư mục cache dùng 1 lần — hiện cho
    người dùng biết đang chiếm bao nhiêu trước khi bấm xoá."""
    total = 0
    for d in CACHE_DIRS.values():
        if d.exists():
            total += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
    return {"bytes": total}


@app.delete("/cache")
def clear_cache():
    """Dọn sạch nội dung media/, exports/, tts_audio/ (xoá file bên trong,
    giữ lại thư mục rỗng) — không đụng memeready/, audio/, gif/,
    avatarthreads/ (thư viện tái dùng, xem CACHE_DIRS ở trên)."""
    removed = 0
    for d in CACHE_DIRS.values():
        if not d.exists():
            continue
        for child in d.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
            removed += 1
        d.mkdir(exist_ok=True)
    return {"ok": True, "removed": removed}


def _resolve_served_url(url: str) -> Path | None:
    """Chuyển URL do CHÍNH server này (hoặc tts_server.py, cùng máy) trả ra
    ngược lại thành Path thật trên đĩa — dùng khi 'đóng gói' meme sẵn (client
    gửi lên URL đang dùng, server cần đọc lại nội dung file đó để copy sang
    memeready/). Nhận diện 4 kiểu: /media/xxx, /library-file/category/xxx,
    /memeready-file/id/xxx (đóng gói lại 1 bộ đã có — hiếm nhưng hợp lệ), và
    /audio/xxx (mp3 TTS từ tts_server.py, port 8007)."""
    if not url:
        return None
    if "/media/" in url:
        p = MEDIA_DIR / url.split("/media/")[-1]
        return p if p.exists() else None
    if "/library-file/" in url:
        rest = url.split("/library-file/")[-1]
        category, _, fname = rest.partition("/")
        lib_dir = LIBRARY_DIRS.get(category)
        if not lib_dir:
            return None
        p = lib_dir / fname
        return p if p.exists() else None
    if "/memeready-file/" in url:
        rest = url.split("/memeready-file/")[-1]
        item_id, _, fname = rest.partition("/")
        p = MEMEREADY_DIR / item_id / fname
        return p if p.exists() else None
    if "/audio/" in url:
        p = TTS_AUDIO_DIR / url.split("/audio/")[-1]
        return p if p.exists() else None
    return None


class MemereadySnapshotRequest(BaseModel):
    label: str = ""
    # 1 hoặc 2 step object NGUYÊN VẸN (đúng format steps[] trong index.html) —
    # đóng gói TOÀN BỘ 1 step bất kỳ (post/comment/reply/image/audio) hoặc 1
    # cặp track-chính + audio song song đứng cạnh nhau, KHÔNG rút gọn field
    # gì (text/ttsText/uname/avatar/stats/offset/rotation... giữ nguyên) —
    # "Meme sẵn" không còn giới hạn ở media/gif+audio nữa, bất kỳ step nào
    # cũng có thể trở thành 1 bộ tái dùng. Field media (mediaUrl cho
    # image/audio, audioUrl/audio2Url là TTS cho post/comment/reply/image)
    # được server resolve + copy file thật vào memeready/<id>/, rồi ghi đè
    # lại URL trỏ vào đó — mọi field khác giữ nguyên y hệt client gửi lên.
    steps: list[dict]


class MemereadyUpdateRequest(BaseModel):
    label: str | None = None
    # Cho sửa lại duration/text của TỪNG step bên trong snapshot (mediaUrl/
    # audioUrl/audio2Url của mỗi step đã là tên file tương đối, giữ nguyên
    # KHÔNG đổi file — client không gửi field media nào khác ngoài tên đã
    # có). None nghĩa là không đổi steps, giữ nguyên như cũ.
    steps: list[dict] | None = None


def _snapshot_media_field(item_dir: Path, url: str | None, prefix: str) -> tuple[str | None, float | None]:
    """Resolve 1 URL media/audio đang phục vụ (từ media_server.py hoặc
    tts_server.py) thành file thật, copy vào item_dir với tên
    "<prefix>_<index>.<ext>", trả (tên file đã copy, duration đo lại bằng
    ffprobe) — hoặc (None, None) nếu url rỗng/không resolve được (không
    raise, để 1 field media lỗi không chặn snapshot cả step)."""
    if not url:
        return None, None
    src = _resolve_served_url(url)
    if not src:
        return None, None
    out = item_dir / f"{prefix}{src.suffix.lower()}"
    shutil.copyfile(src, out)
    return out.name, _probe_duration(out)


def _snapshot_step(item_dir: Path, step: dict, idx: int) -> dict:
    """Copy mọi file media/audio mà 1 step tham chiếu vào item_dir, trả về
    step đã ghi đè URL trỏ vào bản copy — giữ nguyên MỌI field khác."""
    s = dict(step)
    if s.get("mediaUrl"):
        fname, dur = _snapshot_media_field(item_dir, s["mediaUrl"], f"media{idx}")
        if fname:
            s["mediaUrl"] = fname  # tên file tương đối — GET /memeready ghép lại thành URL đầy đủ
            s["mediaName"] = s.get("mediaName") or fname
            s["mediaPath"] = None
            if s.get("type") in ("image", "audio"):
                s["_mediaDuration"] = dur  # duration thật đo lại — dùng khi LẤY RA, không đo lại lần nữa
    if s.get("audioUrl"):
        fname, dur = _snapshot_media_field(item_dir, s["audioUrl"], f"tts{idx}")
        if fname:
            s["audioUrl"] = fname
            s["_ttsAudioDuration"] = dur
    if s.get("audio2Url"):
        fname, dur = _snapshot_media_field(item_dir, s["audio2Url"], f"tts{idx}b")
        if fname:
            s["audio2Url"] = fname
            s["_tts2AudioDuration"] = dur
    return s


def _resolve_snapshot_step_urls(item_id: str, step: dict) -> dict:
    """Ngược lại _snapshot_step() — ghép tên file tương đối đã lưu trong
    meta.json thành URL /memeready-file/ đầy đủ, dùng khi trả GET /memeready
    (để frontend LẤY RA tạo step mới với URL dùng được ngay)."""
    s = dict(step)
    if s.get("mediaUrl"):
        s["mediaUrl"] = f"/memeready-file/{item_id}/{s['mediaUrl']}"
    if s.get("audioUrl"):
        s["audioUrl"] = f"/memeready-file/{item_id}/{s['audioUrl']}"
    if s.get("audio2Url"):
        s["audio2Url"] = f"/memeready-file/{item_id}/{s['audio2Url']}"
    return s


@app.post("/memeready")
def save_memeready(req: MemereadySnapshotRequest):
    if not req.steps:
        raise HTTPException(400, "Cần ít nhất 1 step để lưu thành meme sẵn")

    item_id = uuid.uuid4().hex[:10]
    item_dir = MEMEREADY_DIR / item_id
    item_dir.mkdir(parents=True)

    snapshotted = [_snapshot_step(item_dir, s, i) for i, s in enumerate(req.steps)]
    default_label = next((s.get("mediaName") or s.get("text") for s in req.steps if s.get("mediaName") or s.get("text")), None) or "Meme sẵn"
    meta = {
        "id": item_id,
        "label": req.label.strip() or default_label[:60],
        "steps": snapshotted,
    }
    (item_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return {**meta, "steps": [_resolve_snapshot_step_urls(item_id, s) for s in snapshotted]}


@app.get("/memeready")
def list_memeready():
    items = []
    for d in sorted(MEMEREADY_DIR.iterdir()):
        meta_path = d / "meta.json"
        if d.is_dir() and meta_path.exists():
            meta = json.loads(meta_path.read_text())
            meta["steps"] = [_resolve_snapshot_step_urls(meta["id"], s) for s in meta.get("steps", [])]
            items.append(meta)
    return items


@app.get("/memeready-file/{item_id}/{filename}")
def get_memeready_file(item_id: str, filename: str):
    path = MEMEREADY_DIR / item_id / filename
    if not path.exists() or MEMEREADY_DIR not in path.resolve().parents:
        raise HTTPException(404, "Không tìm thấy file")
    return FileResponse(path)


@app.delete("/memeready/{item_id}")
def delete_memeready(item_id: str):
    item_dir = MEMEREADY_DIR / item_id
    if not item_dir.exists() or MEMEREADY_DIR not in item_dir.resolve().parents:
        raise HTTPException(404, "Không tìm thấy meme sẵn")
    shutil.rmtree(item_dir)
    return {"ok": True}


@app.patch("/memeready/{item_id}")
def update_memeready(item_id: str, req: MemereadyUpdateRequest):
    """Sửa label + duration/text của từng step bên trong snapshot — KHÔNG đổi
    file media/audio đã đóng gói (đổi file coi như tạo bộ mới, xoá bộ cũ, giữ
    đơn giản). req.steps (nếu có) PHẢI cùng số lượng và cùng thứ tự với
    meta['steps'] hiện có — client (index.html:submitMemereadyEdit) luôn gửi
    lại đủ nguyên mảng đã đọc từ GET, chỉ đổi field duration/text."""
    item_dir = MEMEREADY_DIR / item_id
    meta_path = item_dir / "meta.json"
    if not item_dir.exists() or MEMEREADY_DIR not in item_dir.resolve().parents or not meta_path.exists():
        raise HTTPException(404, "Không tìm thấy meme sẵn")
    meta = json.loads(meta_path.read_text())
    if req.label is not None:
        meta["label"] = req.label.strip() or meta["label"]
    if req.steps is not None and len(req.steps) == len(meta.get("steps", [])):
        for old_s, new_s in zip(meta["steps"], req.steps):
            if "duration" in new_s:
                old_s["duration"] = new_s["duration"]
            if "text" in new_s:
                old_s["text"] = new_s["text"]
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return {**meta, "steps": [_resolve_snapshot_step_urls(item_id, s) for s in meta.get("steps", [])]}


# ---------- Cắt/crop video (video_tool.html — mục 5.26 CONTEXT.md) ----------
# Trang video_tool.html (serve qua /static/) cho chọn vùng crop + kéo trim
# filmstrip hoàn toàn phía client; khi bấm Xuất mới upload file gốc qua
# POST /video-tool-upload rồi gọi POST /video-process xử lý thật bằng FFmpeg.
# Job chạy nền bằng threading.Thread (endpoints server này đều sync def,
# không trộn asyncio vào cho rối — khác export_server.py dùng async vì bên
# đó cần Playwright async sẵn rồi). Kết quả lưu trong thư mục tạm của tool,
# chỉ phục vụ nút tải về rồi xóa sau khi trình duyệt tải xong.

video_jobs: dict[str, dict] = {}  # job_id -> {status, progress, message, outputUrl, duration}


class VideoProcessRequest(BaseModel):
    filename: str            # tên file trong threads/media/ (đã upload qua /upload)
    trimStart: float = 0
    trimEnd: float | None = None   # None = tới hết video
    crop: dict | None = None       # {x, y, w, h} theo px NGUỒN, None = không crop


def _run_video_job(job_id: str, in_path: Path, req: VideoProcessRequest):
    try:
        total_dur = _probe_duration(in_path) or 0
        trim_start = max(0.0, float(req.trimStart or 0))
        trim_end = float(req.trimEnd) if req.trimEnd is not None else total_dur
        trim_end = min(trim_end, total_dur) if total_dur else trim_end
        if trim_end - trim_start < 0.1:
            raise ValueError("Đoạn cắt quá ngắn (dưới 0.1s)")
        out_dur = trim_end - trim_start

        cmd = [FFMPEG_BIN, "-y"]
        # -ss/-to TRƯỚC -i: seek nhanh theo keyframe rồi decode chính xác từ
        # đó (cách chuẩn ffmpeg hiện đại, nhanh hơn nhiều so với đặt sau -i
        # với video dài). Bỏ qua nếu trim = toàn bộ video (không cần cắt).
        need_trim = trim_start > 0.01 or (total_dur and trim_end < total_dur - 0.01)
        if need_trim:
            cmd += ["-ss", f"{trim_start:.3f}", "-to", f"{trim_end:.3f}"]
        cmd += ["-i", str(in_path)]
        if req.crop:
            # Server clamp/ép chẵn LẦN NỮA dù client đã làm — không tin dữ
            # liệu client gửi lên (có thể lệch do làm tròn scale hiển thị).
            w = max(2, int(req.crop.get("w", 0)) // 2 * 2)
            h = max(2, int(req.crop.get("h", 0)) // 2 * 2)
            x = max(0, int(req.crop.get("x", 0)))
            y = max(0, int(req.crop.get("y", 0)))
            cmd += ["-vf", f"crop={w}:{h}:{x}:{y}"]
        out_name = f"{in_path.stem}_cut_{uuid.uuid4().hex[:8]}.mp4"
        out_path = VIDEO_TOOL_TMP_DIR / out_name
        # Luôn re-encode (crop bắt buộc re-encode; giữ 1 đường code duy nhất
        # cho cả trường hợp chỉ trim — chậm hơn stream-copy chút nhưng cắt
        # chính xác từng frame, không bị kẹt keyframe).
        cmd += [
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-progress", "pipe:1",
            str(out_path),
        ]
        video_jobs[job_id].update(status="encoding", message="Đang xử lý FFmpeg...")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for line in proc.stdout:
            m = re.search(r"out_time_ms=(\d+)", line)
            if m:
                done_s = int(m.group(1)) / 1_000_000
                pct = int(min(done_s / out_dur, 1.0) * 100)
                video_jobs[job_id].update(progress=pct, message=f"Đang xử lý... {done_s:.1f}s/{out_dur:.1f}s")
        stderr_out = proc.stderr.read()
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg lỗi (mã {proc.returncode}): {stderr_out[-1500:]}")
        video_jobs[job_id].update(
            status="done", progress=100, message="Hoàn tất!",
            outputUrl=f"/video-tool-file/{out_name}", outputFilename=out_name,
            duration=_probe_duration(out_path),
        )
    except Exception as e:
        video_jobs[job_id].update(status="error", message=str(e))
    finally:
        if in_path.exists() and VIDEO_TOOL_TMP_DIR in in_path.resolve().parents:
            in_path.unlink(missing_ok=True)


@app.post("/video-process")
def video_process(req: VideoProcessRequest):
    in_path = VIDEO_TOOL_TMP_DIR / Path(req.filename).name  # .name chặn path traversal
    if not in_path.exists():
        raise HTTPException(404, f"Không tìm thấy file: {req.filename} — cần upload qua /video-tool-upload trước")
    job_id = uuid.uuid4().hex[:12]
    video_jobs[job_id] = {"status": "queued", "progress": 0, "message": "Đang chờ...", "outputUrl": None}
    threading.Thread(target=_run_video_job, args=(job_id, in_path, req), daemon=True).start()
    return {"job_id": job_id}


@app.get("/video-process-status/{job_id}")
def video_process_status(job_id: str):
    job = video_jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Không tìm thấy job")
    return job


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8010)
