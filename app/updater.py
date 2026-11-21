"""อัปเดตคลังคำแปลอัตโนมัติจาก GitHub Releases

ตอนเปิดโปรแกรม (เบื้องหลัง ไม่หน่วงเกม) จะโหลด manifest.json จาก release ล่าสุดของ repo ที่ตั้งไว้ใน
data/update_source.json แล้วถ้าคลังบนเน็ตใหม่กว่าในเครื่อง ก็โหลดเฉพาะ translations.db.gz (+ quick_phrases.json)

ความปลอดภัยของข้อมูล:
  - ตรวจ SHA-256 ของทุกไฟล์ที่โหลด และเปิดฐานข้อมูลใหม่ดูก่อนว่าใช้ได้จริง
  - คำแปลสด (Gemini/MyMemory) ที่เครื่องนี้เก็บไว้เอง ถูกย้ายเข้าคลังใหม่ด้วย ไม่หาย
  - คลังเดิมเก็บสำรองไว้ที่ data/translations.db.bak (สลับไฟล์แบบทีเดียว ไฟฟ้าดับกลางทางไฟล์ไม่พัง)
  - ไม่ส่งข้อมูลอะไรของผู้ใช้ออกไป มีแค่คำขอดาวน์โหลดไฟล์จาก GitHub
"""
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import time
import urllib.error
import urllib.request

from app.matcher import ROOT

DATA = ROOT / "data"
SOURCE_PATH = DATA / "update_source.json"      # {"repo": "เจ้าของ/ชื่อrepo"} — แจกไปพร้อมโปรแกรม
STATE_PATH = DATA / "corpus_version.json"      # เวอร์ชันคลังที่เครื่องนี้มี
DB_PATH = DATA / "translations.db"
QUICK_PATH = DATA / "quick_phrases.json"
MANIFEST = "manifest.json"
USER_AGENT = "GTA6ThaiSub-updater"
TIMEOUT = 20


class UpdateError(Exception):
    pass


def repo():
    """ชื่อ repo ที่ใช้อัปเดต ("" = ยังไม่ได้ตั้ง)"""
    env = os.environ.get("GTA6THAISUB_UPDATE_REPO", "").strip()
    if env:
        return env
    try:
        return json.loads(SOURCE_PATH.read_text(encoding="utf-8")).get("repo", "").strip()
    except (OSError, ValueError):
        return ""


def base_url(name=None):
    """ที่อยู่ไฟล์ใน release ล่าสุด (ใช้ลิงก์ latest/download ไม่ต้องใช้ GitHub API ไม่ติดโควตา)"""
    override = os.environ.get("GTA6THAISUB_UPDATE_URL", "").rstrip("/")     # ใช้ตอนทดสอบกับเซิร์ฟเวอร์ในเครื่อง
    if override:
        return override
    name = name or repo()
    return f"https://github.com/{name}/releases/latest/download" if name else ""


def local_version():
    try:
        return int(json.loads(STATE_PATH.read_text(encoding="utf-8")).get("version", 0))
    except (OSError, ValueError, TypeError):
        return 0


def _get(url, limit):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise UpdateError(f"ไฟล์ใหญ่ผิดปกติ: {url}")
    return data


def _download(url, dest, sha256, size, progress=None):
    h = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    got = 0
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            got += len(chunk)
            if got > size:
                raise UpdateError("ไฟล์ใหญ่กว่าที่ manifest บอก")
            h.update(chunk)
            f.write(chunk)
            if progress:
                progress(got, size)
    if got != size or h.hexdigest() != sha256:
        raise UpdateError("ไฟล์ที่โหลดมาไม่ตรงกับ manifest (เน็ตหลุดหรือไฟล์เสีย)")


def fetch_manifest():
    url = base_url()
    if not url:
        raise UpdateError("ยังไม่ได้ตั้งแหล่งอัปเดต (data/update_source.json)")
    try:
        m = json.loads(_get(f"{url}/{MANIFEST}", 64 * 1024).decode("utf-8"))
    except UpdateError:
        raise
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError("ยังไม่มีคลังบน GitHub (ยังไม่เคยรัน publish_corpus.py หรือ repo เป็น Private)") from e
        raise UpdateError(f"เช็กอัปเดตไม่ได้ (GitHub ตอบ {e.code})") from e
    except Exception as e:
        raise UpdateError(f"เช็กอัปเดตไม่ได้ ({type(e).__name__})") from e
    if not isinstance(m.get("version"), int) or "db" not in m:
        raise UpdateError("manifest ไม่ถูกรูปแบบ")
    return m


