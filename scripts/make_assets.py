"""สร้างไฟล์ภาพของโปรแกรมจากโลโก้ต้นฉบับ (assets_src/logo.png — โลโก้สีขาวพื้นโปร่งใส)

รัน:  .\\.venv\\Scripts\\python.exe scripts\\make_assets.py

ได้ไฟล์ใน app/assets/:
  icon.ico         ไอคอนโปรแกรม (16-256 px) ใช้กับ .exe / ทางลัด
  icon.png         ไอคอน 256 px ใช้กับหน้าต่าง / ถาดระบบ
  logo.png         โลโก้สีขาวตัดขอบแล้ว ใช้ในหัวหน้าต่าง
  chev_down.png, chev_up.png, check.png   ลูกศร/เครื่องหมายถูกของช่องเลือก
"""
import struct
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets_src" / "logo.png"
OUT = ROOT / "app" / "assets"

# ไล่สีพื้นไอคอน (มุมซ้ายบน -> ขวาล่าง) โทนพระอาทิตย์ตก Vice City
GRADIENT = [(0.0, (255, 92, 168)), (0.52, (190, 74, 232)), (1.0, (86, 62, 245))]      # RGB
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]


def read_rgba(path):
    img = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None or img.ndim != 3 or img.shape[2] != 4:
        raise SystemExit(f"ต้องเป็น PNG ที่มีพื้นโปร่งใส: {path}")
    return img


def write_png(path, bgra):
    ok, buf = cv2.imencode(".png", bgra)
    assert ok
    buf.tofile(str(path))


def trimmed_logo(src, width):
    """ตัดขอบว่างรอบโลโก้ แล้วย่อให้กว้างตามที่ต้องการ -> ช่อง alpha (float 0-1)"""
    a = src[:, :, 3]
    ys, xs = np.where(a > 8)
    a = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    h = round(a.shape[0] * width / a.shape[1])
    return cv2.resize(a, (width, h), interpolation=cv2.INTER_AREA).astype(np.float32) / 255


def gradient(size):
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    t = (xx + yy) / (2 * (size - 1))
    out = np.zeros((size, size, 3), np.float32)
    for (t0, c0), (t1, c1) in zip(GRADIENT, GRADIENT[1:]):
        m = (t >= t0) & (t <= t1)
        k = ((t - t0) / (t1 - t0))[..., None]
        seg = np.array(c0, np.float32) * (1 - k) + np.array(c1, np.float32) * k
        out[m] = seg[m]
    # แสงเงาบางๆ ด้านบน + มืดลงเล็กน้อยด้านล่าง ให้ดูมีมิติ
    out *= (1.06 - 0.16 * (yy / size))[..., None]
    return np.clip(out, 0, 255)


def rounded_mask(size, margin, radius):
    big = size * 4
    m = np.zeros((big, big), np.uint8)
    x0, x1, r = margin * 4, big - margin * 4, radius * 4
    cv2.rectangle(m, (x0 + r, x0), (x1 - r, x1), 255, -1)
    cv2.rectangle(m, (x0, x0 + r), (x1, x1 - r), 255, -1)
    for cx, cy in ((x0 + r, x0 + r), (x1 - r, x0 + r), (x0 + r, x1 - r), (x1 - r, x1 - r)):
        cv2.circle(m, (cx, cy), r, 255, -1, cv2.LINE_AA)
    return cv2.resize(m, (size, size), interpolation=cv2.INTER_AREA).astype(np.float32) / 255


def make_tile(src, size=1024, logo_frac=0.80, shadow=True):
    """ไอคอนสี่เหลี่ยมมุมมน พื้นไล่สี + โลโก้ขาวตรงกลาง -> BGRA uint8"""
    margin = round(size * 0.035)
    mask = rounded_mask(size, margin, round(size * 0.215))
    rgb = gradient(size)
    logo = trimmed_logo(src, round((size - 2 * margin) * logo_frac))
    lh, lw = logo.shape
    y0, x0 = (size - lh) // 2, (size - lw) // 2
    la = np.zeros((size, size), np.float32)
    la[y0:y0 + lh, x0:x0 + lw] = logo
    if shadow:
        sh = cv2.GaussianBlur(np.roll(la, round(size * 0.012), axis=0), (0, 0), size * 0.014) * 0.38
        rgb *= (1 - sh)[..., None]
    rgb = rgb * (1 - la[..., None]) + 255 * la[..., None]
    out = np.zeros((size, size, 4), np.uint8)
    out[:, :, :3] = np.clip(rgb[:, :, ::-1], 0, 255)                # RGB -> BGR
    out[:, :, 3] = np.clip(mask * 255, 0, 255)
    return out


