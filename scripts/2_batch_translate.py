"""ส่ง data/raw_segments.json ให้ AI แบ่งประโยค + ใส่วรรคตอน + แปลไทย -> data/translations.db

- ค่าเริ่มต้นใช้ Google Gemini (ฟรี มีโควตา) ต้องตั้ง GEMINI_API_KEY
- ใช้ Claude แทนได้ด้วย --provider claude (ต้องตั้ง ANTHROPIC_API_KEY, เสียเงิน)
- ทำต่อจากเดิมได้ (ชุดที่แปลแล้วจะถูกข้าม) กด Ctrl+C แล้วรันใหม่ได้เลย

ใช้:
  python scripts/2_batch_translate.py --limit 2     # ลองแค่ 2 ชุดก่อน ดูสำนวน
  python scripts/2_batch_translate.py               # แปลทั้งหมด
  python scripts/2_batch_translate.py --model gemini-2.5-flash   # ระบุรุ่นโมเดลเอง
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
sys.path.insert(0, str(ROOT))
# พิมพ์ภาษาไทยเป็น UTF-8 เสมอ (ตอนถูกเรียกจาก run_overnight.py ผ่าน pipe ไม่งั้นไทยจะเพี้ยน)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from app.matcher import normalize  # noqa: E402

SEGMENTS = ROOT / "data" / "raw_segments.json"
GLOSSARY = ROOT / "data" / "glossary.json"
DB_PATH = ROOT / "data" / "translations.db"

DEFAULT_MODELS = {"gemini": "gemini-flash-latest", "claude": "claude-opus-5-5"}
# จำนวน segment ต่อ 1 request: Gemini ฟรีจำกัด ~20 request/วัน จึงส่งชุดใหญ่
DEFAULT_BATCH = {"gemini": 120, "claude": 40}
CONTEXT_SEGMENTS = 4     # segment ก่อนหน้าที่แนบไปให้เป็นบริบท (ไม่ต้องแปล)
GEMINI_DELAY = 13        # วินาทีที่รอระหว่าง request (โควตาฟรี 5 ครั้ง/นาที)

SYSTEM = """คุณเป็นนักแปลซับไตเติลเกมแนวอาชญากรรมโลกเปิด จากอังกฤษเป็นไทย

ข้อมูลที่ได้รับเป็นบทพูดจาก auto-caption ของ YouTube: ไม่มีวรรคตอน ไม่มีชื่อคนพูด \
หลายคนพูดต่อกันในบรรทัดเดียว และชื่อเฉพาะอาจสะกดเพี้ยน \
เครื่องหมาย [__] คือคำหยาบที่ YouTube เซ็นเซอร์ไว้ (ส่วนใหญ่คือ fuck/fucking/shit/bitch/motherfucker/nigga) \
ให้เดาคำจากบริบทแล้วแปลเป็นคำหยาบไทยที่แรงเท่ากัน แต่ในช่อง en ให้คง [__] ไว้ตามเดิม

