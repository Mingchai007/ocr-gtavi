"""ส่งออกคลังคำแปลจาก data/translations.db เป็น JSON ไว้เปิดดู/แก้มือ

- data/translations.json        : {"ประโยคอังกฤษ": "คำแปลไทย", ...} เรียงตามเวลาในคลิป
- data/translations_full.json   : แบบละเอียด มีคลิปต้นทางและเวลา (วินาที)

ใช้: python scripts/3_export_json.py   (รันระหว่างที่กำลังแปลอยู่ก็ได้)
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "translations.db"

con = sqlite3.connect(DB_PATH)
rows = con.execute(
    "SELECT en_text, th_text, source, start_sec FROM translations ORDER BY source, start_sec, rowid"
).fetchall()
con.close()

simple = {en: th for en, th, _, _ in rows}
full = [{"en": en, "th": th, "source": src, "time": t} for en, th, src, t in rows]

(ROOT / "data" / "translations.json").write_text(
    json.dumps(simple, ensure_ascii=False, indent=1), encoding="utf-8"
)
(ROOT / "data" / "translations_full.json").write_text(
    json.dumps(full, ensure_ascii=False, indent=1), encoding="utf-8"
)
print(f"ส่งออก {len(rows)} บรรทัด -> data/translations.json, data/translations_full.json")
