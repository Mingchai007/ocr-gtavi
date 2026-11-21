"""จับคู่ "ประโยคอังกฤษ" กับ "ท่อนคำแปลไทย" ภายในบรรทัดคลังที่รวมหลายประโยคไว้ด้วยกัน

คลังทำจากซับ YouTube ซึ่งมักรวมบทพูด 2-15 ประโยคไว้ในบรรทัดเดียว แต่ในเกมซับขึ้นทีละประโยค
ถ้าแสดงคำแปลทั้งบรรทัดจะยาวเป็นพรืด -> หาว่าท่อนไทยท่อนไหนเป็นคำแปลของประโยคที่อยู่บนจอ แล้วแสดงแค่นั้น

วิธี (แบบเดียวกับการจัดแนวประโยคในคลังข้อความคู่ภาษา): แบ่งอังกฤษตาม . ? !  แบ่งไทยตามช่องว่าง
แล้วหาการจับคู่เรียงลำดับที่ "ต้นทุน" รวมต่ำสุด โดยดูจาก
  - ความยาว: คำแปลไทยยาวประมาณ 0.75 เท่าของอังกฤษ
  - เครื่องหมาย ? และ ! ท้ายประโยคต้องตรงกัน
  - เส้นคั่นคนพูด " / " ในคำแปล
  - คำที่รู้คำแปลแน่นอน (เออ/ไม่/ขอบใจ/ชื่อตัวละคร ฯลฯ)
วัดกับคลังนี้: ท่อนที่เลือกตรงกับคำแปลจริงของประโยคนั้น ~90% ที่เหลือส่วนใหญ่แค่เกิน/ขาดคำอุทานสั้นๆ
"""
import re

SENT_SPLIT = re.compile(r"(?<=[.?!])\s+")
_TH_MARK = re.compile(r"[ัิ-ฺ็-๎]")       # สระบน/ล่าง วรรณยุกต์ (ไม่กินที่)
_TH_KEEP = re.compile(r"[฀-๿a-zA-Z0-9]")
_EN_KEEP = re.compile(r"[A-Za-z0-9]")
_WORD = re.compile(r"[a-z']+")

RATIO = 0.752            # ความยาวไทย / อังกฤษ (วัดจากบรรทัดประโยคเดียว 30,118 บรรทัดในคลัง)
VARIANCE = 1.066         # ความแปรปรวนต่อตัวอักษร
CONFIDENT = 1.5          # ต้นทุนของคู่ที่ต่ำกว่านี้ถือว่ามั่นใจ
_BEADS = [(1, 1), (1, 2), (1, 3), (1, 4), (2, 1), (2, 2), (3, 1), (1, 0), (0, 1)]   # (จำนวนประโยคอังกฤษ, จำนวนท่อนไทย)
_PRIOR = {(1, 1): 0.0, (1, 2): 0.7, (1, 3): 1.6, (1, 4): 2.6, (2, 1): 1.3, (2, 2): 1.8, (3, 1): 2.6}

