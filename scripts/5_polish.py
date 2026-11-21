"""เกลาคำแปลทั้งคลังให้อ่านเหมือนซับหนังไทย (รอบตรวจคุณภาพหลังแปล)

- ส่งทีละฉาก (บรรทัดเรียงตามเวลาในคลิปเดียวกัน) พร้อม style guide + ข้อมูลตัวละคร + glossary
- AI อ่านทั้งฉาก เดาว่าใครพูดกับใคร แล้วเขียนคำแปลใหม่ให้เป็นธรรมชาติ สรรพนามคงที่ทั้งฉาก
- แก้คำอังกฤษที่ซับ YouTube ถอดเสียงผิดได้ด้วย (ช่วยให้จับคู่กับซับในเกมตรงขึ้น)
- บรรทัดขยะ (ถอดเสียงมั่ว/วิทยุ/เพลง) จะถูกลบออกจากคลัง
- คำแปลเดิมเก็บไว้ในคอลัมน์ th_raw ทุกบรรทัด (ย้อนกลับได้) + บันทึกก่อน/หลังใน data/polish_log.tsv
- ทำต่อจากเดิมได้ กด Ctrl+C แล้วรันใหม่ได้เลย ถ้าโควตาวันนี้หมดจะรอแล้วทำต่อเอง

ใช้:
  python scripts/5_polish.py --dry-run          # ดูว่าเหลือกี่บรรทัด/กี่ครั้ง
  python scripts/5_polish.py --limit 2          # ลอง 2 ชุดก่อน แล้วเปิด data/polish_log.tsv ดู
  python scripts/5_polish.py                    # ทำทั้งหมด (~415 ครั้ง ≈ 1 วันของโควตาฟรี)
"""
import argparse
import ctypes
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from app.matcher import normalize  # noqa: E402

DATA = ROOT / "data"
DB_PATH = DATA / "translations.db"
LOG = DATA / "polish_log.tsv"
BATCH = 100       # บรรทัดต่อ 1 ครั้ง
CONTEXT = 8       # บรรทัดก่อนหน้า (เกลาแล้ว) ที่แนบไปให้รู้เรื่องต่อเนื่อง
DELAY = 5         # วินาทีระหว่างครั้ง (Flash Lite ฟรี 15 ครั้ง/นาที)

PROMPT = """คุณเป็นบรรณาธิการซับไทยมืออาชีพ งานนี้คือเกลาซับเกมแอ็กชันอาชญากรรมเรต 18+ \
ให้อ่านแล้วรู้สึกเหมือนดูหนังซับไทยคุณภาพดี

ข้อมูลแต่ละบรรทัด: EN = ซับอังกฤษจาก auto-caption ของ YouTube (ไม่มีชื่อคนพูด อาจถอดเสียงผิด อาจเป็นเศษประโยค) \
TH = คำแปลเดิมจากเครื่อง ซึ่งมักแข็ง แปลตรงตัว และใช้สรรพนามไม่เข้ากับความสัมพันธ์

ขั้นแรก อ่านทั้งฉาก (รวม <context>) แล้วสรุปในใจว่าฉากนี้มีใครบ้าง ใครเป็นผู้ชาย/ผู้หญิง ใครพูดบรรทัดไหน \
ความสัมพันธ์เป็นแบบไหน และจะใช้สรรพนามคู่ไหนตลอดฉาก

ทำกับทุกบรรทัดใน <edit>:
1. เขียน th ใหม่ทุกบรรทัดจาก EN โดยตรง เหมือนนักแปลซับหนังมืออาชีพแปลเองตั้งแต่ต้น \
TH เดิมมีไว้ดูแค่ว่าเคยเข้าใจความหมายยังไง ห้ามลอกโครงประโยคเดิม \
ตรวจทุกบรรทัดว่า: ความหมายถูกไหม (TH เดิมแปลผิดบ่อย), สรรพนามตรงกับเพศ/ความสัมพันธ์ไหม \
(ผู้หญิงลงท้าย ค่ะ ไม่ควรใช้ กู กับคนเดียวกันในประโยคถัดไป), อ่านทันไหม, คนไทยพูดแบบนี้จริงไหม
2. en_fix: ถ้า EN ถอดเสียงผิดชัดเจน (คำไม่มีความหมาย ชื่อสะกดผิด) ให้ใส่ประโยคอังกฤษที่น่าจะพูดจริง \
แก้เฉพาะคำที่ผิด คงคำอื่นและลำดับเดิม ถ้า EN ถูกอยู่แล้วให้เป็น ""
3. เศษประโยค (ขาดหัว/ขาดท้าย): แปลเป็นท่อนที่ต่อกับบรรทัดข้างเคียงได้ ใช้ ... ช่วย ห้ามเติมเนื้อหาที่ไม่มี
4. th เป็น "" เฉพาะบรรทัดขยะ: ถอดเสียงมั่วจนไม่มีความหมาย, เสียงวิทยุ/ทีวี/โฆษณา/เนื้อเพลง

<style_guide>
{style}
</style_guide>

<characters>
{characters}
</characters>

<glossary>
{glossary}
</glossary>

ตอบ JSON ครบทุก id ใน <edit>: {{"lines": [{{"id": <id>, "th": "...", "en_fix": "..."}}]}}

{lines}"""

SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "th": {"type": "string"},
                    "en_fix": {"type": "string"},
                },
                "required": ["id", "th", "en_fix"],
            },
        }
    },
    "required": ["lines"],
}


class QuotaExhausted(Exception):
    pass


def setup_db(con):
    cols = {r[1] for r in con.execute("PRAGMA table_info(translations)")}
    if "th_raw" not in cols:
        con.execute("ALTER TABLE translations ADD COLUMN th_raw TEXT")
    if "en_raw" not in cols:
        con.execute("ALTER TABLE translations ADD COLUMN en_raw TEXT")
    con.execute("CREATE TABLE IF NOT EXISTS polished (rid INTEGER PRIMARY KEY)")
    con.execute("CREATE TABLE IF NOT EXISTS removed (en_text TEXT, th_text TEXT, source TEXT, start_sec REAL)")
    con.commit()


def load_rows(con):
    return con.execute(
        "SELECT rowid, en_text, th_text, source FROM translations "
        "WHERE source NOT LIKE 'live-%' ORDER BY source, start_sec, rowid"
    ).fetchall()


def make_client(model_name):
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        sys.exit("ยังไม่ได้ตั้ง GEMINI_API_KEY")
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=240_000))
    names = [m.name.removeprefix("models/") for m in client.models.list()]
    model = model_name if model_name in names else next(
        (n for n in sorted(names, key=len) if n.startswith(model_name)), model_name)
    off = ["HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
           "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT"]
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_json_schema=SCHEMA,
        max_output_tokens=32000,
        thinking_config=types.ThinkingConfig(thinking_level="MEDIUM"),
        safety_settings=[types.SafetySetting(category=c, threshold="BLOCK_NONE") for c in off],
    )
    return client, model, config


def call(client, model, config, prompt):
    """คืนผล JSON; โยน QuotaExhausted เมื่อโดน 429 ซ้ำหลายครั้ง (น่าจะโควตารายวันหมด)"""
    hits_429 = 0
    for attempt in range(8):
        try:
            parts = []
            for chunk in client.models.generate_content_stream(model=model, contents=prompt, config=config):
                if chunk.text:
                    parts.append(chunk.text)
                    print(".", end="", flush=True)
            print()
            return json.loads("".join(parts))["lines"]
        except Exception as e:
            print()
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                hits_429 += 1
                if hits_429 >= 3:
                    raise QuotaExhausted from e
                wait = 65
            else:
                wait = 20 * (attempt + 1)
            print(f"    {type(e).__name__}: {str(e)[:120]} -> รอ {wait} วิ")
            time.sleep(wait)
    return None


