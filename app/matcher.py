"""ค้นหาคำแปลจากคลัง (data/translations.db)

from app.matcher import Matcher
m = Matcher()
hit = m.lookup("Jason: Hey, how you doing?")   # -> {"th": ..., "en": ..., "score": ...} หรือ None

ลำดับการค้น (หยุดทันทีที่เจอ):
  1. คำอุทาน/ด่า/NPC (quick_phrases.json)  — dict
  2. ตรงเป๊ะหลัง normalize                   — dict
  3. ทนสะกดผิด (fuzz.ratio)                 — คัดผู้สมัครทั้งคลังด้วยลายเซ็นบิต (numpy) ก่อน
                                              แล้วให้ RapidFuzz ให้คะแนนจริงเฉพาะผู้สมัครไม่กี่บรรทัด
  4. ท่อนของประโยคยาว (ซับถูกตัดเป็นช่วง)    — ลายเซ็นบิตของสามตัวอักษร ทดสอบว่าบรรทัดไหน "บรรจุ" คำค้น

ลายเซ็นที่ใช้คัดผู้สมัคร (ข้อ 3):
  - ประโยคสั้น (< 45 ตัวอักษร): "จำนวนตัวอักษรแต่ละตัว" เข้ารหัส 128 บิต
      ถ้าสองข้อความต่างกัน d ตัวอักษร (เพิ่ม/ลบ) จำนวนตัวอักษรรวมต้องต่างกันไม่เกิน d
      -> ตัดบรรทัดที่ "ไม่มีทางถึงเกณฑ์" ทิ้งได้โดยผลลัพธ์ไม่ต่างจากการเทียบทั้งคลังแม้แต่บรรทัดเดียว
  - ประโยคยาว: ชุด "คู่ตัวอักษรติดกัน" 256 บิต / "สามตัวอักษรติดกัน" 512 บิต (ต่างกันได้ไม่เกิน 3d / 5d)
"""
import heapq
import json
import re
import sqlite3
import sys
import threading
from itertools import islice
from pathlib import Path

import numpy as np
from rapidfuzz import fuzz, process

from app import align

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "translations.db"
QUICK_PATH = ROOT / "data" / "quick_phrases.json"
GLOSSARY_PATH = ROOT / "data" / "glossary.json"
MISSING = ROOT / "data" / "missing_lines.txt"

SPEAKER = re.compile(r"^\s*[A-Z][\w'.\- ]{0,24}:\s*")   # "Jason: " / "Dre'Quan: "
NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
# ซับ YouTube เซ็นเซอร์คำหยาบเป็น [__] แต่ซับในเกมแสดงคำจริง -> ตัดคำหยาบทิ้งทั้งสองฝั่งก่อนเทียบ
SWEARS = re.compile(
    r"\b(?:motherfuck\w*|fuck\w*|shit\w*|bullshit|bitch\w*|nigga\w*|nigger\w*|"
    r"asshole\w*|ass|dick\w*|pussy|cunt\w*|bastard\w*|damn\w*|goddamn\w*)\b"
)


def normalize_raw(text: str) -> str:
    """ตัดชื่อคนพูดและวรรคตอน แต่คงคำหยาบไว้ (ใช้จับคู่คำอุทาน/ด่า/NPC)"""
    text = SPEAKER.sub("", text)
    text = text.lower().replace("’", "'").replace("'", "")
    return " ".join(NON_ALNUM.sub(" ", text).split())


def normalize(text: str) -> str:
    """ตัดชื่อคนพูด, คำหยาบ, ตัวพิมพ์เล็ก, ลบวรรคตอน — ใช้ทั้งตอนสร้างคลังและตอนค้นหา"""
    return " ".join(SWEARS.sub(" ", normalize_raw(text)).split())   # [__] หายไปตั้งแต่ normalize_raw


# ---------------------------------------------------------------- ลายเซ็นบิต
_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789 "
_NSYM = len(_ALPHABET)                      # 37
_SEP = 255                                  # ตัวคั่นระหว่างบรรทัดตอนเข้ารหัสทั้งคลังรวดเดียว
_LUT = np.full(256, _NSYM - 1, np.uint8)    # อักขระนอกชุด -> นับเป็นช่องว่าง
for _i, _c in enumerate(_ALPHABET):
    _LUT[ord(_c)] = _i