LEXICON = {
    "yeah": ["เออ", "ใช่", "อืม", "ค่ะ", "ครับ", "อือ", "เอา", "ได้"], "yes": ["ใช่", "ค่ะ", "ครับ", "ได้"],
    "yep": ["เออ", "ใช่", "อืม"], "no": ["ไม่", "เปล่า"], "nah": ["ไม่", "เปล่า"], "nope": ["ไม่", "เปล่า"],
    "okay": ["โอเค", "ได้", "ตกลง", "เอาล่ะ"], "ok": ["โอเค", "ได้"], "alright": ["โอเค", "เอาล่ะ", "ได้"],
    "hey": ["เฮ้", "นี่", "เฮ้ย"], "oh": ["โอ้", "อ้อ", "อ๋อ", "อ้าว", "โอ๊ย", "โห"], "wow": ["ว้าว", "โห"],
    "thanks": ["ขอบ"], "thank": ["ขอบ"], "sorry": ["โทษ"], "please": ["ขอร้อง", "โปรด", "ช่วย", "เถอะ"],
    "what": ["อะไร", "ไร", "ห้ะ", "หา"], "why": ["ทำไม"], "where": ["ไหน"], "who": ["ใคร"],
    "how": ["ไง", "ยังไง", "เท่าไหร่"], "huh": ["ห้ะ", "หา", "ฮะ", "เหรอ"], "right": ["ใช่", "เนอะ", "ถูก", "ขวา"],
    "really": ["จริง", "เหรอ"], "money": ["เงิน"], "car": ["รถ"], "gun": ["ปืน"], "cops": ["ตำรวจ"],
    "police": ["ตำรวจ"], "go": ["ไป"], "come": ["มา"], "wait": ["เดี๋ยว", "รอ"], "stop": ["หยุด"],
    "shit": ["ชิบ", "เชี่ย", "เหี้ย", "ห่า"], "fuck": ["เชี่ย", "เหี้ย", "สัส", "แม่ง"], "man": ["เพื่อน", "พวก"],
    "bro": ["เพื่อน", "พี่"], "good": ["ดี"], "bad": ["แย่", "ไม่ดี"], "love": ["รัก"], "know": ["รู้"],
    "god": ["พระเจ้า"], "damn": ["เวร", "แม่ง"], "sure": ["แน่", "ได้"], "fine": ["ได้", "โอเค", "ดี"],
    "me": ["ฉัน", "กู", "ผม", "หนู"], "you": ["มึง", "นาย", "เธอ", "คุณ", "แก"], "now": ["ตอนนี้", "เดี๋ยวนี้"],
    "here": ["นี่", "ตรงนี้"], "there": ["นั่น", "ตรงนั้น"],
}


def add_names(names):
    """เพิ่มชื่อเฉพาะจาก glossary (เช่น Jason -> เจสัน) เป็นจุดยึดในการจับคู่"""
    for en, th in names.items():
        w = en.lower()
        if _WORD.fullmatch(w) and th:
            LEXICON.setdefault(w, []).append(th)


def split_sentences(en):
    return [s.strip() for s in SENT_SPLIT.split(en.strip()) if s.strip()]


def split_thai(th):
    """คืน (ท่อนไทย, [ท่อนนี้อยู่หลังเส้นคั่นคนพูด "/" ไหม])"""
    chunks, after_slash, slash = [], [], False
    for tok in th.split():
        if tok == "/":
            slash = True
            continue
        chunks.append(tok)
        after_slash.append(slash)
        slash = False
    return chunks, after_slash


def _en_len(s):
    return len(_EN_KEEP.findall(s)) + 0.5 * s.count("[")           # [__] = คำหยาบที่ถูกเซ็นเซอร์ นับนิดหน่อย


def _th_len(s):
    return len(_TH_KEEP.findall(_TH_MARK.sub("", s)))


def _ends(s, mark):
    return s.rstrip("\"' ").endswith(mark)


