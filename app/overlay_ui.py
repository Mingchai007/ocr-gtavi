"""หน้าต่างซับไทยโปร่งใสทับเกม + ตัวเลือกกรอบซับ + หน้าตั้งค่า + ประวัติบทพูด (PyQt6)"""
import ctypes
import os
import time

from PyQt6.QtCore import QEvent, QRect, QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (QColor, QDesktopServices, QFont, QFontMetricsF, QGuiApplication, QLinearGradient, QPainter,
                         QPainterPath, QPen, QRadialGradient)
from PyQt6.QtWidgets import (QApplication, QComboBox, QDialog, QFontComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QMenu, QMessageBox, QSlider, QSpinBox, QStackedWidget, QStyle,
                             QStyledItemDelegate, QVBoxLayout, QWidget)

from app import settings as cfg
from app import theme as T
from app.widgets import ColorButton, OptionRow, Segmented, button, card, hline, polish_popup

WDA_NONE, WDA_EXCLUDEFROMCAPTURE = 0x0, 0x11


def game_monitor(region):
    """ลำดับจอ (แบบ mss) ที่กรอบซับเกมอยู่ หรือ None"""
    if not region:
        return None
    try:
        from app.control_panel import monitors
        cx, cy = region["left"] + region["width"] / 2, region["top"] + region["height"] / 2
        for i, m in enumerate(monitors()):
            if m["left"] <= cx < m["left"] + m["width"] and m["top"] <= cy < m["top"] + m["height"]:
                return i
    except Exception:
        pass
    return None


def screen_dpr():
    return QGuiApplication.primaryScreen().devicePixelRatio()


# ---------------------------------------------------------------- วาดซับ (ใช้ทั้งบนจอจริงและตัวอย่างในหน้าตั้งค่า)
def _font(s, scale=1.0):
    f = QFont(s["font_family"])
    f.setPixelSize(max(8, int(s["font_size"] * scale)))
    f.setWeight(QFont.Weight(s["font_weight"]))
    return f


def wrap_text(text, fm, max_w):
    """ตัดบรรทัดแบบซับหนัง: ถ้าเกิน 1 บรรทัดจะแบ่งความยาว 2 บรรทัดให้สมดุล อ่านกวาดตาได้ไวขึ้น"""
    total_w = fm.horizontalAdvance(text)
    if total_w <= max_w:
        return [text]
    # ถ้าพอดี 2 บรรทัด -> หาจุดแบ่งเว้นวรรคที่ทำให้ 2 บรรทัดยาวใกล้เคียงกันที่สุด
    if total_w <= max_w * 1.92 and " " in text:
        words = text.split(" ")
        best_pair, best_diff = None, 1e9
        for k in range(1, len(words)):
            a, b = " ".join(words[:k]), " ".join(words[k:])
            wa, wb = fm.horizontalAdvance(a), fm.horizontalAdvance(b)
            if wa <= max_w and wb <= max_w:
                diff = abs(wa - wb)
                if diff < best_diff:
                    best_diff, best_pair = diff, [a, b]
        if best_pair:
            return best_pair
    lines, cur = [], ""
    for word in text.split(" "):
        cand = f"{cur} {word}".strip()
        if fm.horizontalAdvance(cand) <= max_w or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = word
        while fm.horizontalAdvance(cur) > max_w and len(cur) > 1:
            cut = len(cur)
            while cut > 1 and fm.horizontalAdvance(cur[:cut]) > max_w:
                cut -= 1
            lines.append(cur[:cut])
            cur = cur[cut:]
    if cur:
        lines.append(cur)
    return lines


def layout_subtitle(s, blocks, max_w):
    """จัดบรรทัดของแต่ละบล็อก -> ([(font, fm, บรรทัด, alpha, คำนำหน้า)], ความสูงรวม, ความกว้างสุด)"""
    laid = []
    for text, scale, alpha, speaker in blocks:
        font = _font(s, scale)
        fm = QFontMetricsF(font)
        prefix = f"{speaker}: " if (speaker and s["show_speaker"]) else ""
        laid.append((font, fm, wrap_text(prefix + text, fm, max_w), alpha, prefix))
    total_h = sum(fm.height() * len(w) for _, fm, w, _, _ in laid) + 6 * (len(laid) - 1)
    widest = max((fm.horizontalAdvance(line) for _, fm, w, _, _ in laid for line in w), default=0)
    return laid, total_h, widest


def paint_subtitle(p, s, blocks, width, bottom):
    """วาดบล็อกซับ [(ข้อความ, scale, alpha, ชื่อคนพูด)] จากบนลงล่าง ให้บรรทัดสุดท้ายชิด bottom"""
    if not blocks:
        return
    laid, total_h, widest = layout_subtitle(s, blocks, width - 40)
    y = bottom - total_h
    if s["box_opacity"] > 0:
        box = QRectF((width - widest) / 2 - 16, y - 8, widest + 32, total_h + 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, s["box_opacity"]))
        p.drawRoundedRect(box, 10, 10)
    for font, fm, wrapped, alpha, prefix in laid:
        for i, line in enumerate(wrapped):
            y += fm.ascent()
            x = (width - fm.horizontalAdvance(line)) / 2
            _draw_text(p, s, font, fm, x, y, line, alpha, prefix if i == 0 else "")
            y += fm.descent()
        y += 6