งานของคุณสำหรับแต่ละ segment:
1. แบ่งเป็นประโยค/บรรทัดซับตามจังหวะการพูดจริง (เปลี่ยนคนพูด = ขึ้นบรรทัดใหม่) \
แต่ละบรรทัดควรยาวประมาณที่ซับในเกมแสดงทีละครั้ง (ราว 3-15 คำ)
2. ช่อง en: ใส่ตัวพิมพ์ใหญ่และวรรคตอน (. ? ! ,) แก้ชื่อเฉพาะให้ถูกตาม glossary \
ห้ามเพิ่ม ตัด หรือเรียบเรียงคำใหม่ — ต้องเป็นคำเดิมตามลำดับเดิม เพราะจะถูกใช้จับคู่กับ OCR
3. ช่อง th: แปลเป็นภาษาพูดไทยที่เป็นธรรมชาติเหมือนคนไทยพูดกันจริงๆ สั้น กระชับ อ่านจบได้ใน 1-2 วินาที \
เกมนี้เป็นเรตผู้ใหญ่ 18+: ห้ามเซ็นเซอร์หรือทำให้คำหยาบเบาลง แปลคำสบถให้แรงเท่าต้นฉบับตามอารมณ์ของฉาก \
(เช่น เหี้ย, สัส, แม่ง, ไอ้เวร) ห้ามใช้คำสุภาพแบบซับเก่าอย่าง "ให้ตายสิ" หรือ "เจ้าบ้า" \
กฎคำหยาบ: ถ้าต้นฉบับมีคำหยาบแรง (fuck, fucking, motherfucker, bitch, shit, [__]) คำแปลไทยต้องมีคำหยาบเสมอ \
เช่น fucking (คำเสริม) -> แม่ง/โคตร/เหี้ย, what the fuck -> เหี้ยไรวะ/อะไรวะเนี่ย, motherfucker -> ไอ้เหี้ย/ไอ้สัส, \
bitch (ด่า) -> อีดอก/อีเหี้ย, shit -> ชิบหาย/เหี้ย, fuck you -> ไปตายซะ/ไอ้สัส; \
ส่วนประโยคที่ต้นฉบับไม่มีคำหยาบ ห้ามเติมคำหยาบเอง ให้แปลเป็นภาษาพูดธรรมดา \
ใช้สรรพนามให้เข้ากับความสัมพันธ์ตัวละครในฉากนั้น (เพื่อนสนิท/คู่รัก/ลูกพี่ด่าลูกน้อง/ศัตรู: กู/มึง, เรา/เธอ, ฉัน/นาย, ผม/คุณ) \
และใช้ให้คงเส้นคงวาตลอดฉาก ชื่อเฉพาะใช้คำไทยตาม glossary สแลงใช้คำไทยตามรายการ slang โดยเลือกคำที่เข้ากับบริบท
4. ข้ามเสียงที่ไม่ใช่บทพูด (เช่น uh, hm ล้วนๆ, เสียงเพลง, คำที่ถอดเสียงมั่วจนไม่เป็นประโยค) — คืน lines ว่างได้
5. ช่อง kind ของแต่ละบรรทัด:
   - "dialogue" = บทพูดของตัวละครในเนื้อเรื่อง ภารกิจ คัตซีน และ NPC ที่คุย/ตะโกน/โต้ตอบกับผู้เล่น
   - "media" = เสียงจากวิทยุ ทีวี โฆษณา พอดแคสต์ ประกาศ หรือเนื้อเพลงในเกม \
(ดูจากบริบท: น้ำเสียงจัดรายการ พูดกับผู้ฟัง ขายของ ร้องเพลง ไม่เกี่ยวกับเหตุการณ์ตรงหน้า) \
บรรทัด media ไม่ต้องแปล ให้ th เป็น "" และไม่ต้องคัดลอกเนื้อเพลงยาวๆ ใส่ en ให้ใส่แค่ช่วงสั้นๆ

