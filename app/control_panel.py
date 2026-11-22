"""หน้าต่างควบคุมหลัก (GUI) — กดปุ่มเดียวใช้ได้ ไม่ต้องจำปุ่มลัด"""
import mss
from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QGuiApplication, QPainter, QTextLayout, QTextOption
from PyQt6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from app import theme as T
from app.widgets import OptionRow, button, card, hline, polish_popup, restyle, section

WINDOW_TITLE = "ซับไทย GTA VI"
PANEL_WIDTH = 468

# ซับ GTA อยู่กลางล่างจอ: กรอบอัตโนมัติ = สัดส่วนของจอ (ซ้าย, บน, กว้าง, สูง)
AUTO_PRESETS = {
    "กลางล่าง (มาตรฐาน)": (0.15, 0.78, 0.70, 0.17),
    "กลางล่าง กว้าง": (0.05, 0.74, 0.90, 0.22),
    "ล่างสุด แคบ": (0.20, 0.84, 0.60, 0.13),
}


def _grabber():
    return (getattr(mss, "MSS", None) or mss.mss)()


def monitors():
    with _grabber() as m:
        return m.monitors[1:]


def screen_for_monitor(index):
    """หน้าจอ (QScreen) ที่ตรงกับจอลำดับ index ของ mss — เทียบจากตำแหน่งมุมซ้ายบน"""
    mons = monitors()
    mon = mons[min(index, len(mons) - 1)]
    screens = QGuiApplication.screens()
    best = min(screens, key=lambda s: abs(s.geometry().x() - mon["left"]) + abs(s.geometry().y() - mon["top"]))
    return best


def auto_region(preset="กลางล่าง (มาตรฐาน)", monitor_index=0):
    mons = monitors()
    mon = mons[min(monitor_index, len(mons) - 1)]
    x, y, w, h = AUTO_PRESETS[preset]
    return {"left": mon["left"] + int(mon["width"] * x), "top": mon["top"] + int(mon["height"] * y),
            "width": int(mon["width"] * w), "height": int(mon["height"] * h)}


def detect_subtitle_region(lookup, monitor_index=0):
    """หยุดคลิปตอนมีซับ แล้วเรียกฟังก์ชันนี้: OCR ทั้งจอ หาบรรทัดที่ตรงกับคลังคำแปล (= ซับจริง)
    แล้วสร้างกรอบรอบซับ (เผื่อซับ 2-3 บรรทัด) คืน (region, ข้อความที่เจอ) หรือ (None, เหตุผล)"""
    import cv2
    import numpy as np
    from app.ocr_engine import OcrEngine
    with _grabber() as m:
        mon = m.monitors[1:][min(monitor_index, len(m.monitors) - 2)]
        gray = cv2.cvtColor(np.asarray(m.grab(mon)), cv2.COLOR_BGRA2GRAY)
    scale = 1.0
    if max(gray.shape) > 2500:
        scale = 2500 / max(gray.shape)
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    ocr = OcrEngine()
    H, W = gray.shape
    lines = []
    for txt, x, y, w, h in ocr.recognize_lines(gray):
        if y < H * 0.35 or len(txt) < 3:                 # ซับอยู่ครึ่งล่างของจอ
            continue
        hit = lookup(txt) if len(txt) >= 4 else None
        cx = (x + w / 2) / W
        lines.append((bool(hit), abs(cx - 0.5), txt, x, y, w, h))
    subs = [l for l in lines if l[0]]
    if not subs:
        return None, "ไม่เจอซับที่ตรงกับคลังบนจอ (หยุดคลิปตอนที่มีซับขึ้นอยู่ แล้วลองใหม่)"
    # รวมบรรทัดซับที่อยู่ติดกัน (ซับ 2 บรรทัด)
    y0 = min(l[4] for l in subs); y1 = max(l[4] + l[6] for l in subs)
    for l in lines:
        if not l[0] and abs(l[1]) < 0.2 and (y0 - 2.5 * l[6] < l[4] < y1 + 1.5 * l[6]):
            y0, y1 = min(y0, l[4]), max(y1, l[4] + l[6])
    lh = max(l[6] for l in subs)
    cx = sum(l[3] + l[5] / 2 for l in subs) / len(subs)
    half = max(max(l[5] for l in subs) * 1.6, W * 0.28) / 2
    top = max(0, y0 - lh * 1.6)                             # เผื่อซับ 3 บรรทัดดันขึ้นข้างบน
    bottom = min(H, y1 + lh * 0.8)
    left = max(0, cx - half); right = min(W, cx + half)
    r = {"left": mon["left"] + int(left / scale), "top": mon["top"] + int(top / scale),
         "width": int((right - left) / scale), "height": int((bottom - top) / scale)}
    return r, " / ".join(l[2] for l in subs)


