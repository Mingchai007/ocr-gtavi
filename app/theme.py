"""ธีมของโปรแกรม (สไตล์ shadcn/ui: พื้นเทาดำ Neutral + สีเน้น Emerald): ชุดสี, สไตล์ชีต, ไอคอน, แถบชื่อหน้าต่างสีเข้ม"""
import ctypes
from pathlib import Path
from string import Template

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QIcon, QIconEngine, QPainter, QPixmap

ASSETS = Path(__file__).resolve().parent / "assets"

# ---------------------------------------------------------------- ชุดสี
BG = "#0A0A0A"          # พื้นหน้าต่าง
CARD = "#171717"        # การ์ด
INSET = "#1F1F1F"       # ช่องกรอก / กล่องด้านใน
BTN = "#1C1C1C"         # ปุ่มทั่วไป (แบบ outline)
BTN_HI = "#262626"
STROKE = "#2A2A2A"      # เส้นขอบ
STROKE_HI = "#404040"
TEXT = "#FAFAFA"
MUTED = "#A1A1A1"
FAINT = "#737373"
ACCENT = "#047857"      # Emerald — ปุ่มหลัก / สวิตช์ / แถบเลื่อน
ACCENT_HI = "#059669"
ACCENT_LO = "#065F46"
PINK = VIOLET = ACCENT  # ชื่อเดิมที่โค้ดส่วนอื่นยังอ้างถึง
CYAN = "#38BDF8"
OK = "#34D399"
WARN = "#FBBF24"
DANGER = "#FB7185"

FONT_UI = '"Leelawadee UI", "Segoe UI"'