def _draw_text(p, s, font, fm, x, y, line, alpha, prefix):
    text_col = QColor(s["text_color"])
    text_col.setAlphaF(alpha)
    out_col = QColor(s["outline_color"])
    out_col.setAlphaF(alpha)
    spk_col = QColor(s["speaker_color"])
    spk_col.setAlphaF(alpha)
    parts = [(prefix, spk_col), (line[len(prefix):], text_col)] if prefix and line.startswith(prefix) \
        else [(line, text_col)]
    cx = x
    for txt, col in parts:
        if not txt:
            continue
        path = QPainterPath()
        path.addText(cx, y, font, txt)
        if s["shadow"]:
            p.save()
            p.translate(2, 3)
            p.fillPath(path, QColor(0, 0, 0, int(140 * alpha)))
            p.restore()
        if s["outline_width"] > 0:
            pen = QPen(out_col, s["outline_width"] * 2, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            p.strokePath(path, pen)
        p.fillPath(path, col)
        cx += fm.horizontalAdvance(txt)


# ---------------------------------------------------------------- overlay
class SubtitleOverlay(QWidget):
    PREV_TTL = 10.0     # ประโยคก่อนหน้าแสดงอยู่ได้นานสุดกี่วินาที

    def __init__(self, s):
        super().__init__(None)
        self.s = s
        self.current = None          # {"th", "en", "speaker"}
        self.history = []            # [(time, line)]
        self.hide_timer = QTimer(self, singleShot=True, timeout=self._hide_current)
        self.shown_at = 0.0
        self.mirror = None           # หน้าต่างสำหรับ OBS (ObsFeed) แสดงซับเดียวกัน
        self.is_sample = False       # บรรทัดที่แสดงอยู่เป็น "ซับตัวอย่าง" (ไม่ใช่ซับจากเกม)
        # เกม/เบราว์เซอร์เต็มจอชอบแย่ง "อยู่บนสุด" -> ยกหน้าต่างซับขึ้นใหม่ทุก 2 วิ
        self.top_timer = QTimer(self, interval=2000, timeout=self._keep_on_top)
        self.top_timer.start()
        self.setWindowTitle("GTA TH Sub")
        self.apply_settings(s)

    # --- รูปแบบหน้าต่าง
    def apply_settings(self, s):
        self.s = s
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput)
        was_visible = self.isVisible()
        if self.windowFlags() != flags:
            self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, not s["obs_mode"])
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.place()
        if was_visible or s["obs_mode"]:
            self.show()
        self._apply_capture()
        self.update()

    def _apply_capture(self):
        """ปกติซ่อนหน้าต่างซับจากโปรแกรมจับภาพทุกตัว (รวมถึง OCR ของเราเอง -> ไม่อ่านซับไทยของตัวเอง)
        ถ้าผู้ใช้เปิด "ให้ OBS เห็นซับไทย" (สตรีม) หรือโหมด OBS -> ให้จับภาพได้"""
        if not self.isVisible():
            return
        visible = self.s.get("obs_mode") or self.s.get("stream_visible")
        try:
            ctypes.windll.user32.SetWindowDisplayAffinity(int(self.winId()), WDA_NONE if visible else WDA_EXCLUDEFROMCAPTURE)
        except Exception:
            pass

    def showEvent(self, e):
        super().showEvent(e)
        self._apply_capture()

    def place(self):
        r = self.s.get("region")
        if not r:
            scr = QGuiApplication.primaryScreen().geometry()
            w, h = int(scr.width() * 0.7), 260
            self.setGeometry(scr.x() + (scr.width() - w) // 2, scr.bottom() - h - 60, w, h)
            return
        # หาจอที่กรอบซับอยู่ (รองรับหลายจอ เช่นภาพ capture card อยู่จอที่ 2)
        cx, cy = r["left"] + r["width"] / 2, r["top"] + r["height"] / 2
        screen = QGuiApplication.primaryScreen()
        for s in QGuiApplication.screens():
            g, d = s.geometry(), s.devicePixelRatio()
            if g.x() <= cx < g.x() + g.width() * d and g.y() <= cy < g.y() + g.height() * d:
                screen = s
                break
        scr, dpr = screen.geometry(), screen.devicePixelRatio()
        # พิกัดจริง (พิกเซลจอ) -> พิกัด Qt ของจอนั้น
        rx = scr.x() + (r["left"] - scr.x()) / dpr
        ry = scr.y() + (r["top"] - scr.y()) / dpr
        rw, rh = r["width"] / dpr, r["height"] / dpr
        w = min(scr.width(), rw * self.s["width_scale"])
        h = max(260, rh * 2.2)
        x = rx + rw / 2 - w / 2
        bottom = (ry + rh) if self.s["position"] == "cover" else ry
        y = bottom - h + self.s["offset_y"]
        x = max(scr.x(), min(x, scr.right() - w))
        self.setGeometry(int(x), int(y), int(w), int(h))

    # --- ข้อมูลซับ
    def set_line(self, th, en="", speaker="", sample=False):
        self.set_parts([(speaker, th)], en, new_line=True, sample=sample)

    def set_parts(self, parts, en="", new_line=True, sample=False):
        """แสดงซับ: parts = [(ชื่อคนพูด, คำแปลไทย)] — หลายคนพูดพร้อมกันได้ (แสดงคนละบรรทัด)
        new_line=False = ซับเดิมบนจอ แค่เติม/แก้คำแปล -> ไม่ดันขึ้นไปเป็นประโยคก่อนหน้า"""
        th = "  ".join(t for _, t in parts)
        speaker = parts[0][0] if len(parts) == 1 else ""
        line = {"th": th, "en": en, "speaker": speaker, "parts": list(parts)}
        was_sample, self.is_sample = self.is_sample, sample
        self.hide_timer.stop()
        if self.current and new_line and not was_sample and self.current["th"] != th:
            self._remember(self.current)
        self.current = line
        self.shown_at = time.time()
        if self.mirror:
            self.mirror.set_parts(parts, en, new_line, sample)
        if not self.isVisible():
            self.show()
        self.update()

    def update_line(self, en, th):
        """คำแปลสดมาถึงทีหลัง -> แทนที่บรรทัดปัจจุบันถ้ายังเป็นประโยคเดิม"""
        if self.current and self.current["en"] == en:
            self.current["th"] = th
            self.update()
        if self.mirror:
            self.mirror.update_line(en, th)

    MIN_SHOW = 1.2

    def clear_soon(self):
        if self.mirror:
            self.mirror.clear_soon()
        if self.is_sample:
            return                                  # ซับตัวอย่างอยู่จนกว่าจะปิดหน้าตั้งค่า/หมดเวลาของมันเอง
        if self.current and not self.hide_timer.isActive():
            # คำนวณเวลาอ่านขั้นต่ำตามความยาวซับไทย (1.2 - 3.2 วินาที) ให้อ่านทัน แต่เปลี่ยนทันทีถ้ามีซับใหม่มา
            th_len = len(self.current.get("th", ""))
            min_read = max(self.MIN_SHOW, min(3.2, th_len / 26.0))
            left = max(0.0, min_read - (time.time() - self.shown_at)) * 1000
            self.hide_timer.start(int(max(self.s["hide_after_ms"], left)))

    def set_sample(self, th, en="", speaker="", hide_after_ms=0):
        """แสดงซับตัวอย่าง (ตอนตั้งค่า/ทดสอบตำแหน่ง) — ไม่เก็บเป็นประโยคก่อนหน้า และซ่อนเองได้"""
        if self.current and not self.is_sample:
            return                                  # มีซับจริงจากเกมอยู่ ไม่ทับ
        self.current = None
        self.set_line(th, en, speaker, sample=True)
        if hide_after_ms:
            self.hide_timer.start(hide_after_ms)

    def clear_sample(self):
        if self.is_sample:
            self.hide_timer.stop()
            self.current = None
            self.is_sample = False
            self.update()

    def _keep_on_top(self):
        try:
            if self.isVisible():
                hwnd = int(self.winId())
                # HWND_TOPMOST, SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE
                ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0010)
        except Exception:
            pass

    def _remember(self, line):
        """เก็บเป็น "ประโยคก่อนหน้า" (ไม่เก็บซ้ำประโยคเดียวกันติดกัน)"""
        if self.history and self.history[-1][1]["th"] == line["th"]:
            self.history[-1] = (time.time(), line)
            return
        self.history.append((time.time(), line))
        self.history = self.history[-5:]

    def _hide_current(self):
        if self.current and not self.is_sample:
            self._remember(self.current)
        self.current = None
        self.is_sample = False
        self.update()

    # --- วาด
    def _blocks(self):
        """คืนรายการ (ข้อความ, scale, ความทึบ, ชื่อคนพูด) จากบนลงล่าง"""
        s = self.s
        out = []
        if not self.current:
            return out
        now = time.time()
        if s["prev_lines"] > 0:
            prev = [ln for t, ln in self.history if now - t < self.PREV_TTL][-s["prev_lines"]:]
            for ln in prev:
                for spk, th in ln.get("parts") or [(ln["speaker"], ln["th"])]:
                    out.append((th, s["prev_scale"], 0.6, spk))
        cur = self.current
        for spk, th in cur.get("parts") or [(cur["speaker"], cur["th"])]:
            out.append((th, 1.0, 1.0, spk))                 # หลายคนพูด -> คนละบรรทัด พร้อมชื่อ
        if s["show_english"] and cur["en"]:
            out.append((cur["en"], 0.55, 0.85, ""))
        return out

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if self.s["obs_mode"]:
            p.fillRect(self.rect(), QColor(self.s["obs_color"]))
        paint_subtitle(p, self.s, self._blocks(), self.width(), self.height() - 12)