def align(en, th):
    """คืน (ประโยคอังกฤษ, ท่อนไทย, [(i0, i1, j0, j1, ต้นทุน)]) — ประโยค i0..i1 คู่กับท่อนไทย j0..j1
    หรือ None ถ้าบรรทัดนี้มีประโยคเดียว / ไม่มีคำแปล"""
    en_s = split_sentences(en)
    th_c, slash = split_thai(th)
    n, m = len(en_s), len(th_c)
    if n < 2 or m < 1:
        return None
    parts = [t.strip() for t in th.split(" / ") if t.strip()]
    if len(parts) == n:                                  # คนแปลคั่นไว้ให้ครบทุกประโยคแล้ว
        beads, j = [], 0
        for i, part in enumerate(parts):
            k = len(part.split())
            beads.append((i, i + 1, j, j + k, 0.0))
            j += k
        if j == m:
            return en_s, th_c, beads
    # ผลรวมสะสม -> หาค่าของช่วงใดก็ได้ทันที (เร็วกว่าบวกใหม่ทุกครั้ง ~5 เท่าสำหรับบรรทัดยาว)
    def prefix(vals):
        out, total = [0], 0
        for v in vals:
            total += v
            out.append(total)
        return out

    el = prefix(_en_len(s) for s in en_s)
    tl = prefix(_th_len(c) for c in th_c)
    eq = [_ends(s, "?") for s in en_s]
    tq = [_ends(c, "?") for c in th_c]
    eqs, tqs = prefix(eq), prefix(tq)
    exs = prefix(_ends(s, "!") for s in en_s)
    txs = prefix(_ends(c, "!") for c in th_c)
    sls = prefix(slash)
    # คำที่รู้คำแปล: found[k][j] = บิตของคำในประโยค k ที่เจอคำแปลในท่อนไทย j
    found = []
    for s in en_s:
        ws = [LEXICON[w] for w in set(_WORD.findall(s.lower())) if w in LEXICON][:8]
        found.append([sum(1 << x for x, targets in enumerate(ws) if any(t in c for t in targets)) for c in th_c] if ws else None)

    def cost(i, a, j, b):
        if a == 0:
            return 4.0 + 0.22 * (tl[j + b] - tl[j])           # ท่อนไทยที่ไม่มีต้นฉบับ
        if b == 0:
            return 2.6 + 0.20 * (el[i + a] - el[i])           # ประโยคที่คนแปลตัดทิ้ง (มักเป็นคำอุทาน)
        le, lt = el[i + a] - el[i], tl[j + b] - tl[j]
        c = (lt - RATIO * le) ** 2 / (VARIANCE * (le + 3)) * 0.5 + _PRIOR[(a, b)]
        qe, qt = eqs[i + a] - eqs[i], tqs[j + b] - tqs[j]
        c += 2.5 * abs(qe - qt) - 1.2 * min(qe, qt)
        if eq[i + a - 1] != tq[j + b - 1]:
            c += 1.5                                           # ? ต้องอยู่ท้ายของทั้งสองฝั่ง
        xe, xt = exs[i + a] - exs[i], txs[j + b] - txs[j]
        c += 0.5 * abs(xe - xt) - 0.3 * min(xe, xt)
        if a == 1 and sls[j + b] - sls[j + 1]:
            c += 3.0                                           # ประโยคเดียวไม่ควรคร่อมเส้นคั่นคนพูด
        hit = 0
        for k in range(i, i + a):
            f = found[k]
            if f:
                bits = 0
                for x in range(j, j + b):
                    bits |= f[x]
                hit += bits.bit_count()
        return c - 0.7 * min(hit, 3) if hit else c

    inf = float("inf")
    best = [[inf] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    best[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            cur = best[i][j]
            if cur == inf:
                continue
            for a, b in _BEADS:
                if i + a > n or j + b > m:
                    continue
                c = cur + cost(i, a, j, b)
                if c < best[i + a][j + b]:
                    best[i + a][j + b] = c
                    back[i + a][j + b] = (a, b, c - cur)
    beads = []
    i, j = n, m
    while (i, j) != (0, 0):
        a, b, c = back[i][j]
        beads.append((i - a, i, j - b, j, c))
        i, j = i - a, j - b
    beads.reverse()
    return en_s, th_c, beads


def part_thai(aligned, i0, i1, whole, short_limit=110):
    """คำแปลไทยของประโยคอังกฤษลำดับ i0..i1 (ไม่รวม i1) จากผลของ align()
    มั่นใจ -> เฉพาะท่อนที่คู่กัน, ไม่มั่นใจ -> ถ้าคำแปลทั้งบรรทัดสั้นก็ใช้ทั้งบรรทัด ไม่งั้นเผื่อท่อนข้างเคียงข้างละ 1"""
    en_s, th_c, beads = aligned
    if i0 <= 0 and i1 >= len(en_s):
        return whole
    use = [b for b in beads if b[0] < i1 and b[1] > i0 and b[1] > b[0]]
    j0 = min((b[2] for b in use), default=0)
    j1 = max((b[3] for b in use), default=0)
    sure = bool(use) and j1 > j0 and all(b[4] < CONFIDENT and b[3] > b[2] for b in use)
    if not sure:
        if len(whole) <= short_limit:
            return whole
        j0, j1 = max(0, j0 - 1), min(len(th_c), max(j1, j0) + 1)
    return " ".join(th_c[j0:j1]) or whole
