"""โปรแกรมซับไทย GTA 6: OCR ซับอังกฤษบนจอ -> ค้นคลังคำแปล (+แปลสด) -> แสดงซับไทยทับเกม

รัน:  ดับเบิลคลิก "ซับไทย.bat" หรือทางลัด "ซับไทย GTA VI" (ไม่มีหน้าต่าง cmd)
      .\\.venv\\Scripts\\python.exe -m app.main --debug    (เปิดจาก Terminal: พิมพ์ข้อความที่ OCR อ่านได้ + เวลา)

ปุ่มลัด:
  Ctrl+Alt+S  เปิด/ปิดซับไทย          Ctrl+Alt+R  เลือกกรอบซับใหม่
  Ctrl+Alt+E  แสดง/ซ่อนอังกฤษต้นฉบับ  Ctrl+Alt+H  ประวัติบทพูด
  Ctrl+Alt+O  หน้าตั้งค่า              Ctrl+Alt+Q  ปิดโปรแกรม

ไฟล์นี้ทำแค่เปิดหน้าต่างโหลดให้เร็วที่สุด แล้วค่อยโหลดตัวโปรแกรมจริง (app/core.py)
"""
import argparse
import ctypes
import os
import sys
import traceback

APP_ID = "FastCity.GTA6ThaiSub"
_INSTANCE = os.environ.get("GTA6THAISUB_INSTANCE", "")          # ใช้ตอนทดสอบ: เปิดตัวทดสอบคู่กับตัวจริงได้
MUTEX_NAME = "GTA6ThaiSubOverlay" + _INSTANCE
SHOW_EVENT = "GTA6ThaiSubOverlay_ShowPanel" + _INSTANCE         # ตัวที่เปิดซ้ำส่งสัญญาณนี้ให้ตัวที่เปิดอยู่โชว์หน้าต่างควบคุม
PANEL_TITLE = "ซับไทย GTA VI"


def _process_name(pid):
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = ctypes.c_void_p
    h = k32.OpenProcess(0x1000, False, pid)              # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        size = ctypes.c_uint32(520)
        if k32.QueryFullProcessImageNameW(ctypes.c_void_p(h), 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        k32.CloseHandle(ctypes.c_void_p(h))


def hide_own_console():
    """ถ้าโปรแกรมถูกเปิดด้วย python.exe จนมีหน้าต่าง cmd ของตัวเองโผล่มา -> ซ่อนไว้ (มีหน้าต่างโปรแกรมแล้ว)
    ไม่แตะหน้าต่าง Terminal / cmd ที่ผู้ใช้เปิดไว้เอง: ซ่อนเฉพาะเมื่อ cmd นั้นมีแต่ตัวโปรแกรมนี้ใช้อยู่
    (venv เรียก Python ซ้อนกัน 2 ชั้น จึงยอมให้มี python.exe ตัวอื่นร่วมได้)"""
    try:
        k32 = ctypes.windll.kernel32
        hwnd = k32.GetConsoleWindow()
        if not hwnd:
            return
        pids = (ctypes.c_uint32 * 16)()
        n = k32.GetConsoleProcessList(pids, 16)
        others = [p for p in pids[:min(n, 16)] if p != os.getpid()]
        if n <= 16 and all(_process_name(p) in ("python.exe", "pythonw.exe", "py.exe") for p in others):
            ctypes.windll.user32.ShowWindow(hwnd, 0)     # SW_HIDE
    except Exception:
        pass


_mutex = None


def single_instance():
    """กันเปิดโปรแกรมซ้อน 2 ตัว (ซับขึ้นซ้อน/ปุ่มลัดชนกัน)"""
    global _mutex
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.restype = ctypes.c_void_p
    h = k32.CreateMutexW(None, False, MUTEX_NAME)
    if k32.GetLastError() == 183:          # ERROR_ALREADY_EXISTS
        k32.CloseHandle(ctypes.c_void_p(h))
        return None
    _mutex = h
    return h


def release_instance():
    """ตอนรีสตาร์ท: ปล่อยให้ตัวใหม่เปิดได้ทันที"""
    global _mutex
    if _mutex:
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(_mutex))
        _mutex = None


def show_running_instance():
    """โปรแกรมเปิดอยู่แล้ว -> ส่งสัญญาณให้ตัวที่เปิดอยู่โชว์หน้าต่างควบคุม (คืน False ถ้าส่งไม่ได้)"""
    k32 = ctypes.windll.kernel32
    k32.OpenEventW.restype = ctypes.c_void_p
    h = k32.OpenEventW(0x0002, False, SHOW_EVENT)        # EVENT_MODIFY_STATE
    if not h:
        return False
    ok = bool(k32.SetEvent(ctypes.c_void_p(h)))
    k32.CloseHandle(ctypes.c_void_p(h))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true", help="พิมพ์ข้อความ OCR และเวลา (เปิดจาก Terminal)")
    ap.add_argument("--select", action="store_true", help="เลือกกรอบซับใหม่ตอนเปิด")
    ap.add_argument("--restarted", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.restarted:
        import time
        time.sleep(1.5)                     # รอตัวเก่าปิดให้เสร็จ (คืนปุ่มลัด/ไอคอนถาด)
    if not args.debug:
        hide_own_console()
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:        # ให้ Windows นับเป็นโปรแกรมของตัวเอง (ไอคอนบนทาสก์บาร์เป็นโลโก้ ไม่ใช่ไอคอน Python)
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass

    from PyQt6.QtWidgets import QApplication, QMessageBox

    from app import theme as T
    from app.widgets import Splash

    qapp = QApplication(sys.argv)
    qapp.setStyle("Fusion")
    qapp.setApplicationName("GTA6ThaiSub")
    qapp.setWindowIcon(T.app_icon())
    qapp.setStyleSheet(T.stylesheet())
    qapp.setQuitOnLastWindowClosed(False)

    mutex = single_instance()
    if mutex is None:
        if not show_running_instance():
            QMessageBox.information(None, PANEL_TITLE, "โปรแกรมเปิดอยู่แล้ว\nดูที่ไอคอนโลโก้ข้างนาฬิกา มุมขวาล่างของจอ")
        return

    splash = Splash()
    splash.show()
    qapp.processEvents()

    from app.applog import log

    def excepthook(et, ev, tb):
        log("[crash] " + "".join(traceback.format_exception(et, ev, tb)))
    sys.excepthook = excepthook
    try:
        from app.core import App
        app = App(qapp, args, on_progress=splash.set_text)
    except Exception as e:
        splash.close()
        log("[start] " + traceback.format_exc())
        QMessageBox.critical(None, "เปิดโปรแกรมไม่ได้", f"{type(e).__name__}: {e}\n\nรายละเอียดอยู่ในไฟล์ data\\app.log")
        raise
    splash.close()
    from app import updater
    updater.mark_code_started()             # เปิดสำเร็จ -> ตัวโหลดไม่ต้องถอยกลับไปใช้รุ่นเดิม
    log(f"รุ่นโปรแกรม {updater.code_version() or 'dev'} (exe {updater.exe_version() or '-'})")
    log("พร้อมแล้ว — ปุ่มลัด Ctrl+Alt+S เปิด/ปิด, Ctrl+Alt+R เลือกกรอบ, Ctrl+Alt+O ตั้งค่า, Ctrl+Alt+Q ปิด")
    code = qapp.exec()
    del app
    sys.exit(code)


if __name__ == "__main__":
    main()