def _check_db(path):
    con = sqlite3.connect(path)
    try:
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise UpdateError("คลังที่โหลดมาเสีย")
        n = con.execute("SELECT count(*) FROM translations").fetchone()[0]
    except sqlite3.DatabaseError as e:
        raise UpdateError("คลังที่โหลดมาเปิดไม่ได้") from e
    finally:
        con.close()
    return n


def _carry_live_rows(old_db, new_db):
    """ย้ายคำแปลสดที่เครื่องนี้แปลเองไว้ (source = live-*) เข้าคลังใหม่ (ถ้าคลังใหม่ยังไม่มีประโยคนั้น)"""
    if not old_db.exists():
        return 0
    con = sqlite3.connect(new_db)
    try:
        con.execute("ATTACH DATABASE ? AS old", (str(old_db),))
        before = con.total_changes
        con.execute(
            "INSERT OR IGNORE INTO translations (en_normalized, en_text, th_text, source, start_sec) "
            "SELECT en_normalized, en_text, th_text, source, start_sec FROM old.translations "
            "WHERE source LIKE 'live-%'")
        con.commit()
        moved = con.total_changes - before
        con.execute("DETACH DATABASE old")
    except sqlite3.DatabaseError:
        moved = 0
    finally:
        con.close()
    return moved


def check_and_update(progress=None, force=False):
    """เช็กและติดตั้งคลังใหม่ถ้ามี
    คืน dict: {"updated": bool, "version": int, "rows": int, "live_kept": int, "notes": str}
    โยน UpdateError ถ้าเช็ก/โหลดไม่สำเร็จ (คลังเดิมไม่ถูกแตะ)"""
    m = fetch_manifest()
    have = local_version()
    if m["version"] <= have and not force:
        return {"updated": False, "version": have, "rows": 0, "live_kept": 0, "notes": ""}
    min_app = m.get("min_app", 0)
    from app import APP_VERSION
    if min_app > APP_VERSION:
        raise UpdateError("คลังใหม่ต้องใช้โปรแกรมรุ่นใหม่กว่านี้ — โหลด ZIP ตัวใหม่")
    url = base_url()
    tmp_dir = DATA / ".update_tmp"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True)
    try:
        # 1) คลังคำแปล
        d = m["db"]
        gz = tmp_dir / "translations.db.gz"
        _download(f"{url}/{d['name']}", gz, d["sha256"], d["size"], progress)
        new_db = tmp_dir / "translations.db"
        with gzip.open(gz, "rb") as src, open(new_db, "wb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        rows = _check_db(new_db)
        if rows < 1000:
            raise UpdateError("คลังที่โหลดมามีข้อมูลน้อยผิดปกติ")
        live_kept = _carry_live_rows(DB_PATH, new_db)
        # 2) คำอุทาน/NPC (ไม่บังคับ)
        new_quick = None
        q = m.get("quick")
        if q:
            new_quick = tmp_dir / "quick_phrases.json"
            _download(f"{url}/{q['name']}", new_quick, q["sha256"], q["size"])
            json.loads(new_quick.read_text(encoding="utf-8"))["phrases"]          # ตรวจว่าอ่านได้
        # 3) สลับไฟล์ (เก็บของเดิมไว้ .bak)
        if DB_PATH.exists():
            shutil.copy2(DB_PATH, DB_PATH.with_suffix(".db.bak"))
        os.replace(new_db, DB_PATH)
        if new_quick:
            os.replace(new_quick, QUICK_PATH)
        STATE_PATH.write_text(json.dumps({"version": m["version"], "installed_at": time.strftime("%Y-%m-%d %H:%M"),
                                          "rows": rows}, ensure_ascii=False), encoding="utf-8")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return {"updated": True, "version": m["version"], "rows": rows, "live_kept": live_kept, "notes": m.get("notes", "")}


def version_label(v):
    """เลขเวอร์ชัน (YYYYMMDDHHMM) -> ข้อความอ่านง่าย"""
    s = str(v)
    if len(s) == 12:
        return f"{s[6:8]}/{s[4:6]}/{s[0:4]} {s[8:10]}:{s[10:12]}"
    return s if v else "ติดมากับโปรแกรม"