_LUT[10] = _SEP
_TRANS = bytes(_LUT)
_M64 = (1 << 64) - 1
_BITS1, _BITS2, _BITS3 = 128, 256, 512      # จำนวนตัวอักษร / คู่ตัวอักษร / สามตัวอักษร
_HALF3 = (1 << (_BITS3 // 2)) - 1
SHORT_MAX = 45                              # สั้นกว่านี้ใช้เกณฑ์ปกติ ยาวตั้งแต่นี้ใช้เกณฑ์ประโยคยาว
LONG_KEY = 60                               # บรรทัดยาวตั้งแต่นี้ถึงเข้าดัชนี "ท่อนของประโยคยาว"
LONG_TOP = 48                               # ประโยคยาว: ให้ RapidFuzz ตรวจเฉพาะผู้สมัครที่ลายเซ็นใกล้สุดไม่เกินเท่านี้
FRAGMENT_TOP = 8                            # จำนวนผู้สมัครสูงสุดที่ให้ RapidFuzz ตรวจแบบท่อนประโยค

if hasattr(np, "bitwise_count"):
    _popcount = np.bitwise_count
else:                                       # numpy < 2.0
    _POP8 = np.array([bin(i).count("1") for i in range(256)], np.uint8)

    def _popcount(a, out=None):
        r = _POP8[a.view(np.uint8).reshape(a.shape + (8,))].sum(axis=-1, dtype=np.uint8)
        if out is None:
            return r
        out[...] = r
        return out


def _encode(keys):
    """คืน (รหัสตัวอักษรของทั้งคลังต่อกัน [_SEP คั่นบรรทัด], เลขบรรทัดของแต่ละตำแหน่ง)"""
    arr = np.frombuffer("\n".join(keys).encode("ascii", "replace"), dtype=np.uint8)
    return _LUT[arr], np.cumsum(arr == 10)


def _pack(flags):
    """ตารางบูลีน (บรรทัด x บิต) -> อาร์เรย์ uint64 (บรรทัด x คำ)"""
    return np.packbits(flags, axis=1, bitorder="little").view(np.uint64)


def _count_signatures(keys):
    """ลายเซ็น "จำนวนตัวอักษรแต่ละตัว": ตัวอักษร c มีบิตของตัวเองเรียงกัน บิตที่ j ตั้งเมื่อบรรทัดนั้นมี c มากกว่า j ตัว
    จำนวนบิตที่ต่างกันของสองลายเซ็น <= ผลต่างของจำนวนตัวอักษร <= จำนวนตัวอักษรที่ต้องเพิ่ม/ลบ"""
    n = len(keys)
    codes, row = _encode(keys)
    ok = codes != _SEP
    cnt = np.bincount(row[ok] * _NSYM + codes[ok], minlength=n * _NSYM).reshape(n, _NSYM)
    # แจกบิต: บิตถัดไปให้ตัวอักษรที่มีโอกาส "มีมากกว่าจำนวนบิตที่ได้ไปแล้ว" สูงสุด
    top = int(cnt.max()) + 2
    tail = np.empty((_NSYM, top + 1))
    for c in range(_NSYM):
        h = np.bincount(cnt[:, c], minlength=top + 1)[:top + 1]
        tail[c] = h[::-1].cumsum()[::-1]
    alloc = [0] * _NSYM
    for _ in range(_BITS1):
        c = max(range(_NSYM), key=lambda c: tail[c][min(alloc[c] + 1, top)])
        alloc[c] += 1
    first = [0] * _NSYM
    for c in range(1, _NSYM):
        first[c] = first[c - 1] + alloc[c - 1]
    flags = np.zeros((n, _BITS1), dtype=bool)
    for c in range(_NSYM):
        for j in range(alloc[c]):
            flags[:, first[c] + j] = cnt[:, c] > j
    sig = _pack(flags)
    cols = [np.ascontiguousarray(sig[:, j]) for j in range(_BITS1 // 64)]
    return cols, first, [f + a for f, a in zip(first, alloc)]


def _assign_bits(freq, nbits):
    """แจก n-gram ลงบิตแบบถ่วงความถี่: ตัวที่เจอบ่อยได้บิตของตัวเอง ตัวหายากแชร์บิตกัน
    ทุกบิตจึงถูกใช้พอๆ กัน (แยกแยะได้ดีกว่า hash ธรรมดา ~5 เท่า จากการวัดกับคลังนี้)"""
    size = len(freq)
    bit_of = ((np.arange(size, dtype=np.uint64) * np.uint64(0x9E3779B97F4A7C15)) >> np.uint64(40)).astype(np.int64) % nbits
    seen = np.flatnonzero(freq)
    order = seen[np.argsort(-freq[seen], kind="stable")]
    heap = [(0, j) for j in range(nbits)]
    for g, f in zip(order.tolist(), freq[order].tolist()):
        load, j = heapq.heappop(heap)
        bit_of[g] = j
        heapq.heappush(heap, (load + f, j))
    return bit_of


def _gram_signatures(keys, gram, nbits):
    """ลายเซ็น "ชุดของ n ตัวอักษรติดกัน" -> (อาร์เรย์ uint64 [บรรทัด x คำ], จำนวนบิตที่ตั้งต่อบรรทัด, ตาราง n-gram -> บิต)"""
    n = len(keys)
    codes, row = _encode(keys)
    ok = codes[:-1] != _SEP
    ok &= codes[1:] != _SEP
    g = codes[:-1].astype(np.int32) * _NSYM + codes[1:]
    if gram == 3:
        ok = ok[:-1] & (codes[2:] != _SEP)
        g = g[:-1] * _NSYM + codes[2:]
    g = g[ok]
    row = row[:len(ok)][ok]
    bit_of = _assign_bits(np.bincount(g, minlength=_NSYM ** gram), nbits)
    flags = np.zeros((n, nbits), dtype=bool)
    flags[row, bit_of[g]] = True
    return _pack(flags), flags.sum(axis=1).astype(np.int32), bit_of.tolist()


_MISS = object()


class Matcher:
    def __init__(self, db_path=DB_PATH, cutoff=83, log_missing=True):
        self.cutoff = cutoff
        self.cutoff_long = min(cutoff, 76)   # ประโยคยาว (>= 45) ยืดหยุ่นกว่า เพราะ OCR/auto-caption มักต่าง 1-2 คำ
        self.log_missing = log_missing
        self._seen_missing = set()
        self._lock = threading.Lock()
        con = sqlite3.connect(db_path)
        raw = con.execute(
            "SELECT en_normalized, en_text, th_text, source FROM translations ORDER BY source, rowid"
        ).fetchall()
        con.close()
        raw = [(k or "", en or "", th or "", src) for k, en, th, src in raw]
        rows = [r[:3] for r in raw]
        # ประโยคที่ถูกตัดกลางทางเป็น 2 บรรทัด (บรรทัดแรกไม่จบด้วย . ? !) -> เพิ่มแบบรวมเป็นประโยคเต็มด้วย
        # เพราะในเกมซับมักขึ้นทั้งประโยคในครั้งเดียว
        have = {r[0] for r in rows}
        for a, b in zip(raw, raw[1:]):
            if a[3] != b[3] or a[1].rstrip().endswith((".", "?", "!", '"')):
                continue
            key = f"{a[0]} {b[0]}"
            if key not in have:
                rows.append((key, f"{a[1]} {b[1]}", f"{a[2]} {b[2]}"))
                have.add(key)
        # แตกประโยคย่อยจากบรรทัดที่รวมหลายประโยคไว้ด้วยกัน (เช่น "Sentence A. Sentence B.")
        # เพื่อให้ตอนเกมขึ้นทีละประโยคยังคงจับคู่ได้ — แถวแบบนี้ยังไม่มีคำแปลของตัวเอง (th = None)
        # จะหา "ท่อนคำแปลที่คู่กับประโยคนั้น" จากบรรทัดแม่ตอนถูกค้นเจอครั้งแรก (ดู app/align.py)
        self.base_count = len(raw)
        for idx in range(len(rows)):
            en_parts = align.split_sentences(rows[idx][1])
            if len(en_parts) < 2:
                continue
            for i, ep in enumerate(en_parts):
                k = normalize(ep)
                if len(k) >= 6 and k not in have:
                    rows.append((k, ep, None, idx, i))
                    have.add(k)
        self._aligned = {}       # ลำดับบรรทัดแม่ -> ผลจับคู่ประโยค/ท่อนไทย (คำนวณเมื่อใช้ครั้งแรก)
        try:
            align.add_names(json.loads(GLOSSARY_PATH.read_text(encoding="utf-8")).get("names", {}))
        except (OSError, ValueError):
            pass
        self.keys = [r[0] for r in rows]
        self.rows = rows
        self.exact = {r[0]: r for r in rows}
        self._index_of = {r[0]: i for i, r in enumerate(rows) if r[2] is not None}
        # คลังคำอุทาน/ด่า/สแลง/NPC ทั่วไป (จับคู่ทันทีก่อนตัดคำหยาบ)
        self.quick = {}
        if QUICK_PATH.exists():
            try:
                qp = json.loads(QUICK_PATH.read_text(encoding="utf-8")).get("phrases", {})
                for k, v in qp.items():
                    nk = normalize_raw(k)
                    if nk:
                        self.quick[nk] = {"en": k, "th": v, "score": 100.0}
            except (OSError, ValueError):
                pass
        self._build_index()
        self._extra = []         # คีย์ที่เพิ่มระหว่างใช้งาน (แปลสด) — มีไม่กี่บรรทัด เทียบตรงๆ ได้
        self._memo = {}          # ข้อความดิบจาก OCR -> ผลลัพธ์ (รวมผล "ไม่เจอ")
        self._qmemo = {}         # คำค้นหลัง normalize -> ผลลัพธ์ของการค้นแบบทนสะกดผิด

    # ------------------------------------------------------------ สร้างดัชนี
    def _build_index(self):
        keys = self.keys
        n = self._n = len(keys)
        order = sorted(range(n), key=lambda i: len(keys[i]))      # เรียงตามความยาว -> ช่วงความยาวเป็นช่วงติดกัน
        skeys = self._skeys = [keys[i].replace("\n", " ") for i in order]
        self._srow = order
        lens = self._lens = np.fromiter(map(len, skeys), dtype=np.int32, count=n)
        top = self._maxlen = int(lens[-1]) if n else 0
        # _start[L] = ตำแหน่งแรกที่ยาว >= L
        start = self._start = np.searchsorted(lens, np.arange(top + 2), side="left").tolist()
        # --- จำนวนตัวอักษร (ทุกบรรทัด) สำหรับประโยคสั้น
        if n:
            self._sig1, self._u_first, self._u_end = _count_signatures(skeys)
        else:
            self._sig1, self._u_first, self._u_end = [np.zeros(0, np.uint64)] * (_BITS1 // 64), [0] * _NSYM, [0] * _NSYM
        self._thr1 = {}          # ความยาวคำค้น -> จำนวนบิตที่ต่างได้มากสุดของแต่ละบรรทัด
        # --- คู่ตัวอักษร (เฉพาะบรรทัดที่ยาวพอจะเทียบกับประโยคยาวได้) สำหรับประโยคยาว
        self._o2 = start[min(SHORT_MAX * 72 // 100, top + 1)]
        self._sig2, self._bit2, self._nk2 = [], [0] * _NSYM ** 2, np.zeros(0, np.int32)
        if n - self._o2 > 0:
            sig, self._nk2, self._bit2 = _gram_signatures(skeys[self._o2:], 2, _BITS2)
            self._sig2 = [np.ascontiguousarray(sig[:, j]) for j in range(_BITS2 // 64)]
        r2 = self._nk2 - 36 * lens[self._o2:] // 100
        # --- สามตัวอักษร (เฉพาะบรรทัดยาว) สำหรับประโยคยาวมาก และท่อนของประโยคยาว
        self._o3 = start[min(LONG_KEY, top + 1)]
        n3 = n - self._o3
        self._sig3, self._bit3, self._nk3 = np.zeros((_BITS3 // 64, 0), np.uint64), [0] * _NSYM ** 3, np.zeros(0, np.int32)
        if n3 > 0:
            sig, self._nk3, self._bit3 = _gram_signatures(skeys[self._o3:], 3, _BITS3)
            self._sig3 = np.ascontiguousarray(sig.T)             # (คำ x บรรทัด) -> ทำทีเดียวทุกคำได้
        r3 = self._nk3 - 54 * lens[self._o3:] // 100
        # เกณฑ์ "ต้องมีบิตตรงกันอย่างน้อยเท่าไร" ของแต่ละบรรทัด ขึ้นกับค่าคงที่ของคำค้นเพียงค่าเดียว
        # -> คำนวณครั้งแรกที่เจอค่านั้นแล้วเก็บไว้ใช้ซ้ำ:  (แคช, r ต่อบรรทัด, ชนิดข้อมูล, ค่าสูงสุด)
        self._need_tab = {2: ({}, r2, np.uint8, 255), 3: ({}, r3, np.uint16, 65535)}
        # บัฟเฟอร์ทำงาน (ใช้ซ้ำทุกการค้น ไม่ต้องจองหน่วยความจำใหม่)
        self._b64 = np.empty(n, np.uint64)
        self._acc = np.empty(n, np.uint8)
        self._acc_b = np.empty(n, np.uint8)
        self._c8 = np.empty(n, np.uint8)
        self._mask = np.empty(n, bool)
        self._b3 = np.empty((_BITS3 // 64, n3), np.uint64)
        self._c3 = np.empty((_BITS3 // 64, n3), np.uint8)
        self._acc16 = np.empty(n3, np.uint16)

    def add(self, key: str, en: str, th: str):
        """เพิ่มคำแปลใหม่ (เช่น จากการแปลสด) เข้าดัชนีในหน่วยความจำ"""
        if key in self.exact:
            return
        row = (key, en, th)
        with self._lock:
            self.keys.append(key)
            self.rows.append(row)
            self.exact[key] = row
            self._extra.append(key)
            self._memo = {}
            self._qmemo = {}

    # ------------------------------------------------------------ ลายเซ็นของคำค้น
    def _qsig1(self, cs):
        pos = self._u_first.copy()
        end = self._u_end
        acc = 0
        for c in cs:
            p = pos[c]
            if p < end[c]:
                acc |= 1 << p
                pos[c] = p + 1
        return acc

    def _qsig2(self, cs):
        bit = self._bit2
        acc = 0
        prev = cs[0]
        for c in cs[1:]:
            acc |= 1 << bit[prev * _NSYM + c]
            prev = c
        return acc

    def _qsig3(self, cs):
        bit = self._bit3
        acc = 0
        a, b = cs[0], cs[1]
        for c in cs[2:]:
            acc |= 1 << bit[(a * _NSYM + b) * _NSYM + c]
            a, b = b, c
        return acc

    # ------------------------------------------------------------ นับบิตทั้งช่วงบรรทัดด้วย numpy
    def _differ1(self, sig, s, e):
        """จำนวนบิตที่ต่างกันของลายเซ็นจำนวนตัวอักษร ระหว่างคำค้นกับบรรทัด s..e (uint8 ต่อบรรทัด)"""
        k = e - s
        b, c, acc = self._b64[:k], self._c8[:k], self._acc[:k]
        for j, col in enumerate(self._sig1):
            np.bitwise_xor(col[s:e], np.uint64((sig >> (64 * j)) & _M64), out=b)
            if j:
                _popcount(b, out=c)
                np.add(acc, c, out=acc)
            else:
                _popcount(b, out=acc)
        return acc

    def _common2(self, sig, s, e):
        """จำนวนบิตคู่ตัวอักษรที่ตรงกัน (s, e นับจากบรรทัดแรกของดัชนีคู่ตัวอักษร) -> uint8 ต่อบรรทัด"""
        k = e - s
        b, c, acc = self._b64[:k], self._c8[:k], self._acc[:k]
        first = True
        for j, col in enumerate(self._sig2):
            w = (sig >> (64 * j)) & _M64
            if not w:
                continue
            np.bitwise_and(col[s:e], np.uint64(w), out=b)
            if first:
                _popcount(b, out=acc)
                first = False
            else:
                _popcount(b, out=c)
                np.add(acc, c, out=acc)
        if first:
            acc[:] = 0
        return acc

    def _common3(self, sig, s, e):
        """จำนวนบิตสามตัวอักษรที่ตรงกัน (s, e นับจากบรรทัดยาวตัวแรก) -> uint16 ต่อบรรทัด"""
        k = e - s
        nw = _BITS3 // 64
        w = np.array([(sig >> (64 * j)) & _M64 for j in range(nw)], dtype=np.uint64).reshape(nw, 1)
        b, c, out = self._b3[:, :k], self._c3[:, :k], self._acc16[:k]
        np.bitwise_and(self._sig3[:, s:e], w, out=b)
        _popcount(b, out=c)
        if (sig & _HALF3).bit_count() > 255 or (sig >> (_BITS3 // 2)).bit_count() > 255:
            np.add.reduce(c, axis=0, dtype=np.uint16, out=out)     # ตัวนับ 8 บิตอาจล้น (ไม่เกิดกับข้อความจริง)
            return out
        h = nw // 2                                                # รวมทีละครึ่งด้วยตัวนับ 8 บิตที่เร็วกว่า
        np.add.reduce(c[:h], axis=0, out=self._acc[:k])
        np.add.reduce(c[h:], axis=0, out=self._acc_b[:k])
        np.add(self._acc[:k], self._acc_b[:k], out=out, dtype=np.uint16)
        return out

    def _need(self, gram, cq):
        """เกณฑ์ต่อบรรทัด: ผ่านเมื่อ 2*ตรงกัน >= cq + r[บรรทัด]  <=>  ตรงกัน >= ceil((cq + r) / 2)"""
        cache, r, dtype, top = self._need_tab[gram]
        a = cache.get(cq)
        if a is None:
            if len(cache) >= 128:
                cache.clear()
            a = cache[cq] = np.clip((r + (cq + 1)) >> 1, 0, top).astype(dtype)
        return a

    def _where(self, ufunc, a, b):
        m = self._mask[:len(a)]
        ufunc(a, b, out=m)
        return np.flatnonzero(m)

    @staticmethod
    def _closest(idx, acc, nk, s):
        """ผู้สมัครเยอะเกิน -> เหลือ LONG_TOP ตัวที่บิตต่างจากคำค้นน้อยสุด (ตัวที่คะแนนถึงเกณฑ์จริงอยู่กลุ่มนี้เสมอ)"""
        if len(idx) <= LONG_TOP:
            return idx
        differ = nk[idx + s] - 2 * acc[idx].astype(np.int32)        # + จำนวนบิตของคำค้น (ค่าคงที่ ไม่มีผลต่ออันดับ)
        return np.sort(idx[np.argpartition(differ, LONG_TOP)[:LONG_TOP]])

    # ------------------------------------------------------------ ค้น
    def lookup(self, ocr_text: str):
        hit = self._memo.get(ocr_text, _MISS)
        if hit is not _MISS:
            return hit
        hit = self._lookup(ocr_text)
        memo = self._memo
        if len(memo) >= 4096:
            for k in list(islice(memo, 2048)):
                memo.pop(k, None)
        memo[ocr_text] = hit
        return hit

    def _lookup(self, ocr_text):
        raw_q = normalize_raw(ocr_text)
        hit = self.quick.get(raw_q)
        if hit:
            return hit
        q = " ".join(SWEARS.sub(" ", raw_q).split())
        if len(q) < 3:
            return None
        row = self.exact.get(q)
        if row:
            return self._hit(row, 100.0)
        best = self._qmemo.get(q, _MISS)
        if best is _MISS:
            cs = q.encode("ascii", "replace").translate(_TRANS)
            with self._lock:
                best = self._ratio(q, cs)
                if best is None and len(q) >= 24 and q.count(" ") >= 4:
                    # ซับเกมอาจตัดประโยคยาวเป็น 2-3 ช่วงโดยไม่มีจุดคั่น
                    best = self._fragment(q, cs)
            if len(self._qmemo) >= 4096:
                self._qmemo = {}
            self._qmemo[q] = best
        if best:
            return best
        if self.log_missing and " " in q and q not in self._seen_missing:
            self._seen_missing.add(q)
            try:
                with MISSING.open("a", encoding="utf-8") as f:
                    f.write(ocr_text.strip() + "\n")
            except OSError:
                pass
        return None

    def _ratio(self, q, cs):
        """บรรทัดที่คล้ายคำค้นที่สุด (fuzz.ratio) ในช่วงความยาวใกล้เคียง หรือ None ถ้าไม่ถึงเกณฑ์"""
        lq = len(q)
        long_q = lq >= SHORT_MAX
        cut = self.cutoff_long if long_q else self.cutoff
        rr = 72 if long_q else 75                      # ความยาวต้องต่างกันไม่เกินสัดส่วนนี้
        lo, hi = -(-lq * rr // 100), lq * 100 // rr
        top = self._maxlen + 1
        s, e = self._start[min(lo, top)], self._start[min(hi + 1, top)]
        best = None
        if e > s:
            if not long_q:
                # จำนวนบิตที่ต่างกัน <= จำนวนตัวอักษรที่ยอมให้ต่างได้ที่เกณฑ์คะแนนนี้ -> ไม่ตัดคู่ที่ถึงเกณฑ์ทิ้งแน่นอน
                thr = self._thr1.get(lq)
                if thr is None:
                    thr = self._thr1[lq] = np.minimum((100 - cut) * (lq + self._lens) // 100, 255).astype(np.uint8)
                idx = self._where(np.less_equal, self._differ1(self._qsig1(cs), s, e), thr[s:e])
            elif lo > LONG_KEY:
                # ยาวมาก: ลายเซ็นคู่ตัวอักษรเริ่มอิ่ม -> ใช้สามตัวอักษร 512 บิต
                # เกณฑ์: บิตต่างกันไม่เกิน 54% ของความยาวรวม
                sig = self._qsig3(cs)
                o = self._o3
                acc = self._common3(sig, s - o, e - o)
                idx = self._where(np.greater_equal, acc, self._need(3, sig.bit_count() - 54 * lq // 100 - 1)[s - o:e - o])
                idx = self._closest(idx, acc, self._nk3, s - o)
            else:
                # เกณฑ์: บิตคู่ตัวอักษรต่างกันไม่เกิน 36% ของความยาวรวม (ครอบคลุมทุกคู่ที่คะแนน >= 88 แน่นอน
                # และคู่ที่ต่างกันเป็นคำๆ ลงไปถึงเกณฑ์ 76)
                sig = self._qsig2(cs)
                nq = sig.bit_count()
                o = self._o2
                if nq > 255 or s < o:                  # ตัวนับ 8 บิตอาจล้น / อยู่นอกดัชนี (ไม่เกิดกับข้อความจริง)
                    idx = np.arange(e - s)
                else:
                    acc = self._common2(sig, s - o, e - o)
                    idx = self._where(np.greater_equal, acc, self._need(2, nq - 36 * lq // 100 - 1)[s - o:e - o])
                    idx = self._closest(idx, acc, self._nk2, s - o)
            if len(idx):
                il = (idx + s).tolist()
                skeys = self._skeys
                hit = process.extractOne(q, [skeys[i] for i in il], scorer=fuzz.ratio, score_cutoff=cut)
                if hit:
                    best = self._hit(self.rows[self._srow[il[hit[2]]]], hit[1])
        if self._extra:
            cand = [k for k in self._extra if lo <= len(k) <= hi]
            hit = process.extractOne(q, cand, scorer=fuzz.ratio,
                                     score_cutoff=best["score"] if best else cut) if cand else None
            if hit and (best is None or hit[1] > best["score"]):
                best = self._hit(self.exact[hit[0]], hit[1])
        return best

    def _fragment(self, q, cs):
        """คำค้นเป็นท่อนหนึ่งของบรรทัดยาวในคลัง (ตรงเป๊ะ หรือเพี้ยนเล็กน้อย)"""
        o = self._o3
        n = self._n
        s = max(self._start[min(len(q) + 1, self._maxlen + 1)], o)
        if s >= n:
            return None
        sig = self._qsig3(cs)
        nq = sig.bit_count()
        acc = self._common3(sig, s - o, n - o)
        skeys = self._skeys
        # 1) ตรงเป๊ะ: ลายเซ็นของบรรทัดต้องมีบิตของคำค้นครบทุกบิต (เรียงตามความยาว -> ตัวแรกที่เจอคือสั้นสุด)
        for i in self._where(np.greater_equal, acc, nq).tolist():
            at = skeys[s + i].find(q)
            if at >= 0:
                return self._hit(self.rows[self._srow[s + i]], 95.0, (at, at + len(q)))
        # 2) เพี้ยนเล็กน้อย: บิตของคำค้นหายได้ไม่เกิน 30% แล้วให้ RapidFuzz ตรวจตัวที่น่าจะใช่ที่สุด
        idx = self._where(np.greater_equal, acc, max(1, nq - nq * 3 // 10))
        if not len(idx):
            return None
        if len(idx) > FRAGMENT_TOP:
            # จัดอันดับด้วย "บิตที่ตรงเกินกว่าที่จะตรงโดยบังเอิญ" (บรรทัดยาวมากมีบิตเยอะ ตรงโดยบังเอิญได้ง่าย)
            gain = acc[idx].astype(np.int64) * _BITS3 - self._nk3[idx + (s - o)] * nq
            idx = idx[np.argsort(-gain, kind="stable")[:FRAGMENT_TOP]]
        il = (idx + s).tolist()
        hit = process.extractOne(q, [skeys[i] for i in il], scorer=fuzz.partial_ratio, score_cutoff=90)
        if not hit:
            return None
        where = fuzz.partial_ratio_alignment(q, hit[0])
        return self._hit(self.rows[self._srow[il[hit[2]]]], hit[1],
                         (where.dest_start, where.dest_end) if where else None)

    # ------------------------------------------------------------ คำแปลของ "บางส่วน" ของบรรทัด
    def _hit(self, row, score, span=None):
        """ผลลัพธ์ของแถวที่ค้นเจอ; span = ช่วงตัวอักษรในคีย์ที่ตรงกับคำค้น (กรณีคำค้นเป็นท่อนของบรรทัดยาว)"""
        th = row[2]
        if th is None:                       # ประโยคย่อยของบรรทัดที่มีหลายประโยค
            th = self._part(row[3], row[4], row[4] + 1)
        elif span is not None:               # ท่อนหนึ่งของบรรทัดยาว -> เฉพาะประโยคที่ช่วงนั้นคร่อมอยู่
            th = self._span_th(row, span)
        return {"en": row[1], "th": th, "score": score}

    def _align(self, idx):
        if idx not in self._aligned:
            row = self.rows[idx]
            try:
                self._aligned[idx] = align.align(row[1], row[2])
            except Exception:                # ข้อมูลแปลกปลอม -> ใช้คำแปลทั้งบรรทัดตามเดิม
                self._aligned[idx] = None
        return self._aligned[idx]

    def _part(self, idx, i0, i1):
        whole = self.rows[idx][2]
        al = self._align(idx)
        return align.part_thai(al, i0, i1, whole) if al else whole

    def _span_th(self, row, span):
        idx = self._index_of.get(row[0])
        al = self._align(idx) if idx is not None else None
        if not al:
            return row[2]
        # ตำแหน่งของแต่ละประโยคในคีย์ (คีย์ = ประโยคที่ normalize แล้วต่อกันด้วยช่องว่าง)
        pos, bounds = 0, []
        for s in al[0]:
            n = normalize(s)
            bounds.append((pos, pos + len(n)))
            if n:
                pos += len(n) + 1
        if pos - 1 != len(row[0]):
            return row[2]
        inside = [i for i, (a, b) in enumerate(bounds) if b > a and a < span[1] and b > span[0]]
        if not inside:
            return row[2]
        return align.part_thai(al, inside[0], inside[-1] + 1, row[2])


if __name__ == "__main__":
    import time

    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    t0 = time.perf_counter()
    m = Matcher(log_missing=False)
    print(f"loaded {len(m.keys):,} lines in {time.perf_counter() - t0:.2f}s")
    for line in sys.argv[1:] or sys.stdin:
        t0 = time.perf_counter()
        hit = m.lookup(line)
        print(f"{line.strip()} => {hit}  [{(time.perf_counter() - t0) * 1000:.3f} ms]")
