"""การตั้งค่าโปรแกรม (data/settings.json) + โค้ดแชร์การตั้งค่าให้เพื่อน"""
import base64
import json
import zlib

from app.matcher import ROOT

PATH = ROOT / "data" / "settings.json"
CODE_PREFIX = "GTHSUB1:"
VERSION = 2
# ค่าที่ผูกกับเครื่อง/จอ/บัญชีของแต่ละคน -> ไม่ใส่ในโค้ดแชร์
LOCAL_KEYS = {"region", "screen_place", "screen_pos","live_translate", "gemini_key", "use_mymemory", "auto_update", "report_missed", "_v"}

DEFAULTS = {
    "_v": VERSION,
    "region": None,              # {"left","top","width","height"} พิกเซลจริงของจอ
    # หน้าตาซับ
    "font_family": "Leelawadee UI",
    "font_size": 30,
    "font_weight": 700,          # 400 ปกติ / 700 หนา / 900 หนามาก
    "text_color": "#FFFFFF",
    "outline_color": "#000000",
    "outline_width": 4,
    "shadow": True,
    "box_opacity": 0,            # 0-255 กล่องพื้นหลังดำ (0 = ไม่มีกล่อง)
    "prev_lines": 1,             # จำนวนประโยคก่อนหน้าที่แสดงเล็กๆ ด้านบน (0 = ปิด)
    "prev_scale": 0.62,
    "show_english": False,       # แสดงอังกฤษต้นฉบับเล็กๆ ใต้ไทย
    "show_speaker": True,        # แสดงชื่อคนพูด เช่น "เจสัน:"
    "speaker_color": "#FFD166",
    # ตำแหน่ง
    "position": "cover",         # cover = ทับซับอังกฤษ / above = เหนือกรอบซับ
    "offset_y": 0,               # เลื่อนขึ้น(-)/ลง(+) พิกเซล
    "width_scale": 1.15,         # ความกว้างหน้าต่างซับเทียบกับกรอบซับ
    "screen_place": "follow",    # ซับบนจอคอม: follow = ตามกรอบซับ / custom = ที่ลากย้ายเอง / hidden = ไม่แสดง
    "screen_pos": None,          # [x, y] มุมซ้ายบนของซับ เมื่อ screen_place = custom
    "obs_mode": False,           # True = พื้นสีทึบให้ OBS ดูดได้ (Chroma key)
    "obs_color": "#00FF00",
    "obs_screen": -1,            # หน้าต่างซับแยก: -1 = หน้าต่างปกติ, 0,1,.. = เต็มจอบนจอที่เลือก (แบบ Fullscreen Projector)
    "web_feed": False,           # เปิดหน้าเว็บซับพื้นโปร่งใส (ใส่ใน TikTok LIVE Studio แบบ "ลิงก์" / OBS Browser Source)
    "stream_visible": False,     # True = ให้ OBS/โปรแกรมจับภาพเห็นซับไทยบนจอ (สตรีมด้วย Display/Game Capture)
    # การทำงาน
    "fps": 30,                   # เช็กซับบนจอกี่ครั้งต่อวินาที (15 ประหยัด / 30 ปกติ / 60 ไวสุด)
    "brightness": 120,           # ความสว่างขั้นต่ำของตัวซับ (ลดลงถ้าอ่านไม่ออก เพิ่มถ้าอ่านพื้นหลังมั่ว)
    "hide_after_ms": 600,        # ซับอังกฤษหายไปนานเท่านี้ -> ซ่อนซับไทย
    "show_untranslated": False,  # ไม่มีในคลังและแปลสดยังไม่เสร็จ -> แสดงอังกฤษไปก่อน
    # แปลสด
    "live_translate": True,
    "gemini_key": "",            # คีย์ Gemini ของผู้ใช้ (เก็บในเครื่องนี้เท่านั้น)
    "use_mymemory": False,       # ตัวแปลสำรองฟรี ไม่ต้องมีคีย์ แต่สำนวนแข็ง
    # อัปเดตคลังคำแปลจาก GitHub Releases ตอนเปิดโปรแกรม
    "auto_update": True,
    # ส่งประโยคที่ยังไม่มีคำแปลให้ผู้ทำคลัง (Discord/Google Sheet ตาม data/report_target.json)
    "report_missed": True,
}


def load():
    s = dict(DEFAULTS)
    if PATH.exists():
        try:
            saved = json.loads(PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        old = saved.get("_v", 1)
        s.update(saved)
        if old < 2 and s.get("fps") == 20:      # 20 คือค่าเริ่มต้นเดิม (ตั้งเองไม่ได้) -> ใช้ค่าเริ่มต้นใหม่
            s["fps"] = DEFAULTS["fps"]
        s["_v"] = VERSION
    return s


def save(s):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(PATH)                           # เขียนไฟล์ใหม่แล้วสลับ กันไฟล์พังถ้าปิดเครื่องกลางคัน


def export_code(s):
    data = {k: v for k, v in s.items() if k not in LOCAL_KEYS and k in DEFAULTS}
    raw = zlib.compress(json.dumps(data, separators=(",", ":")).encode("utf-8"), 9)
    return CODE_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii")


def import_code(s, code):
    """คืน dict ใหม่ หรือโยน ValueError ถ้าโค้ดไม่ถูกต้อง"""
    code = code.strip()
    if not code.startswith(CODE_PREFIX):
        raise ValueError("ไม่ใช่โค้ดการตั้งค่าของโปรแกรมนี้")
    try:
        data = json.loads(zlib.decompress(base64.urlsafe_b64decode(code[len(CODE_PREFIX):])))
    except Exception as e:
        raise ValueError("โค้ดเสียหรือคัดลอกมาไม่ครบ") from e
    out = dict(s)
    out.update({k: v for k, v in data.items() if k in DEFAULTS and k not in LOCAL_KEYS})
    return out