_QSS = Template("""
* { font-family: $font; font-size: 10pt; color: $text; outline: 0; }
QWidget#Root, QDialog, QMessageBox { background-color: $bg; }
QLabel { background: transparent; }
QToolTip { background-color: $card; color: $text; border: 1px solid $stroke_hi; padding: 5px 8px; }

QFrame#Card { background-color: $card; border: 1px solid $stroke; border-radius: 12px; }
QLabel#LogoTile { background-color: $inset; border: 1px solid $stroke; border-radius: 12px; }
QFrame#Inset { background-color: $inset; border: 1px solid $stroke; border-radius: 10px; }
QFrame#Line { background-color: $stroke; border: none; max-height: 1px; min-height: 1px; }

QLabel#Title { font-size: 15pt; font-weight: 700; color: #FFFFFF; }
QLabel#SubTitle { color: $muted; font-size: 9pt; }
QLabel#Section { color: $muted; font-size: 9pt; font-weight: 600; }
QLabel#RowTitle { font-size: 10pt; font-weight: 600; }
QLabel#Muted { color: $muted; font-size: 9pt; }
QLabel#Faint { color: $faint; font-size: 8.5pt; }
QLabel#LiveEn { color: $muted; font-size: 9.5pt; }
QLabel#LiveTh { color: #FFFFFF; font-size: 13pt; font-weight: 700; }
QLabel#Chip { color: $muted; font-size: 8.5pt; background-color: $inset; border: 1px solid $stroke;
              border-radius: 9px; padding: 1px 8px; }
QLabel#Pill { font-size: 9pt; font-weight: 700; border-radius: 12px; padding: 4px 12px; }
QLabel#Pill[state="on"] { color: $ok; background-color: #0F2A24; border: 1px solid #1B5E4B; }
QLabel#Pill[state="off"] { color: $warn; background-color: #2B2210; border: 1px solid #6B5417; }

QPushButton { background-color: $btn; color: $text; border: 1px solid $stroke; border-radius: 8px;
              padding: 7px 14px; font-weight: 600; }
QPushButton:hover { background-color: $btn_hi; border-color: $stroke_hi; }
QPushButton:pressed { background-color: $inset; }
QPushButton:disabled { color: $faint; background-color: $inset; }
QPushButton:focus { border-color: $stroke_hi; }
QPushButton#Primary { color: #FFFFFF; border: none; font-weight: 700;
    background-color: $pink; }
QPushButton#Primary:hover { background-color: $hi; }
QPushButton#Primary:pressed { background-color: $lo; }
QPushButton#Hero { font-size: 11.5pt; font-weight: 700; border-radius: 12px; padding: 10px 14px; }
QPushButton#Hero[running="false"] { color: #FFFFFF; border: none;
    background-color: $pink; }
QPushButton#Hero[running="false"]:hover { background-color: $hi; }
QPushButton#Hero[running="true"] { background-color: $btn; border: 1px solid $stroke_hi; }
QPushButton#Hero[running="true"]:hover { background-color: $btn_hi; border-color: $pink; }
QPushButton#Danger { background-color: transparent; color: $danger; border: 1px solid #4A2338; }
QPushButton#Danger:hover { background-color: #E11D48; color: #FFFFFF; border-color: #E11D48; }
QPushButton#Seg { background-color: transparent; border: none; border-radius: 8px; padding: 7px 14px; color: $muted; }
QPushButton#Seg:hover { color: $text; background-color: $btn; }
QPushButton#Seg:checked { color: #FFFFFF; background-color: $btn_hi; border: 1px solid $stroke_hi; }
QPushButton#Swatch { text-align: left; padding: 5px 10px; background-color: $inset; }

QComboBox, QSpinBox, QLineEdit, QFontComboBox {
    background-color: $inset; color: $text; border: 1px solid $stroke; border-radius: 8px;
    padding: 5px 10px; min-height: 22px; selection-background-color: $pink; selection-color: #FFFFFF; }
QComboBox:hover, QSpinBox:hover, QLineEdit:hover, QFontComboBox:hover { border-color: $stroke_hi; }
QComboBox:focus, QSpinBox:focus, QLineEdit:focus, QFontComboBox:focus, QComboBox:on { border-color: $pink; }
QComboBox::drop-down, QFontComboBox::drop-down { border: none; width: 28px; }
QComboBox::down-arrow, QFontComboBox::down-arrow { image: url("$chev_down"); width: 12px; height: 12px; }
QComboBox QAbstractItemView { background-color: $card; color: $text; border: 1px solid $stroke_hi;
    padding: 4px; selection-background-color: #262626; selection-color: #FFFFFF; outline: 0; }
QComboBox QAbstractItemView::item { padding: 5px 10px; min-height: 24px; border-radius: 6px; }
QSpinBox { padding-right: 4px; }
QSpinBox::up-button, QSpinBox::down-button { border: none; background: transparent; width: 24px; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background-color: $btn_hi; border-radius: 5px; }
QSpinBox::up-arrow { image: url("$chev_up"); width: 10px; height: 10px; }
QSpinBox::down-arrow { image: url("$chev_down"); width: 10px; height: 10px; }

QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 18px; height: 18px; border-radius: 5px; border: 1px solid $stroke_hi; background-color: $inset; }
QCheckBox::indicator:checked { background-color: $pink; border-color: $pink; image: url("$check"); }

QSlider::groove:horizontal { height: 4px; background-color: $stroke; border-radius: 2px; }
QSlider::sub-page:horizontal { border-radius: 2px;
    background-color: $pink; }
QSlider::handle:horizontal { background-color: #FFFFFF; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }
QSlider::handle:horizontal:hover { background-color: #D1FAE5; }

QListView { background-color: transparent; border: none; outline: 0; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background-color: $stroke_hi; border-radius: 3px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background-color: $faint; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { height: 0; }

QMenu { background-color: $card; border: 1px solid $stroke_hi; padding: 6px; }
QMenu::item { padding: 7px 26px 7px 14px; border-radius: 6px; }
QMenu::item:selected { background-color: #262626; }
QMenu::separator { height: 1px; background-color: $stroke; margin: 5px 8px; }
QMessageBox QLabel { color: $text; font-size: 10pt; }
""")


