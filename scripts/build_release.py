"""สร้างตัวโปรแกรมพร้อมแจกเพื่อน (dist/GTA6ThaiSub/ และ dist/GTA6ThaiSub.zip)

รัน:  .\\.venv\\Scripts\\python.exe scripts\\build_release.py

- ตัว .exe ใช้ไอคอนจากโลโก้ (app/assets/icon.ico) และเปิดแบบไม่มีหน้าต่าง cmd
- ถ้าเปลี่ยนโลโก้: วางไฟล์ใหม่ทับ assets_src/logo.png แล้วรัน scripts\\make_assets.py ก่อน
"""
import shutil
import time
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
APP_NAME = "GTA6ThaiSub"
OUT_DIR = DIST / APP_NAME
ZIP_PATH = DIST / f"{APP_NAME}.zip"
ASSETS = ROOT / "app" / "assets"
ICON_PATH = ASSETS / "icon.ico"
VERSION = (1, 2, 0, 0)

README_TH = """========================================
  ซับไทย GTA VI (ซับไทยสดทับหน้าจอ)
========================================

วิธีใช้งาน:
1. แตกไฟล์ ZIP ทั้งโฟลเดอร์ไว้ในเครื่อง (ห้ามรันจากในไฟล์ ZIP โดยตรง)
2. ดับเบิลคลิก GTA6ThaiSub.exe (ไอคอนโลโก้สีชมพู-ม่วง) — ไม่มีหน้าต่างดำ cmd ขึ้นมาแล้ว
3. เปิดเกม GTA VI (ผ่าน PS Remote Play / Capture Card / YouTube)
4. หยุดเกมหรือคลิปตอนที่มีซับอังกฤษขึ้นอยู่ แล้วกดปุ่ม "หาซับอัตโนมัติ"
   (หรือกด "ลากกรอบเอง" เพื่อครอบบริเวณซับ)
5. กด "ทดสอบซับ" เพื่อเช็กตำแหน่ง แล้วกดลูกศรขึ้น/ลงปรับได้ตามใจชอบ

6. กด "เริ่มจับซับไทย"

----------------------------------------
สตรีมพร้อมซับไทย (OBS / TikTok LIVE Studio) — ไม่ต้องใช้ฉากเขียว
----------------------------------------
ในโปรแกรมซับ:
1. ตั้งค่า > ตำแหน่ง > เปิด "ลิงก์ซับพื้นโปร่งใส"
2. กด "คัดลอกลิงก์" (ห้ามพิมพ์ลิงก์เอง — ต้องคัดลอกจากโปรแกรมในเครื่องตัวเอง)
3. "ตำแหน่งวางซับ" เลือก "เหนือซับอังกฤษ"
4. "ซับบนจอคอม" เลือก "ไม่แสดง" (หรือ "ลากย้ายซับบนจอ" ไปไว้จออื่น)
   จะได้ไม่เห็นซับซ้อน 2 ชั้นบนพรีวิว — คนดูไลฟ์ยังเห็นซับตามปกติ
5. "หน้าต่างซับแยก / ฉากเขียว (วิธีเก่า)" ปิดไว้ ไม่ต้องเปิด

ใน OBS:
1. แหล่งที่มา > + > "เบราว์เซอร์" (Browser) > ตั้งชื่อ "ซับไทย"
2. URL: วางลิงก์ที่คัดลอกมา / ความกว้าง 1920 / ความสูง 1080 > ตกลง
3. ลาก "ซับไทย" ไว้บนสุดของรายการ แล้วกด Ctrl+F (เต็มภาพ)
   ห้ามใช้ "จับภาพหน้าต่าง" หรือ "จับภาพเกม" จับซับ — จะไม่ขึ้น

ใน TikTok LIVE Studio:
1. เพิ่มแหล่ง "ลิงก์" > วางลิงก์ > ปิด "เปิดเสียง" > เพิ่ม
2. ลากกรอบของลิงก์ให้เต็มภาพ (ทำทั้งแนวนอนและแนวตั้ง ถ้าใช้ทั้งสองแบบ)

ทดสอบก่อนไลฟ์ทุกครั้ง:
1. เปิดโปรแกรมซับค้างไว้ + กด "เริ่มจับซับไทย"
2. ตั้งค่า > ตำแหน่ง > กด "ทดสอบ" -> พรีวิว OBS/TikTok ต้องขึ้น "เจสัน: ทดสอบซับไทย…"
3. ดูป้ายมุมซ้ายบนของพรีวิว:
   - "✓ เชื่อมต่อโปรแกรมซับไทยแล้ว" = ใช้ได้
   - "รอโปรแกรมซับไทย…" ค้าง = โปรแกรมซับปิดอยู่ หรือยังไม่ได้เปิดสวิตช์ลิงก์
   - ไม่มีป้ายอะไรเลย = แหล่งเบราว์เซอร์/ลิงก์ยังไม่โหลด ลองลบแล้วเพิ่มใหม่

ปิดหน้าต่างควบคุมได้ โปรแกรมยังทำงานต่อที่ไอคอนโลโก้ข้างนาฬิกา (มุมขวาล่าง)
คลิกไอคอนนั้นเพื่อเปิดหน้าต่างควบคุมอีกครั้ง

ปุ่มลัด:
- Ctrl+Alt+S  เปิด/ปิดซับไทยชั่วคราว
- Ctrl+Alt+E  แสดง/ซ่อนซับอังกฤษใต้ซับไทย
- Ctrl+Alt+R  ลากเลือกกรอบซับใหม่
- Ctrl+Alt+H  ดูประวัติบทพูด
- Ctrl+Alt+O  เปิดหน้าตั้งค่าฟอนต์/สี/ขนาด
- Ctrl+Alt+Q  ปิดโปรแกรม

แปลสดประโยคที่ไม่มีในคลัง (ไม่บังคับ):
- ตั้งค่า > การแปล > วางคีย์ Gemini (ขอฟรีที่ aistudio.google.com/apikey)
- คีย์เก็บอยู่ในเครื่องคุณเท่านั้น

หมายเหตุ:
- ใช้ได้บน Windows 10/11 (ต้องมีภาษา English ติดตั้งใน Windows สำหรับระบบอ่านซับ)
- คลังคำแปลออฟไลน์ในตัวกว่า 41,400 บรรทัด ใช้งานได้ทันทีไม่ต้องตั้งค่าอะไร
- ประโยคที่ยังไม่มีในคลัง โปรแกรมจะจดไว้ใน data/missed.tsv
  ส่งไฟล์นี้กลับมาเพื่อเติมเข้าคลังได้
- ถ้ามีปัญหา เปิดดู data/app.log (ตั้งค่า > ระบบ > บันทึกการทำงาน)
"""

VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers={v}, prodvers={v}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('FileDescription', 'ซับไทย GTA VI'),
      StringStruct('ProductName', 'GTA6ThaiSub'),
      StringStruct('InternalName', 'GTA6ThaiSub'),
      StringStruct('OriginalFilename', 'GTA6ThaiSub.exe'),
      StringStruct('FileVersion', '{vs}'),
      StringStruct('ProductVersion', '{vs}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def ensure_assets():
    if not ICON_PATH.exists():
        print("ยังไม่มีไอคอน -> สร้างจาก assets_src/logo.png ...")
        subprocess.run([sys.executable, str(ROOT / "scripts" / "make_assets.py")], cwd=ROOT, check=True)


def main():
    ensure_assets()
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    BUILD.mkdir(parents=True, exist_ok=True)
    version_file = BUILD / "version_info.txt"
    version_file.write_text(VERSION_INFO.format(v=VERSION, vs=".".join(map(str, VERSION))), encoding="utf-8")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",                      # ไม่มีหน้าต่าง cmd
        "--name", APP_NAME,
        "--icon", str(ICON_PATH),
        "--version-file", str(version_file),
        "--add-data", f"{ASSETS};app/assets",
        "--collect-all", "winrt",
        "--collect-all", "rapidfuzz",
        "--exclude-module", "yt_dlp",
        "--exclude-module", "anthropic",
        "--exclude-module", "tkinter",
        "--exclude-module", "matplotlib",
        "--exclude-module", "pytest",
        str(ROOT / "run_app.py"),
    ]
    # ประทับรุ่นโค้ดลงใน .exe (ระบบอัปเดตจะโหลดเฉพาะรุ่นที่ใหม่กว่านี้) แล้วคืนค่าเดิมหลัง build
    version_py = ROOT / "app" / "version.py"
    original = version_py.read_text(encoding="utf-8")
    code_version = int(time.strftime("%Y%m%d%H%M"))
    version_py.write_text(f"# สร้างโดย scripts/build_release.py\nCODE_VERSION = {code_version}\n", encoding="utf-8")
    print(f"Running PyInstaller... (รุ่นโค้ด {code_version})")
    try:
        subprocess.run(cmd, cwd=ROOT, check=True)
    finally:
        version_py.write_text(original, encoding="utf-8")

    # คัดลอกไฟล์ data ที่จำเป็นเท่านั้น (ไม่รวม backup / settings ส่วนตัว / คีย์)
    data_out = OUT_DIR / "data"
    data_out.mkdir(parents=True, exist_ok=True)
    for fname in ("translations.db", "glossary.json", "style_guide.md", "quick_phrases.json", "update_source.json"):
        shutil.copy2(ROOT / "data" / fname, data_out / fname)
    # เวอร์ชันของคลังที่ติดไปกับโปรแกรม (จาก publish_corpus.py ครั้งล่าสุด) -> เพื่อนไม่ต้องโหลดคลังเดิมซ้ำ
    # ปลายทางรับประโยคตกหล่น (ลิงก์ webhook ไม่เก็บใน git จึงคัดลอกจากเครื่องนี้ตอน build)
    if (ROOT / "data" / "report_target.json").exists():
        shutil.copy2(ROOT / "data" / "report_target.json", data_out / "report_target.json")
    if (ROOT / "data" / "corpus_version.json").exists():
        shutil.copy2(ROOT / "data" / "corpus_version.json", data_out / "corpus_version.json")

    (OUT_DIR / "วิธีใช้งาน.txt").write_text(README_TH, encoding="utf-8")

    # บีบอัดเป็น ZIP พร้อมส่งเพื่อน
    print(f"Creating {ZIP_PATH} ...")
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(OUT_DIR.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(DIST))

    size_mb = ZIP_PATH.stat().st_size / (1024 * 1024)
    print(f"DONE: {OUT_DIR}")
    print(f"ZIP : {ZIP_PATH} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
