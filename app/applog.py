"""บันทึกการทำงานลง data/app.log

โปรแกรมเปิดแบบไม่มีหน้าต่าง cmd แล้ว ข้อความแจ้งปัญหาจึงเก็บลงไฟล์นี้แทน
(เปิดดูได้จาก ตั้งค่า > ระบบ > บันทึกการทำงาน)
"""
import sys
import threading
import time

from app.matcher import ROOT

LOG_PATH = ROOT / "data" / "app.log"
_lock = threading.Lock()


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg)
    if sys.stdout:                      # มีหน้าต่าง cmd (โหมด --debug) -> พิมพ์ให้เห็นด้วย
        try:
            print(line)
        except Exception:
            pass
    with _lock:
        try:
            if LOG_PATH.exists() and LOG_PATH.stat().st_size > 1_000_000:
                LOG_PATH.unlink()
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
