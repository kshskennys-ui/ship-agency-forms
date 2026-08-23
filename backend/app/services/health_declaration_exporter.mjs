import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const [templatePath, outputPath, payloadPath] = process.argv.slice(2);
if (!templatePath || !outputPath || !payloadPath) {
  throw new Error("健康申报表导出参数不完整");
}

const payload = JSON.parse(await fs.readFile(payloadPath, "utf8"));
const source = await FileBlob.load(templatePath);
const workbook = await SpreadsheetFile.importXlsx(source);
const sheet = workbook.worksheets.getItem("Sheet1");
const crew = payload.crew || [];
const templateRows = 19;

function dateValue(value) {
  if (!value) return null;
  const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})/);
  return match ? new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]))) : value;
}

sheet.getRange("B2").values = [[payload.ship_name || ""]];
sheet.getRange("B3").values = [[payload.imo || ""]];
sheet.getRange("B4").values = [[payload.voyage_number || ""]];
sheet.getRange("B5").values = [[dateValue(payload.declaration_date)]];
sheet.getRange("B5").format.numberFormat = "yyyy-mm-dd";

const crewRows = Array.from({ length: templateRows }, (_, index) => {
  const person = crew[index];
  return [
    person ? index + 1 : null,
    person?.name || null,
    person?.document_no || null,
    person?.gender || null,
    dateValue(person?.birth_date),
    person?.temperature || null,
  ];
});
sheet.getRange("A7:F25").values = crewRows;
sheet.getRange("E7:E25").format.numberFormat = "yyyy-mm-dd";

const recentPlaces = Array.from({ length: templateRows }, (_, index) => [
  crew[index] ? (payload.recent_places || null) : null,
  null,
  null,
]);
sheet.getRange("G7:I25").values = recentPlaces;
sheet.getRange("J7:J25").values = Array.from({ length: templateRows }, (_, index) => [crew[index] ? (payload.contact_default || "No") : null]);
sheet.getRange("M7:M25").values = Array.from({ length: templateRows }, (_, index) => [crew[index] ? (payload.symptom_default || "No") : null]);

function writeContinuationSheet(continuation, people, startIndex, pageNumber) {
  const lastRow = 3 + people.length;
  continuation.showGridLines = false;
  continuation.mergeCells("A1:M1");
  continuation.getRange("A1").values = [[`出入境船员体温和健康申报表（续表${pageNumber}）`]];
  continuation.getRange("A1:M1").format = { font: { bold: true }, horizontalAlignment: "Center", verticalAlignment: "Center" };
  continuation.mergeCells("G3:I3");
  continuation.mergeCells("J3:K3");
  continuation.mergeCells("L3:M3");
  continuation.getRange("A3:M3").values = [[
    "序号", "中文全名 Family name.Given name", "身份证件号（护照） Passport ID", "性别 M/F",
    "出生日期 Date of birth", "体温 Body temperature", "最近14天去过的国内省市或途经的国家和地区？", null, null,
    "最近14天是否接触过有发热、乏力、干咳等呼吸道感染症状的患者", null,
    "你是否有不适或有发热、乏力、干咳或其他症状？或曾接受病毒检测？", null,
  ]];
  continuation.getRange(`A3:M${lastRow}`).format = { borders: { preset: "all", style: "thin", color: "#000000" }, wrapText: true, verticalAlignment: "Center" };
  continuation.getRange("A1:A100").format.columnWidth = 8;
  continuation.getRange("B1:B100").format.columnWidth = 20;
  continuation.getRange("C1:C100").format.columnWidth = 18;
  continuation.getRange("D1:D100").format.columnWidth = 8;
  continuation.getRange("E1:E100").format.columnWidth = 15;
  continuation.getRange("F1:F100").format.columnWidth = 14;
  continuation.getRange("G1:I100").format.columnWidth = 12;
  continuation.getRange("J1:K100").format.columnWidth = 12;
  continuation.getRange("L1:M100").format.columnWidth = 12;
  continuation.getRange("A1:M1").format.rowHeight = 28;
  continuation.getRange(`A3:M${lastRow}`).format.rowHeight = 30;
  continuation.getRange("A3:M3").format.rowHeight = 72;

  for (let offset = 0; offset < people.length; offset += 1) {
    const row = 4 + offset;
    continuation.mergeCells(`G${row}:I${row}`);
    continuation.mergeCells(`J${row}:K${row}`);
    continuation.mergeCells(`L${row}:M${row}`);
  }
  continuation.getRange(`A4:F${lastRow}`).values = people.map((person, offset) => [
    startIndex + offset + 1,
    person.name || null,
    person.document_no || null,
    person.gender || null,
    dateValue(person.birth_date),
    person.temperature || null,
  ]);
  continuation.getRange(`E4:E${lastRow}`).format.numberFormat = "yyyy-mm-dd";
  continuation.getRange(`G4:G${lastRow}`).values = people.map(() => [payload.recent_places || null]);
  continuation.getRange(`J4:J${lastRow}`).values = people.map(() => [payload.contact_default || "No"]);
  continuation.getRange(`L4:L${lastRow}`).values = people.map(() => [payload.symptom_default || "No"]);
}

if (crew.length > templateRows) {
  const remaining = crew.slice(templateRows);
  const continuationNames = ["Sheet2", "Sheet3"];
  let pageNumber = 1;
  for (let offset = 0; offset < remaining.length; offset += templateRows) {
    const batch = remaining.slice(offset, offset + templateRows);
    const sheetName = continuationNames[pageNumber - 1] || `续表${pageNumber}`;
    const continuation = continuationNames[pageNumber - 1]
      ? workbook.worksheets.getItem(sheetName)
      : workbook.worksheets.add(sheetName);
    writeContinuationSheet(continuation, batch, templateRows + offset, pageNumber);
    pageNumber += 1;
  }
}

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
