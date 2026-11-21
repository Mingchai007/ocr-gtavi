"""จุดเริ่มโปรแกรม (ฝังอยู่ใน GTA6ThaiSub.exe — ไฟล์นี้อัปเดตอัตโนมัติไม่ได้ จึงต้องเล็กและไม่พัง)

ถ้ามีโค้ดโปรแกรมรุ่นใหม่ที่โหลดมาจาก GitHub (data/app_update/current.json) จะใช้รุ่นนั้นแทนรุ่นที่ติดมากับ .exe
ระบบกันพัง: ถ้ารุ่นใหม่เปิดไม่ขึ้น 2 ครั้งติด หรือ import ไม่ผ่าน -> ทิ้งรุ่นนั้น แล้วใช้รุ่นที่ติดมากับ .exe
"""
import importlib
import importlib.machinery
import json
import sys
from pathlib import Path

# รุ่นของตัวโหลด/ไลบรารีใน .exe — เพิ่มเลขเมื่อเปลี่ยนไลบรารีที่ติดมา (โค้ดใหม่ที่ต้องใช้ไลบรารีใหม่ระบุ min_exe ได้)
EXE_VERSION = 2
sys._gta6_exe_version = EXE_VERSION


def _base_dir():
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


class _AppFinder:
    """ให้ "import app..." หยิบจากโฟลเดอร์รุ่นใหม่ก่อนโค้ดที่ฝังใน .exe"""

    def __init__(self, root):
        self.root = str(root)

    def find_spec(self, name, path=None, target=None):
        if name == "app":
            return importlib.machinery.PathFinder.find_spec(name, [self.root])
        if name.startswith("app."):
            return importlib.machinery.PathFinder.find_spec(name, path)
        return None


def _use_update():
    """คืนข้อมูลรุ่นใหม่ที่จะใช้ หรือ None"""
    upd = _base_dir() / "data" / "app_update"
    try:
        cur = json.loads((upd / "current.json").read_text(encoding="utf-8"))
        ver, folder = int(cur["version"]), upd / cur["folder"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if cur.get("min_exe", 0) > EXE_VERSION or not (folder / "app" / "main.py").is_file():
        return None
    try:
        from app.version import CODE_VERSION as builtin          # รุ่นที่ติดมากับ .exe
    except Exception:
        builtin = 0
    if ver <= builtin:
        return None
    # นับจำนวนครั้งที่เปิดรุ่นนี้แล้วยังไม่สำเร็จ (โปรแกรมจะลบเครื่องหมายนี้เมื่อเปิดเสร็จ)
    tries_path = upd / "starting.json"
    try:
        tries = json.loads(tries_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        tries = {}
    if tries.get("version") == ver and tries.get("count", 0) >= 2:
        _reject(upd, ver, "เปิดไม่สำเร็จ 2 ครั้ง")
        return None
    try:
        tries_path.write_text(json.dumps({"version": ver, "count": tries.get("count", 0) + 1 if tries.get("version") == ver else 1}),
                              encoding="utf-8")
    except OSError:
        pass
    return {"version": ver, "folder": folder, "dir": upd}


def _reject(upd, ver, why):
    try:
        (upd / "rejected.json").write_text(json.dumps({"version": ver, "why": why}), encoding="utf-8")
        (upd / "current.json").unlink()
    except OSError:
        pass


def _purge_app_modules():
    for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
        del sys.modules[name]


def run():
    upd = _use_update()
    if upd:
        finder = _AppFinder(upd["folder"])
        sys.meta_path.insert(0, finder)
        _purge_app_modules()
        try:                                   # ตรวจว่าโค้ดรุ่นใหม่ import ได้ครบก่อนเปิดจริง
            importlib.import_module("app.core")
            importlib.import_module("app.main")
        except Exception as e:
            sys.meta_path.remove(finder)
            _purge_app_modules()
            _reject(upd["dir"], upd["version"], f"import ไม่ผ่าน: {type(e).__name__}: {e}")
    from app.main import main
    main()


if __name__ == "__main__":
    run()
