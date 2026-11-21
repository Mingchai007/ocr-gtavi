"""แกนของโปรแกรม: OCR ซับอังกฤษบนจอ -> ค้นคลังคำแปล (+แปลสด) -> แสดงซับไทยทับเกม

(ไฟล์นี้โหลดไลบรารีหนักทั้งหมด จึงถูกเรียกจาก app/main.py หลังหน้าต่างโหลดขึ้นแล้ว)
"""
import ctypes
import ctypes.wintypes
import json
import re
import threading
import time
import traceback

from rapidfuzz import fuzz

from PyQt6.QtCore import QObject, QThread, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from app import settings as cfg
from app import theme as T
from app.applog import log
from app.control_panel import ControlPanel, auto_region, detect_subtitle_region, monitors, screen_for_monitor
from app.live_translate import SubtitleTranslator, looks_like_dialogue
from app.matcher import ROOT
from app.ocr_engine import OcrEngine
from app.overlay_ui import HistoryWindow, ObsFeed, RegionSelector, SettingsDialog, SubtitleOverlay
from app.widgets import polish_popup

SPEAKER = re.compile(r"^\s*([A-Z][\w'.\-]*(?: [A-Z][\w'.\-]*){0,2}):\s*(.+)$")
SENTENCE = re.compile(r"(?<=[.?!])\s+")
CLAUSE = re.compile(r"(?<=[,;—–])\s+")