class ObsFeed(SubtitleOverlay):
    """หน้าต่างพื้นสีทึบแยกต่างหาก ให้ OBS / TikTok LIVE Studio จับภาพ (Window Capture หรือจับทั้งจอ)
    (ไม่ทับเกม จึงไม่บังซับอังกฤษที่ OCR ต้องอ่าน)
    เลือกให้แสดงเต็มจอบนจอที่สองได้ แบบ Fullscreen Projector ของ OBS — ดับเบิลคลิกหรือกด Esc เพื่อออกจากเต็มจอ"""
    exit_fullscreen = pyqtSignal()

    def apply_settings(self, s):
        self.s = {**s, "obs_mode": True, "prev_lines": s.get("prev_lines", 1)}
        self.setWindowTitle("GTA TH Sub - OBS")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self._set_fullscreen(s.get("obs_screen", -1))
        self.update()

    def _set_fullscreen(self, index):
        if getattr(self, "_fs_index", None) == index:
            return
        self._fs_index = index
        screen = None
        if index is not None and index >= 0:
            try:
                from app.control_panel import screen_for_monitor
                screen = screen_for_monitor(index)
            except Exception:
                screen = None
        was_visible = self.isVisible()
        if screen is not None:
            self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
            self.setGeometry(screen.geometry())
            if was_visible:
                self.showFullScreen()
        else:
            self.setWindowFlags(Qt.WindowType.Window)
            if was_visible:
                self.showNormal()
            self.resize(1280, 220)

    def show(self):
        if getattr(self, "_fs_index", -1) is not None and getattr(self, "_fs_index", -1) >= 0:
            self.showFullScreen()
        else:
            super().show()

    def mouseDoubleClickEvent(self, e):
        if getattr(self, "_fs_index", -1) >= 0:
            self.exit_fullscreen.emit()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape and getattr(self, "_fs_index", -1) >= 0:
            self.exit_fullscreen.emit()

    def place(self):
        pass

    def showEvent(self, e):
        QWidget.showEvent(self, e)

    def _keep_on_top(self):
        pass

    def closeEvent(self, e):
        if not e.spontaneous():              # โปรแกรมสั่งปิด (ปิดโปรแกรม) -> ปิดได้
            e.accept()
            return
        e.ignore()                           # ผู้ใช้กดกากบาท -> แค่ย่อ (OBS/TikTok ยังจับหน้าต่างได้ตอนเปิดกลับ)
        if self.isFullScreen():
            self.exit_fullscreen.emit()
        else:
            self.showMinimized()


