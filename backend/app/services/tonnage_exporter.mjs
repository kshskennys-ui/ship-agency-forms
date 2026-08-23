import fs from "node:fs/promises";
import JSZip from "jszip";

const [, , templatePath, outputPath, payloadPath] = process.argv;
const payload = JSON.parse(await fs.readFile(payloadPath, "utf8"));

function text(value) {
  return value === null || value === undefined ? "" : String(value);
}

function escapeXml(value) {
  return text(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function cellPatternFor(xml, address) {
  const selfClosingPattern = new RegExp(
    "<c\\b(?=[^>]*\\br=\"" + address + "\"[^>]*)[^>]*/>",
  );
  const fullCellPattern = new RegExp(
    "<c\\b(?=[^>]*\\br=\"" + address + "\"[^>]*)[^>]*>[\\s\\S]*?</c>",
  );
  return selfClosingPattern.test(xml) ? selfClosingPattern : fullCellPattern;
}

function replaceCell(xml, address, value, valueType = "string") {
  const cellPattern = cellPatternFor(xml, address);
  const match = xml.match(cellPattern);
  if (!match) {
    throw new Error("吨税表模板未找到单元格 " + address);
  }

  const original = match[0];
  let openingTag = original.match(/^<c\b[^>]*>/)[0];
  openingTag = openingTag.replace(/\s+t="[^"]*"/, "");
  openingTag = openingTag.replace(/\/>$/, ">");

  if (valueType === "number") {
    return xml.replace(cellPattern, openingTag + "<v>" + text(value) + "</v></c>");
  }

  openingTag = openingTag.replace(/>$/, ' t="inlineStr">');
  const stringValue = text(value);
  const preserve = /^\s|\s$/.test(stringValue) ? ' xml:space="preserve"' : "";
  const body = "<is><t" + preserve + ">" + escapeXml(stringValue) + "</t></is>";
  return xml.replace(cellPattern, openingTag + body + "</c>");
}

function excelDateSerial(value) {
  if (!value) return null;
  const match = String(value).match(/^([0-9]{4})-([0-9]{2})-([0-9]{2})/);
  if (!match) return null;
  const utc = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return (utc - Date.UTC(1899, 11, 30)) / 86400000;
}

const nationalityEnglish = {
  "中国": "CHINA",
  "中国香港": "HONG KONG, CHINA",
  "中国澳门": "MACAO, CHINA",
  "中国台湾": "TAIWAN, CHINA",
  "利比里亚": "LIBERIA",
  "瓦努阿图": "VANUATU",
  "巴拿马": "PANAMA",
  "马耳他": "MALTA",
  "新加坡": "SINGAPORE",
  "日本": "JAPAN",
  "韩国": "KOREA",
  "泰国": "THAILAND",
  "越南": "VIETNAM",
  "菲律宾": "PHILIPPINES",
  "印度": "INDIA",
  "乌克兰": "UKRAINE",
  "斯里兰卡": "SRI LANKA",
  "黑山共和国": "MONTENEGRO",
  "埃及": "EGYPT",
  "马来西亚": "MALAYSIA",
  "印度尼西亚": "INDONESIA",
};

const vessel = payload.vessel || {};
const voyage = payload.voyage || {};
const application = payload.application || {};
const today = payload.today || {};
const nationality = text(vessel.nationality);
const nationalityEn = nationalityEnglish[nationality] || nationality;

const workbook = await JSZip.loadAsync(await fs.readFile(templatePath));
let sheetXml = await workbook.file("xl/worksheets/sheet30.xml").async("string");

const textValues = {
  A1: "金额：" + text(application.amount),
  A2: "编号：" + text(application.pre_entry_no),
  C4: vessel.imo,
  F19: vessel.chinese_name,
  F21: vessel.english_name,
  F23: nationality,
  E25: nationalityEn,
  Q19: "集装箱轮Container",
  Q27: application.charter_relation || "其他",
  C31: "按三十天期、九十天期或一年期（由申请人选定一种）",
  C33: "Tonnage Dues Certificate valid for 30days / 90days / 1year",
  C34: application.duration_label || "待选择",
};

for (const [address, value] of Object.entries(textValues)) {
  sheetXml = replaceCell(sheetXml, address, value, "string");
}

const arrivalSerial = excelDateSerial(voyage.arrival_date);
if (arrivalSerial !== null) {
  sheetXml = replaceCell(sheetXml, "F27", arrivalSerial, "number");
}
sheetXml = replaceCell(sheetXml, "P23", vessel.net_tonnage ?? "", "number");

for (const [address, value] of Object.entries({
  B51: today.year,
  E51: today.month,
  G51: today.day,
  L51: today.year,
  O51: today.month,
  Q51: today.day,
})) {
  sheetXml = replaceCell(sheetXml, address, value, "number");
}

workbook.file("xl/worksheets/sheet30.xml", sheetXml);
await fs.writeFile(outputPath, await workbook.generateAsync({
  type: "nodebuffer",
  compression: "DEFLATE",
  compressionOptions: { level: 6 },
}));
