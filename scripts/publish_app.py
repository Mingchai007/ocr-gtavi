"""ปล่อยโค้ดโปรแกรมรุ่นใหม่ขึ้น GitHub ให้โปรแกรมของเพื่อนโหลดเอง (ไม่ต้องส่ง ZIP 109 MB)

รัน:  .\\.venv\\Scripts\\python.exe scripts\\publish_app.py --notes "แก้ซับกระพริบ"
      --dry-run   สร้างไฟล์ใน build\\app_release\\ อย่างเดียว ไม่อัปโหลด
      --with-corpus  ปล่อยคลังคำแปลชุดใหม่ไปด้วยในคำสั่งเดียว

โทเคน GitHub อ่านจากไฟล์ .env (GITHUB_TOKEN=...) เหมือน publish_corpus.py

ส่งเฉพาะโค้ดในโฟลเดอร์ app/ (+ ไอคอน) ประมาณ 0.5 MB — ไลบรารีอยู่ใน .exe อยู่แล้ว
ถ้าโค้ดใหม่ต้องใช้ไลบรารีที่ .exe ไม่มี ให้เพิ่ม EXE_VERSION ใน run_app.py แล้วใส่ --min-exe เลขนั้น
(เครื่องเพื่อนจะได้ข้อความให้โหลด ZIP ตัวเต็มแทน)
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from app.updater import APP_MANIFEST, APP_TAG, repo  # noqa: E402
from publish_corpus import API, api, load_env  # noqa: E402

OUT = ROOT / "build" / "app_release"
ZIP_NAME = "app_code.zip"


def build(version, notes, min_exe):
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    z = OUT / ZIP_NAME
    files = sorted(p for p in (ROOT / "app").rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for p in files:
            arc = p.relative_to(ROOT).as_posix()
            if arc == "app/version.py":
                zf.writestr(arc, f"# สร้างโดย scripts/publish_app.py\nCODE_VERSION = {version}\n")
            else:
                if p.suffix == ".py":
                    compile(p.read_text(encoding="utf-8"), str(p), "exec")      # ไม่ปล่อยโค้ดที่ syntax เสีย
                zf.write(p, arc)
    sha = hashlib.sha256(z.read_bytes()).hexdigest()
    manifest = {"version": version, "min_exe": min_exe, "notes": notes,
                "name": ZIP_NAME, "sha256": sha, "size": z.stat().st_size}
    (OUT / APP_MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest, len(files)


def publish(token, name):
    """อัปโหลดเข้า release ตายตัว app-channel (แทนที่ไฟล์เดิม) — ไม่ทำให้เป็น release ล่าสุด จึงไม่กวนคลังคำแปล"""
    info = api("GET", f"{API}/repos/{name}", token)
    try:
        rel = api("GET", f"{API}/repos/{name}/releases/tags/{APP_TAG}", token)
    except SystemExit as e:
        if "404" not in str(e):
            raise
        rel = api("POST", f"{API}/repos/{name}/releases", token, {
            "tag_name": APP_TAG, "target_commitish": info.get("default_branch", "main"),
            "name": "ตัวโปรแกรม (อัปเดตอัตโนมัติ)", "body": "ไฟล์สำหรับระบบอัปเดตโปรแกรมอัตโนมัติ — ไม่ต้องโหลดเอง",
            "make_latest": "false"})
    for asset in rel.get("assets", []):
        if asset["name"] in (ZIP_NAME, APP_MANIFEST):
            api("DELETE", f"{API}/repos/{name}/releases/assets/{asset['id']}", token)
    upload = rel["upload_url"].split("{")[0]
    for fname in (ZIP_NAME, APP_MANIFEST):            # manifest ทีหลังสุด: เครื่องเพื่อนจะไม่เห็นรุ่นใหม่ก่อนไฟล์โค้ดพร้อม
        path = OUT / fname
        ctype = "application/zip" if fname.endswith(".zip") else "application/json"
        api("POST", f"{upload}?name={fname}", token, data=path.read_bytes(), ctype=ctype)
        print(f"  อัปโหลด {fname} ({path.stat().st_size / 1024:,.0f} KB)")
    print(f"เสร็จแล้ว: {rel['html_url']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--notes", default="", help="ข้อความสั้นๆ ว่ารุ่นนี้แก้อะไร (เพื่อนจะเห็นตอนอัปเดต)")
    ap.add_argument("--min-exe", type=int, default=2, help="รุ่น .exe ต่ำสุดที่ใช้โค้ดนี้ได้ (ดู EXE_VERSION ใน run_app.py)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--with-corpus", action="store_true", help="ปล่อยคลังคำแปลชุดใหม่ไปด้วย")
    args = ap.parse_args()
    version = int(time.strftime("%Y%m%d%H%M"))
    m, n = build(version, args.notes, args.min_exe)
    print(f"สร้างไฟล์ใน {OUT}: รุ่น {version}, {n} ไฟล์, {m['size'] / 1024:,.0f} KB")
    if args.dry_run:
        return
    import os
    token = os.environ.get("GITHUB_TOKEN", "").strip() or load_env().get("GITHUB_TOKEN", "").strip()
    if not token:
        raise SystemExit("ยังไม่ได้ตั้งโทเคน: เปิดไฟล์ .env ในโฟลเดอร์โปรเจกต์ แล้ววางโทเคนต่อจาก GITHUB_TOKEN=")
    name = repo()
    if not name:
        raise SystemExit("ยังไม่ได้ตั้ง repo ใน data/update_source.json")
    publish(token, name)
    if args.with_corpus:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "publish_corpus.py"), "--notes", args.notes], check=True)


if __name__ == "__main__":
    main()
