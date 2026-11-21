"""แปลใหม่เฉพาะบรรทัดที่ต้นฉบับมีคำหยาบแรง แต่คำแปลไทยไม่มีคำหยาบ (ถูกแปลให้เบาลง)

- ส่งไปพร้อมบรรทัดก่อน/หลังในฉากเดียวกันเป็นบริบท ให้ AI รู้ว่าใครพูดกับใคร อารมณ์ไหน
- แก้เฉพาะช่อง th ในฐานข้อมูล (en ไม่เปลี่ยน การจับคู่กับ OCR จึงเหมือนเดิม)
- รันซ้ำได้ บรรทัดที่แก้แล้วจะไม่ถูกเลือกอีก

ใช้:
  python scripts/4_fix_profanity.py --dry-run     # ดูจำนวนก่อน ไม่ส่ง AI
  python scripts/4_fix_profanity.py               # แก้จริง (ต้องตั้ง GEMINI_API_KEY)
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DB_PATH = ROOT / "data" / "translations.db"
GLOSSARY = ROOT / "data" / "glossary.json"

EN_STRONG = re.compile(r"\[__\]|\b(?:motherfuck\w*|fuck\w*|bitch\w*|shit\w*|bullshit)\b", re.I)
TH_SWEAR = re.compile(r"เหี้ย|สัส|สัด|แม่ง|ไอ้เวร|อีดอก|ควย|เชี่ย|ห่า|ระยำ|ไอ้สัตว์|ชิบหาย|โคตร|วะ|ไปตาย")
CONTEXT = 3     # บรรทัดก่อน/หลังที่แนบเป็นบริบท
BATCH = 50      # บรรทัดที่ต้องแก้ต่อ 1 ครั้ง

PROMPT = """คุณเป็นนักแปลซับเกมแอ็กชันอาชญากรรมเรตผู้ใหญ่ 18+ จากอังกฤษเป็นไทย
บรรทัดใน <fix> ถูกแปลไว้แบบสุภาพเกินไป ทั้งที่ต้นฉบับมีคำหยาบ ให้แปลใหม่โดย:
- ต้องมีคำหยาบไทยที่แรงเท่าต้นฉบับทุกบรรทัด ห้ามเซ็นเซอร์ ห้ามใช้ "ให้ตายสิ" "เจ้าบ้า"
- [__] คือคำหยาบที่ YouTube เซ็นเซอร์ไว้ ให้เดาจากบริบท (ส่วนใหญ่ fuck/fucking/shit/bitch/motherfucker)
- เลือกคำตามอารมณ์และความสัมพันธ์ในฉาก (ดูจาก context): \
fucking (คำเสริม) -> แม่ง/โคตร/เหี้ย, what the fuck -> เหี้ยไรวะ/อะไรวะเนี่ย, motherfucker -> ไอ้เหี้ย/ไอ้สัส, \
bitch (ด่า) -> อีดอก/อีเหี้ย, shit -> ชิบหาย/เหี้ย, fuck you -> ไปตายซะ/ไอ้สัส
- เพื่อนสนิทแซวกันใช้คำหยาบเบาๆ แบบขำๆ ได้ ทะเลาะ/ยิงกันใช้คำแรง
- ภาษาพูด สั้น อ่านจบใน 1-2 วินาที สรรพนามตามความสัมพันธ์ (กู/มึง, ฉัน/นาย ฯลฯ)
- ชื่อเฉพาะตาม glossary: {glossary}
ตอบเป็น JSON: {{"fixes": [{{"id": <id>, "th": "<คำแปลใหม่>"}}]}} ครบทุก id ใน <fix>