def apply(con, batch, results, log):
    by_id = {r[0]: r for r in batch}
    n_th = n_en = n_del = 0
    for res in results:
        rid = res.get("id")
        if rid not in by_id:
            continue
        _, en, th_old, src = by_id[rid]
        th = (res.get("th") or "").strip()
        en_fix = (res.get("en_fix") or "").strip()
        if not th:
            row = con.execute("SELECT en_text, th_text, source, start_sec FROM translations WHERE rowid=?",
                              (rid,)).fetchone()
            con.execute("INSERT INTO removed VALUES (?,?,?,?)", row)
            con.execute("DELETE FROM translations WHERE rowid=?", (rid,))
            n_del += 1
            log.write(f"{src}\tDEL\t{en}\t{th_old}\t\n")
        else:
            con.execute("UPDATE translations SET th_raw=COALESCE(th_raw, th_text), th_text=? WHERE rowid=?",
                        (th, rid))
            n_th += th != th_old
            if en_fix and normalize(en_fix) != normalize(en) and len(normalize(en_fix)) >= 3:
                try:
                    con.execute("UPDATE translations SET en_raw=COALESCE(en_raw, en_text), en_text=?, "
                                "en_normalized=? WHERE rowid=?",
                                (en_fix, normalize(en_fix), rid))
                    n_en += 1
                except sqlite3.IntegrityError:
                    pass    # ประโยคที่แก้แล้วมีในคลังอยู่แล้ว -> คง EN เดิม
            log.write(f"{src}\t{'EN' if en_fix else ''}\t{en_fix or en}\t{th_old}\t{th}\n")
        con.execute("INSERT OR IGNORE INTO polished VALUES (?)", (rid,))
    # บรรทัดที่ AI ไม่ได้ตอบกลับมา -> ไม่ mark ไว้ รอบหน้าจะทำใหม่
    con.commit()
    log.flush()
    return n_th, n_en, n_del


