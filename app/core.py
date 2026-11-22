"""แกนของโปรแกรม: OCR ซับอังกฤษบนจอ -> ค้นคลังคำแปล (+แปลสด) -> แสดงซับไทยทับเกม

(ไฟล์นี้โหลดไลบรารีหนักทั้งหมด จึงถูกเรียกจาก app/main.py หลังหน้าต่างโหลดขึ้นแล้ว)
"""
import collections
import ctypes
import ctypes.wintypes
import json
import re
import threading
import time
import traceback

from rapidfuzz import fuzz, process

from PyQt6.QtCore import QObject, QThread, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu, QMessageBox, QSystemTrayIcon

from app import settings as cfg
from app import theme as T
from app.applog import log
from app.control_panel import ControlPanel, auto_region, detect_subtitle_region, monitors, screen_for_monitor
from app.live_translate import SubtitleTranslator, looks_like_dialogue
from app.matcher import ROOT
from app.ocr_engine import OcrEngine
from app.overlay_ui import HistoryWindow, ObsFeed, RegionSelector, SettingsDialog, SubtitleOverlay, game_monitor
from app.widgets import polish_popup

SPEAKER = re.compile(r"^\s*([A-Z][\w'.\-]*(?: [A-Z][\w'.\-]*){0,2}):\s*(.+)$", re.S)
TURN_START = re.compile(r"^(?:[-–—]\s*)?[A-Z][\w'.\-]*(?: [A-Z][\w'.\-]*){0,2}:\s+\S")   # บรรทัดขึ้นต้นด้วย "ชื่อ:"
DASH = re.compile(r"^[-–—]\s*(?=\S)")                                                     # "- ประโยค" = อีกคนพูด
SENTENCE = re.compile(r"(?<=[.?!])\s+")
JUNK_CHAR = re.compile(r"(?<!\S)[B-HJ-Zb-z0Oo@&*#%$^~=+<>]{1}(?!\S)")
CLAUSE = re.compile(r"(?<=[,;—–])\s+")
SAME_LINE = 70          # ความเหมือนของข้อความ (0-100) ที่ถือว่าเป็นซับประโยคเดิม แค่ OCR อ่านเพี้ยน
EMPTY_GRACE_MS = 220    # ซับหายจากจอต่อเนื่องนานเท่านี้ก่อนถึงจะเริ่มซ่อน (กันซับสั้นๆ ที่ OCR อ่านพลาดเฟรมเดียว)
CONFIRM_MS = 160        # ซับใหม่ที่ "คล้ายซับเดิม" ต้องไม่มีเฟรมซับเดิมแทรกภายในเวลานี้ ถึงจะเปลี่ยน (กัน OCR อ่านเพี้ยน)
UNTRANSLATED_CONFIRM_MS = 600   # ข้อความที่ไม่มีคำแปลเลย ต้องค้างนานเท่านี้ถึงจะล้างซับไทยที่แสดงอยู่


def flat(text):
    return " ".join(text.split())


def _fix_colons(turn):
    """โคลอน/อัฒภาคที่อยู่หลังชื่อคนพูดแล้ว = OCR อ่านจุดเพี้ยน ("I'm driving: Obey.") -> เปลี่ยนเป็นจุด
    ไม่งั้นตัวค้นคลังจะเข้าใจว่าข้อความหน้าโคลอนเป็นชื่อคนพูดแล้วตัดทิ้ง"""
    m = SPEAKER.match(turn)
    head, body = (turn[:m.start(2)], m.group(2)) if m else ("", turn)
    return head + re.sub(r"\s*[:;]+(?=\s|$)", ".", body)


def split_turns(text, names=()):
    """แยกข้อความจากจอเป็นบทพูดของแต่ละคน (ซับเกมอาจขึ้นหลายคนพร้อมกัน เช่น
    "Jason: Hey." บรรทัดหนึ่ง "Lucia: What?" อีกบรรทัด) — บรรทัดที่ไม่มีชื่อ = ประโยคเดิมที่ขึ้นบรรทัดใหม่"""
    turns = []
    for line in text.split("\n"):
        line = line.strip().lstrip(".,;:'\"`~•·*«»").strip()       # เศษจุด/เงาหน้าบรรทัดจาก OCR
        if not line:
            continue
        if not turns or TURN_START.match(line) or DASH.match(line):
            turns.append(DASH.sub("", line))
        else:
            turns[-1] += " " + line
    # ตัวอักษรเดี่ยวที่หลุดมาจากขอบ/เงา (เช่น "No. O") -> ตัดทิ้ง ไม่งั้นไปตรงกับประโยคอื่นในคลัง
    if names:                                  # "Lucia; Yeah." / "Lucia. Yeah." = OCR อ่านโคลอนหลังชื่อเพี้ยน
        lead = re.compile(r"^(" + "|".join(re.escape(n) for n in names) + r")\s*[;.,]\s+")
        turns = [lead.sub(r"\1: ", t) for t in turns]
    turns = [" ".join(JUNK_CHAR.sub("", t).split()) for t in turns]
    turns = [_fix_colons(t) for t in turns if t]
    if names:                                  # OCR รวมสองคนเป็นบรรทัดเดียว: "Hey. Lucia: What?"
        pat = re.compile(r"(?<=[.?!])\s+(?=(?:" + "|".join(re.escape(n) for n in names) + r"):\s)")
        turns = [p for t in turns for p in pat.split(t) if p.strip()]
    return turns