def stylesheet():
    def url(name):
        return (ASSETS / name).as_posix()
    return _QSS.substitute(
        font=FONT_UI, bg=BG, card=CARD, inset=INSET, btn=BTN, btn_hi=BTN_HI, stroke=STROKE, stroke_hi=STROKE_HI,
        text=TEXT, muted=MUTED, faint=FAINT, pink=ACCENT, violet=ACCENT, hi=ACCENT_HI, lo=ACCENT_LO, ok=OK, warn=WARN, danger=DANGER,
        chev_down=url("chev_down.png"), chev_up=url("chev_up.png"), check=url("check.png"))


# ---------------------------------------------------------------- ไอคอนโปรแกรม / โลโก้
def app_icon():
    icon = QIcon(str(ASSETS / "icon.ico"))
    if icon.isNull() or not icon.availableSizes():
        icon = QIcon(str(ASSETS / "icon.png"))
    return icon


def pixmap(name, height, dpr=1.0):
    """ภาพจาก app/assets ย่อให้สูงตามที่ต้องการ (คมชัดบนจอ HiDPI)"""
    pm = QPixmap(str(ASSETS / name))
    if pm.isNull():
        return pm
    pm = pm.scaledToHeight(max(1, round(height * dpr)), Qt.TransformationMode.SmoothTransformation)
    pm.setDevicePixelRatio(dpr)
    return pm


# ---------------------------------------------------------------- ไอคอนแบบเส้น (ฟอนต์ไอคอนของ Windows)
class Glyph:
    PLAY = ""
    PAUSE = ""
    SEARCH = ""
    SETTINGS = ""
    HISTORY = ""
    CROP = ""
    VIEW = ""
    UP = ""
    DOWN = ""
    RESET = ""
    POWER = ""
    COPY = ""
    PASTE = ""
    PALETTE = ""
    MOVE = ""
    GLOBE = ""
    SYSTEM = ""
    DELETE = ""
    FOLDER = ""
    DOCUMENT = ""
    KEY = ""
    CHECK = ""
    PIN = ""


_icon_font = None


def icon_font():
    """ชื่อฟอนต์ไอคอนที่มีในเครื่อง ("" ถ้าไม่มี -> ปุ่มจะไม่มีไอคอน แต่ยังใช้งานได้)"""
    global _icon_font
    if _icon_font is None:
        fams = set(QFontDatabase.families())
        _icon_font = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in fams), "")
    return _icon_font


class _GlyphEngine(QIconEngine):
    def __init__(self, ch, color):
        super().__init__()
        self.ch, self.color = ch, color

    def paint(self, painter, rect, mode, state):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        f = QFont(icon_font())
        f.setPixelSize(max(6, int(min(rect.width(), rect.height()) * 0.92)))
        painter.setFont(f)
        col = QColor(self.color)
        if mode == QIcon.Mode.Disabled:
            col = QColor(FAINT)
        painter.setPen(col)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.ch)
        painter.restore()

    def pixmap(self, size, mode, state):
        pm = QPixmap(size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        self.paint(p, QRect(QPoint(0, 0), size), mode, state)
        p.end()
        return pm

    def clone(self):
        return _GlyphEngine(self.ch, self.color)


def glyph_icon(ch, color=TEXT):
    return QIcon(_GlyphEngine(ch, color)) if icon_font() else QIcon()


# ---------------------------------------------------------------- แถบชื่อหน้าต่าง
def _colorref(hex_color):
    c = QColor(hex_color)
    return c.red() | (c.green() << 8) | (c.blue() << 16)


def dark_titlebar(widget):
    """ทำแถบชื่อหน้าต่างของ Windows ให้เป็นสีเดียวกับโปรแกรม (Windows 10 2004+ / 11)"""
    try:
        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        on = ctypes.c_int(1)
        for attr in (20, 19):                                        # DWMWA_USE_IMMERSIVE_DARK_MODE
            if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on), 4) == 0:
                break
        for attr, col in ((35, BG), (34, STROKE)):                   # สีแถบชื่อ / สีขอบ (Windows 11)
            val = ctypes.c_int(_colorref(col))
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), 4)
    except Exception:
        pass

