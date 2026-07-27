"""Nén video output (giảm dung lượng) bằng ffmpeg CRF — dùng cho video đã export
từ export_server.py (mặc định không đặt -crf nên bitrate khá cao, ~4Mbps/1080x1920).

Dùng: python3 threads/compress_video.py <file_hoặc_thư_mục> [--crf 28] [--preset medium]

- Nếu truyền 1 file .mp4: nén đúng file đó, ghi ra "<tên>_compressed.mp4" cạnh file gốc.
- Nếu truyền 1 thư mục: nén mọi *.mp4 tìm thấy (đệ quy), mỗi file ra "<tên>_compressed.mp4"
  cạnh file gốc, bỏ qua các file đã có hậu tố "_compressed".
- CRF càng cao càng nén mạnh (dung lượng nhỏ hơn) nhưng chất lượng giảm. 18=gần lossless,
  23=mặc định ffmpeg, 28=nén khá mạnh vẫn xem ổn trên mobile, 32+=nén rất mạnh, dễ vỡ nét.
"""
import argparse
import subprocess
import sys
from pathlib import Path

FFMPEG_BIN = "ffmpeg"


def compress_one(src: Path, crf: int, preset: str) -> None:
    dst = src.with_name(src.stem + "_compressed.mp4")
    cmd = [
        FFMPEG_BIN, "-y", "-i", str(src),
        "-c:v", "libx264", "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        str(dst),
    ]
    print(f"→ Nén {src.name} (crf={crf}, preset={preset})...")
    subprocess.run(cmd, check=True)

    before = src.stat().st_size
    after = dst.stat().st_size
    pct = 100 * (1 - after / before) if before else 0
    print(f"  {before/1e6:.1f}MB → {after/1e6:.1f}MB (giảm {pct:.0f}%) — {dst}")


def main():
    ap = argparse.ArgumentParser(description="Nén video mp4 bằng ffmpeg CRF")
    ap.add_argument("path", help="File .mp4 hoặc thư mục chứa video cần nén")
    ap.add_argument("--crf", type=int, default=28, help="Chất lượng nén, 18-32 (mặc định 28)")
    ap.add_argument("--preset", default="medium",
                     help="Tốc độ/hiệu quả nén ffmpeg: ultrafast..veryslow (mặc định medium)")
    args = ap.parse_args()

    target = Path(args.path)
    if not target.exists():
        print(f"Không tìm thấy: {target}", file=sys.stderr)
        sys.exit(1)

    if target.is_file():
        files = [target]
    else:
        files = [p for p in target.rglob("*.mp4") if not p.stem.endswith("_compressed")]

    if not files:
        print("Không tìm thấy file .mp4 nào cần nén.")
        return

    for f in files:
        compress_one(f, args.crf, args.preset)


if __name__ == "__main__":
    main()