# ---------------------------------------------------------------- แปลข้อความจาก OCR
class Composer:
    """แบ่งข้อความเป็นประโยค แล้วจับคู่ช่วงที่ยาวที่สุดที่มีในคลัง (ซับเกมมักรวม 2 ประโยคในจอเดียว
    แต่คลังเก็บแยกกัน หรือกลับกัน) ส่วนที่ไม่มีในคลังส่งแปลสด"""

    def __init__(self, translator, names):
        self.tr = translator
        self.names = names

    def split_speaker(self, text):
        m = SPEAKER.match(text)
        if not m:
            return "", text
        name = m.group(1)
        return self.names.get(name, name), m.group(2)

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
            4: ("H", "history"), 5: ("O", "settings"), 6: ("Q", "quit")}

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
        self.composer = Composer(self.tr, names)
        m = self.tr.matcher
        log(f"โหลดคลัง {m.base_count:,} บรรทัด (ดัชนีค้นหา {len(m.keys):,} รายการ) ใน {time.time() - t:.1f} วิ")
        progress("กำลังเตรียมหน้าต่าง…")

        self.overlay = SubtitleOverlay({**self.s, "obs_mode": False})
        self.obs = None
        self._sync_obs()
        self.history = HistoryWindow()
        self.settings_dlg = None
        self.current_text = ""
        self.shown_en = ""
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
        self._update_status = (self._corpus_label(), True)
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
                 ("ประวัติบทพูด\tCtrl+Alt+H", T.Glyph.HISTORY, self.show_history), None,
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
            self.settings_dlg.set_update_status(text, ok, busy)

    def check_update(self, manual=True):
        if self._updating:
            return
        self._updating = True
        self._set_update_status("กำลังเช็กคลังใหม่…", True, busy=True)

        def work():
            from app import updater
            from app.matcher import Matcher
            try:
                res = updater.check_and_update()
                if res["updated"]:
                    res["matcher"] = Matcher(log_missing=False)      # สร้างดัชนีใหม่เบื้องหลัง (~0.5 วิ)
                self.update_done.emit(res, None)
            except Exception as e:                                   # เน็ตหลุด/GitHub ล่ม -> ใช้คลังเดิมต่อ
                msg = str(e) if isinstance(e, updater.UpdateError) else f"{type(e).__name__}: {e}"
                log(f"[update] {msg}")
                self.update_done.emit(None, msg)
        self._update_manual = manual
        threading.Thread(target=work, daemon=True).start()

    def _on_update_done(self, res, err):
        from app import updater
        self._updating = False
        if err:
            self._set_update_status(f"{err} — ใช้คลังเดิมต่อ", ok=False)
            return
        if not res["updated"]:
            self._set_update_status(f"{self._corpus_label()} — ล่าสุดแล้ว", True)
            return
        old = self.tr.matcher.base_count
        with self.tr.lock:
            self.tr.matcher = res["matcher"]                        # สลับคลังทันที ไม่ต้องปิดโปรแกรม
        new = self.tr.matcher.base_count
        self.panel.set_total_lines(new)
        diff = new - old
        msg = f"อัปเดตคลังแล้ว ({updater.version_label(res['version'])})"
        if diff > 0:
            msg += f" +{diff:,} ประโยค"
        log(f"[update] {msg}, เก็บคำแปลสดของเครื่องนี้ไว้ {res['live_kept']} ประโยค")
        self._set_update_status(msg, True)
        self.tray.showMessage("ซับไทย GTA VI", msg + (f"\n{res['notes']}" if res["notes"] else ""), T.app_icon(), 4000)

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

    def _on_text(self, text, ms):
        self.current_text = text
        if not text:
            if self.debug:
                log(f"[ocr {ms:.1f}ms] (ซับหาย)")
            self.panel.show_reading("", "")
            self.shown_en = ""
            self.overlay.clear_soon()
            return
        t = time.perf_counter()
        th, partial, speaker = self.composer.compose(
            text, live_cb=lambda en, th, src: self.live_done.emit(en, th), live=self.s["live_translate"], show_miss=self.s["show_untranslated"])
        find_ms = (time.perf_counter() - t) * 1000
        if self.debug:
            log(f"[ocr {ms:.1f}ms + ค้น {find_ms:.2f}ms] {text!r} -> {th!r}{' (บางส่วนยังไม่มีในคลัง)' if partial else ''}")
        if partial:
            self.record_miss(text)
        if partial and self.shown_en and fuzz.ratio(text, self.shown_en) >= 80:
            return                                  # อ่านเพี้ยนจากประโยคเดิม ไม่ต้องสลับ
        if not partial and self.shown_en and fuzz.ratio(text, self.shown_en) >= 93:
            return                                  # ประโยคเดิมแค่อ่านอักขระต่างนิดเดียว ไม่ต้องกระพริบซ้ำ
        self.panel.show_reading(text, th, ms + find_ms, "miss" if partial else "hit")
        if th:
            if not partial:
                self.shown_en = text
            if self.enabled:
                self.overlay.set_line(th, text, speaker)
                if not partial:
                    self.history.add(th, text)
        elif self.shown_en and fuzz.ratio(text, self.shown_en) < 70:
            # ขึ้นประโยคใหม่แล้วแต่ยังไม่มีคำแปล -> ล้างซับเก่าออก ไม่ให้ค้างซับเดิม
            self.shown_en = ""
            self.overlay.clear_soon()

    def _on_live(self, en, th):
        try:
            self._on_live2(en, th)
        except Exception:
            log("[app] live:\n" + traceback.format_exc())

    def _on_live2(self, en, th):
        # แปลสดเสร็จ -> ประกอบประโยคปัจจุบันใหม่ (ตอนนี้มีในคลังแล้ว) แล้วขึ้นจอทันที
        if self.current_text and en in self.current_text:
            th_all, partial, speaker = self.composer.compose(
                self.current_text, live=False, show_miss=self.s["show_untranslated"])
            if th_all and self.enabled:
                self.panel.show_reading(self.current_text, th_all, None, "live")
                self.overlay.set_line(th_all, self.current_text, speaker)
                if not partial:
                    self.shown_en = self.current_text
                    self.history.add(th_all, self.current_text)

    def record_miss(self, text):
        """เก็บประโยคที่แปลไม่ได้ไว้ (data/missed.tsv) เอาไปแปลเพิ่มเข้าคลังทีหลัง"""
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
            if self.obs is None:
                self.obs = ObsFeed(self.s)
                self.obs.setWindowIcon(T.app_icon())
                self.obs.resize(1280, 220)
                self.obs.set_line("ซับไทยจะขึ้นตรงนี้ (หน้าต่างสำหรับ OBS)", "", "")
            self.obs.apply_settings(self.s)
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
            self.settings_dlg.set_update_status(*self._update_status, busy=self._updating)
            self.settings_dlg.update_req.connect(lambda: self.check_update(manual=True))
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
         "history": self.show_history, "quit": self.quit,
         "english": lambda: self.apply_settings({**self.s, "show_english": not self.s["show_english"]}),
         }[name]()

    def quit(self):
        self.reporter.flush(2.0)                    # ส่งประโยคที่ค้างอยู่ก่อนปิด
        self.worker.running = False
        self.worker.wait(1500)
        self.tray.hide()
        self.qapp.quit()
