"""จับภาพกรอบซับบนจอ -> แต่งภาพ -> Windows OCR (ในเครื่อง ไม่ใช้เน็ต)

from app.ocr_engine import OcrEngine
ocr = OcrEngine()
text = ocr.read_region({"left": 300, "top": 900, "width": 1300, "height": 150})   # None = ซับไม่เปลี่ยน

ทดสอบกับภาพนิ่ง:  python -m app.ocr_engine ภาพ.png
"""
import asyncio
import re
import time

import cv2
import mss
import numpy as np
from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.ocr import OcrEngine as WinOcr
from winrt.windows.storage.streams import DataWriter

MIN_TEXT_HEIGHT = 32     # ตัวอักษรเล็กกว่านี้ Windows OCR อ่านพลาดบ่อย -> ขยายภาพก่อน
SMALL_CHANGE = 24        # จุดสว่าง (หลังย่อ 2x2) ที่เปลี่ยนอย่างน้อยเท่านี้ = อาจมีซับสั้นๆ ขึ้น/หาย (~ครึ่งตัวอักษร)
MAX_HOLD = 0.15          # ภาพพื้นหลังระยิบระยับแต่ข้อความเดิม -> เว้นช่วง OCR ของการเปลี่ยนเล็กๆ ได้นานสุดเท่านี้ (วินาที)


def new_screen_grabber():
    """ตัวจับภาพจอของ mss (ต้องสร้างในเธรดที่ใช้งาน)"""
    return (getattr(mss, "MSS", None) or mss.mss)()


def _create_engine():
    for tag in ("en-US", "en-GB", "en"):
        try:
            eng = WinOcr.try_create_from_language(Language(tag))
        except Exception:
            eng = None
        if eng is not None:
            return eng
    try:                                    # เครื่องที่ลงภาษาอังกฤษแบบอื่นไว้ (en-AU, en-CA ...)
        for lang in WinOcr.available_recognizer_languages:
            if lang.language_tag.lower().startswith("en"):
                eng = WinOcr.try_create_from_language(lang)
                if eng is not None:
                    return eng
    except Exception:
        pass
    raise RuntimeError("Windows OCR ภาษาอังกฤษไม่พร้อมใช้ (Settings > Time & language > Language เพิ่ม English)")