# ---------------------------------------------------------------- แปลข้อความจาก OCR
class Composer:
    """แบ่งข้อความเป็นประโยค แล้วจับคู่ช่วงที่ยาวที่สุดที่มีในคลัง (ซับเกมมักรวม 2 ประโยคในจอเดียว
    แต่คลังเก็บแยกกัน หรือกลับกัน) ส่วนที่ไม่มีในคลังส่งแปลสด"""

    def __init__(self, translator, names, people=None):
        self.tr = translator
        self.names = names
        # ตัวละครที่พูดได้ (glossary.json "people") — ไม่รวมชื่อเมือง/แก๊ง จะได้ไม่เดาชื่อคนพูดเป็นชื่อสถานที่
        people = list(people) if people else [n for n in names if n[:1].isupper() and " " not in n]
        self.name_keys = sorted(people, key=len, reverse=True)
        self._people = {re.sub(r"[^a-z]", "", n.lower()): n for n in self.name_keys}

    def canonical(self, raw, cutoff=60):
        """ชื่อคนพูดจาก OCR (อาจเพี้ยน เช่น "OLucia", "I-Cia", "Nason") -> ชื่อตัวละครที่รู้จัก หรือ "" ถ้าไม่รู้จัก"""
        k = re.sub(r"[^a-z]", "", raw.lower())
        if len(k) < 3:
            return ""
        if k in self._people:
            return self._people[k]
        hit = process.extractOne(k, list(self._people), scorer=fuzz.ratio, score_cutoff=cutoff)
        return self._people[hit[0]] if hit else ""

    def split_speaker(self, text):
        m = SPEAKER.match(text)
        if not m:
            return "", text
        name = self.canonical(m.group(1)) or m.group(1)
        return self.names.get(name, name), m.group(2)

    def compose_turns(self, text, live_cb=None, live=True, show_miss=True):
        """แปลทุกบทพูดบนจอ -> [{"key": ชื่ออังกฤษ, "speaker": ชื่อไทย, "body": อังกฤษ, "th": ไทย, "partial": bool}]"""
        out = []
        for turn in split_turns(text, self.name_keys):
            m = SPEAKER.match(turn)
            key = self.canonical(m.group(1)) if m else ""          # "" = ไม่มีชื่อ / ชื่อที่ไม่รู้จัก
            th, partial, speaker = self.compose(turn, live_cb, live, show_miss)
            out.append({"key": key, "speaker": speaker, "body": m.group(2) if m else turn, "th": th, "partial": partial})
        return out

    def compose(self, text, live_cb=None, live=True, show_miss=True):
        """คืน (คำแปลไทยรวม, มีส่วนที่ยังไม่ได้แปลไหม, ชื่อคนพูด)"""
        speaker, body = self.split_speaker(text)
        sents = [s for s in SENTENCE.split(body) if s.strip()]
        out, missing, i = [], [], 0
        lookup = self.tr.matcher.lookup
        while i < len(sents):
            for j in range(len(sents), i, -1):
                hit = lookup(" ".join(sents[i:j]))
                if hit:
                    break
            else:
                hit, j = None, i + 1
            if hit:
                if missing:
                    out.append(self._miss(missing, live_cb, live))
                    missing = []
                out.append((hit["th"], True))
            else:
                missing.append(sents[i])
            i = j
        if missing:
            out.append(self._miss(missing, live_cb, live))
        # ถ้าประโยคยาว (ไม่มีจุดคั่นกลาง) ไม่เจอในคลัง ลองแบ่งตามเครื่องหมายจุลภาค (, ;) แล้วจับคู่ทีละช่วง
        if not any(ok for _, ok in out) and len(sents) == 1 and len(body) >= 45:
            clauses = [c.strip() for c in CLAUSE.split(body) if c.strip()]
            if len(clauses) >= 2:
                c_out, c_hit = [], 0
                for c in clauses:
                    h = lookup(c)
                    if h:
                        c_out.append((h["th"], True))
                        c_hit += 1
                    else:
                        c_out.append(self._miss([c], live_cb, live))
                if c_hit > 0:
                    out = c_out
        th = " ".join(t for t, ok in out if t and (ok or show_miss))
        return th, not all(ok for _, ok in out), speaker

    def _miss(self, sents, live_cb, live):
        en = " ".join(sents)
        if live:
            th = self.tr.get(en, on_ready=live_cb)     # ส่งแปลสดเบื้องหลัง (ถ้ายังไม่เคยส่ง)
            if th:
                return th, True
        return (en, False)


# ---------------------------------------------------------------- เธรด OCR
class OcrWorker(QThread):
    text = pyqtSignal(str, float)

    def __init__(self, s, debug=False):
        super().__init__()
        self.s = s
        self.debug = debug
        self.paused = False
        self.running = True
        self.reset = False

    def update_settings(self, s):
        self.s = s
        self.reset = True

    def run(self):
        try:
            self._run()
        except Exception:
            log("[ocr] เธรดหยุด:\n" + traceback.format_exc())

    def _run(self):
        ocr = OcrEngine(brightness=self.s["brightness"])
        ocr.warm_up()
        pending, sent, errors = None, None, 0
        while self.running:
            t0 = time.perf_counter()
            region = self.s.get("region")
            if self.reset:
                ocr.brightness = self.s["brightness"]
                ocr.reset()
                self.reset = False
            if region and not self.paused:
                try:
                    txt = ocr.read_region(region)
                    errors = 0
                except Exception as e:      # จอเปลี่ยนความละเอียด/สลับจอ ฯลฯ
                    errors += 1
                    if errors in (1, 5):
                        log(f"[ocr] {type(e).__name__}: {e}")
                    txt = None
                    if errors >= 5:          # พังติดกัน -> สร้างตัวจับภาพ/OCR ใหม่
                        try:
                            ocr = OcrEngine(brightness=self.s["brightness"])
                        except Exception as e2:
                            log(f"[ocr] สร้างใหม่ไม่ได้: {e2}")
                        errors = 0
                        time.sleep(1)
                if txt is not None:
                    if txt:
                        # มีข้อความใหม่ -> ส่งออกทันที (ไม่หน่วง 1 เฟรม) เพื่อให้ซับขึ้นไวสุด
                        pending = None
                        if txt != sent:
                            sent = txt
                            self.text.emit(txt, ocr.last_ms)
                    else:
                        # ข้อความหาย -> รออีก 1 เฟรมให้แน่ใจว่าซับหายจริง (กันซับกระพริบ)
                        pending = ""
                elif pending == "" and sent != "" and ocr.last_text == "":
                    sent = ""
                    pending = None
                    self.text.emit("", ocr.last_ms)
            dt = 1 / max(1, self.s["fps"]) - (time.perf_counter() - t0)
            if dt > 0:
                time.sleep(dt)


