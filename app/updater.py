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
    """ย้ายคำแปลของเครื่องนี้เข้าคลังใหม่:
    - คำแปลสด (source = live-*) -> เฉพาะประโยคที่คลังใหม่ยังไม่มี
    - คำที่ผู้ใช้แก้เอง (source = user-fix) -> ใช้ของผู้ใช้เสมอ"""
    if not old_db.exists():
        return 0
    con = sqlite3.connect(new_db)
    try:
        con.execute("ATTACH DATABASE ? AS old", (str(old_db),))
        before = con.total_changes
        cols = "en_normalized, en_text, th_text, source, start_sec"
        con.execute(f"INSERT OR IGNORE INTO translations ({cols}) SELECT {cols} FROM old.translations "
                    "WHERE source LIKE 'live-%'")
        has_fixes = con.execute("SELECT 1 FROM old.sqlite_master WHERE type='table' AND name='user_fixes'").fetchone()
        if has_fixes:
            con.execute("CREATE TABLE IF NOT EXISTS user_fixes (en_normalized TEXT PRIMARY KEY, en_text TEXT, "
                        "th_text TEXT, fixed_at TEXT)")
            con.execute("INSERT OR REPLACE INTO user_fixes SELECT * FROM old.user_fixes")
            con.execute("UPDATE translations SET th_text = (SELECT u.th_text FROM user_fixes u "
                        "WHERE u.en_normalized = translations.en_normalized) "
                        "WHERE en_normalized IN (SELECT en_normalized FROM user_fixes)")
            con.execute("INSERT OR IGNORE INTO translations (en_normalized, en_text, th_text, source, start_sec) "
                        "SELECT en_normalized, en_text, th_text, 'user-fix', NULL FROM user_fixes")
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


# ================================================================ อัปเดตตัวโปรแกรม (โค้ด)
# ใช้ release ชื่อตายตัว "app-channel" (ไม่ใช่ release ล่าสุด ซึ่งเป็นของคลังคำแปล) — ไฟล์ข้างในถูกแทนที่ทุกครั้งที่ปล่อยรุ่นใหม่
APP_TAG = "app-channel"
APP_MANIFEST = "app_manifest.json"
UPDATE_DIR = DATA / "app_update"


def code_version():
    """รุ่นของโค้ดที่กำลังรันอยู่ (0 = รันจากโค้ดในเครื่อง)"""
    try:
        from app.version import CODE_VERSION
        return int(CODE_VERSION)
    except Exception:
        return 0


def exe_version():
    import sys
    return getattr(sys, "_gta6_exe_version", 0)


def app_base_url():
    override = os.environ.get("GTA6THAISUB_APP_URL", "").rstrip("/")         # ใช้ตอนทดสอบ
    if override:
        return override
    name = repo()
    return f"https://github.com/{name}/releases/download/{APP_TAG}" if name else ""


def code_updates_supported():
    """อัปเดตโค้ดได้เฉพาะตัว .exe ที่มีตัวโหลดรุ่นใหม่ (ตอนรันจากโค้ดในเครื่องไม่ต้องอัปเดต)"""
    return exe_version() >= 2 and (code_version() > 0 or bool(os.environ.get("GTA6THAISUB_APP_URL")))


def pending_code_version():
    """รุ่นโค้ดที่โหลดไว้แล้ว รอใช้ตอนเปิดโปรแกรมครั้งหน้า (0 = ไม่มี)"""
    try:
        v = int(json.loads((UPDATE_DIR / "current.json").read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    return v if v > code_version() else 0


def mark_code_started():
    """เปิดโปรแกรมสำเร็จ -> บอกตัวโหลดว่ารุ่นนี้ใช้ได้ (ไม่ต้องถอยกลับ)"""
    try:
        (UPDATE_DIR / "starting.json").unlink()
    except OSError:
        pass


def check_code_update(progress=None):
    """เช็กและโหลดโค้ดโปรแกรมรุ่นใหม่ (มีผลเมื่อเปิดโปรแกรมใหม่)
    คืน {"downloaded": bool, "version": int, "notes": str, "need_exe": bool}"""
    url = app_base_url()
    if not url:
        raise UpdateError("ยังไม่ได้ตั้งแหล่งอัปเดต")
    try:
        m = json.loads(_get(f"{url}/{APP_MANIFEST}", 64 * 1024).decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"downloaded": False, "version": code_version(), "notes": "", "need_exe": False}
        raise UpdateError(f"เช็กอัปเดตโปรแกรมไม่ได้ (GitHub ตอบ {e.code})") from e
    except Exception as e:
        raise UpdateError(f"เช็กอัปเดตโปรแกรมไม่ได้ ({type(e).__name__})") from e
    ver = int(m.get("version", 0))
    have = max(code_version(), pending_code_version())
    try:
        rejected = json.loads((UPDATE_DIR / "rejected.json").read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError):
        rejected = None
    if ver <= have or ver == rejected:
        return {"downloaded": False, "version": have, "notes": "", "need_exe": False}
    if m.get("min_exe", 0) > exe_version():
        return {"downloaded": False, "version": ver, "notes": m.get("notes", ""), "need_exe": True}
    import zipfile
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = UPDATE_DIR / ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    try:
        z = tmp / "app_code.zip"
        _download(f"{url}/{m['name']}", z, m["sha256"], m["size"], progress)
        dest = tmp / "unpacked"
        with zipfile.ZipFile(z) as zf:
            names = zf.namelist()
            if any(n.startswith("/") or ".." in n.split("/") for n in names) or "app/main.py" not in names:
                raise UpdateError("ไฟล์อัปเดตโปรแกรมไม่ถูกรูปแบบ")
            zf.extractall(dest)
        for py in (dest / "app").rglob("*.py"):                   # โค้ดต้องคอมไพล์ผ่านทุกไฟล์ก่อนใช้
            compile(py.read_text(encoding="utf-8"), str(py), "exec")
        folder = f"v{ver}"
        shutil.rmtree(UPDATE_DIR / folder, ignore_errors=True)
        os.replace(dest, UPDATE_DIR / folder)
        (UPDATE_DIR / "current.json").write_text(json.dumps({"version": ver, "folder": folder,
                                                             "min_exe": m.get("min_exe", 0)}), encoding="utf-8")
        for old in UPDATE_DIR.glob("v*"):                         # เก็บแค่รุ่นที่ใช้อยู่กับรุ่นใหม่
            if old.is_dir() and old.name not in (folder, f"v{code_version()}"):
                shutil.rmtree(old, ignore_errors=True)
    except SyntaxError as e:
        raise UpdateError(f"โค้ดรุ่นใหม่เสีย ({e.filename})") from e
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"downloaded": True, "version": ver, "notes": m.get("notes", ""), "need_exe": False}


def version_label(v):
    """เลขเวอร์ชัน (YYYYMMDDHHMM) -> ข้อความอ่านง่าย"""
    s = str(v)
    if len(s) == 12:
        return f"{s[6:8]}/{s[4:6]}/{s[0:4]} {s[8:10]}:{s[10:12]}"
    return s if v else "ติดมากับโปรแกรม"
