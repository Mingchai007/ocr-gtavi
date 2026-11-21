"""ปล่อยคลังคำแปลชุดใหม่ขึ้น GitHub Releases ให้โปรแกรมของทุกคนโหลดเองตอนเปิดเครื่อง

รัน (ใน PowerShell):
    $env:GITHUB_TOKEN = "โทเคนของคุณ"
    .\\.venv\\Scripts\\python.exe scripts\\publish_corpus.py --notes "เติมภารกิจเสริม 300 ประโยค"

    --dry-run   สร้างไฟล์ใน build\\corpus_release\\ อย่างเดียว ไม่อัปโหลด

โทเคน: github.com > Settings > Developer settings > Fine-grained tokens > Generate
       เลือกเฉพาะ repo นี้ + Permissions: Contents = Read and write

สิ่งที่อัปโหลด (ไม่ต้องส่ง ZIP 109 MB ใหม่):
    translations.db.gz   คลังแบบย่อ (เฉพาะตาราง translations)
    quick_phrases.json   คำอุทาน/ด่า/NPC
    manifest.json        เลขเวอร์ชัน + SHA-256 ของทุกไฟล์ (โปรแกรมใช้ตรวจว่าไฟล์ไม่เสีย)
"""
import argparse
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from app import APP_VERSION  # noqa: E402
from app.updater import repo  # noqa: E402

DATA = ROOT / "data"
OUT = ROOT / "build" / "corpus_release"
API = "https://api.github.com"


def load_env(path=ROOT / ".env"):
    """อ่านไฟล์ .env (บรรทัดแบบ KEY=ค่า) — ไฟล์นี้อยู่ใน .gitignore ไม่ขึ้น GitHub"""
    out = {}
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_files(notes):
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    # คลังแบบย่อ: คัดลอกเฉพาะตาราง translations (ตารางช่วยงานตอนแปล/เกลาไม่ต้องแจก)
    slim = OUT / "translations.db"
    src = sqlite3.connect(DATA / "translations.db")
    schema = src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='translations'").fetchone()[0]
    rows = src.execute("SELECT * FROM translations").fetchall()
    src.close()
    dst = sqlite3.connect(slim)
    dst.execute(schema)
    dst.executemany(f"INSERT INTO translations VALUES ({','.join('?' * len(rows[0]))})", rows)
    dst.commit()
    dst.execute("VACUUM")
    dst.close()
    gz = OUT / "translations.db.gz"
    with open(slim, "rb") as f, gzip.open(gz, "wb", compresslevel=9) as g:
        shutil.copyfileobj(f, g)
    slim.unlink()
    quick = OUT / "quick_phrases.json"
    shutil.copy2(DATA / "quick_phrases.json", quick)
    version = int(time.strftime("%Y%m%d%H%M"))
    manifest = {
        "version": version,
        "min_app": APP_VERSION,
        "rows": len(rows),
        "notes": notes,
        "db": {"name": gz.name, "sha256": sha256(gz), "size": gz.stat().st_size},
        "quick": {"name": quick.name, "sha256": sha256(quick), "size": quick.stat().st_size},
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


def api(method, url, token, body=None, data=None, ctype="application/json"):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "GTA6ThaiSub-publisher"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    if data is not None:
        headers["Content-Type"] = ctype
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"GitHub ตอบ {e.code}: {msg}") from None


def publish(manifest, token, name):
    info = api("GET", f"{API}/repos/{name}", token)
    if info.get("private"):
        print("คำเตือน: repo นี้เป็น Private — เครื่องเพื่อนจะโหลดคลังไม่ได้ (ต้องเปลี่ยนเป็น Public)")
    tag = f"corpus-{manifest['version']}"
    rel = api("POST", f"{API}/repos/{name}/releases", token, {
        "tag_name": tag,
        "target_commitish": info.get("default_branch", "main"),
        "name": f"คลังคำแปล {manifest['rows']:,} บรรทัด ({tag})",
        "body": manifest["notes"] or "อัปเดตคลังคำแปล",
        "make_latest": "true",
    })
    upload = rel["upload_url"].split("{")[0]
    for fname in ("translations.db.gz", "quick_phrases.json", "manifest.json"):
        path = OUT / fname
        ctype = "application/gzip" if fname.endswith(".gz") else "application/json"
        api("POST", f"{upload}?name={fname}", token, data=path.read_bytes(), ctype=ctype)
        print(f"  อัปโหลด {fname} ({path.stat().st_size / 1024:,.0f} KB)")
    print(f"เสร็จแล้ว: {rel['html_url']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--notes", default="", help="ข้อความสั้นๆ ว่าคลังชุดนี้เพิ่มอะไร")
    ap.add_argument("--dry-run", action="store_true", help="สร้างไฟล์อย่างเดียว ไม่อัปโหลด")
    args = ap.parse_args()
    m = build_files(args.notes)
    print(f"สร้างไฟล์ใน {OUT}: เวอร์ชัน {m['version']}, {m['rows']:,} บรรทัด, "
          f"translations.db.gz {m['db']['size'] / 1024 / 1024:.1f} MB")
    if args.dry_run:
        return
    token = os.environ.get("GITHUB_TOKEN", "").strip() or load_env().get("GITHUB_TOKEN", "").strip()
    if not token:
        raise SystemExit("ยังไม่ได้ตั้งโทเคน: เปิดไฟล์ .env ในโฟลเดอร์โปรเจกต์ แล้ววางโทเคนต่อจาก GITHUB_TOKEN=")
    name = repo()
    if not name:
        raise SystemExit("ยังไม่ได้ตั้ง repo ใน data/update_source.json")
    publish(m, token, name)
    (DATA / "corpus_version.json").write_text(json.dumps({"version": m["version"], "rows": m["rows"],
                                                          "installed_at": time.strftime("%Y-%m-%d %H:%M")}),
                                              encoding="utf-8")


if __name__ == "__main__":
    main()
