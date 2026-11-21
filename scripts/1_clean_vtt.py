"""คลีนไฟล์ .vtt ใน data/raw_vtt/ -> data/raw_segments.json

- ตัด header / timestamp / แท็ก <c> <00:00:00.000>
- ตัดบรรทัดซ้ำที่เกิดจากซับ auto-caption แบบเลื่อนบรรทัดของ YouTube
- รวมคำเป็น "ช่วงบทพูด" (segment) โดยตัดเมื่อเงียบเกิน GAP วินาที หรือยาวเกิน MAX_WORDS
  (การแบ่งประโยคจริง + ใส่วรรคตอน ให้ AI ทำในขั้นที่ 2)

ใช้: python scripts/1_clean_vtt.py
"""
import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw_vtt"
OUT = ROOT / "data" / "raw_segments.json"
GLOSSARY = ROOT / "data" / "glossary.json"

GAP = 1.5         # เงียบเกินกี่วินาทีถือว่าขึ้นช่วงใหม่
MAX_WORDS = 60    # ความยาวสูงสุดต่อช่วง

TS = re.compile(r"(\d+):(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(\d+):(\d{2}):(\d{2})\.(\d{3})")
TAG = re.compile(r"<[^>]+>")
CENSORED = re.compile(r"\[\s*_+\s*\]")             # YouTube เซ็นเซอร์คำหยาบเป็น [ __ ] -> เก็บไว้เป็น [__]
NOISE = re.compile(r"\[(?!__\])[^\]]*\]|\([^)]*\)|♪+|>>")  # [Music] (laughs) ♪ >>


def secs(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def parse_cues(text):
    """คืน list ของ (start, end, [บรรทัดข้อความที่ลบแท็กแล้ว])"""
    cues, cur = [], None
    for line in text.splitlines():
        m = TS.search(line)
        if m:
            g = m.groups()
            cur = [secs(*g[:4]), secs(*g[4:]), []]
            cues.append(cur)
        elif cur is not None:
            if not line.strip():
                cur = None
                continue
            clean = html.unescape(TAG.sub("", line))
            clean = CENSORED.sub(" [__] ", clean)
            clean = NOISE.sub(" ", clean)
            clean = re.sub(r"\s+", " ", clean).strip()
            if clean:
                cur[2].append(clean)
    return cues


def dedupe_lines(cues):
    """ซับเลื่อนบรรทัด: บรรทัดเดิมจะโผล่ซ้ำใน cue ถัดไป -> เก็บเฉพาะบรรทัดใหม่"""
    out, recent = [], []
    for start, end, lines in cues:
        for ln in lines:
            if ln in recent:
                continue
            out.append((start, end, ln))
            recent.append(ln)
            recent = recent[-4:]
    return out


def build_segments(lines, source):
    segs, cur = [], None
    for start, end, text in lines:
        n = len(text.split())
        if cur and (start - cur["end"] > GAP or cur["words"] + n > MAX_WORDS):
            segs.append(cur)
            cur = None
        if cur is None:
            cur = {"source": source, "start": round(start, 2), "end": end, "text": text, "words": n}
        else:
            cur["text"] += " " + text
            cur["end"] = end
            cur["words"] += n
    if cur:
        segs.append(cur)
    for s in segs:
        s["end"] = round(s["end"], 2)
        del s["words"]
    return segs


def apply_fixes(text, fixes):
    for wrong, right in fixes.items():
        text = re.sub(rf"\b{re.escape(wrong)}\b", right, text, flags=re.IGNORECASE)
    return text


def main():
    fixes = json.loads(GLOSSARY.read_text(encoding="utf-8")).get("asr_fixes", {})
    # เก็บ segment ของคลิปที่คลีนไปแล้วไว้เหมือนเดิม (id ต้องไม่เปลี่ยน เพราะขั้นแปลใช้ id จำว่าแปลอะไรไปแล้ว)
    # ถ้าต้องการคลีนคลิปเดิมใหม่ ให้ลบ data/raw_segments.json ก่อน (และลบ data/translations.db ด้วย)
    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else []
    done_sources = {s["source"] for s in old}
    if done_sources:
        print("มีอยู่แล้ว (ข้าม):", ", ".join(sorted(done_sources)))
    all_segs = []
    for vtt in sorted(RAW_DIR.glob("*.vtt")):
        source = vtt.name.split(".")[0]
        if source in done_sources:
            continue
        cues = parse_cues(vtt.read_text(encoding="utf-8", errors="replace"))
        lines = dedupe_lines(cues)
        segs = build_segments(lines, source)
        for s in segs:
            s["text"] = apply_fixes(s["text"], fixes)
        words = sum(len(s["text"].split()) for s in segs)
        print(f"{vtt.name}: {len(cues)} cues -> {len(lines)} lines -> {len(segs)} segments, {words} words")
        all_segs.extend(segs)

    next_id = max((s["id"] for s in old), default=-1) + 1
    for i, s in enumerate(all_segs):
        s["id"] = next_id + i
    merged = old + all_segs
    OUT.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"เพิ่มใหม่ {len(all_segs)} segments, รวม {len(merged)} -> {OUT}")


if __name__ == "__main__":
    main()