ทุก segment ในส่วน <translate> ต้องมีในผลลัพธ์ โดยใช้ id เดิม ส่วน <context> มีไว้ให้เข้าใจบริบทเท่านั้น ห้ามแปล"""

SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "lines": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "en": {"type": "string"},
                                "th": {"type": "string"},
                                "kind": {"type": "string", "enum": ["dialogue", "media"]},
                            },
                            "required": ["en", "th", "kind"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["id", "lines"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["segments"],
    "additionalProperties": False,
}


class RetryableError(Exception):
    """โควตาเต็ม / เน็ตหลุด / เซิร์ฟเวอร์ล่ม -> รอแล้วลองใหม่"""


class BatchFailed(Exception):
    """ชุดนี้แปลไม่ได้ (ถูกบล็อก/ผลลัพธ์เสีย) -> ข้ามไปก่อน รันใหม่ภายหลังได้"""


# ---------------------------------------------------------------- Gemini
class GeminiTranslator:
    def __init__(self, model, system):
        from google import genai
        from google.genai import errors, types

        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            sys.exit("ยังไม่ได้ตั้ง GEMINI_API_KEY  ->  $env:GEMINI_API_KEY = \"...\"")
        # timeout 5 นาทีต่อ request (หน่วยมิลลิวินาที) กันค้างไม่รู้จบ
        self.client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=300_000))
        self.errors = errors
        self.model = self.resolve_model(model)
        # รุ่น Lite โควตาฟรี 15 ครั้ง/นาที -> รอสั้นกว่า
        self.delay = 5 if "lite" in self.model else GEMINI_DELAY
        off = [
            "HARM_CATEGORY_HARASSMENT",
            "HARM_CATEGORY_HATE_SPEECH",
            "HARM_CATEGORY_SEXUALLY_EXPLICIT",
            "HARM_CATEGORY_DANGEROUS_CONTENT",
        ]
        self.config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=SCHEMA,
            max_output_tokens=65536,
            # งานแปลไม่ต้องคิดลึก -> LOW เร็วกว่าและกินโควตาน้อยกว่า
            thinking_config=types.ThinkingConfig(thinking_level="LOW"),
            safety_settings=[types.SafetySetting(category=c, threshold="BLOCK_NONE") for c in off],
        )

    def available_models(self):
        names = []
        for m in self.client.models.list():
            actions = m.supported_actions or []
            if "generateContent" in actions:
                names.append(m.name.removeprefix("models/"))
        return names

    def resolve_model(self, wanted):
        """ถ้ารุ่นที่ขอไม่มีในบัญชีนี้ ให้เลือกรุ่น Flash ตัวใหม่สุดที่ใช้ได้แทน"""
        names = self.available_models()
        if wanted in names:
            return wanted
        # ชื่อไม่เต็ม เช่น "gemini-3.1-flash-lite" -> "gemini-3.1-flash-lite-preview-xx"
        prefixed = sorted((n for n in names if n.startswith(wanted)), key=len)
        if prefixed:
            print(f"ใช้รุ่น '{prefixed[0]}'")
            return prefixed[0]
        skip = ("lite", "image", "tts", "live", "audio", "preview", "exp", "embedding", "thinking")
        flash = [n for n in names if "flash" in n and not any(s in n for s in skip)]
        if not flash:
            flash = [n for n in names if "flash" in n]
        if not flash:
            sys.exit("ไม่พบรุ่น Gemini Flash ในบัญชีนี้ รุ่นที่ใช้ได้:\n  " + "\n  ".join(names))

        def version(n):
            nums = [int(p) if p.isdigit() else 0 for p in n.replace("gemini-", "").split("-")[0].split(".")]
            return nums, n

        pick = max(flash, key=version)
        print(f"ไม่พบรุ่น '{wanted}' -> ใช้ '{pick}' แทน")
        return pick

    def __call__(self, user):
        # stream: รับคำตอบทีละท่อน และพิมพ์ความคืบหน้า จะได้รู้ว่ายังทำงานอยู่
        parts, resp, t0, last_print = [], None, time.time(), 0.0
        try:
            for chunk in self.client.models.generate_content_stream(
                model=self.model, contents=user, config=self.config
            ):
                resp = chunk
                if chunk.text:
                    parts.append(chunk.text)
                now = time.time()
                if now - last_print >= 5:
                    n = sum(len(p) for p in parts)
                    print(f"\r    ...ได้มาแล้ว {n:,} ตัวอักษร ({now - t0:.0f} วิ)   ", end="", flush=True)
                    last_print = now
            print()
        except self.errors.ClientError as e:
            if e.code == 429:
                raise RetryableError("โควตาเต็ม (429)") from e
            raise BatchFailed(f"{e.code} {e.message}") from e
        except self.errors.ServerError as e:
            raise RetryableError(f"server {e.code}") from e
        except OSError as e:
            raise RetryableError(str(e)) from e

        if resp is None:
            raise RetryableError("ไม่ได้รับคำตอบ")
        if resp.prompt_feedback and resp.prompt_feedback.block_reason:
            raise BatchFailed(f"ถูกบล็อก: {resp.prompt_feedback.block_reason}")
        cand = resp.candidates[0] if resp.candidates else None
        reason = str(cand.finish_reason) if cand else "NO_CANDIDATE"
        if "MAX_TOKENS" in reason:
            raise BatchFailed("ผลลัพธ์ยาวเกิน (ลองใส่ --batch 40)")
        text = "".join(parts)
        if not text:
            raise BatchFailed(f"ไม่มีผลลัพธ์ ({reason})")
        u = resp.usage_metadata
        n_in = (u.prompt_token_count or 0) if u else 0
        n_out = (u.candidates_token_count or 0) if u else 0
        return text, n_in, n_out


# ---------------------------------------------------------------- Claude (เสียเงิน)
class ClaudeTranslator:
    def __init__(self, model, system):
        import anthropic

        self.anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.model = model
        self.system = system
        self.delay = 0

    def __call__(self, user):
        a = self.anthropic
        try:
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=32000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
                output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
                messages=[{"role": "user", "content": user}],
            ) as stream:
                resp = stream.get_final_message()
        except (a.RateLimitError, a.APIConnectionError, a.InternalServerError) as e:
            raise RetryableError(type(e).__name__) from e
        except a.APIStatusError as e:
            raise BatchFailed(str(e)) from e

        if resp.stop_reason == "refusal":
            raise BatchFailed("model refused this batch")
        if resp.stop_reason == "max_tokens":
            raise BatchFailed("ผลลัพธ์ยาวเกิน (ลองใส่ --batch 20)")
        text = next(b.text for b in resp.content if b.type == "text")
        u = resp.usage
        return text, u.input_tokens + (u.cache_read_input_tokens or 0), u.output_tokens


# ---------------------------------------------------------------- main
def open_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS translations (
            en_normalized TEXT PRIMARY KEY,
            en_text TEXT NOT NULL,
            th_text TEXT NOT NULL,
            source TEXT,
            start_sec REAL
        );
        CREATE TABLE IF NOT EXISTS done_segments (id INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS skipped_segments (id INTEGER PRIMARY KEY);
    """)
    return con