def reset(con):
    """คืนค่าเดิมจาก th_raw/en_raw (+ สำรอง translations_backup_before_polish.db) และกู้บรรทัดที่ถูกลบ"""
    backup = DATA / "translations_backup_before_polish.db"
    if backup.exists():
        con.execute("ATTACH DATABASE ? AS bk", (str(backup),))
        con.execute("""UPDATE translations SET
            en_text = (SELECT en_text FROM bk.translations b WHERE b.rowid = translations.rowid),
            en_normalized = (SELECT en_normalized FROM bk.translations b WHERE b.rowid = translations.rowid)
            WHERE rowid IN (SELECT rid FROM polished) AND en_raw IS NULL
              AND rowid IN (SELECT rowid FROM bk.translations)""")
        con.commit()
        con.execute("DETACH DATABASE bk")
    con.execute("UPDATE translations SET th_text=th_raw, th_raw=NULL WHERE th_raw IS NOT NULL")
    con.execute("UPDATE OR IGNORE translations SET en_text=en_raw, en_raw=NULL WHERE en_raw IS NOT NULL")
    con.execute("UPDATE translations SET en_normalized=en_normalized")
    from app.matcher import normalize as norm
    for rid, en in con.execute("SELECT rowid, en_text FROM translations WHERE rowid IN (SELECT rid FROM polished)").fetchall():
        try:
            con.execute("UPDATE translations SET en_normalized=? WHERE rowid=?", (norm(en), rid))
        except sqlite3.IntegrityError:
            pass
    for row in con.execute("SELECT en_text, th_text, source, start_sec FROM removed").fetchall():
        con.execute("INSERT OR IGNORE INTO translations (en_normalized, en_text, th_text, source, start_sec) "
                    "VALUES (?,?,?,?,?)", (normalize(row[0]), *row))
    con.execute("DELETE FROM removed")
    n = con.execute("SELECT COUNT(*) FROM polished").fetchone()[0]
    con.execute("DELETE FROM polished")
    con.commit()
    print(f"ย้อนกลับ {n} บรรทัดเป็นของเดิมแล้ว")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, help="ทำแค่กี่ชุด (ไว้ลองดูสำนวนก่อน)")
    ap.add_argument("--model", default="gemini-3.1-flash-lite")
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--reset", action="store_true",
                    help="ย้อนบรรทัดที่เกลาแล้วกลับเป็นของเดิม (ไว้ลองกฎใหม่)")
    ap.add_argument("--max-hours", type=float, default=30, help="ถ้าโควตาหมด รอได้นานสุดกี่ชั่วโมง")
    args = ap.parse_args()

    con = sqlite3.connect(DB_PATH, timeout=30)
    setup_db(con)
    if args.reset:
        reset(con)
    rows = load_rows(con)
    con.execute("CREATE TABLE IF NOT EXISTS claude_done (rid INTEGER PRIMARY KEY)")
    # บรรทัดที่ Claude เกลาแล้ว ไม่ต้องส่ง Gemini
    done = {r[0] for r in con.execute("SELECT rid FROM polished UNION SELECT rid FROM claude_done")}
    todo = [r for r in rows if r[0] not in done]
    print(f"เกลาแล้ว {len(rows) - len(todo)} / {len(rows)} บรรทัด, เหลือ {len(todo)} -> ~{-(-len(todo) // args.batch)} ครั้ง")
    if args.dry_run or not todo:
        return

    client, model, config = make_client(args.model)
    print(f"ใช้รุ่น {model}")
    style = (DATA / "style_guide.md").read_text(encoding="utf-8")
    characters = (DATA / "characters.json").read_text(encoding="utf-8")
    g = json.loads((DATA / "glossary.json").read_text(encoding="utf-8"))
    glossary = json.dumps({"names": g["names"], "slang": g["slang"]}, ensure_ascii=False)
    # กันเครื่องหลับระหว่างรันยาว
    if os.name == "nt":
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)

    pos = {r[0]: i for i, r in enumerate(rows)}
    started = time.time()
    total = [0, 0, 0]
    n_batches = 0
    with LOG.open("a", encoding="utf-8") as log:
        if LOG.stat().st_size == 0:
            log.write("source\tflag\ten\tth_old\tth_new\n")
        i = 0
        while i < len(todo):
            # ชุดต้องอยู่ในคลิปเดียวกัน (ฉากต่อเนื่อง)
            src = todo[i][3]
            batch = [r for r in todo[i:i + args.batch] if r[3] == src]
            first = pos[batch[0][0]]
            ctx = [rows[j] for j in range(max(0, first - CONTEXT), first) if rows[j][3] == src]
            lines = [f'<context id="{r[0]}">EN: {r[1]} | TH: {r[2]}</context>' for r in ctx]
            lines += [f'<edit id="{r[0]}">EN: {r[1]} | TH: {r[2]}</edit>' for r in batch]
            prompt = PROMPT.format(style=style, characters=characters, glossary=glossary, lines="\n".join(lines))

            print(f"  [{src}] เกลา {len(batch)} บรรทัด (เหลือ {len(todo) - i}) ", end="", flush=True)
            try:
                results = call(client, model, config, prompt)
            except QuotaExhausted:
                if time.time() - started > args.max_hours * 3600:
                    print("โควตาหมดและเกินเวลาที่ตั้งไว้ -> หยุด (รันใหม่จะทำต่อ)")
                    break
                print(f"  [{time.strftime('%H:%M')}] โควตาวันนี้น่าจะหมด -> รอ 60 นาทีแล้วลองใหม่")
                time.sleep(3600)
                continue
            if results is None:
                print("    ข้ามชุดนี้ไปก่อน (รันใหม่จะทำซ้ำ)")
                i += len(batch)
                continue
            n = apply(con, batch, results, log)
            total = [a + b for a, b in zip(total, n)]
            print(f"    เปลี่ยนคำแปล {n[0]}, แก้ EN {n[1]}, ลบขยะ {n[2]}")
            i += len(batch)
            n_batches += 1
            if args.limit and n_batches >= args.limit:
                break
            time.sleep(DELAY)

    left = len(load_rows(con)) - con.execute("SELECT COUNT(*) FROM polished p JOIN translations t ON t.rowid=p.rid").fetchone()[0]
    print(f"เสร็จรอบนี้: เปลี่ยนคำแปล {total[0]}, แก้ EN {total[1]}, ลบขยะ {total[2]} | ยังไม่ได้เกลา {left} บรรทัด")
    print("ดูก่อน/หลังได้ที่ data/polish_log.tsv  แล้วรัน scripts/3_export_json.py เพื่ออัปเดต JSON")


if __name__ == "__main__":
    main()
