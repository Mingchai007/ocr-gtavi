"""รันทุกขั้นต่อเองจนเสร็จ (ทิ้งไว้ข้ามคืนได้)

1. แปลคลัง (2_batch_translate.py) จนครบ — ถ้าโควตาหมด จะรอแล้วลองใหม่ทุก 1 ชั่วโมง
2. แก้คำหยาบที่ถูกแปลเบาลง (4_fix_profanity.py)
3. ส่งออก JSON (3_export_json.py)

- กันคอมพักเครื่องระหว่างรัน (จอดับได้ แต่เครื่องไม่หลับ)
- เขียน log ที่ data/overnight.log

ใช้ (ต้องตั้ง GEMINI_API_KEY ใน Terminal เดียวกันก่อน):
  python scripts/run_overnight.py
"""
import ctypes
import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
LOG = ROOT / "data" / "overnight.log"
MODEL = "gemini-3.1-flash-lite"
WAIT_QUOTA = 3600          # โควตาหมด -> รอ 1 ชม.แล้วลองใหม่
MAX_HOURS = 30             # กันรันไม่จบ


def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def keep_awake(on=True):
    # ES_CONTINUOUS | ES_SYSTEM_REQUIRED : ไม่ให้ Windows พักเครื่องขณะสคริปต์นี้ทำงาน
    flags = 0x80000000 | (0x00000001 if on else 0)
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except Exception:
        pass


def remaining():
    segs = len(json.loads((ROOT / "data" / "raw_segments.json").read_text(encoding="utf-8")))
    con = sqlite3.connect(ROOT / "data" / "translations.db", timeout=30)
    done = con.execute("SELECT COUNT(*) FROM done_segments").fetchone()[0]
    skip = con.execute("SELECT COUNT(*) FROM skipped_segments").fetchone()[0]
    rows = con.execute("SELECT COUNT(*) FROM translations").fetchone()[0]
    con.close()
    return segs - done - skip, rows


def run(args):
    """รันสคริปต์ลูก แสดงผลบนจอและเก็บลง log"""
    p = subprocess.Popen([PY, "-u", *args], cwd=ROOT, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    with LOG.open("a", encoding="utf-8") as f:
        for line in p.stdout:
            print(line, end="", flush=True)
            f.write(line)
    return p.wait()


def main():
    import os
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        sys.exit("ยังไม่ได้ตั้ง GEMINI_API_KEY ใน Terminal นี้")
    keep_awake(True)
    start = time.time()
    log("=== เริ่มรันข้ามคืน ===")
    try:
        # 1) แปลคลังจนครบ
        while True:
            left, rows = remaining()
            log(f"คลัง {rows} บรรทัด, ยังไม่ได้แปล {left} segments")
            if left <= 0:
                break
            code = run(["scripts/2_batch_translate.py", "--model", MODEL, "--batch", "40"])
            left2, _ = remaining()
            if left2 <= 0:
                break
            if time.time() - start > MAX_HOURS * 3600:
                log("เกินเวลาที่กำหนด หยุด")
                break
            if code == 3:
                log(f"โควตาหมด -> รอ {WAIT_QUOTA // 60} นาทีแล้วลองใหม่")
                time.sleep(WAIT_QUOTA)
            elif left2 >= left:
                # รอบนี้ไม่คืบหน้าเลย (เช่น error อื่น) -> รอสั้นๆ แล้วลองใหม่
                log(f"รอบนี้ไม่คืบหน้า (exit {code}) -> รอ 10 นาทีแล้วลองใหม่")
                time.sleep(600)
            # มีความคืบหน้าแต่ยังเหลือ (บางชุด fail) -> วนรันใหม่ทันทีเพื่อเก็บตก

        # 2) แก้คำหยาบ (รัน 2 รอบเผื่อเก็บตก)
        for _ in range(2):
            log("แก้คำหยาบที่ถูกแปลเบาลง")
            run(["scripts/4_fix_profanity.py", "--model", MODEL])

        # 3) ส่งออก JSON
        run(["scripts/3_export_json.py"])
        left, rows = remaining()
        log(f"=== เสร็จ: คลัง {rows} บรรทัด, ยังไม่ได้แปล {left} segments, "
            f"ใช้เวลา {(time.time() - start) / 3600:.1f} ชม. ===")
    finally:
        keep_awake(False)


if __name__ == "__main__":
    main()
