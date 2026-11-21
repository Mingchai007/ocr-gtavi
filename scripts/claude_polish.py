"""เครื่องมือให้ Claude Code เกลาคำแปลทีละชุด (ใช้คู่กับแชท ไม่ต้องใช้ API key)

  python scripts/claude_polish.py next [N]      # ดึง N บรรทัดถัดไป (ค่าเริ่มต้น 200) + บริบทก่อนหน้า
  python scripts/claude_polish.py apply FILE    # บันทึกผล: แต่ละบรรทัด "rowid<TAB>คำแปลใหม่" (หรือ DEL = ลบขยะ)
                                                #   บรรทัดในชุดที่ไม่ได้ระบุ = คำแปลเดิมดีแล้ว
  python scripts/claude_polish.py status        # ความคืบหน้า

- คำแปลเดิมเก็บไว้ใน th_raw, บรรทัดที่ลบเก็บไว้ในตาราง removed
- บรรทัดที่ Claude ตรวจแล้วอยู่ในตาราง claude_done (5_polish.py ของ Gemini จะข้ามบรรทัดเหล่านี้)
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DATA = ROOT / "data"
DB_PATH = DATA / "translations.db"
import os
STATE = Path(os.environ["CP_STATE"]) if os.environ.get("CP_STATE") else DATA / "claude_batch.json"
CONTEXT = 6


def db():
    con = sqlite3.connect(DB_PATH, timeout=60)
    cols = {r[1] for r in con.execute("PRAGMA table_info(translations)")}
    if "th_raw" not in cols:
        con.execute("ALTER TABLE translations ADD COLUMN th_raw TEXT")
    con.execute("CREATE TABLE IF NOT EXISTS claude_done (rid INTEGER PRIMARY KEY)")
    con.execute("CREATE TABLE IF NOT EXISTS claimed (rid INTEGER PRIMARY KEY)")
    con.execute("CREATE TABLE IF NOT EXISTS removed (en_text TEXT, th_text TEXT, source TEXT, start_sec REAL)")
    con.commit()
    return con


def rows(con):
    return con.execute(
        "SELECT rowid, en_text, th_text, source FROM translations "
        "WHERE source NOT LIKE 'live-%' ORDER BY source, start_sec, rowid"
    ).fetchall()


def cmd_next(n):
    con = db()
    allr = rows(con)
    if os.environ.get("CP_STATE"):   # โหมดขนาน: ข้ามบรรทัดที่ worker อื่นจองไว้
        con.execute("BEGIN IMMEDIATE")
        done = {r[0] for r in con.execute("SELECT rid FROM claude_done UNION SELECT rid FROM claimed")}
    else:
        done = {r[0] for r in con.execute("SELECT rid FROM claude_done")}
    start = next((i for i, r in enumerate(allr) if r[0] not in done), None)
    if start is None:
        if os.environ.get("CP_STATE"):
            con.commit()
        print("เสร็จครบทุกบรรทัดแล้ว")
        return
    src = allr[start][3]
    batch = [r for r in allr[start:start + n] if r[3] == src and r[0] not in done]
    ctx = [r for r in allr[max(0, start - CONTEXT):start] if r[3] == src]
    STATE.write_text(json.dumps([r[0] for r in batch]), encoding="utf-8")
    if os.environ.get("CP_STATE"):
        con.executemany("INSERT OR IGNORE INTO claimed VALUES (?)", [(r[0],) for r in batch])
        con.commit()
    header = f"## {src}  ({len(done)}/{len(allr)} เสร็จ)  ชุดนี้ {len(batch)} บรรทัด"
    lines = [header] + [f"ctx|{r[1]}|{r[2]}" for r in ctx] + [f"{r[0]}|{r[1]}|{r[2]}" for r in batch]
    if os.environ.get("CP_OUT"):
        Path(os.environ["CP_OUT"]).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(header)
    else:
        print("\n".join(lines))


def cmd_apply(path):
    con = db()
    ids = json.loads(STATE.read_text(encoding="utf-8"))
    idset = set(ids)
    changed = deleted = 0
    con.execute("BEGIN IMMEDIATE")
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if "\t" not in line:
            continue
        rid, th = line.split("\t", 1)
        rid, th = int(rid.strip()), th.strip()
        if rid not in idset or not th:
            print(f"  ข้าม id {rid} (ไม่อยู่ในชุดนี้)")
            continue
        if th == "DEL":
            row = con.execute("SELECT en_text, th_text, source, start_sec FROM translations WHERE rowid=?",
                              (rid,)).fetchone()
            if row:
                con.execute("INSERT INTO removed VALUES (?,?,?,?)", row)
                con.execute("DELETE FROM translations WHERE rowid=?", (rid,))
                deleted += 1
        else:
            con.execute("UPDATE translations SET th_raw=COALESCE(th_raw, th_text), th_text=? WHERE rowid=?",
                        (th, rid))
            changed += 1
    con.executemany("INSERT OR IGNORE INTO claude_done VALUES (?)", [(i,) for i in ids])
    con.executemany("DELETE FROM claimed WHERE rid=?", [(i,) for i in ids])
    con.commit()
    STATE.unlink()
    copied = propagate(con, ids)
    cmd_status(con, f"บันทึก: แก้ {changed}, ลบ {deleted}, คงเดิม {len(ids) - changed - deleted}, "
                    f"คัดลอกไปประโยคซ้ำในคลิปอื่น {copied} | ")


def propagate(con, ids, cutoff=92):
    """คัดลอกคำแปลที่เกลาแล้วไปให้ประโยคที่เกือบเหมือนกันในคลิปอื่น (Shirrako/Sirloin เล่นฉากเดียวกัน)"""
    from rapidfuzz import fuzz, process
    done = {r[0] for r in con.execute("SELECT rid FROM claude_done UNION SELECT rid FROM claimed")}
    import re
    swear = re.compile(r"\[__\]|fuck|shit|bitch|damn|asshole|dick|pussy|cunt", re.I)
    pool = [(rid, key, bool(swear.search(en))) for rid, key, en in con.execute(
        "SELECT rowid, en_normalized, en_text FROM translations WHERE source NOT LIKE 'live-%'") if rid not in done]
    if not pool:
        return 0
    keys = [p[1] for p in pool]
    src = con.execute(
        f"SELECT rowid, en_normalized, th_text, en_text FROM translations WHERE rowid IN ({','.join('?' * len(ids))})",
        ids).fetchall()
    hits = {}
    for _, key, th, en in src:
        has_swear = bool(swear.search(en))
        if len(key) < 12:          # ประโยคสั้นๆ ความหมายขึ้นกับบริบท ไม่คัดลอก
            continue
        for _, score, idx in process.extract(key, keys, scorer=fuzz.ratio, score_cutoff=cutoff, limit=10):
            k2 = keys[idx]
            if min(len(k2), len(key)) / max(len(k2), len(key)) >= 0.9 and pool[idx][2] == has_swear:
                hits.setdefault(pool[idx][0], th)
    for rid, th in hits.items():
        con.execute("UPDATE translations SET th_raw=COALESCE(th_raw, th_text), th_text=? WHERE rowid=?", (th, rid))
    con.executemany("INSERT OR IGNORE INTO claude_done VALUES (?)", [(r,) for r in hits])
    con.commit()
    return len(hits)


def cmd_status(con=None, prefix=""):
    con = con or db()
    total = con.execute("SELECT COUNT(*) FROM translations WHERE source NOT LIKE 'live-%'").fetchone()[0]
    done = con.execute("SELECT COUNT(*) FROM claude_done d JOIN translations t ON t.rowid=d.rid").fetchone()[0]
    print(f"{prefix}Claude เกลาแล้ว {done}/{total} ({done / total:.1%})")


MISSED = DATA / "missed.tsv"


def cmd_missed():
    """แสดงประโยคที่โปรแกรมซับเจอแต่แปลไม่ได้ (ยังไม่ได้เพิ่มเข้าคลัง)"""
    if not MISSED.exists():
        print("ยังไม่มีประโยคที่แปลไม่ได้")
        return
    seen = set()
    for line in MISSED.read_text(encoding="utf-8").splitlines():
        en = line.split("\t")[-1].strip()
        if en and en not in seen:
            seen.add(en)
            print(en)


def cmd_add_missed(path):
    """เพิ่มคำแปลเข้าคลัง: แต่ละบรรทัด "อังกฤษ<TAB>ไทย" แล้วลบออกจาก missed.tsv"""
    sys.path.insert(0, str(ROOT))
    from app.matcher import normalize
    con = db()
    added = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if "\t" not in line:
            continue
        en, th = (x.strip() for x in line.split("\t", 1))
        if not en or not th or th == "DEL":
            added.add(en)
            continue
        con.execute("INSERT OR REPLACE INTO translations (en_normalized, en_text, th_text, source, start_sec) "
                    "VALUES (?,?,?,?,?)", (normalize(en), en, th, "missed", None))
        added.add(en)
    con.commit()
    if MISSED.exists():
        keep = [l for l in MISSED.read_text(encoding="utf-8").splitlines() if l.split("\t")[-1].strip() not in added]
        MISSED.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
    print(f"เพิ่มเข้าคลัง {len(added)} ประโยค")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "missed":
        cmd_missed()
    elif a and a[0] == "add-missed":
        cmd_add_missed(a[1])
    elif a and a[0] == "next":
        cmd_next(int(a[1]) if len(a) > 1 else 200)
    elif a and a[0] == "apply":
        cmd_apply(a[1])
    else:
        cmd_status()

