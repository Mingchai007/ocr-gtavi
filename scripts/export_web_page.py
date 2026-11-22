"""เขียนหน้าเว็บซับ (app/webfeed.py PAGE) ไปที่ docs/tiktok/index.html สำหรับ GitHub Pages

TikTok LIVE Studio ไม่รับลิงก์ที่ชี้มาที่เครื่องตัวเอง จึงวางหน้าเว็บไว้บน GitHub Pages
แล้วให้หน้าเว็บดึงซับจากโปรแกรมในเครื่องคนดูเอง (http://127.0.0.1:8765/state)
รันใหม่ทุกครั้งที่แก้ PAGE แล้ว commit + push (GitHub Pages อัปเดตเองภายใน ~1 นาที)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.webfeed import PAGE  # noqa: E402

out = ROOT / "docs" / "tiktok" / "index.html"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(PAGE, encoding="utf-8", newline="\n")
(ROOT / "docs" / ".nojekyll").write_text("", encoding="utf-8")
print("เขียนแล้ว:", out)