<context_and_fix>
{lines}
</context_and_fix>"""

SCHEMA = {
    "type": "object",
    "properties": {
        "fixes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "th": {"type": "string"}},
                "required": ["id", "th"],
            },
        }
    },
    "required": ["fixes"],
}


def load_rows(con):
    rows = con.execute(
        "SELECT rowid, en_text, th_text, source FROM translations "
        "WHERE source NOT LIKE 'live-%' ORDER BY source, rowid"
    ).fetchall()
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--model", default="gemini-3.1-flash-lite")
    ap.add_argument("--batch", type=int, default=BATCH, help=f"บรรทัดต่อครั้ง (ค่าเริ่มต้น {BATCH})")
    args = ap.parse_args()

    con = sqlite3.connect(DB_PATH, timeout=30)
    rows = load_rows(con)
    pos = {r[0]: i for i, r in enumerate(rows)}
    targets = [r for r in rows if EN_STRONG.search(r[1]) and not TH_SWEAR.search(r[2])]
    print(f"บรรทัดที่มีคำหยาบแรงแต่คำแปลไม่หยาบ: {len(targets)} -> ~{-(-len(targets) // args.batch)} ครั้ง")
    if args.dry_run or not targets:
        return

    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        sys.exit("ยังไม่ได้ตั้ง GEMINI_API_KEY")
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=180_000))
    names = [m.name.removeprefix("models/") for m in client.models.list()]
    model = args.model if args.model in names else next(
        (n for n in sorted(names, key=len) if n.startswith(args.model)), args.model)
    off = ["HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
           "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT"]
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_json_schema=SCHEMA,
        max_output_tokens=16000,
        thinking_config=types.ThinkingConfig(thinking_level="LOW"),
        safety_settings=[types.SafetySetting(category=c, threshold="BLOCK_NONE") for c in off],
    )
    glossary = json.dumps(json.loads(GLOSSARY.read_text(encoding="utf-8"))["names"], ensure_ascii=False)
    print(f"ใช้รุ่น {model}")

    fixed = 0
    for b in range(0, len(targets), args.batch):
        batch = targets[b:b + args.batch]
        want = {r[0] for r in batch}
        # รวมบรรทัดที่ต้องแก้ + บรรทัดข้างเคียง (ฉากเดียวกัน) เรียงตามลำดับเดิม
        show = set()
        for r in batch:
            i = pos[r[0]]
            for j in range(max(0, i - CONTEXT), min(len(rows), i + CONTEXT + 1)):
                if rows[j][3] == r[3]:
                    show.add(j)
        lines = []
        for j in sorted(show):
            rid, en, th, _ = rows[j]
            tag = "fix" if rid in want else "context"
            lines.append(f'<{tag} id="{rid}">EN: {en} | TH เดิม: {th}</{tag}>')

        for attempt in range(5):
            try:
                print(f"  ชุด {b // args.batch + 1}: แก้ {len(batch)} บรรทัด ...", flush=True)
                # stream: Google ไม่ตัดการเชื่อมต่อกลางทาง (กัน 504 DEADLINE_EXCEEDED)
                parts = []
                for chunk in client.models.generate_content_stream(
                    model=model,
                    contents=PROMPT.format(glossary=glossary, lines="\n".join(lines)),
                    config=config,
                ):
                    if chunk.text:
                        parts.append(chunk.text)
                fixes = json.loads("".join(parts))["fixes"]
                break
            except Exception as e:
                wait = 30 * (attempt + 1)
                print(f"    {type(e).__name__}: {str(e)[:100]} -> รอ {wait} วิ")
                time.sleep(wait)
        else:
            print("    ข้ามชุดนี้")
            continue

        n = 0
        for f in fixes:
            th = (f.get("th") or "").strip()
            if f.get("id") in want and th:
                con.execute("UPDATE translations SET th_text=? WHERE rowid=?", (th, f["id"]))
                n += 1
        con.commit()
        fixed += n
        print(f"    แก้แล้ว {n} บรรทัด")
        time.sleep(5)

    left = sum(1 for r in load_rows(con) if EN_STRONG.search(r[1]) and not TH_SWEAR.search(r[2]))
    print(f"เสร็จ: แก้ {fixed} บรรทัด, ยังไม่หยาบอยู่ {left} บรรทัด (รันซ้ำได้)")


if __name__ == "__main__":
    main()