def elide_lines(text, font, width, max_lines):
    """ตัดข้อความให้ไม่เกิน max_lines บรรทัดที่ความกว้างนี้ (เติม … ท้ายถ้าเกิน) -> (ข้อความ, จำนวนบรรทัดที่ใช้)"""
    if not text:
        return text, 0
    layout = QTextLayout(text, font)
    opt = QTextOption()
    opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(opt)
    layout.beginLayout()
    lines = []
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        lines.append((line.textStart(), line.textLength()))
    layout.endLayout()
    if len(lines) <= max_lines:
        return text, len(lines)
    start, _ = lines[max_lines - 1]
    last = QFontMetricsF(font).elidedText(text[start:], Qt.TextElideMode.ElideRight, width - 4)
    return text[:start] + last, max_lines


class LiveCard(QFrame):
    """กล่อง "กำลังอ่านจากจอ": อังกฤษที่ OCR อ่านได้ + คำแปลไทย + เวลาที่ใช้"""
    STATES = {"hit": (T.OK, "เจอในคลัง"), "live": (T.CYAN, "แปลสด"), "miss": (T.WARN, "ยังไม่มีในคลัง"),
              "idle": (T.FAINT, "รอซับขึ้นจอ")}

    def __init__(self):
        super().__init__()
        self.setObjectName("Card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 9, 14, 10)
        lay.setSpacing(5)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.dot = QLabel("●")
        self.state_lbl = QLabel()
        self.state_lbl.setObjectName("Section")
        top.addWidget(self.dot)
        top.addWidget(self.state_lbl)
        top.addStretch(1)
        self.chip = QLabel()
        self.chip.setObjectName("Chip")
        self.chip.setToolTip("เวลาที่ใช้ตั้งแต่จับภาพ อ่านตัวหนังสือ จนค้นคำแปลเสร็จ")
        self.chip.hide()
        top.addWidget(self.chip)
        lay.addLayout(top)
        self.en = QLabel()
        self.en.setObjectName("LiveEn")
        self.en.setWordWrap(True)
        self.en.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.th = QLabel()
        self.th.setObjectName("LiveTh")
        self.th.setWordWrap(True)
        self.th.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.en)
        lay.addWidget(self.th, 1)
        # ฟอนต์สำหรับวัดความยาวข้อความ (ต้องตรงกับ QLabel#LiveEn / #LiveTh ในธีม)
        self._en_font = QFont("Leelawadee UI")
        self._en_font.setPointSizeF(9.5)
        self._th_font = QFont("Leelawadee UI")
        self._th_font.setPointSizeF(13)
        self._th_font.setBold(True)
        self.setFixedHeight(126)                      # สูงคงที่ หน้าต่างจะได้ไม่ขยับตามความยาวซับ
        self.show_reading("", "")

    def show_reading(self, en, th, ms=None, state=None):
        state = state or ("idle" if not en else "hit" if th else "miss")
        col, label = self.STATES[state]
        self.dot.setStyleSheet(f"color:{col}; font-size:8pt;")
        self.state_lbl.setText(label)
        self.chip.setVisible(ms is not None and bool(en))
        if ms is not None:
            self.chip.setText(f"{ms:.0f} ms" if ms >= 10 else f"{ms:.1f} ms")
        cw = self.width()
        if not 200 <= cw <= PANEL_WIDTH:              # ยังไม่ได้จัดวาง -> ใช้ความกว้างจริงของการ์ดในหน้าต่าง
            cw = PANEL_WIDTH - 32
        w = cw - 40                                   # ขอบการ์ดซ้ายขวา + เผื่อเล็กน้อย
        th_text, th_lines = elide_lines(th, self._th_font, w, 2)
        # กล่องสูงคงที่: คำแปลไทยได้ 2 บรรทัดก่อน อังกฤษใช้ที่ที่เหลือ
        en_text, _ = elide_lines(en, self._en_font, w, 1 if th_lines >= 2 else 2)
        self.en.setText(en_text if en else "เปิดเกมหรือคลิปที่มีซับอังกฤษ แล้วซับไทยจะขึ้นเอง")
        self.th.setText(th_text if th else ("—" if en else ""))


