"""ชิ้นส่วน UI ที่ใช้ร่วมกันทุกหน้าต่าง: สวิตช์เปิด/ปิด, แถวตัวเลือก, แท็บแบบปุ่ม, ปุ่มเลือกสี, การ์ด ฯลฯ"""
from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, pyqtProperty, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QAbstractButton, QApplication, QButtonGroup, QColorDialog, QComboBox, QFrame,
                             QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from app import theme as T


# ---------------------------------------------------------------- สวิตช์
class Switch(QAbstractButton):
    """สวิตช์เปิด/ปิดแบบเลื่อน (แทนช่องติ๊กถูก)"""

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(bool(checked))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._t = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"t", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._slide)

    def sizeHint(self):
        return QSize(44, 24)

    def _slide(self, on):
        self._anim.stop()
        if not self.isVisible():
            self._t = 1.0 if on else 0.0
            self.update()
            return
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _get_t(self):
        return self._t

    def _set_t(self, v):
        self._t = v
        self.update()

    t = pyqtProperty(float, _get_t, _set_t)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        track = QRectF(0.5, 0.5, w - 1, h - 1)
        p.setPen(QPen(QColor(T.STROKE_HI), 1))
        p.setBrush(QColor(T.INSET))
        p.drawRoundedRect(track, h / 2, h / 2)
        if self._t > 0:
            g = QLinearGradient(0, 0, w, 0)
            g.setColorAt(0, QColor(T.PINK))
            g.setColorAt(1, QColor(T.VIOLET))
            p.setOpacity(self._t)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawRoundedRect(track, h / 2, h / 2)
            p.setOpacity(1.0)
        d = h - 8
        x = 4 + self._t * (w - d - 8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 60))
        p.drawEllipse(QRectF(x, 5, d, d))
        knob = QColor("#FFFFFF") if self._t > 0.5 else QColor(T.MUTED)
        if not self.isEnabled():
            knob = QColor(T.FAINT)
        p.setBrush(knob)
        p.drawEllipse(QRectF(x, 4, d, d))
        if self.hasFocus():
            p.setPen(QPen(QColor(T.PINK), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(track, h / 2, h / 2)


class OptionRow(QWidget):
    """แถวตัวเลือก: ชื่อ + คำอธิบายสั้นๆ ทางซ้าย สวิตช์ทางขวา (คลิกตรงไหนของแถวก็สลับได้)"""
    toggled = pyqtSignal(bool)

    def __init__(self, title, hint="", checked=False, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(12)
        box = QVBoxLayout()
        box.setSpacing(0)
        self.title = QLabel(title)
        self.title.setObjectName("RowTitle")
        self.title.setWordWrap(True)
        box.addWidget(self.title)
        self.hint = QLabel(hint)
        self.hint.setObjectName("Muted")
        self.hint.setWordWrap(True)
        self.hint.setVisible(bool(hint))
        box.addWidget(self.hint)
        lay.addLayout(box, 1)
        self.switch = Switch(checked)
        self.switch.toggled.connect(self.toggled)
        lay.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignVCenter)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.switch.toggle()

    def isChecked(self):
        return self.switch.isChecked()

    def setChecked(self, v, silent=True):
        if silent:
            self.switch.blockSignals(True)
        self.switch.setChecked(bool(v))
        if silent:
            self.switch.blockSignals(False)
            self.switch._slide(bool(v))

    def setHint(self, text, color=None):
        self.hint.setText(text)
        self.hint.setVisible(bool(text))
        self.hint.setStyleSheet(f"color:{color};" if color else "")


# ---------------------------------------------------------------- แท็บแบบปุ่ม
class Segmented(QFrame):
    """กลุ่มปุ่มเลือกได้ทีละปุ่ม (ใช้เป็นแท็บ หรือตัวเลือก 2-4 ค่า)"""
    changed = pyqtSignal(int)

    def __init__(self, labels, current=0, icons=None, parent=None):
        super().__init__(parent)
        self.setObjectName("Inset")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)   # ห้ามถูกบีบจนตัวหนังสือขาด
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for i, text in enumerate(labels):
            b = QPushButton(text)
            b.setObjectName("Seg")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if icons and icons[i]:
                b.setIcon(T.glyph_icon(icons[i], T.MUTED))
            b.setChecked(i == current)
            self.group.addButton(b, i)
            lay.addWidget(b, 1)
        self.group.idClicked.connect(self.changed)

    def currentIndex(self):
        return self.group.checkedId()

    def setCurrentIndex(self, i):
        b = self.group.button(i)
        if b:
            b.setChecked(True)


# ---------------------------------------------------------------- ปุ่มเลือกสี
class ColorButton(QPushButton):
    colorChanged = pyqtSignal(str)

    def __init__(self, color, parent=None):
        super().__init__(parent)
        self.setObjectName("Swatch")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(34)
        self._color = color
        self.clicked.connect(self._pick)
        self._sync()

    def color(self):
        return self._color

    def setColor(self, color):
        self._color = color
        self._sync()

    def _sync(self):
        self.setText("        " + self._color.upper())
        self.update()

    def _pick(self):
        c = QColorDialog.getColor(QColor(self._color), self.window(), "เลือกสี")
        if c.isValid():
            self.setColor(c.name())
            self.colorChanged.emit(c.name())

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = 18
        r = QRectF(10, (self.height() - d) / 2, d, d)
        p.setPen(QPen(QColor(T.STROKE_HI), 1))
        p.setBrush(QColor(self._color))
        p.drawEllipse(r)


# ---------------------------------------------------------------- หน้าต่างโหลดตอนเปิดโปรแกรม
class Splash(QWidget):
    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.SplashScreen)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(340, 150)
        self.text = "กำลังเปิดโปรแกรม…"
        self.logo = T.pixmap("logo.png", 46, self.devicePixelRatioF())
        g = QGuiApplication.primaryScreen().availableGeometry()
        self.move(g.center().x() - self.width() // 2, g.center().y() - self.height() // 2)

    def set_text(self, text):
        self.text = text
        self.repaint()
        QApplication.processEvents()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(T.STROKE_HI), 1))
        p.setBrush(QColor(T.BG))
        p.drawRoundedRect(r, 18, 18)
        path = QPainterPath()
        path.addRoundedRect(r, 18, 18)
        p.setClipPath(path)
        p.drawPixmap(28, 52, self.logo)
        f = QFont("Leelawadee UI")
        f.setPointSizeF(14)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(QRectF(108, 44, 220, 30), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "ซับไทย GTA VI")
        f.setPointSizeF(9.5)
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor(T.MUTED))
        p.drawText(QRectF(108, 76, 220, 24), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.text)


