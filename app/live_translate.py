"""แปลสดสำรอง: ประโยคที่ไม่มีในคลัง -> แปลเบื้องหลัง แล้วเก็บเข้า data/translations.db

ลำดับตัวแปล (ฟรีทั้งหมด):
  1. Gemini Flash Lite  (ใส่คีย์ในหน้าตั้งค่า หรือตั้ง GEMINI_API_KEY, โควตาฟรี ~500 ครั้ง/วัน, สำนวนดี รู้จักชื่อตัวละคร)
  2. MyMemory           (ไม่ต้องใช้คีย์, ~5,000 ตัวอักษร/วัน, แปลตรงตัว สำนวนแข็ง — ปิดไว้เป็นค่าเริ่มต้น เปิดได้ในหน้าตั้งค่า)

ใช้:
    from app.live_translate import SubtitleTranslator
    tr = SubtitleTranslator()
    th = tr.get("Jason: Where you going?", on_ready=lambda en, th, src: print(th))
    # th = คำแปลจากคลังทันที หรือ None ถ้ากำลังแปลสดอยู่ (ผลจะมาทาง on_ready)
"""
import json
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from app.applog import log
from app.matcher import DB_PATH, ROOT, Matcher, normalize

GLOSSARY = ROOT / "data" / "glossary.json"
GEMINI_MODEL = "gemini-3.1-flash-lite"

PROMPT = """แปลซับไตเติลเกมแอ็กชันอาชญากรรมเรต 18+ จากอังกฤษเป็นไทย 1 บรรทัด ตาม style guide
- ข้อความมาจาก OCR อาจสะกดผิดเล็กน้อย ให้เดาคำที่ถูก
- ถ้ามีชื่อคนพูดนำหน้า (เช่น "Jason:") ใช้ช่วยเลือกสรรพนาม แต่ไม่ต้องใส่ชื่อในคำแปล
- ชื่อเฉพาะใช้ตาม glossary: {glossary}
- สแลง: {slang}
ตอบเฉพาะคำแปลไทยบรรทัดเดียว ไม่ต้องอธิบาย

<style_guide>
""" + (ROOT / "data" / "style_guide.md").read_text(encoding="utf-8").replace("{", "{{").replace("}", "}}") + """
</style_guide>

ข้อความ: {text}"""

# ข้อความที่น่าจะเป็นบทพูดจริง (ไม่ใช่ขยะจาก OCR/UI): ตัวอักษรอังกฤษปกติเป็นส่วนใหญ่ ไม่มีสัญลักษณ์เพี้ยน
LETTERS = re.compile(r"[A-Za-z]")
WEIRD_CHARS = re.compile(r"[^\x20-\x7E\u2018\u2019\u201C\u201D]")
NORMAL_WORD = re.compile(r"^[A-Za-z]+(?:['’-][A-Za-z]+)*[.,?!:;\"']*$")
THAI_CHAR = re.compile(r"[\u0E00-\u0E7F]")
UI_NOISE = re.compile(r"Ctrl\+Alt|GTA6ThaiSub|PS Remote Play", re.I)


def looks_like_dialogue(text: str) -> bool:
    if not text or UI_NOISE.search(text):
        return False
    body = re.sub(r"^\s*[A-Z][\w'.\- ]{0,24}:\s*", "", text).strip()
    if len(normalize(body).split()) < 2:
        return False
    visible = [c for c in body if not c.isspace()]
    if not visible:
        return False
    # ตัดข้อความที่มีอักขระประหลาดจาก OCR อ่านภาษาไทยเป็นละติน (เช่น å ä « •)
    if len(WEIRD_CHARS.findall(body)) / len(visible) > 0.04:
        return False
    if sum(1 for c in visible if LETTERS.match(c)) / len(visible) <= 0.72:
        return False
    # คำส่วนใหญ่ต้องเป็นคำอังกฤษปกติ ไม่ใช่ตัวอักษรปนตัวเลข/ตัวใหญ่สลับมั่ว (เช่น JL5uaIMN)
    words = body.split()
    good = sum(1 for w in words if NORMAL_WORD.match(w) and not re.search(r"[a-z][A-Z]", w))
    return good / len(words) >= 0.65


