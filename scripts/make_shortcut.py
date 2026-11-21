"""สร้างทางลัด "ซับไทย GTA VI" (มีไอคอนโลโก้ เปิดแล้วไม่มีหน้าต่าง cmd) สำหรับรันจากโค้ดในโฟลเดอร์นี้

รัน:  .\\.venv\\Scripts\\python.exe scripts\\make_shortcut.py            (สร้างในโฟลเดอร์โปรเจกต์)
      .\\.venv\\Scripts\\python.exe scripts\\make_shortcut.py --desktop  (สร้างบนเดสก์ท็อปด้วย)

ทางลัดจำตำแหน่งโฟลเดอร์ไว้ ถ้าย้ายโฟลเดอร์โปรเจกต์ให้รันสคริปต์นี้ใหม่
"""
import base64
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "ซับไทย GTA VI.lnk"
TARGET = ROOT / ".venv" / "Scripts" / "pythonw.exe"       # pythonw = Python แบบไม่มีหน้าต่าง cmd
ICON = ROOT / "app" / "assets" / "icon.ico"


def create(link: Path):
    def q(p):
        return str(p).replace("'", "''")

    script = (
        "$ProgressPreference = 'SilentlyContinue';"
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{q(link)}');"
        f"$s.TargetPath = '{q(TARGET)}';"
        "$s.Arguments = '-m app.main';"
        f"$s.WorkingDirectory = '{q(ROOT)}';"
        f"$s.IconLocation = '{q(ICON)},0';"
        "$s.Description = 'ซับไทย GTA VI';"
        "$s.Save()"
    )
    # ส่งคำสั่งแบบเข้ารหัส กันชื่อภาษาไทยเพี้ยน
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                   check=True, stderr=subprocess.DEVNULL)
    print(f"สร้างทางลัดแล้ว: {link}")


def main():
    if not TARGET.exists():
        raise SystemExit(f"ไม่พบ {TARGET} (ยังไม่ได้สร้าง .venv?)")
    create(ROOT / NAME)
    if "--desktop" in sys.argv:
        create(Path.home() / "Desktop" / NAME)


if __name__ == "__main__":
    main()