def resize_rgba(img, size):
    """ย่อภาพแบบคูณ alpha ก่อน (ขอบไม่เป็นเส้นดำ)"""
    f = img.astype(np.float32)
    a = f[:, :, 3:4] / 255
    pm = np.concatenate([f[:, :, :3] * a, f[:, :, 3:4]], axis=2)
    pm = cv2.resize(pm, (size, size), interpolation=cv2.INTER_AREA)
    a = pm[:, :, 3:4] / 255
    rgb = np.divide(pm[:, :, :3], a, out=np.zeros_like(pm[:, :, :3]), where=a > 0)
    return np.clip(np.concatenate([rgb, pm[:, :, 3:4]], axis=2), 0, 255).astype(np.uint8)


def ico_bmp_entry(bgra):
    """ข้อมูลภาพแบบ DIB 32 บิต + AND mask (รูปแบบมาตรฐานของไอคอนขนาดเล็ก)"""
    h, w = bgra.shape[:2]
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    pixels = bgra[::-1].tobytes()
    row = ((w + 31) // 32) * 4
    mask = np.zeros((h, row), np.uint8)
    bits = np.packbits(bgra[::-1, :, 3] == 0, axis=1)
    mask[:, :bits.shape[1]] = bits
    return header + pixels + mask.tobytes()


def write_ico(path, images):
    """images: {ขนาด: BGRA} -> ไฟล์ .ico (256 px เก็บแบบ PNG, เล็กกว่านั้นแบบ DIB)"""
    entries = []
    for size in sorted(images):
        img = images[size]
        data = cv2.imencode(".png", img)[1].tobytes() if size >= 256 else ico_bmp_entry(img)
        entries.append((size, data))
    out = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    for size, data in entries:
        out += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    for _, data in entries:
        out += data
    Path(path).write_bytes(out)


def glyph(points, size=24, color=(181, 147, 154), thick=2.6, closed=False):
    """ลูกศร/เครื่องหมายถูก เส้นเรียบ -> BGRA (points เป็นสัดส่วน 0-1)"""
    big = size * 8
    a = np.zeros((big, big), np.uint8)
    pts = (np.array(points, np.float32) * big).astype(np.int32).reshape(-1, 1, 2)
    cv2.polylines(a, [pts], closed, 255, round(thick * 8), cv2.LINE_AA)
    a = cv2.resize(a, (size, size), interpolation=cv2.INTER_AREA)
    out = np.zeros((size, size, 4), np.uint8)
    out[:, :, :3] = color                                       # BGR
    out[:, :, 3] = a
    return out


def main():
    src = read_rgba(Path(sys.argv[1]) if len(sys.argv) > 1 else SRC)
    OUT.mkdir(parents=True, exist_ok=True)

    big = make_tile(src, 1024)
    small = make_tile(src, 1024, logo_frac=0.88, shadow=False)   # ขนาดเล็ก: โลโก้ใหญ่ขึ้น ไม่มีเงา จะได้ไม่เบลอ
    images = {s: resize_rgba(small if s <= 32 else big, s) for s in ICO_SIZES}
    write_ico(OUT / "icon.ico", images)
    write_png(OUT / "icon.png", images[256])

    logo = trimmed_logo(src, 640)
    bgra = np.full(logo.shape + (4,), 255, np.uint8)
    bgra[:, :, 3] = np.clip(logo * 255, 0, 255)
    write_png(OUT / "logo.png", bgra)

    muted = (181, 147, 154)                                      # BGR ของ #9A93B5
    write_png(OUT / "chev_down.png", glyph([(0.24, 0.38), (0.5, 0.64), (0.76, 0.38)], color=muted))
    write_png(OUT / "chev_up.png", glyph([(0.24, 0.62), (0.5, 0.36), (0.76, 0.62)], color=muted))
    write_png(OUT / "check.png", glyph([(0.22, 0.52), (0.42, 0.72), (0.78, 0.30)], color=(255, 255, 255), thick=3.0))

    for p in sorted(OUT.iterdir()):
        print(f"{p.relative_to(ROOT)}  {p.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