class OcrEngine:
    def __init__(self, brightness=180, change_threshold=0.004):
        """brightness: ซับเกมเป็นตัวขาวขอบดำ -> จุดที่สว่างกว่านี้ (0-255) ถือเป็นตัวซับ
        change_threshold: สัดส่วนจุดที่เปลี่ยนถึงจะถือว่า "เปลี่ยนเยอะ" (OCR ทันทีเสมอ)"""
        self.brightness = brightness
        self.change_threshold = change_threshold
        self.engine = _create_engine()
        self.loop = asyncio.new_event_loop()
        self.sct = None
        self.last_text = ""
        self.last_ms = 0.0
        self.reset()

    def warm_up(self):
        """OCR ครั้งแรกของโปรแกรมช้า (~1 วินาที เพราะ Windows ต้องโหลดตัวอ่านตัวหนังสือ)
        -> อ่านภาพตัวอย่างทิ้งไว้ก่อนตั้งแต่ตอนเปิดโปรแกรม ซับประโยคแรกจะได้ไม่ช้า"""
        try:
            img = np.full((72, 420), 255, np.uint8)
            cv2.putText(img, "Warm up 123", (14, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.4, 0, 3, cv2.LINE_AA)
            self._recognize(img)
        except Exception:
            pass

    def reset(self):
        """ลืมภาพก่อนหน้า (เปลี่ยนกรอบ/ตั้งค่า) -> รอบถัดไป OCR ใหม่แน่นอน"""
        self.ref = None          # mask ของเฟรมที่ OCR ล่าสุด (ย่อ 2x2)
        self.hold = 0.0          # ช่วงเว้น OCR ของการเปลี่ยนเล็กๆ
        self.last_ocr_at = 0.0
        self.last_text = ""

    @property
    def prev_mask(self):         # ชื่อเดิม (โค้ดเก่าตั้งเป็น None เพื่อบังคับ OCR ใหม่)
        return self.ref

    @prev_mask.setter
    def prev_mask(self, v):
        self.ref = v

    # ---------- ภาพ ----------
    def grab_bgra(self, region):
        if self.sct is None:          # mss ต้องสร้างในเธรดที่ใช้งาน
            self.sct = new_screen_grabber()
        return np.asarray(self.sct.grab(region))               # (สูง, กว้าง, 4) BGRA ไม่คัดลอกข้อมูล

    def grab(self, region):
        return self.grab_bgra(region)[:, :, :3]                # BGR

    def changed(self, mask, now=None):
        """ภาพเปลี่ยนจากเฟรมที่ OCR ล่าสุดมากพอไหม
        - เปลี่ยนเยอะ (ซับใหม่ทั้งบรรทัด) -> True ทันที
        - เปลี่ยนนิดเดียว (ซับสั้นๆ เช่น "No." หรือพื้นหลังระยิบ) -> True เว้นแต่เพิ่ง OCR ไปแล้วได้ข้อความเดิมซ้ำๆ"""
        small = mask[::2, ::2]
        ref = self.ref
        if ref is None or ref.shape != small.shape:
            return True
        diff = np.count_nonzero(ref != small)
        if diff < min(SMALL_CHANGE, self.change_threshold * small.size):
            return False
        if diff >= self.change_threshold * small.size:
            return True
        return (now if now is not None else time.perf_counter()) - self.last_ocr_at >= self.hold

    # ---------- OCR ----------
    def _recognize(self, gray):
        h, w = gray.shape
        writer = DataWriter()
        writer.write_bytes(np.ascontiguousarray(gray).tobytes())
        # Windows OCR รับภาพเทา 8 บิตตรงๆ ได้ (ผลเท่ากับ BGRA แต่ไม่ต้องขยายข้อมูล 4 เท่า)
        bmp = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.GRAY8, w, h)
        return self.loop.run_until_complete(self.engine.recognize_async(bmp))

    def recognize(self, gray):
        return clean(" ".join(ln.text for ln in self._recognize(gray).lines))

    def recognize_lines(self, gray):
        """OCR ภาพสีเทา คืน [(ข้อความ, x, y, w, h)] ต่อบรรทัด"""
        out = []
        for ln in self._recognize(gray).lines:
            rs = [wd.bounding_rect for wd in ln.words]
            if not rs:
                continue
            x0 = min(r.x for r in rs); y0 = min(r.y for r in rs)
            x1 = max(r.x + r.width for r in rs); y1 = max(r.y + r.height for r in rs)
            out.append((clean(ln.text), x0, y0, x1 - x0, y1 - y0))
        return out

    def read_image(self, bgr):
        return self.read_gray(bgr)

    def _text_box(self, mask):
        """กรอบ (x0, y0, x1, y1) ที่ครอบจุดสว่างทั้งหมด + ขอบเผื่อ หรือ None ถ้าครอบแล้วไม่ช่วยให้ภาพเล็กลง"""
        h, w = mask.shape
        rows = np.flatnonzero(np.count_nonzero(mask, axis=1) >= 2)
        cols = np.flatnonzero(np.count_nonzero(mask, axis=0) >= 2)
        if not len(rows) or not len(cols):
            return None
        th = int(rows[-1] - rows[0]) + 1         # ความสูงของแถบตัวหนังสือ
        pad_y, pad_x = max(12, th // 2), max(24, th * 2)
        y0, y1 = max(0, int(rows[0]) - pad_y), min(h, int(rows[-1]) + 1 + pad_y)
        x0, x1 = max(0, int(cols[0]) - pad_x), min(w, int(cols[-1]) + 1 + pad_x)
        if y1 - y0 < 48:                         # OCR ต้องการภาพสูงพอ
            mid = (y0 + y1) // 2
            y0, y1 = max(0, mid - 24), min(h, mid + 24)
        min_w = max(320, th * 16)                # ซับสั้นมาก ("No.") ต้องเหลือพื้นที่รอบๆ ไม่งั้น OCR อ่านไม่ออก
        if x1 - x0 < min_w:
            mid = (x0 + x1) // 2
            x0, x1 = max(0, mid - min_w // 2), min(w, mid + min_w // 2)
        if (y1 - y0) * (x1 - x0) > 0.85 * h * w:
            return None
        return x0, y0, x1, y1

    def read_gray(self, bgr, gray=None, mask=None):
        """OCR ภาพสีเทาตรงๆ (ขยายตามความสูงจริงของกรอบ) แล้วเก็บเฉพาะบรรทัดที่อยู่กึ่งกลาง = ซับ
        (แม่นกว่าการ threshold เพราะซับเกมเป็นสีเทาอ่อน ความสว่างไม่เท่ากัน)
        mask: จุดที่สว่างพอจะเป็นตัวซับ -> ส่ง OCR เฉพาะแถบที่มีจุดสว่าง (ภาพเล็กลง OCR เร็วขึ้น 2-4 เท่า)"""
        if gray is None:
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        h, full_w = gray.shape
        scale = 2.0 if h < 140 else 1.5 if h < 220 else 1.0
        x_off = 0
        box = self._text_box(mask) if mask is not None else None
        if box:
            x_off = box[0]
            gray = gray[box[1]:box[3], box[0]:box[2]]
        big = gray if scale == 1.0 else cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
        keep = []
        for txt, x, y, w, _ in self.recognize_lines(big):
            if not txt:
                continue
            center = x_off + (x + w / 2) / scale
            if abs(center / full_w - 0.5) > 0.26:     # ซับอยู่กลาง ตัวหนังสือที่เยื้องข้างมาก = พื้นหลัง/UI
                continue
            keep.append((y, txt))
        return clean(" ".join(t for _, t in sorted(keep)))

    def read_region(self, region):
        """คืนข้อความใหม่, "" ถ้าไม่มีซับ, หรือ None ถ้าภาพไม่เปลี่ยนจากครั้งก่อน"""
        t0 = time.perf_counter()
        gray = cv2.cvtColor(self.grab_bgra(region), cv2.COLOR_BGRA2GRAY)
        mask = gray >= self.brightness
        if not self.changed(mask, t0):
            return None
        self.ref = mask[::2, ::2]
        text = self.read_gray(None, gray=gray, mask=mask) if np.count_nonzero(mask) >= 30 else ""
        self.last_ocr_at = time.perf_counter()
        self.last_ms = (self.last_ocr_at - t0) * 1000
        if text == self.last_text:
            self.hold = min(MAX_HOLD, self.hold + 0.05)     # อ่านแล้วได้ข้อความเดิม = แค่พื้นหลังขยับ
            return None
        self.hold = 0.0
        self.last_text = text
        return text


def clean(text):
    text = text.replace("|", "I").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"\s+", " ", text).strip()
    # ตัดเศษอักขระที่ OCR อ่านจากขอบ/เงา (ไม่มีตัวอักษรเลย)
    return text if re.search(r"[A-Za-z]{2,}", text) else ""


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    ocr = OcrEngine()
    for path in sys.argv[1:]:
        img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)     # รองรับชื่อไฟล์ภาษาไทย
        t = time.perf_counter()
        txt = ocr.read_image(img)
        print(f"{path}: {txt!r}  ({(time.perf_counter() - t) * 1000:.0f} ms)")