# ---------------------------------------------------------------- เลือกกรอบซับ
class RegionSelector(QWidget):
    selected = pyqtSignal(dict)

    def __init__(self, screen=None):
        super().__init__(None)
        self.scr = screen or QGuiApplication.primaryScreen()
        self.shot = self.scr.grabWindow(0)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setGeometry(self.scr.geometry())
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.a = self.b = None

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.drawPixmap(self.rect(), self.shot)
        p.fillRect(self.rect(), QColor(0, 0, 0, 150))
        dpr = self.shot.devicePixelRatio()
        if self.a and self.b:
            r = QRect(self.a, self.b).normalized()
            p.drawPixmap(r, self.shot, QRectF(r.x() * dpr, r.y() * dpr, r.width() * dpr, r.height() * dpr).toRect())
            p.setPen(QPen(QColor(T.OK), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(r).adjusted(-1, -1, 1, 1), 4, 4)
            real_dpr = self.scr.devicePixelRatio()
            self._pill(p, f"{int(r.width() * real_dpr)} × {int(r.height() * real_dpr)} px", r.center().x(), r.bottom() + 12, 10)
        self._pill(p, "ลากกรอบครอบบริเวณที่ซับอังกฤษขึ้น (เผื่อซับ 2-3 บรรทัด)   ·   Esc = ยกเลิก", self.width() / 2, 36, 12)

    def _pill(self, p, text, cx, top, point):
        f = QFont("Leelawadee UI")
        f.setPointSizeF(point)
        f.setBold(True)
        p.setFont(f)
        fm = QFontMetricsF(f)
        w, h = fm.horizontalAdvance(text) + 32, fm.height() + 14
        box = QRectF(cx - w / 2, top, w, h)
        box.moveLeft(max(8, min(box.left(), self.width() - w - 8)))
        if box.bottom() > self.height() - 8:
            box.moveBottom(self.height() - 8)
        p.setPen(QPen(QColor(T.STROKE_HI), 1))
        p.setBrush(QColor(23, 23, 23, 235))
        p.drawRoundedRect(box, h / 2, h / 2)
        p.setPen(QColor(T.TEXT))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    def mousePressEvent(self, e):
        self.a = self.b = e.position().toPoint()
        self.update()

    def mouseMoveEvent(self, e):
        self.b = e.position().toPoint()
        self.update()

    def mouseReleaseEvent(self, e):
        if self.a is None:
            return
        r = QRect(self.a, e.position().toPoint()).normalized()
        self.close()
        if r.width() > 40 and r.height() > 15:
            dpr = self.scr.devicePixelRatio()
            g = self.scr.geometry()
            self.selected.emit({"left": int(g.x() + r.x() * dpr), "top": int(g.y() + r.y() * dpr),
                                "width": int(r.width() * dpr), "height": int(r.height() * dpr)})

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close()


# ---------------------------------------------------------------- ตัวอย่างซับในหน้าตั้งค่า
class SubtitlePreview(QWidget):
    """แถบตัวอย่าง: วาดซับด้วยการตั้งค่าปัจจุบันบนพื้นคล้ายฉากเกม (ย่ออัตโนมัติถ้าตัวใหญ่เกินแถบ)"""

    def __init__(self, s):
        super().__init__()
        self.s = s
        self.setFixedHeight(128)

    def set_settings(self, s):
        self.s = s
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(r, 12, 12)
        p.setClipPath(clip)
        if self.s.get("obs_mode"):
            p.fillRect(self.rect(), QColor(self.s["obs_color"]))
        else:
            sky = QLinearGradient(0, 0, 0, self.height())            # ท้องฟ้ายามเย็น + แสงเมือง
            sky.setColorAt(0.0, QColor("#3A3F47"))
            sky.setColorAt(0.55, QColor("#5A5148"))
            sky.setColorAt(1.0, QColor("#141414"))
            p.fillRect(self.rect(), sky)
            sun = QRadialGradient(self.width() * 0.72, self.height() * 0.62, self.height() * 0.9)
            sun.setColorAt(0, QColor(255, 150, 90, 150))
            sun.setColorAt(1, QColor(255, 150, 90, 0))
            p.fillRect(self.rect(), sun)
        s = self.s
        blocks = []
        if s["prev_lines"] > 0:
            blocks.append(("ประโยคก่อนหน้า", s["prev_scale"], 0.6, "ลูเซีย"))
        blocks.append(("ตัวอย่างซับไทย ปรับแล้วเห็นผลทันที", 1.0, 1.0, "เจสัน"))
        if s["show_english"]:
            blocks.append(("Sample subtitle, changes apply instantly", 0.55, 0.85, ""))
        _, total_h, widest = layout_subtitle(s, blocks, 4000)
        k = min(1.0, (self.height() - 22) / max(1.0, total_h + 16), (self.width() - 24) / max(1.0, widest + 40))
        p.save()
        p.scale(k, k)
        paint_subtitle(p, s, blocks, self.width() / k, (self.height() - 12) / k)
        p.restore()
        p.setClipping(False)
        p.setPen(QPen(QColor(T.STROKE), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 12, 12)
        if k < 0.99:
            f = QFont("Leelawadee UI")
            f.setPointSizeF(8)
            p.setFont(f)
            p.setPen(QColor(255, 255, 255, 170))
            p.drawText(self.rect().adjusted(10, 6, -10, -6), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                       f"ย่อ {k * 100:.0f}%")


# ---------------------------------------------------------------- หน้าตั้งค่า
class SettingsDialog(QDialog):
    changed = pyqtSignal(dict)
    update_req = pyqtSignal()                   # ผู้ใช้กด "เช็กอัปเดตตอนนี้"
    restart_req = pyqtSignal()                  # ผู้ใช้กด "รีสตาร์ทเพื่อใช้รุ่นใหม่"
    WEIGHTS = [("ปกติ", 400), ("กลาง", 600), ("หนา", 700), ("หนามาก", 900)]
    FPS = [("ประหยัดเครื่อง", 15), ("ปกติ", 30), ("ไวที่สุด", 60)]

    def __init__(self, s, parent=None, live_status=None, can_report=False, web_url=None):
        super().__init__(parent)
        self.setObjectName("Root")
        self.setWindowTitle("ตั้งค่า — ซับไทย GTA VI")
        self.setWindowIcon(T.app_icon())
        self.setFixedWidth(500)
        self.s = dict(s)
        self.live_status = live_status          # ฟังก์ชันคืน (ข้อความสถานะตัวแปลสด, พร้อมใช้ไหม)
        self._update_status = ("", True)
        self._restart = False
        self.can_report = can_report            # มีปลายทางรับประโยคตกหล่น (data/report_target.json) ไหม
        self.web_url = web_url                  # ฟังก์ชันคืนลิงก์หน้าเว็บซับ ("" = ยังไม่ได้เปิด)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 14)
        lay.setSpacing(12)
        self.preview = SubtitlePreview(self.s)
        lay.addWidget(self.preview)
        self.tabs = Segmented(["หน้าตา", "ตำแหน่ง", "การแปล", "ระบบ"], 0,
                              [T.Glyph.PALETTE, T.Glyph.MOVE, T.Glyph.GLOBE, T.Glyph.SYSTEM])
        lay.addWidget(self.tabs)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        self.tabs.changed.connect(self.stack.setCurrentIndex)
        self._build_pages()

        row = QHBoxLayout()
        row.setSpacing(8)
        reset = button(" คืนค่าเริ่มต้น", T.Glyph.RESET, tip="คืนค่าหน้าตาและตำแหน่งซับเป็นค่าเริ่มต้น (ไม่แตะกรอบซับและคีย์)")
        reset.clicked.connect(self._reset)
        done = button("เสร็จสิ้น", kind="Primary")
        done.setMinimumSize(120, 36)
        done.clicked.connect(self.accept)
        row.addWidget(reset)
        row.addStretch(1)
        row.addWidget(done)
        lay.addLayout(row)

    def showEvent(self, e):
        super().showEvent(e)
        T.dark_titlebar(self)

    def done(self, r):
        try:
            self._key_changed()                 # เผื่อพิมพ์คีย์แล้วปิดหน้าต่างเลย
        except RuntimeError:
            pass
        super().done(r)

    # --- สร้างหน้า
    def _build_pages(self):
        while self.stack.count():
            w = self.stack.widget(0)
            self.stack.removeWidget(w)
            w.deleteLater()
        for build in (self._page_look, self._page_position, self._page_translate, self._page_system):
            frame, lay = card(spacing=10, margins=(16, 14, 16, 14))
            build(lay)
            lay.addStretch(1)
            self.stack.addWidget(frame)
        self.stack.setCurrentIndex(max(0, self.tabs.currentIndex()))

    @staticmethod
    def _form():
        f = QFormLayout()
        f.setHorizontalSpacing(16)
        f.setVerticalSpacing(10)
        f.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        return f

    def _page_look(self, lay):
        f = self._form()
        font = QFontComboBox()
        font.setCurrentFont(QFont(self.s["font_family"]))
        font.currentFontChanged.connect(lambda fnt: self._set("font_family", fnt.family()))
        polish_popup(font)
        f.addRow("ฟอนต์", font)
        f.addRow("ขนาดตัวอักษร", self._slider("font_size", 12, 96, "{} px"))
        weight = Segmented([n for n, _ in self.WEIGHTS],
                           next((i for i, (_, w) in enumerate(self.WEIGHTS) if w == self.s["font_weight"]), 2))
        weight.changed.connect(lambda i: self._set("font_weight", self.WEIGHTS[i][1]))
        f.addRow("ความหนา", weight)
        colors = QHBoxLayout()
        colors.setSpacing(8)
        colors.addWidget(self._color("text_color", "สีตัวอักษร"), 1)
        colors.addWidget(self._color("outline_color", "สีขอบ"), 1)
        f.addRow("สีตัวอักษร / ขอบ", colors)
        f.addRow("ความหนาขอบ", self._slider("outline_width", 0, 12, "{} px"))
        f.addRow("กล่องพื้นหลัง", self._slider("box_opacity", 0, 255, lambda v: "ปิด" if v == 0 else f"{v * 100 // 255}%"))
        lay.addLayout(f)
        lay.addWidget(hline())
        lay.addWidget(self._option("shadow", "เงาใต้ตัวอักษร"))
        spk = QHBoxLayout()
        spk.setSpacing(10)
        spk.addWidget(self._option("show_speaker", "แสดงชื่อคนพูด (เช่น เจสัน:)"), 1)
        c = self._color("speaker_color", "สีชื่อคนพูด")
        c.setFixedWidth(118)
        spk.addWidget(c)
        lay.addLayout(spk)
        lay.addWidget(self._option("show_english", "แสดงอังกฤษต้นฉบับใต้ซับไทย"))

    def _page_position(self, lay):
        f = self._form()
        pos = Segmented(["ทับซับอังกฤษ", "เหนือซับอังกฤษ"], 0 if self.s["position"] == "cover" else 1)
        pos.changed.connect(lambda i: self._set("position", "cover" if i == 0 else "above"))
        f.addRow("ตำแหน่งวางซับ", pos)
        f.addRow("เลื่อนขึ้น / ลง", self._spin("offset_y", -600, 600, " px"))
        f.addRow("ความกว้างบรรทัด", self._slider("width_scale", 80, 160, "{}%", scale=100))
        prev = Segmented(["ไม่แสดง", "1 บรรทัด", "2", "3"], int(self.s["prev_lines"]))
        prev.changed.connect(lambda i: self._set("prev_lines", i))
        f.addRow("ประโยคก่อนหน้า", prev)
        lay.addLayout(f)
        lay.addWidget(hline())
        lay.addWidget(self._option("web_feed", "ลิงก์ซับพื้นโปร่งใส (TikTok LIVE Studio)",
                                   "ใส่ใน TikTok เป็นแหล่งภาพแบบ \"ลิงก์\" — ซับลอยบนเกม ไม่มีฉากเขียว ไม่ต้องใช้ OBS"))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.web_lbl = QLineEdit()
        self.web_lbl.setReadOnly(True)
        self.web_btn = button(" คัดลอกลิงก์", T.Glyph.COPY)
        self.web_btn.clicked.connect(self._copy_web_url)
        row.addWidget(self.web_lbl, 1)
        row.addWidget(self.web_btn)
        lay.addLayout(row)
        self._refresh_web_url()
        lay.addWidget(hline())
        lay.addWidget(self._option("stream_visible", "ให้ OBS / โปรแกรมสตรีมเห็นซับไทย",
                                   "เปิดตอนสตรีมด้วยการจับทั้งจอ — แนะนำตั้งตำแหน่งเป็น \"เหนือซับอังกฤษ\""))
        lay.addWidget(self._option("obs_mode", "โหมด OBS (หน้าต่างแยก)",
                                   "หน้าต่างซับแยก ให้ OBS / TikTok จับภาพ (เลือกเต็มจอบนจอสองได้ด้านล่าง)"))
        f2 = self._form()
        f2.addRow("สีพื้นหน้าต่าง OBS", self._color("obs_color", "สีพื้น OBS"))
        f2.addRow("แสดงหน้าต่างแยก", self._obs_screen_combo())
        lay.addLayout(f2)
        self.obs_screen_note = QLabel()
        self.obs_screen_note.setObjectName("Muted")
        self.obs_screen_note.setWordWrap(True)
        lay.addWidget(self.obs_screen_note)
        self._obs_screen_hint()

    def _page_translate(self, lay):
        lay.addWidget(self._option("live_translate", "แปลสดประโยคที่ไม่มีในคลัง",
                                   "ส่งประโยคไปแปลเบื้องหลัง แล้วเก็บเข้าคลังให้ครั้งหน้าขึ้นทันที"))
        lay.addWidget(hline())
        t = QLabel("คีย์ Gemini (แปลสำนวนดี รู้จักชื่อตัวละคร)")
        t.setObjectName("RowTitle")
        lay.addWidget(t)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.key_edit = QLineEdit(self.s.get("gemini_key", ""))
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("วางคีย์ที่นี่ (ขึ้นต้นด้วย AIza…)")
        self.key_edit.setClearButtonEnabled(True)
        self.key_edit.editingFinished.connect(self._key_changed)
        eye = button("", T.Glyph.VIEW, tip="แสดง/ซ่อนคีย์")
        eye.setCheckable(True)
        eye.setFixedWidth(38)
        eye.toggled.connect(lambda on: self.key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        row.addWidget(self.key_edit, 1)
        row.addWidget(eye)
        lay.addLayout(row)
        how = QLabel('ขอคีย์ฟรีได้ที่ <a style="color:#34D399" href="https://aistudio.google.com/apikey">aistudio.google.com/apikey</a>'
                     " — คีย์เก็บไว้ในเครื่องนี้เท่านั้น ไม่อยู่ในโค้ดแชร์")
        how.setObjectName("Muted")
        how.setWordWrap(True)
        how.setOpenExternalLinks(True)
        lay.addWidget(how)
        self.live_lbl = QLabel()
        self.live_lbl.setObjectName("Muted")
        self.live_lbl.setWordWrap(True)
        lay.addWidget(self.live_lbl)
        lay.addWidget(hline())
        lay.addWidget(self._option("use_mymemory", "ใช้ MyMemory เป็นตัวแปลสำรอง",
                                   "ฟรี ไม่ต้องมีคีย์ แต่แปลตรงตัว สำนวนแข็งกว่า Gemini"))
        lay.addWidget(self._option("show_untranslated", "แสดงอังกฤษไปก่อนถ้ายังไม่มีคำแปล"))
        self._refresh_live_status()

    def _page_system(self, lay):
        f = self._form()
        fps = Segmented([n for n, _ in self.FPS],
                        min(range(len(self.FPS)), key=lambda i: abs(self.FPS[i][1] - int(self.s["fps"]))))
        fps.changed.connect(lambda i: self._set("fps", self.FPS[i][1]))
        fps.setToolTip("ความถี่ที่โปรแกรมเช็กว่าซับบนจอเปลี่ยนหรือยัง — ยิ่งถี่ ซับไทยยิ่งขึ้นไว แต่ใช้ CPU มากขึ้น")
        f.addRow("ความไวจับซับ", fps)
        f.addRow("ความสว่างตัวซับ", self._slider("brightness", 80, 250, "{}"))
        f.addRow("ซ่อนซับหลังซับหาย", self._slider("hide_after_ms", 200, 3000, lambda v: f"{v / 1000:.1f} วิ", step=100))
        lay.addLayout(f)
        note = QLabel("ความสว่างตัวซับ: ลดลงถ้าโปรแกรมอ่านซับไม่ออก เพิ่มขึ้นถ้าอ่านพื้นหลังมั่ว")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addWidget(hline())
        lay.addWidget(self._option("auto_update", "อัปเดตอัตโนมัติ",
                                   "เช็กตอนเปิดโปรแกรม โหลดเฉพาะคลังคำแปลใหม่ (~4 MB) และโค้ดโปรแกรมรุ่นใหม่ (<1 MB)"))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.update_lbl = QLabel()
        self.update_lbl.setObjectName("Muted")
        self.update_lbl.setWordWrap(True)
        self.update_btn = button(" เช็กตอนนี้", T.Glyph.RESET)
        self.update_btn.clicked.connect(lambda: self.restart_req.emit() if self._restart else self._ask_update())
        row.addWidget(self.update_lbl, 1)
        row.addWidget(self.update_btn)
        lay.addLayout(row)
        self.set_update_status(*self._update_status, restart=self._restart)
        if self.can_report:
            lay.addWidget(hline())
            lay.addWidget(self._option("report_missed", "ส่งประโยคที่ยังไม่มีคำแปลให้ผู้ทำคลัง",
                                       "ส่งเฉพาะซับอังกฤษที่อ่านได้จากจอ เบื้องหลัง ไม่หน่วงเกม — จะได้เติมคลังให้ทุกคน"))
        lay.addWidget(hline())
        t = QLabel("แชร์การตั้งค่าให้เพื่อน")
        t.setObjectName("RowTitle")
        lay.addWidget(t)
        row = QHBoxLayout()
        row.setSpacing(8)
        b_copy = button(" คัดลอกโค้ด", T.Glyph.COPY)
        b_copy.clicked.connect(self._copy_code)
        b_paste = button(" วางโค้ด", T.Glyph.PASTE)
        b_paste.clicked.connect(self._paste_code)
        row.addWidget(b_copy, 1)
        row.addWidget(b_paste, 1)
        lay.addLayout(row)
        self.share_lbl = QLabel("โค้ดรวมฟอนต์ สี ตำแหน่ง แต่ไม่รวมกรอบซับและคีย์ของเครื่องคุณ")
        self.share_lbl.setObjectName("Muted")
        self.share_lbl.setWordWrap(True)
        lay.addWidget(self.share_lbl)
        lay.addWidget(hline())
        row = QHBoxLayout()
        row.setSpacing(8)
        b_dir = button(" โฟลเดอร์ข้อมูล", T.Glyph.FOLDER, tip="เปิดโฟลเดอร์ data (คลังคำแปล, ประโยคที่ยังไม่มีคำแปล)")
        b_dir.clicked.connect(lambda: self._open(cfg.PATH.parent))
        b_log = button(" บันทึกการทำงาน", T.Glyph.DOCUMENT, tip="เปิดไฟล์ app.log (ใช้ดูเวลามีปัญหา)")
        b_log.clicked.connect(lambda: self._open(cfg.PATH.parent / "app.log"))
        row.addWidget(b_dir, 1)
        row.addWidget(b_log, 1)
        lay.addLayout(row)

    # --- ลิงก์ซับสำหรับ TikTok
    def _refresh_web_url(self):
        if not hasattr(self, "web_lbl"):
            return
        try:
            url = self.web_url() if self.web_url else ""
        except RuntimeError:
            return
        self.web_lbl.setText(url or "เปิดสวิตช์ด้านบนก่อน แล้วลิงก์จะขึ้นตรงนี้")
        if url:
            port = url.rstrip("/").rsplit(":", 1)[-1]
            self.web_lbl.setToolTip(f"ถ้า TikTok ไม่รับลิงก์นี้ ลองแบบอื่น:\nhttp://127.0.0.1.nip.io:{port}/\n"
                                    f"http://localhost:{port}/")
        self.web_lbl.setEnabled(bool(url))
        self.web_btn.setEnabled(bool(url))

    def _copy_web_url(self):
        url = self.web_url() if self.web_url else ""
        if url:
            QApplication.clipboard().setText(url)
            self.web_btn.setText(" คัดลอกแล้ว")
            QTimer.singleShot(1500, lambda: self.web_btn.setText(" คัดลอกลิงก์"))

    # --- หน้าต่างซับแยกแบบเต็มจอ
    def _obs_screen_combo(self):
        from app.control_panel import monitors
        cb = QComboBox()
        cb.addItem("หน้าต่างปกติ (ลากปรับขนาดได้)", -1)
        try:
            for i, m in enumerate(monitors()):
                cb.addItem(f"เต็มจอ — จอ {i + 1}  {m['width']}×{m['height']}", i)
        except Exception:
            pass
        cb.setCurrentIndex(max(0, cb.findData(int(self.s.get("obs_screen", -1)))))
        cb.currentIndexChanged.connect(lambda i: (self._set("obs_screen", cb.itemData(i)), self._obs_screen_hint()))
        polish_popup(cb)
        return cb

    def _obs_screen_hint(self):
        idx = int(self.s.get("obs_screen", -1))
        if idx < 0:
            text, warn = "", False
        elif game_monitor(self.s.get("region")) == idx:
            text, warn = "จอนี้คือจอที่โปรแกรมอ่านซับเกมอยู่ — ถ้าแสดงเต็มจอตรงนี้จะบังเกม ให้เลือกจออื่น", True
        else:
            text, warn = "ดับเบิลคลิกหรือกด Esc บนจอนั้นเพื่อออกจากเต็มจอ", False
        self.obs_screen_note.setText(text)
        self.obs_screen_note.setVisible(bool(text))
        self.obs_screen_note.setStyleSheet(f"color:{T.WARN};" if warn else "")

    # --- อัปเดตคลัง
    def _ask_update(self):
        self.update_btn.setEnabled(False)
        self.set_update_status("กำลังเช็ก…", True)
        self.update_req.emit()

    def set_update_status(self, text, ok=True, busy=False, restart=False):
        self._update_status = (text, ok)
        self._restart = restart
        try:
            self.update_btn.setText(" รีสตาร์ท" if restart else " เช็กตอนนี้")
            self.update_lbl.setText(text)
            self.update_lbl.setStyleSheet("" if ok else f"color:{T.WARN};")
            self.update_btn.setEnabled(not busy)
        except (AttributeError, RuntimeError):     # หน้ายังไม่ถูกสร้าง/ถูกสร้างใหม่
            pass

    # --- ตัวช่วยสร้างช่องตั้งค่า
    def _set(self, k, v):
        self.s[k] = v
        self.preview.set_settings(self.s)
        self.changed.emit(dict(self.s))
        if k in ("live_translate", "use_mymemory", "gemini_key"):
            QTimer.singleShot(0, self._refresh_live_status)
        if k == "web_feed":
            QTimer.singleShot(50, self._refresh_web_url)

    def _slider(self, k, lo, hi, fmt, scale=1, step=1):
        """แถบเลื่อน + ตัวเลขด้านขวา (scale: ค่าที่เก็บ = ค่าแถบ / scale)"""
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        sl = QSlider(Qt.Orientation.Horizontal)
        sl.setRange(lo // step, hi // step)
        sl.setCursor(Qt.CursorShape.PointingHandCursor)
        val = QLabel()
        val.setObjectName("Muted")
        val.setFixedWidth(52)
        val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        def show(v):
            v *= step
            val.setText(fmt(v) if callable(fmt) else fmt.format(v))

        def on(v):
            show(v)
            self._set(k, v * step / scale if scale != 1 else v * step)

        sl.setValue(int(round(self.s[k] * scale / step)))
        show(sl.value())
        sl.valueChanged.connect(on)
        row.addWidget(sl, 1)
        row.addWidget(val)
        return box

    def _spin(self, k, lo, hi, suffix=""):
        w = QSpinBox()
        w.setRange(lo, hi)
        w.setSuffix(suffix)
        w.setSingleStep(5)
        w.setValue(int(self.s[k]))
        w.valueChanged.connect(lambda v: self._set(k, v))
        return w

    def _option(self, k, label, hint=""):
        w = OptionRow(label, hint, bool(self.s.get(k)))
        w.toggled.connect(lambda v: self._set(k, v))
        return w

    def _color(self, k, tip=""):
        b = ColorButton(self.s[k])
        b.setToolTip(tip)
        b.colorChanged.connect(lambda c: self._set(k, c))
        return b

    def _key_changed(self):
        key = self.key_edit.text().strip()
        if key != self.s.get("gemini_key", ""):
            self._set("gemini_key", key)

    def _refresh_live_status(self):
        if not hasattr(self, "live_lbl"):
            return
        try:
            text, ok = self.live_status() if self.live_status else ("", True)
            self.live_lbl.setText(text)
            self.live_lbl.setStyleSheet(f"color:{T.OK if ok else T.WARN};")
            self.live_lbl.setVisible(bool(text))
        except RuntimeError:                      # หน้าถูกสร้างใหม่ระหว่างรอ
            pass

    @staticmethod
    def _open(path):
        try:
            if not os.path.exists(path):
                open(path, "a", encoding="utf-8").close()
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except OSError:
            pass

    # --- แชร์ / คืนค่า
    def _copy_code(self):
        QApplication.clipboard().setText(cfg.export_code(self.s))
        self.share_lbl.setStyleSheet(f"color:{T.OK};")
        self.share_lbl.setText("คัดลอกโค้ดแล้ว ส่งให้เพื่อนกด “วางโค้ด” ได้เลย")

    def _paste_code(self):
        try:
            new = cfg.import_code(self.s, QApplication.clipboard().text())
        except ValueError as e:
            self.share_lbl.setStyleSheet(f"color:{T.DANGER};")
            self.share_lbl.setText(f"วางไม่ได้: {e}")
            return
        self._replace(new)
        self.share_lbl.setStyleSheet(f"color:{T.OK};")
        self.share_lbl.setText("ใช้การตั้งค่าจากโค้ดแล้ว")

    def _reset(self):
        if QMessageBox.question(self, "คืนค่าเริ่มต้น", "คืนค่าหน้าตาและตำแหน่งซับเป็นค่าเริ่มต้น?\n(กรอบซับและคีย์ Gemini ไม่เปลี่ยน)") \
                != QMessageBox.StandardButton.Yes:
            return
        new = dict(self.s)
        new.update({k: v for k, v in cfg.DEFAULTS.items() if k not in cfg.LOCAL_KEYS})
        self._replace(new)

    def _replace(self, new):
        self.s = dict(new)
        self._build_pages()
        self.preview.set_settings(self.s)
        self.changed.emit(dict(self.s))


# ---------------------------------------------------------------- ประวัติบทพูด
class _HistoryDelegate(QStyledItemDelegate):
    PAD = 12
    TIME_W = 70          # ที่ว่างด้านขวาของบรรทัดไทยสำหรับเวลา

    def _fonts(self, option):
        th = QFont(option.font)
        th.setPointSizeF(11)
        th.setBold(True)
        en = QFont(option.font)
        en.setPointSizeF(9.5)
        return th, en

    def _inner_width(self, option):
        """ความกว้างของพื้นที่ข้อความในการ์ด (ต้องตรงกับที่ paint ใช้ ไม่งั้นบรรทัดสุดท้ายโดนตัด)"""
        return max(160, option.widget.viewport().width() - 6 - 2 * self.PAD)

    def sizeHint(self, option, index):
        th_f, en_f = self._fonts(option)
        w = self._inner_width(option)
        flags = int(Qt.TextFlag.TextWordWrap)
        h1 = QFontMetricsF(th_f).boundingRect(QRectF(0, 0, w - self.TIME_W, 5000), flags, index.data(Qt.ItemDataRole.UserRole)).height()
        h2 = QFontMetricsF(en_f).boundingRect(QRectF(0, 0, w, 5000), flags, index.data(Qt.ItemDataRole.UserRole + 1)).height()
        return QSize(w, int(h1 + h2) + 2 * self.PAD + 3 + 8 + 2)

    def paint(self, p, option, index):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self._inner_width(option)
        r = QRectF(option.rect.x() + 1, option.rect.y() + 4, w + 2 * self.PAD, option.rect.height() - 8)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        p.setPen(QPen(QColor(T.ACCENT_HI if selected else T.STROKE_HI if hover else T.STROKE), 1))
        p.setBrush(QColor(T.BTN if (selected or hover) else T.CARD))
        p.drawRoundedRect(r, 10, 10)
        th_f, en_f = self._fonts(option)
        inner = r.adjusted(self.PAD, self.PAD - 2, -self.PAD, -self.PAD + 2)
        flags = int(Qt.TextFlag.TextWordWrap)
        small = QFont(en_f)
        small.setPointSizeF(8.5)
        p.setFont(small)
        p.setPen(QColor(T.FAINT))
        p.drawText(inner, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, index.data(Qt.ItemDataRole.UserRole + 2))
        th = index.data(Qt.ItemDataRole.UserRole)
        th_rect = QRectF(inner.left(), inner.top(), inner.width() - self.TIME_W, inner.height())
        p.setFont(th_f)
        p.setPen(QColor("#FFFFFF"))
        used = p.drawText(th_rect, flags, th)
        p.setFont(en_f)
        p.setPen(QColor(T.MUTED))
        p.drawText(QRectF(inner.left(), used.bottom() + 3, inner.width(), inner.bottom() - used.bottom()),
                   flags, index.data(Qt.ItemDataRole.UserRole + 1))
        p.restore()


class FixDialog(QDialog):
    """แก้คำแปลของประโยคเดียว"""

    def __init__(self, en, th, parent=None):
        super().__init__(parent)
        self.setObjectName("Root")
        self.setWindowTitle("แก้คำแปล")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 14)
        lay.setSpacing(10)
        t = QLabel("ภาษาอังกฤษ")
        t.setObjectName("Section")
        lay.addWidget(t)
        en_lbl = QLabel(en)
        en_lbl.setWordWrap(True)
        en_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        en_lbl.setObjectName("RowTitle")
        lay.addWidget(en_lbl)
        t = QLabel("คำแปลไทย")
        t.setObjectName("Section")
        lay.addWidget(t)
        self.edit = QLineEdit(th)
        self.edit.setMinimumHeight(34)
        self.edit.selectAll()
        lay.addWidget(self.edit)
        note = QLabel("บันทึกลงคลังในเครื่องนี้และมีผลทันที — ถ้าคุณเป็นคนปล่อยคลัง รอบหน้าเพื่อนจะได้คำแปลนี้ด้วย")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        lay.addWidget(note)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = button("ยกเลิก")
        cancel.clicked.connect(self.reject)
        save = button("บันทึก", kind="Primary")
        save.setDefault(True)
        save.clicked.connect(self._save)
        row.addWidget(cancel)
        row.addWidget(save)
        lay.addLayout(row)
        self.edit.returnPressed.connect(self._save)

    def showEvent(self, e):
        super().showEvent(e)
        T.dark_titlebar(self)
        self.edit.setFocus()

    def _save(self):
        if self.edit.text().strip():
            self.accept()

    def value(self):
        return " ".join(self.edit.text().split())


class HistoryWindow(QDialog):
    MAX = 500
    correction = pyqtSignal(str, str, str)      # (อังกฤษ, คำแปลเดิม, คำแปลใหม่)

    def __init__(self, parent=None, search_fn=None):
        super().__init__(parent)
        self.setObjectName("Root")
        self.setWindowTitle("ประวัติบทพูด — ซับไทย GTA VI")
        self.setWindowIcon(T.app_icon())
        self.resize(720, 560)
        self._items = []                     # [(ไทยที่แสดง, อังกฤษ, เวลา, ไทยล้วน)]
        self.search_fn = search_fn           # ค้นทั้งคลัง: ข้อความ -> [(อังกฤษ, ไทย, ที่มา)]
        self.mode = 0                        # 0 = ประวัติ, 1 = ค้นทั้งคลัง

        self.tabs = Segmented(["ประวัติบทพูด", "ค้นทั้งคลัง (แก้คำแปล)"], 0, [T.Glyph.HISTORY, T.Glyph.SEARCH])
        self.tabs.changed.connect(self._set_mode)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("ค้นหาบทพูด (ไทยหรืออังกฤษ)")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(T.glyph_icon(T.Glyph.SEARCH, T.MUTED), QLineEdit.ActionPosition.LeadingPosition)
        self.search_timer = QTimer(self, singleShot=True, interval=250, timeout=lambda: self._filter(self.search.text()))
        self.search.textChanged.connect(lambda _: self.search_timer.start())
        self.count = QLabel()
        self.count.setObjectName("Muted")
        self.btn_clear = button(" ล้างประวัติ", T.Glyph.DELETE)
        self.btn_clear.clicked.connect(self._clear)
        top.addWidget(self.search, 1)
        top.addWidget(self.count)
        top.addWidget(self.btn_clear)

        self.list = QListWidget()
        self.list.setItemDelegate(_HistoryDelegate(self.list))
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setMouseTracking(True)
        self.list.setUniformItemSizes(False)
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.itemDoubleClicked.connect(self._fix)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.empty = QLabel("ยังไม่มีบทพูด\nเปิดเกมหรือคลิปที่มีซับอังกฤษ ประโยคที่แปลแล้วจะมาอยู่ที่นี่")
        self.empty.setObjectName("Muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 12, 12)
        lay.setSpacing(10)
        lay.addWidget(self.tabs)
        lay.addLayout(top)
        lay.addWidget(self.empty, 1)
        lay.addWidget(self.list, 1)
        tip = QLabel("ดับเบิลคลิกเพื่อแก้คำแปล · คลิกขวาเพื่อคัดลอก")
        tip.setObjectName("Faint")
        lay.addWidget(tip)
        self._sync()

    def showEvent(self, e):
        super().showEvent(e)
        T.dark_titlebar(self)
        self.raise_()
        self.activateWindow()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.Type.ActivationChange:
            self.list.viewport().update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.list.doItemsLayout()

    def _row(self, th, en, when, raw=None):
        it = QListWidgetItem()
        it.setData(Qt.ItemDataRole.UserRole, th)
        it.setData(Qt.ItemDataRole.UserRole + 1, en)
        it.setData(Qt.ItemDataRole.UserRole + 2, when)
        it.setData(Qt.ItemDataRole.UserRole + 3, raw if raw is not None else th)
        self.list.addItem(it)

    def _sync(self):
        n = self.list.count()
        self.empty.setVisible(n == 0)
        self.list.setVisible(n > 0)
        if self.mode == 1:
            q = self.search.text().strip()
            self.empty.setText("พิมพ์คำไทยหรืออังกฤษที่อยากหา (อย่างน้อย 2 ตัวอักษร)\nเช่น  เหี้ยนั่น  หรือ  baby" if len(q) < 2
                               else "ไม่เจอในคลัง")
            self.count.setText(f"{n:,} บรรทัด" + (" (แสดง 300 แรก)" if n >= 300 else ""))
            return
        self.empty.setText("ยังไม่มีบทพูด\nเปิดเกมหรือคลิปที่มีซับอังกฤษ ประโยคที่แปลแล้วจะมาอยู่ที่นี่")
        total = len(self._items)
        self.count.setText(f"{n:,} / {total:,} ประโยค" if n != total else f"{total:,} ประโยค")

    def _set_mode(self, i):
        self.mode = i
        self.btn_clear.setVisible(i == 0)
        self.search.setPlaceholderText("ค้นหาบทพูด (ไทยหรืออังกฤษ)" if i == 0 else "ค้นทั้งคลัง เช่น เหี้ยนั่น หรือ baby")
        self._filter(self.search.text())
        self.search.setFocus()

    def add(self, th, en, raw=None):
        """th = ข้อความที่แสดง (อาจมีชื่อคนพูด), en = อังกฤษ, raw = คำแปลไทยล้วน (ใช้ตอนแก้)"""
        when = time.strftime("%H:%M:%S")
        self._items.append((th, en, when, raw if raw is not None else th))
        if len(self._items) > self.MAX:
            self._items.pop(0)
        if self.mode == 1:
            return
        q = self.search.text().strip().lower()
        if not q or q in f"{th}\n{en}".lower():
            at_end = self.list.verticalScrollBar().value() >= self.list.verticalScrollBar().maximum() - 4
            self._row(th, en, when, raw)
            if self.list.count() > self.MAX:
                self.list.takeItem(0)
            if at_end:
                self.list.scrollToBottom()
        self._sync()

    def _filter(self, q):
        q = q.strip().lower()
        self.list.clear()
        if self.mode == 1:
            if len(q) >= 2 and self.search_fn:
                for en, th, src in self.search_fn(q):
                    self._row(th, en, src or "")
            self.list.scrollToTop()
            self._sync()
            return
        for th, en, when, raw in self._items:
            if not q or q in f"{th}\n{en}".lower():
                self._row(th, en, when, raw)
        self.list.scrollToBottom()
        self._sync()

    def _menu(self, pos):
        item = self.list.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        menu.addAction(T.glyph_icon(T.Glyph.PALETTE, T.MUTED), "แก้คำแปล", lambda: self._fix(item))
        menu.addAction(T.glyph_icon(T.Glyph.COPY, T.MUTED), "คัดลอก", lambda: self._copy(item))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def _fix(self, item):
        en = item.data(Qt.ItemDataRole.UserRole + 1)
        old = item.data(Qt.ItemDataRole.UserRole + 3)
        dlg = FixDialog(en, old, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new = dlg.value()
        if not new or new == old:
            return
        self.correction.emit(en, old, new)
        shown = item.data(Qt.ItemDataRole.UserRole)
        item.setData(Qt.ItemDataRole.UserRole, shown.replace(old, new) if old and old in shown else new)
        item.setData(Qt.ItemDataRole.UserRole + 3, new)
        self._items = [(t.replace(old, new) if (e == en and r == old) else t, e, w, new if (e == en and r == old) else r)
                       for t, e, w, r in self._items]
        self.list.doItemsLayout()
        self.count.setText("บันทึกคำแปลแล้ว")
        QTimer.singleShot(1500, self._sync)

    def _copy(self, item):
        QApplication.clipboard().setText(f"{item.data(Qt.ItemDataRole.UserRole)}\n{item.data(Qt.ItemDataRole.UserRole + 1)}")
        self.count.setText("คัดลอกแล้ว")
        QTimer.singleShot(1200, self._sync)

    def _clear(self):
        self._items.clear()
        self.list.clear()
        self._sync()