class ControlPanel(QWidget):
    toggle_req = pyqtSignal()
    auto_req = pyqtSignal(str, int)
    detect_req = pyqtSignal(int)
    manual_req = pyqtSignal(int)
    preview_req = pyqtSignal()
    settings_req = pyqtSignal()
    history_req = pyqtSignal()
    option_changed = pyqtSignal(str, bool)
    move_req = pyqtSignal(int)
    quit_req = pyqtSignal()

    def __init__(self, s, total_lines=0):
        super().__init__()
        self.setObjectName("Root")
        self.setWindowTitle(WINDOW_TITLE)
        self.setWindowIcon(T.app_icon())
        self.setFixedWidth(PANEL_WIDTH)

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(10)

        # --- ส่วนหัว: โลโก้ + ชื่อ + สถานะ
        hdr = QHBoxLayout()
        hdr.setSpacing(12)
        logo = QLabel()
        logo.setObjectName("LogoTile")
        logo.setPixmap(T.pixmap("logo.png", 30, self.devicePixelRatioF()))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedSize(50, 50)
        hdr.addWidget(logo)
        box = QVBoxLayout()
        box.setSpacing(0)
        title = QLabel("ซับไทย GTA VI")
        title.setObjectName("Title")
        sub = QLabel()
        sub.setObjectName("SubTitle")
        self.sub_lbl = sub
        self.set_total_lines(total_lines)
        box.addStretch(1)
        box.addWidget(title)
        box.addWidget(sub)
        box.addStretch(1)
        hdr.addLayout(box, 1)
        self.status = QLabel()
        self.status.setObjectName("Pill")
        hdr.addWidget(self.status, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(hdr)

        # --- กำลังอ่านจากจอ
        self.live = LiveCard()
        lay.addWidget(self.live)

        # --- ปุ่มหลัก เปิด/หยุด
        self.btn_toggle = button("", kind="Hero")
        self.btn_toggle.setMinimumHeight(44)
        self.btn_toggle.setIconSize(QSize(16, 16))
        self.btn_toggle.setToolTip("Ctrl+Alt+S")
        self.btn_toggle.clicked.connect(self.toggle_req)
        lay.addWidget(self.btn_toggle)

        # --- กรอบซับในเกม
        lay.addWidget(section("กรอบซับในเกม"))
        frame, gl = card()
        self.btn_detect = button(" หาซับอัตโนมัติ", T.Glyph.SEARCH, "Primary",
                                 tip="หยุดเกมหรือคลิปตอนที่มีซับอังกฤษขึ้นอยู่ แล้วกดปุ่มนี้")
        self.btn_detect.setMinimumHeight(38)
        self.btn_detect.clicked.connect(lambda: self.detect_req.emit(self.monitor.currentIndex()))
        gl.addWidget(self.btn_detect)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.preset = QComboBox()
        self.preset.addItems(AUTO_PRESETS)
        self.monitor = QComboBox()
        region = s.get("region")
        for i, m in enumerate(monitors()):
            self.monitor.addItem(f"จอ {i + 1}  {m['width']}×{m['height']}")
            if region and m["left"] <= region["left"] + region["width"] / 2 < m["left"] + m["width"] \
                    and m["top"] <= region["top"] + region["height"] / 2 < m["top"] + m["height"]:
                self.monitor.setCurrentIndex(i)          # เริ่มที่จอที่กรอบซับอยู่
        for cb in (self.preset, self.monitor):
            polish_popup(cb)
            cb.setCursor(Qt.CursorShape.PointingHandCursor)
        b_auto = button("ใช้", tip="ตั้งกรอบตามตำแหน่งที่เลือก")
        b_auto.clicked.connect(lambda: self.auto_req.emit(self.preset.currentText(), self.monitor.currentIndex()))
        row.addWidget(self.preset, 5)
        row.addWidget(self.monitor, 4)
        row.addWidget(b_auto, 0)
        gl.addLayout(row)

        row = QHBoxLayout()
        row.setSpacing(8)
        b_man = button(" ลากกรอบเอง", T.Glyph.CROP, tip="Ctrl+Alt+R")
        b_man.clicked.connect(lambda: self.manual_req.emit(self.monitor.currentIndex()))
        b_prev = button(" ทดสอบซับ", T.Glyph.VIEW, tip="แสดงซับตัวอย่างบนจอ ดูตำแหน่งและขนาด")
        b_prev.clicked.connect(self.preview_req)
        row.addWidget(b_man, 1)
        row.addWidget(b_prev, 1)
        row.addSpacing(4)
        for glyph, d, tip in ((T.Glyph.UP, -15, "เลื่อนซับไทยขึ้น"), (T.Glyph.DOWN, 15, "เลื่อนซับไทยลง"),
                              (T.Glyph.RESET, 0, "เลื่อนซับไทยกลับตำแหน่งเดิม")):
            mb = button("", glyph, tip=tip)
            mb.setFixedWidth(36)
            mb.clicked.connect(lambda _, d=d: self.move_req.emit(d))
            row.addWidget(mb)
        gl.addLayout(row)
        self.region_lbl = QLabel()
        self.region_lbl.setObjectName("Faint")
        self.region_lbl.setWordWrap(True)
        gl.addWidget(self.region_lbl)
        lay.addWidget(frame)

        # --- ตัวเลือกด่วน
        lay.addWidget(section("ตัวเลือกด่วน"))
        frame, gl = card(spacing=8)
        self.opts = {}
        rows = [
            ("show_english", "แสดงอังกฤษต้นฉบับใต้ซับไทย", ""),
            ("live_translate", "แปลสดประโยคที่ไม่มีในคลัง", ""),
            ("obs_mode", "โหมด OBS (หน้าต่างพื้นเขียวสำหรับ Chroma key)", ""),
        ]
        for i, (key, label, tip) in enumerate(rows):
            if i:
                gl.addWidget(hline())
            r = OptionRow(label, tip, bool(s.get(key)))
            r.toggled.connect(lambda v, k=key: self.option_changed.emit(k, v))
            gl.addWidget(r)
            self.opts[key] = r
        lay.addWidget(frame)

        # --- แถบปุ่มด้านล่าง
        row = QHBoxLayout()
        row.setSpacing(8)
        b_set = button(" ตั้งค่า", T.Glyph.SETTINGS, tip="Ctrl+Alt+O")
        b_set.clicked.connect(self.settings_req)
        b_hist = button(" ประวัติบทพูด", T.Glyph.HISTORY, tip="Ctrl+Alt+H")
        b_hist.clicked.connect(self.history_req)
        b_quit = button(" ปิดโปรแกรม", T.Glyph.POWER, "Danger", tip="Ctrl+Alt+Q", color=T.DANGER)
        b_quit.clicked.connect(self.quit_req)
        row.addWidget(b_set, 1)
        row.addWidget(b_hist, 1)
        row.addWidget(b_quit, 1)
        lay.addLayout(row)

        tip = QLabel("Ctrl+Alt+S เปิด/ปิดซับ  ·  Ctrl+Alt+E อังกฤษ  ·  Ctrl+Alt+B แจ้งบัคซับ\n"
                     "ปิดหน้าต่างนี้ได้ โปรแกรมยังทำงานต่อที่ไอคอนข้างนาฬิกา")
        tip.setObjectName("Faint")
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(tip)

        # จอเตี้ยกว่าหน้าต่าง -> เลื่อนดูได้
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)
        body.setAutoFillBackground(False)
        self.scroll.setWidget(body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.scroll)
        self._body = body

        self.set_running(True)
        self.set_region(s.get("region"))
        self._fit_height()

    def _fit_height(self):
        """ปรับความสูงหน้าต่างให้พอดีเนื้อหา (ไม่เกินความสูงจอ — ถ้าเกินจะเลื่อนดูได้)"""
        # ข้อความที่เพิ่ม/ซ่อนตอนหน้าต่างยังไม่แสดง Qt จะยังไม่คำนวณขนาดใหม่ให้ -> สั่งคำนวณใหม่ทุกชั้น
        for w in self._body.findChildren(QWidget):
            if w.layout() is not None:
                w.layout().invalidate()
        lay = self._body.layout()
        lay.invalidate()
        lay.activate()
        want = max(self._body.sizeHint().height(), lay.totalHeightForWidth(self.width()))
        scr = self.screen() or QGuiApplication.primaryScreen()
        avail = scr.availableGeometry().height() - 60 if scr else want
        self.setFixedHeight(max(360, min(want, avail)))

    def showEvent(self, e):
        super().showEvent(e)
        T.dark_titlebar(self)
        self._fit_height()
        QTimer.singleShot(0, self._fit_height)

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(T.BG))

    # --- สถานะ
    def set_running(self, on):
        self.status.setText("● กำลังทำงาน" if on else "● หยุดอยู่")
        self.status.setProperty("state", "on" if on else "off")
        restyle(self.status)
        self.btn_toggle.setText("  หยุดจับซับชั่วคราว" if on else "  เริ่มจับซับไทย")
        self.btn_toggle.setIcon(T.glyph_icon(T.Glyph.PAUSE if on else T.Glyph.PLAY, T.TEXT if on else "#FFFFFF"))
        self.btn_toggle.setProperty("running", "true" if on else "false")
        restyle(self.btn_toggle)

    def set_total_lines(self, n):
        self.sub_lbl.setText(f"คลังคำแปล {n:,} ประโยค พร้อมใช้งาน" if n else "ซับไทยทับเกมแบบเรียลไทม์")

    def set_note(self, text, ok=True):
        self.region_lbl.setStyleSheet(f"color:{T.OK if ok else T.DANGER}; font-size:9pt; font-weight:600;")
        self.region_lbl.setText(text)

    def set_region(self, r):
        self.region_lbl.setStyleSheet("")
        self.region_lbl.setText(
            f"กรอบตอนนี้ {r['width']}×{r['height']} px ที่ ({r['left']}, {r['top']})  ·  หยุดเกมตอนมีซับแล้วกด หาซับอัตโนมัติ"
            if r else "ยังไม่ได้ตั้งกรอบซับ — หยุดเกมตอนมีซับอังกฤษขึ้น แล้วกด หาซับอัตโนมัติ")

    def set_option(self, key, v):
        if key in self.opts:
            self.opts[key].setChecked(v, silent=True)

    def set_option_hint(self, key, text, ok=True):
        if key in self.opts and self.opts[key].hint.text() != text:
            self.opts[key].setHint(text, None if ok else T.WARN)
            QTimer.singleShot(0, self._fit_height)       # ข้อความเพิ่ม/ลดบรรทัด -> ปรับความสูงหน้าต่าง

    def show_reading(self, en, th, ms=None, state=None):
        self.live.show_reading(en, th, ms, state)

    def closeEvent(self, e):
        if not e.spontaneous():              # โปรแกรมสั่งปิด -> ปิดได้
            e.accept()
            return
        e.ignore()                           # ผู้ใช้กดกากบาท -> ซ่อนไว้ โปรแกรมยังทำงานที่ไอคอนข้างนาฬิกา
        self.hide()
