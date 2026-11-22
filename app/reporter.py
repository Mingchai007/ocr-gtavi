"""ส่งประโยคที่ไม่มีในคลัง (จากเครื่องเพื่อน) ไปให้เจ้าของคลังเบื้องหลัง — Discord Webhook หรือ Google Sheet

ตั้งปลายทางใน data/report_target.json:  {"url": "https://discord.com/api/webhooks/..."}
  - ลิงก์ discord.com/api/webhooks/...  -> ส่งเป็นข้อความเข้าห้อง Discord
  - ลิงก์อื่น (เช่น Google Apps Script)  -> POST JSON {"app", "corpus", "lines": [...]}  (ดู scripts/google_sheet_receiver.gs)

ไม่หน่วงเกม: เก็บเข้าคิวในหน่วยความจำ แล้วเธรดเบื้องหลังส่งเป็นชุดทุก ~60 วินาที (และตอนปิดโปรแกรม)
ส่งเฉพาะข้อความซับอังกฤษที่อ่านได้จากจอ — ไม่ส่งชื่อเครื่อง ชื่อผู้ใช้ หรือข้อมูลอื่น
ปิดได้ที่ ตั้งค่า > ระบบ > "ส่งประโยคที่ยังไม่มีคำแปลให้ผู้ทำคลัง"
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request

from app.applog import log
from app.matcher import ROOT, normalize

TARGET_PATH = ROOT / "data" / "report_target.json"
SENT_PATH = ROOT / "data" / "reported.txt"       # ประโยคที่เครื่องนี้ส่งไปแล้ว (กันส่งซ้ำข้ามวัน)
INTERVAL = 60                                    # ส่งเป็นชุดทุกกี่วินาที
MAX_BATCH = 25                                   # ประโยคต่อชุด
MAX_PER_DAY = 300                                # กันส่งรัวถ้าเกมขึ้นขยะเยอะ
TIMEOUT = 15


def target_url():
    env = os.environ.get("GTA6THAISUB_REPORT_URL", "").strip()
    if env:
        return env
    try:
        return json.loads(TARGET_PATH.read_text(encoding="utf-8")).get("url", "").strip()
    except (OSError, ValueError):
        return ""


def is_discord(url):
    return "discord.com/api/webhooks/" in url or "discordapp.com/api/webhooks/" in url


class Reporter:
    def __init__(self, enabled=True, corpus_version=0):
        self.url = target_url()
        self.enabled = enabled
        self.corpus = corpus_version
        self.queue = []
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stop_flag = False
        self.sent_today = 0
        self.day = time.strftime("%Y-%m-%d")
        self.backoff_until = 0.0
        self.total_sent = 0
        self.last_error = ""
        try:
            self.seen = set(SENT_PATH.read_text(encoding="utf-8").splitlines())
        except OSError:
            self.seen = set()
        self.thread = None
        if self.url:
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()

    @property
    def available(self):
        return bool(self.url)

    def add(self, text):
        """เรียกจากเธรดหลักได้ทันที (แค่ใส่คิว)"""
        if not (self.enabled and self.url):
            return
        key = normalize(text)
        if len(key) < 6:
            return
        with self.lock:
            if key in self.seen or any(normalize(t) == key for t in self.queue):
                return
            self.queue.append(text.strip()[:300])

    def add_raw(self, text):
        """ข้อความยาวที่ไม่ต้องกันซ้ำ (เช่น แจ้งบัค) — ส่งเป็นข้อความเดี่ยวรอบถัดไป"""
        if self.enabled and self.url:
            with self.lock:
                self.queue.append(text[:1800])
            self.wake.set()

    def flush(self, wait=2.0):
        """ตอนปิดโปรแกรม: ส่งที่ค้างอยู่ (รอไม่เกิน wait วินาที)"""
        if not self.thread:
            return
        self.stop_flag = True
        self.wake.set()
        self.thread.join(wait)

    # ------------------------------------------------------------ เบื้องหลัง
    def _loop(self):
        while True:
            self.wake.wait(INTERVAL)
            self.wake.clear()
            if self.stop_flag:
                self._send_batch()
                return
            if time.time() >= self.backoff_until:
                self._send_batch()

    def _send_batch(self):
        today = time.strftime("%Y-%m-%d")
        if today != self.day:
            self.day, self.sent_today = today, 0
        with self.lock:
            room = min(MAX_BATCH, MAX_PER_DAY - self.sent_today)
            batch = self.queue[:max(0, room)]
        if not batch:
            return
        try:
            self._post(batch)
        except Exception as e:                     # เน็ตหลุด/ลิงก์เสีย -> เก็บไว้ส่งรอบหน้า
            self.last_error = f"{type(e).__name__}"
            retry = 60
            if isinstance(e, urllib.error.HTTPError):
                if e.code == 429:
                    try:
                        retry = float(json.loads(e.read().decode("utf-8")).get("retry_after", 60))
                    except Exception:
                        pass
                elif e.code in (401, 403, 404):
                    retry = 3600                  # ลิงก์ถูกลบ -> ไม่ต้องยิงถี่
                self.last_error = f"HTTP {e.code}"
            self.backoff_until = time.time() + retry
            log(f"[report] ส่งไม่สำเร็จ ({self.last_error}) จะลองใหม่ใน {retry:.0f} วิ")
            return
        with self.lock:
            del self.queue[:len(batch)]
            keys = [normalize(t) for t in batch]
            self.seen.update(keys)
        self.sent_today += len(batch)
        self.total_sent += len(batch)
        self.last_error = ""
        try:
            with open(SENT_PATH, "a", encoding="utf-8") as f:
                f.write("".join(k + "\n" for k in keys))
        except OSError:
            pass

    def _post(self, lines):
        if is_discord(self.url):
            body = "```\n" + "\n".join(lines).replace("```", "'''") + "\n```"
            if len(body) > 1900:                   # Discord จำกัด 2000 ตัวอักษรต่อข้อความ
                body = body[:1890] + "\n```"
            payload = {"username": "ซับไทย GTA VI", "content": body,
                       "allowed_mentions": {"parse": []}}
        else:
            payload = {"app": "GTA6ThaiSub", "corpus": self.corpus, "lines": lines}
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "GTA6ThaiSub"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            r.read()
