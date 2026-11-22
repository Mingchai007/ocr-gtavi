"""หน้าเว็บซับไทยพื้นโปร่งใส สำหรับใส่ใน TikTok LIVE Studio (แหล่งภาพแบบ "ลิงก์") หรือ OBS (Browser Source)

โปรแกรมเปิดเว็บเซิร์ฟเวอร์เล็กๆ ในเครื่อง (127.0.0.1 เท่านั้น — เครื่องอื่นเข้าไม่ได้)
  /          หน้าเว็บซับ (พื้นโปร่งใส ตัวหนังสือมีขอบ/เงาตามการตั้งค่า)
  /state     ซับที่กำลังแสดงอยู่ (JSON) — หน้าเว็บถามทุก 0.1 วินาที

ไม่ต้องใช้ฉากเขียว ไม่ต้องใช้ OBS และไม่ต้องให้โปรแกรมจับภาพเห็นหน้าต่างซับ
"""
import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.applog import log

DEFAULT_PORT = 8765

PAGE = """<!doctype html>
<html lang="th"><head><meta charset="utf-8"><title>GTA TH Sub</title>
<style>
  html, body { margin: 0; height: 100%; background: transparent; overflow: hidden; }
  #wrap { position: absolute; left: 0; right: 0; bottom: 4vh; display: flex; flex-direction: column;
          align-items: center; gap: 0.25em; padding: 0 3vw; text-align: center; }
  .line { display: inline-block; line-height: 1.35; padding: 0.05em 0.45em; border-radius: 0.3em;
          white-space: pre-wrap; word-break: break-word; max-width: 94vw; }
  .spk { margin-right: 0.3em; }
  .fade { transition: opacity .15s; }
</style></head>
<body><div id="wrap"></div>
<script>
let last = "";
function outline(w, c) {
  if (w <= 0) return "";
  const s = [];
  for (let a = 0; a < 16; a++) {
    const r = a * Math.PI / 8;
    s.push(`${(Math.cos(r) * w).toFixed(1)}px ${(Math.sin(r) * w).toFixed(1)}px 0 ${c}`);
  }
  return s.join(",");
}
function render(st) {
  const wrap = document.getElementById("wrap");
  wrap.innerHTML = "";
  const s = st.style || {};
  for (const b of (st.blocks || [])) {
    const el = document.createElement("div");
    el.className = "line fade";
    el.style.fontFamily = `"${s.font}", "Leelawadee UI", "Tahoma", sans-serif`;
    el.style.fontSize = Math.round(s.size * b.scale) + "px";
    el.style.fontWeight = s.weight;
    el.style.color = s.color;
    el.style.opacity = b.alpha;
    let sh = outline(s.outline * b.scale, s.outline_color);
    if (s.shadow) sh = (sh ? sh + "," : "") + `2px 3px 4px rgba(0,0,0,.55)`;
    el.style.textShadow = sh;
    if (s.box > 0) el.style.background = `rgba(0,0,0,${(s.box / 255).toFixed(2)})`;
    if (b.speaker) {
      const sp = document.createElement("span");
      sp.className = "spk"; sp.style.color = s.speaker_color; sp.textContent = b.speaker + ":";
      el.appendChild(sp);
    }
    el.appendChild(document.createTextNode(b.text));
    wrap.appendChild(el);
  }
}
async function tick() {
  try {
    const r = await fetch("/state", {cache: "no-store"});
    const t = await r.text();
    if (t !== last) { last = t; render(JSON.parse(t)); }
  } catch (e) { /* โปรแกรมซับปิดอยู่ -> ลองใหม่รอบหน้า */ }
  setTimeout(tick, 100);
}
tick();
</script></body></html>
"""


class WebFeed:
    """เว็บเซิร์ฟเวอร์ในเครื่องที่ส่งซับปัจจุบันให้หน้าเว็บ (ใช้จากเธรดหลักผ่าน update())"""

    def __init__(self, port=DEFAULT_PORT):
        self._state = json.dumps({"blocks": [], "style": {}}).encode("utf-8")
        self._lock = threading.Lock()
        self.httpd = None
        self.port = None
        for p in range(port, port + 10):                     # พอร์ตถูกใช้อยู่ -> ลองพอร์ตถัดไป
            try:
                self.httpd = ThreadingHTTPServer(("127.0.0.1", p), self._handler())
                self.port = p
                break
            except OSError:
                continue
        if self.httpd is None:
            raise OSError("เปิดเว็บซับไม่ได้ (พอร์ต 8765-8774 ถูกใช้หมด)")
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        # https (TikTok LIVE Studio รับเฉพาะลิงก์ https) — ใช้ใบรับรองของเครื่องนี้ (app/webcert.py)
        self.httpsd = None
        self.https_port = None
        try:
            from app.webcert import ensure_cert
            crt, key = ensure_cert()
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(str(crt), str(key))
            for p in range(port + 1, port + 12):
                if p == self.port:
                    continue
                try:
                    srv = ThreadingHTTPServer(("127.0.0.1", p), self._handler())
                except OSError:
                    continue
                srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
                srv.daemon_threads = True
                self.httpsd, self.https_port = srv, p
                threading.Thread(target=srv.serve_forever, daemon=True).start()
                break
        except Exception as e:
            log(f"[web] เปิด https ไม่ได้: {type(e).__name__}: {e}")
        log(f"[web] เปิดหน้าเว็บซับที่ {self.url}")

    @property
    def url(self):
        """ลิงก์ที่ใส่ใน TikTok: https + ชื่อโดเมน localtest.me ที่ชี้กลับมาที่เครื่องตัวเองเสมอ
        (TikTok ไม่รับลิงก์ http / ตัวเลข IP — ข้อมูลซับไม่ออกจากเครื่อง)"""
        return self.urls[0]

    @property
    def urls(self):
        """ลิงก์ทั้งหมดที่ใช้ได้ (เผื่อแบบแรกไม่ได้ผล)"""
        out = []
        if self.https_port:
            out += [f"https://localtest.me:{self.https_port}/", f"https://127.0.0.1.nip.io:{self.https_port}/"]
        out += [f"http://localtest.me:{self.port}/", f"http://127.0.0.1.nip.io:{self.port}/",
                f"http://localhost:{self.port}/"]
        return out

    def _handler(self):
        feed = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path.startswith("/state"):
                    with feed._lock:
                        body, ctype = feed._state, "application/json; charset=utf-8"
                elif self.path in ("/", "/index.html") or self.path.startswith("/?"):
                    body, ctype = PAGE.encode("utf-8"), "text/html; charset=utf-8"
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

        return Handler

    def update(self, blocks, s):
        """blocks = [(ข้อความ, scale, ความทึบ, ชื่อคนพูด)] จาก SubtitleOverlay._blocks()"""
        state = {
            "blocks": [{"text": t, "scale": sc, "alpha": a, "speaker": spk if s.get("show_speaker") else ""}
                       for t, sc, a, spk in blocks],
            "style": {"font": s.get("font_family"), "size": s.get("font_size"), "weight": s.get("font_weight"),
                      "color": s.get("text_color"), "outline": s.get("outline_width"),
                      "outline_color": s.get("outline_color"), "shadow": bool(s.get("shadow")),
                      "speaker_color": s.get("speaker_color"), "box": s.get("box_opacity", 0)},
        }
        data = json.dumps(state, ensure_ascii=False).encode("utf-8")
        with self._lock:
            self._state = data

    def close(self):
        for srv in (self.httpd, self.httpsd):
            try:
                if srv is not None:
                    srv.shutdown()
                    srv.server_close()
            except Exception:
                pass