# ---------------------------------------------------------------- ปุ่มลัดทั้งระบบ
class Hotkeys(QObject):
    pressed = pyqtSignal(str)
    KEYS = {1: ("S", "toggle"), 2: ("R", "region"), 3: ("E", "english"),
            4: ("H", "history"), 5: ("O", "settings"), 6: ("Q", "quit"), 7: ("B", "bug")}

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        user32 = ctypes.windll.user32
        mods = 0x0001 | 0x0002 | 0x4000     # Alt + Ctrl + ไม่ซ้ำตอนกดค้าง
        for hid, (key, name) in self.KEYS.items():
            if not user32.RegisterHotKey(None, hid, mods, ord(key)):
                log(f"[hotkey] Ctrl+Alt+{key} ถูกโปรแกรมอื่นใช้อยู่")
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == 0x0312 and msg.wParam in self.KEYS:     # WM_HOTKEY
                self.pressed.emit(self.KEYS[msg.wParam][1])


# ---------------------------------------------------------------- ตัวโปรแกรม
class App(QObject):
    live_done = pyqtSignal(str, str)
    update_done = pyqtSignal(object, object)      # (ผลอัปเดต dict หรือ None, ข้อความผิดพลาด หรือ None)
    MISSED = ROOT / "data" / "missed.tsv"

    def __init__(self, qapp, args, on_progress=None):
        super().__init__()
        self.qapp = qapp
        self.debug = args.debug
        self.s = cfg.load()
        progress = on_progress or (lambda _text: None)
        progress("กำลังโหลดคลังคำแปล…")
        t = time.time()
        self.tr = SubtitleTranslator(gemini_key=self.s["gemini_key"], use_mymemory=self.s["use_mymemory"])
        g = json.loads((ROOT / "data" / "glossary.json").read_text(encoding="utf-8"))
        names = {k: v for k, v in g["names"].items() if "/" not in k}
        self.composer = Composer(self.tr, names, g.get("people"))
        m = self.tr.matcher
        log(f"โหลดคลัง {m.base_count:,} บรรทัด (ดัชนีค้นหา {len(m.keys):,} รายการ) ใน {time.time() - t:.1f} วิ")
        progress("กำลังเตรียมหน้าต่าง…")

        self.overlay = SubtitleOverlay({**self.s, "obs_mode": False})
        self.obs = None
        self._sync_obs()
        self.history = HistoryWindow(search_fn=lambda q: self.tr.matcher.search(q))
        self.history.correction.connect(self.apply_correction)
        self.settings_dlg = None
        self.current_text = ""
        self.trace = collections.deque(maxlen=800)   # เหตุการณ์ล่าสุด (เวลา, ชนิด, รายละเอียด) สำหรับแจ้งบัค
        self.shown = None                # บทพูดที่ซับไทยบนจอแสดงอยู่ (ผลของ compose_turns)
        self.empty_timer = QTimer(self, singleShot=True, interval=EMPTY_GRACE_MS, timeout=self._subtitle_gone)
        self._pending = None             # ซับใหม่ที่รอยืนยัน (turns, text, ms)
        self.confirm_timer = QTimer(self, singleShot=True, interval=CONFIRM_MS, timeout=self._confirm_pending)
        self.enabled = True
        self._missed = None
        self.live_done.connect(self._on_live)

        self.worker = OcrWorker(self.s, self.debug)
        self.worker.text.connect(self.on_text)
        self.worker.start()

        self.hotkeys = Hotkeys()
        self.hotkeys.pressed.connect(self.on_hotkey)
        self.hotkeys.start()

        self.panel = ControlPanel(self.s, total_lines=self.tr.matcher.base_count)
        self.panel.toggle_req.connect(self.toggle)
        self.panel.auto_req.connect(self.auto_set)
        self.panel.manual_req.connect(self.select_region)
        self.panel.detect_req.connect(self.detect)
        self.panel.preview_req.connect(self.preview_sub)
        self.panel.move_req.connect(self.move_sub)
        self.panel.settings_req.connect(self.open_settings)
        self.panel.history_req.connect(self.show_history)
        self.panel.quit_req.connect(self.quit)
        self.panel.option_changed.connect(lambda k, v: self.apply_settings({**self.s, k: v}))
        self._tray()
        self._sync_live_hint()
        self.panel.show()
        self._watch_show_request()

        from app import updater
        from app.reporter import Reporter
        self.reporter = Reporter(enabled=self.s["report_missed"], corpus_version=updater.local_version())
        self.update_done.connect(self._on_update_done)
        self._updating = False
        self._restart_ready = False
        self._update_status = (self._corpus_label(), True)
        self.tray.messageClicked.connect(lambda: self._restart_ready and self.restart())
        if self.s["auto_update"]:
            QTimer.singleShot(3000, lambda: self.check_update(manual=False))     # รอโปรแกรมเปิดเสร็จก่อน

        if args.select:
            self.select_region()
        elif not self.s.get("region"):
            self.auto_set("กลางล่าง (มาตรฐาน)", 0)      # ครั้งแรก: ตั้งกรอบกลางล่างจออัตโนมัติ
        else:
            self.overlay.show()

    # --- tray
    def _tray(self):
        self.tray = QSystemTrayIcon(T.app_icon())
        self.tray.setToolTip("ซับไทย GTA VI")
        menu = QMenu()
        polish_popup(menu)
        self.tray.activated.connect(
            lambda reason: self.show_panel() if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                                                           QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        items = [("เปิดหน้าต่างควบคุม", T.Glyph.SYSTEM, self.show_panel), None,
                 ("เปิด/ปิดซับ\tCtrl+Alt+S", T.Glyph.PLAY, self.toggle),
                 ("เลือกกรอบซับใหม่\tCtrl+Alt+R", T.Glyph.CROP, self.select_region),
                 ("ตั้งค่า\tCtrl+Alt+O", T.Glyph.SETTINGS, self.open_settings),
                 ("ประวัติบทพูด\tCtrl+Alt+H", T.Glyph.HISTORY, self.show_history),
                 ("แจ้งบัคซับ (กดทันทีที่เห็น)\tCtrl+Alt+B", T.Glyph.DOCUMENT, self.save_bug_report), None,
                 ("ปิดโปรแกรม\tCtrl+Alt+Q", T.Glyph.POWER, self.quit)]
        for item in items:
            if item is None:
                menu.addSeparator()
                continue
            label, glyph, fn = item
            act = QAction(T.glyph_icon(glyph, T.MUTED), label, menu)
            act.triggered.connect(fn)
            menu.addAction(act)
        self.tray.setContextMenu(menu)
        self.tray.show()
        self.menu = menu

    # --- อัปเดตคลังคำแปล
    def _corpus_label(self):
        from app import updater
        v = updater.local_version()
        src = updater.repo()
        if not src:
            return "ยังไม่ได้ตั้งแหล่งอัปเดต"
        return f"คลังเวอร์ชัน {updater.version_label(v)}" if v else "คลังที่ติดมากับโปรแกรม"

    def _set_update_status(self, text, ok=True, busy=False):
        self._update_status = (text, ok)
        if self.settings_dlg is not None and self.settings_dlg.isVisible():
            self.settings_dlg.set_update_status(text, ok, busy, restart=self._restart_ready)

    def check_update(self, manual=True):
        if self._updating:
            return
        self._updating = True
        self._set_update_status("กำลังเช็กอัปเดต…", True, busy=True)

        def work():
            from app import updater
            from app.matcher import Matcher
            res = {"updated": False, "code": None, "code_error": None, "corpus_error": None}
            try:
                res.update(updater.check_and_update())
                if res["updated"]:
                    res["matcher"] = Matcher(log_missing=False)      # สร้างดัชนีใหม่เบื้องหลัง (~0.5 วิ)
            except Exception as e:                                   # เน็ตหลุด/GitHub ล่ม -> ใช้คลังเดิมต่อ
                res["corpus_error"] = str(e) if isinstance(e, updater.UpdateError) else f"{type(e).__name__}: {e}"
                log(f"[update] {res['corpus_error']}")
            if updater.code_updates_supported():                     # เช็กตัวโปรแกรมแยกกัน (คลังพังก็ยังอัปเดตโปรแกรมได้)
                try:
                    res["code"] = updater.check_code_update()
                except Exception as e:
                    res["code_error"] = str(e) if isinstance(e, updater.UpdateError) else type(e).__name__
                    log(f"[update] โปรแกรม: {res['code_error']}")
            self.update_done.emit(res, None)
        self._update_manual = manual
        threading.Thread(target=work, daemon=True).start()

    def _on_update_done(self, res, err):
        from app import updater
        self._updating = False
        if err:
            self._set_update_status(f"{err} — ใช้คลังเดิมต่อ", ok=False)
            return
        msgs = []
        if res["updated"]:
            old = self.tr.matcher.base_count
            with self.tr.lock:
                self.tr.matcher = res["matcher"]                    # สลับคลังทันที ไม่ต้องปิดโปรแกรม
            new = self.tr.matcher.base_count
            self.panel.set_total_lines(new)
            msg = f"อัปเดตคลังแล้ว ({updater.version_label(res['version'])})"
            if new - old > 0:
                msg += f" +{new - old:,} ประโยค"
            log(f"[update] {msg}, เก็บคำแปลของเครื่องนี้ไว้ {res['live_kept']} ประโยค")
            msgs.append(msg + (f" — {res['notes']}" if res.get("notes") else ""))
        code = res.get("code")
        if code and code["need_exe"]:
            msgs.append("มีโปรแกรมรุ่นใหม่ที่ต้องโหลด ZIP ตัวเต็ม — ขอไฟล์ใหม่จากคนแจก")
        elif code and code["downloaded"] or updater.pending_code_version():
            self._restart_ready = True
            ver = updater.version_label(code["version"] if code and code["downloaded"] else updater.pending_code_version())
            msgs.append(f"โปรแกรมรุ่นใหม่ ({ver}) พร้อมแล้ว — คลิกที่นี่เพื่อรีสตาร์ท"
                        + (f"\n{code['notes']}" if code and code.get("notes") else ""))
            log(f"[update] โหลดโปรแกรมรุ่น {ver} แล้ว ใช้ตอนเปิดครั้งหน้า")
        if not msgs:
            if res.get("corpus_error"):
                status = f"{res['corpus_error']} — ใช้คลังเดิมต่อ"
            else:
                status = f"{self._corpus_label()} — ล่าสุดแล้ว"
            if res.get("code_error"):
                status += f" (โปรแกรม: {res['code_error']})"
            self._set_update_status(status, not (res.get("code_error") or res.get("corpus_error")))
            return
        self._set_update_status(msgs[-1].split("\n")[0], True)
        self.tray.showMessage("ซับไทย GTA VI", "\n".join(msgs), T.app_icon(), 6000)

    def restart(self):
        """ปิดแล้วเปิดโปรแกรมใหม่ (ใช้โค้ดรุ่นที่เพิ่งโหลดมา)"""
        import sys
        from PyQt6.QtCore import QProcess
        from app import main as M
        log("[update] รีสตาร์ทเพื่อใช้โปรแกรมรุ่นใหม่")
        M.release_instance()
        if getattr(sys, "frozen", False):
            QProcess.startDetached(sys.executable, ["--restarted"])
        else:
            QProcess.startDetached(sys.executable, ["-m", "app.main", "--restarted"])
        self.quit()

    def show_panel(self):
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()

    def _watch_show_request(self):
        """ผู้ใช้ดับเบิลคลิกเปิดโปรแกรมซ้ำ -> ตัวใหม่ส่งสัญญาณมาแล้วปิดตัวเอง ตัวนี้โชว์หน้าต่างควบคุมแทน"""
        from app.main import SHOW_EVENT
        k32 = ctypes.windll.kernel32
        k32.CreateEventW.restype = ctypes.c_void_p
        self._show_event = k32.CreateEventW(None, False, False, SHOW_EVENT)
        if not self._show_event:
            return

        def check():
            if k32.WaitForSingleObject(ctypes.c_void_p(self._show_event), 0) == 0:
                self.show_panel()
        self._show_timer = QTimer(self, interval=400, timeout=check)
        self._show_timer.start()

    def show_history(self):
        self.history.show()
        self.history.raise_()
        self.history.activateWindow()

    # --- การทำงานหลัก
    def on_text(self, text, ms):
        try:
            self._on_text(text, ms)
        except Exception:
            log("[app] on_text:\n" + traceback.format_exc())

    def _trace(self, kind, detail=""):
        self.trace.append((time.time(), kind, detail))

    def _on_text(self, text, ms):
        self._trace("ocr", f"{text!r} ({ms:.0f} ms)")
        self.current_text = text
        if not text:
            # OCR อ่านไม่เจอซับ — อาจแค่พลาดเฟรมเดียว (ฉากสว่างวาบ/ซับสั้นๆ) -> รอให้หายจริงก่อน
            if not self.empty_timer.isActive():
                self.empty_timer.start()
            return
        self.empty_timer.stop()
        t = time.perf_counter()
        turns = self.composer.compose_turns(
            text, live_cb=lambda en, th, src: self.live_done.emit(en, th), live=self.s["live_translate"],
            show_miss=self.s["show_untranslated"])
        find_ms = (time.perf_counter() - t) * 1000
        partial = any(p["partial"] for p in turns)
        if self.debug:
            log(f"[ocr {ms:.1f}ms + ค้น {find_ms:.2f}ms] {text!r} -> {[(p['speaker'], p['th']) for p in turns]}"
                f"{' (บางส่วนยังไม่มีในคลัง)' if partial else ''}")
        for p in turns:
            if p["partial"]:
                self.record_miss(p["body"])
        self._show(turns, text, ms + find_ms)

    def _screen_text(self, turns):
        """ข้อความทั้งจอสำหรับเทียบ: ตัดชื่อคนพูด (รวมชื่อที่ OCR อ่านเพี้ยน เช่น "Jasorv") และเศษตัวอักษรเดี่ยว"""
        body = " ".join(p["body"] for p in turns).lower().replace("'", "").replace("’", "")
        words = re.sub(r"[^a-z0-9 ]+", " ", body).split()
        c = self.composer
        return " ".join(w for w in words if (len(w) > 1 or w in ("i", "a")) and not (len(w) >= 4 and c.canonical(w, 70)))

    def _compare(self, old, new):
        """เทียบซับบนจอเดิมกับที่เพิ่งอ่านได้ -> "same" (ซับเดิม OCR อ่านเพี้ยน/อ่านขาด), "grow" (ซับเดิมยาวขึ้น),
        หรือ "new" (ซับใหม่) — คนพูดที่รู้จักเปลี่ยน = ซับใหม่เสมอ แม้ข้อความเหมือนกัน (เจสัน "Yeah." -> ลูเซีย "Yeah.")"""
        ko = [p["key"] for p in old if p["key"]]
        kn = [p["key"] for p in new if p["key"]]
        if ko and kn and not (set(kn) <= set(ko) or set(ko) <= set(kn)):
            return "new"
        a, b = self._screen_text(old), self._screen_text(new)
        if not b:
            return "same"            # เฟรมนี้เหลือแต่ชื่อคนพูด/เศษตัวอักษร (เช่น "Lucia9Yeah.") -> ไม่นับเป็นซับใหม่
        if not a:
            return "new"
        if len(a) >= 8 and len(b) >= len(a) + 8 and fuzz.partial_ratio(a, b) >= 90:
            return "grow"            # ซับเดิมมีท่อนต่อท้ายเพิ่ม (ยาวขึ้นเกินกว่าเศษที่ OCR อ่านเกิน)
        if fuzz.ratio(a, b) >= SAME_LINE:
            return "same"
        short, long_ = (a, b) if len(a) <= len(b) else (b, a)
        if len(short) >= 8 and len(short) >= 0.4 * len(long_) and fuzz.partial_ratio(short, long_) >= 90:
            return "same" if len(b) <= len(a) else "grow"        # อ่านขาดไปบางส่วน / ซับขึ้นเพิ่ม
        return "new"

    def _clearly_new(self, old, new):
        """ซับใหม่แน่ๆ (ไม่ต้องรอยืนยัน): คนพูดเปลี่ยน หรือข้อความคนละเรื่องกันเลย"""
        ko = {p["key"] for p in old if p["key"]}
        kn = {p["key"] for p in new if p["key"]}
        if ko and kn and not (kn <= ko or ko <= kn):
            return True
        a, b = self._screen_text(old), self._screen_text(new)
        if not b:
            return False
        if not a:
            return True
        return fuzz.ratio(a, b) < 45 and fuzz.partial_ratio(a, b) < 70

    def _show(self, turns, text, ms, live=False):
        shown = self.shown
        verdict = "new" if shown is None else self._compare(shown, turns)
        full_new = not any(p["partial"] for p in turns) and any(p["th"] for p in turns)
        full_old = shown is not None and not any(p["partial"] for p in shown) and any(p["th"] for p in shown)
        if verdict in ("same", "grow"):
            self._drop_pending()                     # เฟรมนี้ยืนยันว่ายังเป็นซับเดิม
            # ซับเดิมบนจอ แค่ OCR อ่านเพี้ยน -> คงคำแปลเดิมไว้ ไม่วาดใหม่ ไม่สลับคำแปล
            # ยกเว้นรอบนี้อ่านได้ดีกว่า (แปลได้ครบ ในขณะที่ที่แสดงอยู่ยังไม่ครบ) -> เติมให้
            # ซับยาวขึ้น (เกมขึ้นทีละท่อน/ทีละบรรทัด) และท่อนที่ยาวขึ้นแปลได้ครบ -> เปลี่ยนเป็นคำแปลเต็ม
            grown = verdict == "grow" and full_new and [p["th"] for p in turns] != [p["th"] for p in shown]
            if not grown and (full_old or not full_new):
                return
            self._display(turns, text, ms, "fill", live)
            return
        if shown is not None and not any(p["th"] for p in turns) and any(p["th"] for p in shown):
            # อ่านได้ข้อความที่ไม่มีคำแปลเลย: ถ้าเป็นเศษ/ขยะ หรือแค่ซับเดิมที่อ่านเพี้ยนหนัก -> ไม่ต้องล้างซับ
            a, b = self._screen_text(shown), self._screen_text(turns)
            if not looks_like_dialogue(flat(text)) or (b and len(b) <= 1.6 * len(a) + 6 and fuzz.partial_ratio(a, b) >= 80):
                return
        untranslated = not any(p["th"] for p in turns) and any(p["th"] for p in shown or ())
        if shown is not None and not live and (untranslated or not self._clearly_new(shown, turns)):
            pend = self._pending
            if pend is None or self._compare(pend[0], turns) == "new":
                self._pending = (turns, text, ms)    # คล้ายซับเดิม -> รอดูอีกนิดก่อนเปลี่ยน
                # ข้อความที่ไม่มีคำแปลเลย (เช่น ป้าย HUD/แจ้งเตือนที่แวบเข้ากรอบ) ต้องค้างนานกว่าถึงจะล้างซับเดิม
                self.confirm_timer.start(UNTRANSLATED_CONFIRM_MS if untranslated else CONFIRM_MS)
                self._trace("ยืนยัน", "รอดูก่อนเปลี่ยนซับ")
                return
            pend_full = not any(p["partial"] for p in pend[0]) and any(p["th"] for p in pend[0])
            same_th = [p["th"] for p in pend[0]] == [p["th"] for p in turns]
            if not (full_new and pend_full and same_th):
                # ยังไม่ชัวร์ (เฟรมเพี้ยนที่บังเอิญคล้ายกัน) -> รอให้ครบเวลายืนยัน เก็บเฟรมที่อ่านได้ดีกว่าไว้
                if full_new and not pend_full:
                    self._pending = (turns, text, ms)
                return
        self._drop_pending()
        self._display(turns, text, ms, "new", live)

    def _drop_pending(self):
        if self._pending is not None:
            self._trace("ยืนยัน", "ยกเลิก (เฟรมถัดไปยังเป็นซับเดิม)")
        self._pending = None
        self.confirm_timer.stop()

    def _confirm_pending(self):
        """ไม่มีเฟรมซับเดิมแทรกเข้ามา -> ซับเปลี่ยนจริง"""
        pend, self._pending = self._pending, None
        if pend and self.current_text:
            self._display(*pend, "new", False)

    def _display(self, turns, text, ms, verdict, live):
        shown_th = [p for p in turns if p["th"]]
        self._trace("จอ", f"{verdict}: " + (" | ".join(f"{p['speaker']}: {p['th']}" if p["speaker"] else p["th"]
                                                     for p in shown_th) or "(ไม่มีคำแปล)"))
        state = "live" if live else ("miss" if any(p["partial"] for p in turns) else "hit")
        self.panel.show_reading(flat(text), "  ".join(p["th"] for p in shown_th), ms, state)
        if not shown_th:
            # ประโยคใหม่ที่ยังไม่มีคำแปล -> ล้างซับเก่าออก ไม่ให้ค้างซับประโยคก่อน
            if self.shown is not None and any(p["th"] for p in self.shown):
                self.overlay.clear_soon()
            self.shown = turns
            return
        self.shown = turns
        if self.enabled:
            parts = [(p["speaker"], p["th"]) for p in shown_th]
            self.overlay.set_parts(parts, flat(text), new_line=verdict == "new")
            if not any(p["partial"] for p in turns):
                for p in shown_th:                           # เก็บทีละคนพูด -> แก้คำแปลทีละประโยคได้
                    self.history.add(f"{p['speaker']}: {p['th']}" if p["speaker"] else p["th"], p["body"], p["th"])

    def _subtitle_gone(self):
        """ซับอังกฤษหายจากจอจริงแล้ว"""
        if self.current_text:
            return
        self._trace("จอ", "ซับหาย -> เริ่มซ่อน")
        self._drop_pending()
        self.shown = None
        self.panel.show_reading("", "")
        self.overlay.clear_soon()

    def _on_live(self, en, th):
        try:
            self._on_live2(en, th)
        except Exception:
            log("[app] live:\n" + traceback.format_exc())

    def _on_live2(self, en, th):
        # แปลสดเสร็จ -> ประกอบบทพูดปัจจุบันใหม่ (ตอนนี้มีในคลังแล้ว) แล้วเติมคำแปลบนจอทันที
        if self.current_text and en in flat(self.current_text):
            turns = self.composer.compose_turns(self.current_text, live=False, show_miss=self.s["show_untranslated"])
            self._show(turns, self.current_text, None, live=True)

    BUG_DIR = ROOT / "data" / "bug_reports"

    def save_bug_report(self):
        """ผู้ใช้เห็นซับกระพริบ/ค้าง แล้วกด Ctrl+Alt+B -> เก็บเหตุการณ์ 40 วินาทีล่าสุด + ภาพกรอบซับ ณ ตอนนั้น"""
        from app import updater
        try:
            self.BUG_DIR.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            now = time.time()
            events = [(t, k, d) for t, k, d in self.trace if now - t <= 40]
            cur = self.overlay.current
            lines = [f"แจ้งบัคซับ {time.strftime('%Y-%m-%d %H:%M:%S')}",
                     f"รุ่นโปรแกรม {updater.code_version() or 'dev'} / exe {updater.exe_version() or '-'} / คลัง {updater.local_version()}",
                     f"ตั้งค่า: fps={self.s['fps']} brightness={self.s['brightness']} hide_after_ms={self.s['hide_after_ms']} "
                     f"prev_lines={self.s['prev_lines']} position={self.s['position']} region={self.s.get('region')}",
                     "ซับบนจอตอนกด: " + (" | ".join(f"{s}: {t}" if s else t for s, t in cur.get("parts", [])) if cur else "(ว่าง)"),
                     "OCR อ่านได้ล่าสุด: " + repr(self.current_text), "",
                     "วินาทีก่อนกด | ชนิด | รายละเอียด"]
            lines += [f"{t - now:7.2f} | {k:6s} | {d}" for t, k, d in events]
            path = self.BUG_DIR / f"bug-{stamp}.txt"
            path.write_text("\n".join(lines), encoding="utf-8")
            region = self.s.get("region")
            if region:
                try:
                    import cv2
                    import numpy as np
                    from app.ocr_engine import new_screen_grabber
                    with new_screen_grabber() as g:
                        img = np.asarray(g.grab(region))
                    cv2.imencode(".png", img)[1].tofile(str(self.BUG_DIR / f"bug-{stamp}.png"))
                except Exception as e:
                    log(f"[bug] เก็บภาพไม่ได้: {e}")
            # ส่งสรุปให้ผู้ทำโปรแกรม (ถ้าเปิดส่งประโยคตกหล่นไว้) — เฉพาะข้อความซับ ไม่มีข้อมูลส่วนตัว
            self.reporter.add_raw("[แจ้งบัค] " + "\n".join(lines[:5] + lines[-25:]))
            log(f"[bug] บันทึกแจ้งบัคที่ {path.name} ({len(events)} เหตุการณ์)")
            self.tray.showMessage("ซับไทย GTA VI", f"บันทึกแจ้งบัคแล้ว\n{path.name} (โฟลเดอร์ data\\bug_reports)", T.app_icon(), 3000)
        except Exception:
            log("[bug] " + traceback.format_exc())

    CORRECTIONS = ROOT / "data" / "corrections.tsv"

    def apply_correction(self, en, old, new):
        """ผู้ใช้แก้คำแปลจากหน้าประวัติ -> บันทึกลงคลัง, แก้ซับบนจอ, จดไว้, ส่งให้ผู้ทำคลัง (ถ้าเปิดไว้)"""
        try:
            with self.tr.lock:
                self.tr.matcher.correct(en, new)
        except Exception as e:
            log(f"[fix] แก้ไม่สำเร็จ: {type(e).__name__}: {e}")
            QMessageBox.warning(self.history, "แก้คำแปลไม่สำเร็จ", str(e))
            return
        log(f"[fix] {en!r}: {old!r} -> {new!r}")
        try:
            with open(self.CORRECTIONS, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M')}\t{flat(en)}\t{old}\t{new}\n")
        except OSError:
            pass
        self.reporter.add(f"[แก้คำแปล] {flat(en)}  =>  {new}  (เดิม: {old})")
        cur = self.overlay.current
        if cur and any(t == old for _, t in cur.get("parts") or []):
            parts = [(s, new if t == old else t) for s, t in cur["parts"]]
            self.overlay.set_parts(parts, cur["en"], new_line=False)
        if self.shown:
            for p in self.shown:
                if p["th"] == old:
                    p["th"] = new

    def record_miss(self, text):
        """เก็บประโยคที่แปลไม่ได้ไว้ (data/missed.tsv) เอาไปแปลเพิ่มเข้าคลังทีหลัง"""
        text = flat(text)
        if len(text) < 6 or not looks_like_dialogue(text):
            return
        if self._missed is None:
            self._missed = []
            try:
                if self.MISSED.exists():
                    self._missed = [l.split("\t")[-1] for l in self.MISSED.read_text(encoding="utf-8").splitlines()]
            except OSError:
                pass
        if text in self._missed or any(fuzz.ratio(text, m) >= 85 for m in self._missed[-300:]):
            return
        self._missed.append(text)
        self.reporter.add(text)                     # ส่งให้ผู้ทำคลังเบื้องหลัง (ถ้าเปิดไว้)
        try:
            with open(self.MISSED, "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M") + "\t" + text + "\n")
        except OSError:
            pass

    def _sync_obs(self):
        """โหมด OBS: เปิดหน้าต่างพื้นเขียวแยก ให้ OBS จับภาพ"""
        if self.s.get("obs_mode"):
            s = self.s
            if s.get("obs_screen", -1) >= 0 and game_monitor(s.get("region")) == s["obs_screen"]:
                s = {**s, "obs_screen": -1}         # เต็มจอบนจอเกม = บังเกม OCR อ่านซับไม่ได้ -> ใช้หน้าต่างปกติแทน
                log("[obs] จอที่เลือกแสดงเต็มจอคือจอเกม -> ใช้หน้าต่างปกติแทน")
            if self.obs is None:
                self.obs = ObsFeed(s)
                self.obs.setWindowIcon(T.app_icon())
                self.obs.resize(1280, 220)
                self.obs.set_line("ซับไทยจะขึ้นตรงนี้ (หน้าต่างสำหรับ OBS)", "", "")
                self.obs.exit_fullscreen.connect(lambda: self.apply_settings({**self.s, "obs_screen": -1}))
            self.obs.apply_settings(s)
            self.obs.show()
            self.overlay.mirror = self.obs
        elif self.obs is not None:
            self.overlay.mirror = None
            self.obs.hide()

    def _sync_live_hint(self):
        """บอกในหน้าหลักว่าตัวแปลสดพร้อมไหม (เปิดแปลสดไว้แต่ไม่มีคีย์ = ไม่ได้แปลจริง)"""
        text, ok = self.tr.status()
        if self.s["live_translate"] and not ok:
            self.panel.set_option_hint("live_translate", "ยังใช้ไม่ได้ — ใส่คีย์ Gemini ที่ ตั้งค่า > การแปล", ok=False)
        else:
            self.panel.set_option_hint("live_translate", "")

    def apply_settings(self, s):
        old = self.s
        region_changed = s.get("region") != old.get("region")
        self.s = s
        cfg.save(s)
        self.reporter.enabled = s["report_missed"]
        if (s["gemini_key"], s["use_mymemory"]) != (old["gemini_key"], old["use_mymemory"]):
            self.tr.configure(s["gemini_key"], s["use_mymemory"])
        self.overlay.apply_settings({**s, "obs_mode": False})
        self._sync_obs()
        if any(s[k] != old[k] for k in ("region", "brightness", "fps")):
            self.worker.update_settings(s)
        else:
            self.worker.s = s
        self.panel.set_region(s.get("region"))
        for k in ("show_english", "live_translate", "obs_mode"):
            self.panel.set_option(k, bool(s.get(k)))
        self._sync_live_hint()
        if region_changed:
            self.overlay.show()

    # --- คำสั่ง
    def toggle(self):
        self.enabled = not self.enabled
        self.worker.paused = not self.enabled
        if self.enabled:
            self.worker.reset = True                # อ่านจอใหม่ทันทีที่กลับมาเปิด
        self.overlay.setVisible(self.enabled)
        self.panel.set_running(self.enabled)
        self.tray.showMessage("ซับไทย GTA VI", "เปิดซับแล้ว" if self.enabled else "หยุดจับซับชั่วคราว",
                              T.app_icon(), 1500)

    def auto_set(self, preset, monitor):
        self.apply_settings({**self.s, "region": auto_region(preset, monitor)})
        self.overlay.show()

    SAMPLE = ("ตัวอย่างซับไทย GTA VI — ถ้าตำแหน่งยังไม่ตรง กดเลื่อนขึ้น/ลงได้เลย",
              "Sample GTA VI subtitle — adjust position if needed", "เจสัน")

    def preview_sub(self):
        self.overlay.show()
        self.overlay.set_sample(*self.SAMPLE, hide_after_ms=6000)

    def move_sub(self, d):
        self.apply_settings({**self.s, "offset_y": 0 if d == 0 else self.s["offset_y"] + d})
        self.overlay.show()
        self.overlay.set_sample("ตัวอย่างตำแหน่งซับไทย", hide_after_ms=3000)
        self.panel.set_note(f"เลื่อนซับไทย {self.s['offset_y']:+d} px จากตำแหน่งเดิม" if self.s["offset_y"] else "ซับไทยกลับตำแหน่งเดิมแล้ว")

    def detect(self, monitor):
        self.overlay.hide()
        self.panel.hide()                 # ไม่ให้หน้าต่างควบคุมบังซับ
        self.qapp.processEvents()
        time.sleep(0.3)
        order = [monitor] + [i for i in range(len(monitors())) if i != monitor]   # จอที่เลือกก่อน แล้วไล่ทุกจอ
        r, info, found_on = None, "", None
        for i in order:
            try:
                r, info = detect_subtitle_region(self.tr.matcher.lookup, i)
            except Exception as e:
                r, info = None, f"{type(e).__name__}: {e}"
                log("[detect] " + traceback.format_exc())
            if r:
                found_on = i
                break
        self.panel.show()
        if r:
            self.apply_settings({**self.s, "region": r})
            self.panel.set_note(f"เจอซับที่จอ {found_on + 1}: “{info[:44]}” ตั้งกรอบให้แล้ว")
        else:
            self.panel.set_note(info, ok=False)
        self.overlay.show()

    def select_region(self, monitor=None):
        self.overlay.hide()
        if monitor is None or isinstance(monitor, bool):
            monitor = self.panel.monitor.currentIndex()
        try:
            screen = screen_for_monitor(monitor)
        except Exception:
            screen = None
        self.selector = RegionSelector(screen)
        self.selector.selected.connect(lambda r: self.apply_settings({**self.s, "region": r}))
        self.selector.destroyed.connect(lambda: self.overlay.show())
        self.selector.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.selector.show()
        self.selector.raise_()
        self.selector.activateWindow()

    def open_settings(self):
        if self.settings_dlg is None or not self.settings_dlg.isVisible():
            self.settings_dlg = SettingsDialog(self.s, live_status=self.tr.status, can_report=self.reporter.available)
            self.settings_dlg.set_update_status(*self._update_status, busy=self._updating, restart=self._restart_ready)
            self.settings_dlg.update_req.connect(lambda: self.check_update(manual=True))
            self.settings_dlg.restart_req.connect(self.restart)
            self.settings_dlg.changed.connect(lambda s: self.apply_settings({**s, "region": self.s.get("region")}))
            self.settings_dlg.finished.connect(lambda _r: self.overlay.clear_sample())
            self.settings_dlg.show()
        self.settings_dlg.raise_()
        self.settings_dlg.activateWindow()
        # ให้เห็นซับตัวอย่างบนจอจริงระหว่างปรับ (ตำแหน่ง/ขนาดจริง) — หายเองเมื่อปิดหน้าตั้งค่าหรือมีซับจากเกม
        if self.enabled:
            self.overlay.show()
            self.overlay.set_sample("ตัวอย่างซับไทย ปรับฟอนต์และสีได้เลย", "Sample subtitle", "เจสัน")

    def on_hotkey(self, name):
        {"toggle": self.toggle, "region": self.select_region, "settings": self.open_settings,
         "history": self.show_history, "quit": self.quit, "bug": self.save_bug_report,
         "english": lambda: self.apply_settings({**self.s, "show_english": not self.s["show_english"]}),
         }[name]()

    def quit(self):
        self.reporter.flush(2.0)                    # ส่งประโยคที่ค้างอยู่ก่อนปิด
        self.worker.running = False
        self.worker.wait(1500)
        self.tray.hide()
        self.qapp.exit(0)                           # ออกทันที (quit() ถามหน้าต่างก่อน และหน้าต่างอาจปฏิเสธการปิด)
