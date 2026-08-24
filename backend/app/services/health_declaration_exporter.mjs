import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";
import JSZip from "jszip";

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
const dataRows = Math.max(crew.length, 1);
const dataEndRow = 6 + dataRows;
const footerStartRow = dataEndRow + 2;

function dateValue(value) {
  if (!value) return null;
  const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})/);
  return match ? new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]))) : value;
}

async function getTemplateRowStyles(path) {
  const zip = await JSZip.loadAsync(await fs.readFile(path));
  const xml = await zip.file("xl/worksheets/sheet1.xml").async("string");
  const rowMatch = xml.match(/<row\b[^>]*\br="25"[^>]*>[\s\S]*?<\/row>/);
  const styles = new Map();
  if (!rowMatch) return styles;
  for (const match of rowMatch[0].matchAll(/<c\b([^>]*\br="([A-Z]+25)"[^>]*)/g)) {
    const style = match[1].match(/\bs="([^"]+)"/)?.[1];
    if (style) styles.set(match[2], style);
  }
  return styles;
}

async function getTemplateMergeRefs(path) {
  const zip = await JSZip.loadAsync(await fs.readFile(path));
  const xml = await zip.file("xl/worksheets/sheet1.xml").async("string");
  return [...xml.matchAll(/<mergeCell\b[^>]*\bref="([^"]+)"[^>]*\/>/g)].map((match) => match[1]);
}

function restoreMergeCells(xml, refs) {
  const mergeXml = `<x:mergeCells count="${refs.length}">${refs.map((ref) => `<x:mergeCell ref="${ref}" />`).join("")}</x:mergeCells>`;
  const existing = /<x:mergeCells\b[\s\S]*?<\/x:mergeCells>/;
  if (existing.test(xml)) return xml.replace(existing, mergeXml);
  return xml.replace("</x:sheetData>", `</x:sheetData>${mergeXml}`);
}

function replaceCellStyle(xml, address, styleId) {
  const pattern = new RegExp(`<(?:[A-Za-z0-9_]+:)?c\\b(?=[^>]*\\br="${address}"[^>]*)[^>]*(?:/>|>[\\s\\S]*?<\\/(?:[A-Za-z0-9_]+:)?c>)`);
  const match = xml.match(pattern);
  if (!match) return xml;
  let cell = match[0];
  if (/\bs="[^"]*"/.test(cell)) {
    cell = cell.replace(/\bs="[^"]*"/, `s="${styleId}"`);
  } else {
    cell = cell.replace(/^(<(?:(?:[A-Za-z0-9_]+):)?c)\b/, `$1 s="${styleId}"`);
  }
  return xml.replace(pattern, cell);
}

// The template labels occupy A:B, while the merged value cells start at C.
// Writing to B would place values inside the label merge and render blank.
sheet.getRange("C2").values = [[payload.ship_name || ""]];
sheet.getRange("C3").values = [[payload.imo || ""]];
sheet.getRange("C4").values = [[payload.voyage_number || ""]];
sheet.getRange("C5").values = [[dateValue(payload.declaration_date)]];
sheet.getRange("C5").format.numberFormat = "yyyy-mm-dd";

// The source workbook reserves 19 rows, but the declaration must have exactly
// one data row per crew member. Move the signature footer and extend the table
// with copies of the last formatted data row when needed.
for (let row = 7; row <= templateRows + 6; row += 1) {
  if (row <= dataEndRow) continue;
  try { sheet.unmergeCells(`G${row}:I${row}`); } catch {}
  try { sheet.unmergeCells(`J${row}:L${row}`); } catch {}
  try { sheet.unmergeCells(`M${row}:O${row}`); } catch {}
  sheet.getRange(`A${row}:O${row}`).clear({ applyTo: "all" });
}

if (footerStartRow !== 27) {
  sheet.getRange(`A${footerStartRow}:O${footerStartRow + 1}`).copyFrom(sheet.getRange("A27:O28"), "all");
  sheet.getRange("A27:O28").clear({ applyTo: "all" });
}
for (let row = templateRows + 7; row <= dataEndRow; row += 1) {
  for (const column of ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O"]) {
    sheet.getRange(`${column}${row}`).copyFrom(sheet.getRange(`${column}25`), "all");
  }
  sheet.mergeCells(`G${row}:I${row}`);
  sheet.mergeCells(`J${row}:L${row}`);
  sheet.mergeCells(`M${row}:O${row}`);
  sheet.getRange(`A${row}:O${row}`).format = {
    borders: { preset: "all", style: "thin", color: "auto" },
    horizontalAlignment: "Center",
    verticalAlignment: "Center",
  };
  sheet.getRange(`A${row}:O${row}`).format.rowHeight = 30;
}

const crewRows = Array.from({ length: dataRows }, (_, index) => {
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
sheet.getRange(`A7:F${dataEndRow}`).values = crewRows;
sheet.getRange(`E7:E${dataEndRow}`).format.numberFormat = "yyyy-mm-dd";

const recentPlaces = Array.from({ length: dataRows }, (_, index) => [
  crew[index] ? (payload.recent_places || null) : null,
  null,
  null,
]);
sheet.getRange(`G7:I${dataEndRow}`).values = recentPlaces;
sheet.getRange(`J7:J${dataEndRow}`).values = Array.from({ length: dataRows }, (_, index) => [crew[index] ? (payload.contact_default || "No") : null]);
sheet.getRange(`M7:M${dataEndRow}`).values = Array.from({ length: dataRows }, (_, index) => [crew[index] ? (payload.symptom_default || "No") : null]);

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

const output = await SpreadsheetFile.exportXlsx(workbook);
const artifactPath = `${outputPath}.artifact.xlsx`;
await output.save(artifactPath);

// artifact-tool can normalize the template's "automatic" border into an
// explicit black border when rows are added. Restore the exact style IDs from
// the template's final data row so the appended rows print identically.
if (dataEndRow > templateRows + 6) {
  const templateStyles = await getTemplateRowStyles(templatePath);
  const resultZip = await JSZip.loadAsync(await fs.readFile(artifactPath));
  let sheetXml = await resultZip.file("xl/worksheets/sheet1.xml").async("string");
  for (let row = templateRows + 7; row <= dataEndRow; row += 1) {
    for (const column of ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O"]) {
      const style = templateStyles.get(`${column}25`);
      if (style) sheetXml = replaceCellStyle(sheetXml, `${column}${row}`, style);
    }
  }
  const templateMerges = await getTemplateMergeRefs(templatePath);
  const retainedMerges = templateMerges.filter((ref) => {
    const match = ref.match(/^[GJM](\d+):/);
    return !match || Number(match[1]) <= dataEndRow;
  });
  for (let row = templateRows + 7; row <= dataEndRow; row += 1) {
    retainedMerges.push(`G${row}:I${row}`, `J${row}:L${row}`, `M${row}:O${row}`);
  }
  sheetXml = restoreMergeCells(sheetXml, retainedMerges);
  resultZip.file("xl/worksheets/sheet1.xml", sheetXml);
  await fs.writeFile(outputPath, await resultZip.generateAsync({
    type: "nodebuffer",
    compression: "DEFLATE",
    compressionOptions: { level: 6 },
  }));
  await fs.rm(artifactPath, { force: true });
} else {
  await fs.rename(artifactPath, outputPath);
}