# ---------------------------------------------------------------- ตัวช่วยจัดหน้า
def card(spacing=10, margins=(14, 12, 14, 12)):
    frame = QFrame()
    frame.setObjectName("Card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return frame, lay


def section(text):
    lbl = QLabel(text)
    lbl.setObjectName("Section")
    lbl.setContentsMargins(4, 4, 0, 0)
    return lbl


def hline():
    ln = QFrame()
    ln.setObjectName("Line")
    ln.setFixedHeight(1)
    return ln


def button(text, glyph=None, kind=None, tip=None, color=None):
    b = QPushButton(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if kind:
        b.setObjectName(kind)
    if glyph:
        b.setIcon(T.glyph_icon(glyph, color or ("#FFFFFF" if kind == "Primary" else T.TEXT)))
        b.setIconSize(QSize(15, 15))
    if tip:
        b.setToolTip(tip)
    return b


def restyle(widget):
    """ให้ Qt คำนวณสไตล์ใหม่หลังเปลี่ยน property ที่สไตล์ชีตอ้างถึง"""
    st = widget.style()
    st.unpolish(widget)
    st.polish(widget)
    widget.update()


def polish_popup(widget):
    """เมนู/รายการแบบเด้ง: ตัดเงาสี่เหลี่ยมของ Windows ออก ให้เข้ากับธีม"""
    win = widget.view().window() if isinstance(widget, QComboBox) else widget
    win.setWindowFlags(win.windowFlags() | Qt.WindowType.NoDropShadowWindowHint)