class GeminiLive:
    name = "live-gemini"

    def __init__(self, key=""):
        self.key = (key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
        self.ok = bool(self.key)
        self.client = None
        self.blocked_until = 0.0  # โควตาเต็ม -> พักไว้ก่อน
        self.last_error = ""
        self._init_lock = threading.Lock()

    def _ensure(self):
        """ต่อ Gemini ตอนใช้ครั้งแรก (อยู่ในเธรดเบื้องหลัง โปรแกรมจึงเปิดได้ทันทีแม้เน็ตช้า)"""
        with self._init_lock:
            if self.client is not None:
                return
            from google import genai
            from google.genai import types

            g = json.loads(GLOSSARY.read_text(encoding="utf-8"))
            self.glossary = json.dumps(g.get("names", {}), ensure_ascii=False)
            self.slang = json.dumps(g.get("slang", {}), ensure_ascii=False)
            client = genai.Client(api_key=self.key, http_options=types.HttpOptions(timeout=20_000))
            self.model = self._resolve(client, GEMINI_MODEL)
            off = ["HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
                   "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT"]
            self.config = types.GenerateContentConfig(
                max_output_tokens=300,
                thinking_config=types.ThinkingConfig(thinking_level="MINIMAL"),
                safety_settings=[types.SafetySetting(category=c, threshold="BLOCK_NONE") for c in off],
            )
            self.client = client

    @staticmethod
    def _resolve(client, wanted):
        try:
            names = [m.name.removeprefix("models/") for m in client.models.list()]
        except Exception:
            return wanted
        if wanted in names:
            return wanted
        pref = sorted((n for n in names if n.startswith(wanted)), key=len)
        return pref[0] if pref else wanted

    def __call__(self, text):
        if not self.ok or time.time() < self.blocked_until:
            return None
        try:
            self._ensure()
            resp = self.client.models.generate_content(
                model=self.model,
                contents=PROMPT.format(glossary=self.glossary, slang=self.slang, text=text),
                config=self.config,
            )
        except Exception as e:
            msg = str(e)
            if "429" in msg:
                self.blocked_until = time.time() + 60
                self.last_error = "โควตา Gemini เต็มชั่วคราว (จะลองใหม่เอง)"
            elif "API key" in msg or "API_KEY" in msg or "401" in msg or "403" in msg:
                self.blocked_until = time.time() + 300
                self.last_error = "คีย์ Gemini ใช้ไม่ได้ ตรวจคีย์อีกครั้ง"
            else:
                self.last_error = f"ต่อ Gemini ไม่ได้ ({type(e).__name__})"
            log(f"[live] gemini: {type(e).__name__}: {msg[:160]}")
            return None
        self.last_error = ""
        th = (resp.text or "").strip().splitlines()
        out = th[0].strip() if th else None
        return out if (out and THAI_CHAR.search(out)) else None


class MyMemoryLive:
    name = "live-mymemory"

    def __init__(self):
        self.blocked_until = 0.0

    def __call__(self, text):
        if time.time() < self.blocked_until:
            return None
        try:
            from deep_translator import MyMemoryTranslator

            th = MyMemoryTranslator(source="en-US", target="th-TH").translate(text)
        except Exception as e:
            self.blocked_until = time.time() + 300
            log(f"[live] mymemory: {type(e).__name__}: {str(e)[:120]}")
            return None
        if not th or "MYMEMORY WARNING" in th.upper() or not THAI_CHAR.search(th):
            if th and "MYMEMORY WARNING" in th.upper():
                self.blocked_until = time.time() + 3600
            return None
        return th.strip()


class SubtitleTranslator:
    """คลังคำแปล + แปลสดสำรอง + บันทึกคำแปลใหม่เข้าคลังอัตโนมัติ"""

    def __init__(self, db_path=DB_PATH, cutoff=83, gemini_key="", use_mymemory=True):
        self.db_path = db_path
        self.matcher = Matcher(db_path, cutoff=cutoff, log_missing=False)
        self.gemini = None
        self.mymemory = MyMemoryLive()
        self.engines = []
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.pending = set()
        self.failed = {}            # key -> เวลาที่ล้มเหลว (กันยิงซ้ำถี่ๆ)
        self.lock = threading.Lock()
        self.configure(gemini_key, use_mymemory)

    def configure(self, gemini_key="", use_mymemory=True):
        """เลือกตัวแปลสดที่จะใช้ (เรียกซ้ำได้เมื่อผู้ใช้เปลี่ยนคีย์/ตัวเลือกในหน้าตั้งค่า)"""
        new = GeminiLive(gemini_key)
        if self.gemini is None or new.key != self.gemini.key:
            self.gemini = new
            with self.lock:
                self.failed.clear()
        self.engines = ([self.gemini] if self.gemini.ok else []) + ([self.mymemory] if use_mymemory else [])

    def status(self):
        """(ข้อความสถานะตัวแปลสดสำหรับแสดงใน UI, พร้อมใช้ไหม)"""
        if self.gemini.ok and self.gemini.last_error:
            return self.gemini.last_error, False
        names = (["Gemini"] if self.gemini.ok else []) + (["MyMemory"] if self.mymemory in self.engines else [])
        if not names:
            return "ยังไม่มีตัวแปลสด — ใส่คีย์ Gemini หรือเปิด MyMemory", False
        return "ตัวแปลสดพร้อมใช้: " + " แล้วค่อย ".join(names), True

    def get(self, ocr_text, on_ready=None):
        """คืนคำแปลไทยทันทีถ้ามีในคลัง; ถ้าไม่มี -> ส่งแปลสดเบื้องหลังแล้วคืน None"""
        hit = self.matcher.lookup(ocr_text)
        if hit:
            return hit["th"]
        if not self.engines or not looks_like_dialogue(ocr_text):
            return None
        key = normalize(ocr_text)
        with self.lock:
            if key in self.pending or time.time() - self.failed.get(key, 0) < 600:
                return None
            self.pending.add(key)
        self.pool.submit(self._live, ocr_text, key, on_ready)
        return None

    def _live(self, ocr_text, key, on_ready):
        en = re.sub(r"^\s*[A-Z][\w'.\- ]{0,24}:\s*", "", ocr_text).strip()
        try:
            for engine in list(self.engines):
                th = engine(en)
                if th:
                    self._save(key, en, th, engine.name)
                    log(f"[live] {engine.name}: {en} -> {th}")
                    if on_ready:
                        on_ready(en, th, engine.name)
                    return
            with self.lock:
                self.failed[key] = time.time()
            try:
                with (ROOT / "data" / "missing_lines.txt").open("a", encoding="utf-8") as f:
                    f.write(en + "\n")
            except OSError:
                pass
        except Exception as e:
            log(f"[live] {type(e).__name__}: {e}")
        finally:
            with self.lock:
                self.pending.discard(key)

    def _save(self, key, en, th, source):
        con = sqlite3.connect(self.db_path, timeout=10)
        con.execute(
            "INSERT OR IGNORE INTO translations (en_normalized, en_text, th_text, source, start_sec) "
            "VALUES (?,?,?,?,?)", (key, en, th, source, None)
        )
        con.commit()
        con.close()
        with self.lock:
            self.matcher.add(key, en, th)


if __name__ == "__main__":
    import sys

    done = threading.Event()
    tr = SubtitleTranslator()
    for line in sys.argv[1:]:
        th = tr.get(line, on_ready=lambda en, th, src: done.set())
        print(f"{line!r} -> {th if th else '(ไม่มีในคลัง กำลังแปลสด...)'}")
        if th is None:
            done.wait(25)
            done.clear()