WORD = re.compile(r"[a-z']+")


def grams(text, n=6):
    """วลี 6 คำติดกัน ใช้วัดว่าช่วงบทพูดซ้ำกับที่แปลไปแล้วหรือไม่"""
    w = WORD.findall(text.lower().replace("[__]", " "))
    return {tuple(w[i:i + n]) for i in range(len(w) - n + 1)}


def build_user(batch, context):
    user = ""
    if context:
        user += "<context>\n" + "\n".join(s["text"] for s in context) + "\n</context>\n\n"
    user += "<translate>\n" + json.dumps(
        [{"id": s["id"], "text": s["text"]} for s in batch], ensure_ascii=False
    ) + "\n</translate>"
    return user


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["gemini", "claude"], default="gemini")
    ap.add_argument("--model", help="ชื่อรุ่นโมเดล (ค่าเริ่มต้น: %s)" % DEFAULT_MODELS)
    ap.add_argument("--limit", type=int, default=0, help="จำนวนชุดสูงสุดที่จะแปลรอบนี้ (0 = ทั้งหมด)")
    ap.add_argument("--batch", type=int, help="จำนวน segment ต่อครั้ง (ค่าเริ่มต้น: %s)" % DEFAULT_BATCH)
    ap.add_argument("--skip-dupes", action=argparse.BooleanOptionalAction, default=True,
                    help="ข้ามช่วงที่ซ้ำกับที่แปลไปแล้ว (ค่าเริ่มต้น: เปิด, ปิดด้วย --no-skip-dupes)")
    ap.add_argument("--dupe-threshold", type=float, default=0.9,
                    help="สัดส่วนวลีที่ซ้ำถึงจะข้าม (ค่าเริ่มต้น 0.9 = 90%%)")
    ap.add_argument("--list-models", action="store_true", help="แสดงรุ่น Gemini ที่คีย์นี้ใช้ได้ แล้วจบ")
    args = ap.parse_args()

    if args.list_models:
        t = GeminiTranslator.__new__(GeminiTranslator)
        from google import genai
        t.client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
        print("\n".join(t.available_models()))
        return

    segments = json.loads(SEGMENTS.read_text(encoding="utf-8"))
    glossary = json.loads(GLOSSARY.read_text(encoding="utf-8"))
    system = (
        SYSTEM
        + "\n\nglossary (อังกฤษ -> ไทย):\n" + json.dumps(glossary["names"], ensure_ascii=False)
        + "\n\nslang (อังกฤษ -> ตัวเลือกคำไทย):\n" + json.dumps(glossary.get("slang", {}), ensure_ascii=False)
    )

    model = args.model or DEFAULT_MODELS[args.provider]
    cls = GeminiTranslator if args.provider == "gemini" else ClaudeTranslator
    translate = cls(model, system)

    con = open_db()
    done = {r[0] for r in con.execute("SELECT id FROM done_segments")}
    skipped = {r[0] for r in con.execute("SELECT id FROM skipped_segments")}
    by_id = {s["id"]: s for s in segments}
    todo = [s for s in segments if s["id"] not in done and s["id"] not in skipped]
    print(f"[{args.provider}:{translate.model}] {len(segments)} segments, เหลือ {len(todo)}"
          + (f" (ข้ามเพราะซ้ำไปแล้ว {len(skipped)})" if skipped else ""))

    # ---- ข้ามช่วงที่ซ้ำกับของที่แปลไปแล้ว (คลิปคนละช่องที่เล่นเนื้อเรื่องเดียวกัน)
    known_grams, known_texts = set(), set(
        r[0] for r in con.execute("SELECT en_normalized FROM translations")
    )
    for sid in done:
        if sid in by_id:
            known_grams |= grams(by_id[sid]["text"])

    def covered(seg):
        g = grams(seg["text"])
        if not g:   # ช่วงสั้นกว่า 6 คำ: ซ้ำเมื่อข้อความตรงกับบรรทัดที่มีในคลัง
            return normalize(seg["text"]) in known_texts
        return len(g & known_grams) / len(g) >= args.dupe_threshold

    size = args.batch or DEFAULT_BATCH[args.provider]
    if args.skip_dupes:
        est = sum(1 for s in todo if not covered(s))
        print(f"ข้ามช่วงที่ซ้ำ >= {args.dupe_threshold:.0%}: ต้องแปลจริงประมาณ {est} segments "
              f"(~{-(-est // size)} ครั้ง) จาก {len(todo)}")
    else:
        print(f"ชุดละ {size} segments -> ต้องส่งประมาณ {-(-len(todo) // size)} ครั้ง")

    def batches():
        batch, n_skip = [], 0
        for seg in todo:
            if args.skip_dupes and covered(seg):
                con.execute("INSERT OR IGNORE INTO skipped_segments VALUES (?)", (seg["id"],))
                n_skip += 1
                continue
            batch.append(seg)
            if len(batch) == size:
                con.commit()
                yield batch, n_skip
                batch, n_skip = [], 0
        con.commit()
        if batch:
            yield batch, n_skip
        elif n_skip:
            print(f"  ข้าม {n_skip} segments ที่ซ้ำ")

    tin = tout = n_batches = n_fail = 0
    for batch, n_skip in batches():
        if args.limit and n_batches >= args.limit:
            break
        if n_skip:
            print(f"  ข้าม {n_skip} segments ที่ซ้ำ")
        first, last = batch[0]["id"], batch[-1]["id"]
        context = [by_id[j] for j in range(max(0, first - CONTEXT_SEGMENTS), first) if j in by_id]
        user = build_user(batch, context)

        result = None
        exhausted = False
        for attempt in range(6):
            try:
                print(f"  กำลังแปล segments {first}-{last} ... (รอได้ 1-3 นาที)", flush=True)
                t0 = time.time()
                text, n_in, n_out = translate(user)
                print(f"  ได้คำตอบใน {time.time() - t0:.0f} วิ", flush=True)
                result = json.loads(text)["segments"]
                break
            except RetryableError as e:
                if attempt == 5:
                    exhausted = True
                    break
                wait = 30 * (attempt + 1)
                print(f"  {e} -> รอ {wait} วิแล้วลองใหม่")
                time.sleep(wait)
            except (BatchFailed, json.JSONDecodeError, KeyError) as e:
                print(f"  [fail] segments {first}-{last}: {e}")
                break
        n_batches += 1
        if exhausted:
            # ลองครบ 6 รอบแล้วยังโควตาเต็ม/เซิร์ฟเวอร์ล่ม -> หยุด (ให้ run_overnight รอแล้วรันใหม่)
            con.close()
            print("โควตาเต็มหรือเซิร์ฟเวอร์ไม่ตอบ — หยุดไว้ก่อน รันใหม่ภายหลังจะทำต่อจากเดิม")
            sys.exit(3)
        if result is None:
            n_fail += 1
            if n_fail >= 3 and n_fail == n_batches:
                sys.exit("ล้มเหลว 3 ชุดแรกติดกัน — ตรวจ key / ชื่อโมเดล / โควตา ก่อนรันใหม่")
            continue

        added = n_media = 0
        for seg in result:
            src = by_id.get(seg.get("id"))
            if src is None:
                continue
            for ln in seg.get("lines", []):
                if ln.get("kind") == "media":   # วิทยุ/ทีวี/โฆษณา/เพลง -> ไม่เก็บเข้าคลัง
                    n_media += 1
                    continue
                key = normalize(ln.get("en", ""))
                th = ln.get("th", "").strip()
                if len(key) < 2 or not th:
                    continue
                # ถ้าประโยคนี้เคยถูกแปลสดไว้ (source = live-...) ให้ทับด้วยคำแปลจากชุดที่มีบริบทฉาก
                cur = con.execute(
                    "INSERT INTO translations VALUES (?,?,?,?,?) "
                    "ON CONFLICT(en_normalized) DO UPDATE SET en_text=excluded.en_text, "
                    "th_text=excluded.th_text, source=excluded.source, start_sec=excluded.start_sec "
                    "WHERE translations.source LIKE 'live-%'",
                    (key, ln["en"].strip(), th, src["source"], src["start"]),
                )
                added += cur.rowcount
                known_texts.add(key)
            con.execute("INSERT OR IGNORE INTO done_segments VALUES (?)", (seg["id"],))
            known_grams.update(grams(src["text"]))
        con.commit()
        tin += n_in
        tout += n_out
        media = f", ตัดวิทยุ/ทีวี/เพลง {n_media}" if n_media else ""
        print(f"  segments {first}-{last}: +{added} บรรทัด{media} (in {tin:,} / out {tout:,} tokens รวม)")
        time.sleep(translate.delay)

    total = con.execute("SELECT COUNT(*) FROM translations").fetchone()[0]
    n_done = con.execute("SELECT COUNT(*) FROM done_segments").fetchone()[0]
    n_skip = con.execute("SELECT COUNT(*) FROM skipped_segments").fetchone()[0]
    left = len(segments) - n_done - n_skip
    print(f"คลังคำแปลตอนนี้: {total} บรรทัด | แปลแล้ว {n_done}, ข้ามเพราะซ้ำ {n_skip}, "
          f"เหลือ {left} segments -> {DB_PATH}")
    con.close()


if __name__ == "__main__":
    main()
