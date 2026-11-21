"""ดาวน์โหลดเฉพาะไฟล์ซับ (.vtt) จากลิงก์ใน data/sources.txt ลง data/raw_vtt/

ใช้: python scripts/0_download_subs.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "data" / "sources.txt"
OUT_DIR = ROOT / "data" / "raw_vtt"


def read_sources():
    for line in SOURCES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, rest = line.split(maxsplit=1)
        yield name, rest.split()[0]   # ตัดคอมเมนต์ท้ายบรรทัด (# ชื่อคลิป) ออก


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, url in read_sources():
        if list(OUT_DIR.glob(f"{name}.*vtt")):
            print(f"[skip] {name} มีไฟล์แล้ว")
            continue
        print(f"[download] {name} <- {url}")
        # --write-sub มาก่อน: ถ้าคลิปมีซับที่คนทำเอง (แม่นกว่า) จะได้ไฟล์นั้น
        # ถ้าไม่มี yt-dlp จะใช้ auto-caption แทน
        cmd = [
            sys.executable, "-m", "yt_dlp",
            "--write-sub", "--write-auto-sub",
            "--sub-lang", "en",
            "--sub-format", "vtt",
            "--skip-download",
            "-o", str(OUT_DIR / f"{name}.%(ext)s"),
            url,
        ]
        subprocess.run(cmd, check=False)
    print("files:", *sorted(p.name for p in OUT_DIR.glob("*.vtt")), sep="\n  ")


if __name__ == "__main__":
    main()
