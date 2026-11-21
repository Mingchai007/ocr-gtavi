// ตัวรับประโยคตกหล่นจากเครื่องเพื่อน -> เขียนลง Google Sheet (ใช้แทน Discord ได้)
//
// วิธีติดตั้ง:
// 1. สร้าง Google Sheet ใหม่ > เมนู ส่วนขยาย (Extensions) > Apps Script
// 2. ลบโค้ดเดิม วางโค้ดนี้ทั้งหมด แล้วกดบันทึก
// 3. กด Deploy > New deployment > เลือกประเภท Web app
//      Execute as: Me      Who has access: Anyone
// 4. กด Deploy แล้วคัดลอกลิงก์ Web app URL (ลงท้าย /exec)
// 5. วางลิงก์ใน data/report_target.json:  {"url": "https://script.google.com/macros/s/.../exec"}
//
// คอลัมน์ที่ได้: เวลา | ประโยคอังกฤษ | เวอร์ชันคลังของเครื่องที่ส่ง

function doPost(e) {
  var data = JSON.parse(e.postData.contents);
  var lines = (data.lines || []).slice(0, 50);
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
  if (sheet.getLastRow() === 0) sheet.appendRow(["เวลา", "ประโยค", "คลัง"]);
  var now = new Date();
  lines.forEach(function (line) {
    sheet.appendRow([now, String(line).slice(0, 300), data.corpus || ""]);
  });
  return ContentService.createTextOutput("ok");
}
