import fs from "node:fs/promises";
import JSZip from "jszip";

const [, , templatePath, outputPath, payloadPath, formType] = process.argv;
const payload = JSON.parse(await fs.readFile(payloadPath, "utf8"));

const text = value => value === null || value === undefined ? "" : String(value);

function escapeXml(value) {
  return text(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function excelSerial(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const localAsUtc = Date.UTC(
    date.getFullYear(),
    date.getMonth(),
    date.getDate(),
    date.getHours(),
    date.getMinutes(),
    date.getSeconds(),
    date.getMilliseconds(),
  );
  return (localAsUtc - Date.UTC(1899, 11, 30)) / 86400000;
}

function replaceCell(xml, address, value, valueType = "string") {
  const cellPattern = new RegExp(
    "<c\\b[^>]*\\br=\"" + address + "\"[^>]*(?:/>|>[\\s\\S]*?</c>)",
  );
  const match = xml.match(cellPattern);
  if (!match) throw new Error(`Template cell ${address} was not found`);

  const original = match[0];
  const openingTag = original.match(/^<c\b[^>]*>/)[0];
  let newOpeningTag = openingTag.replace(/\s+t="[^"]*"/, "");
  let body;
  if (valueType === "number") {
    body = `<v>${value}</v>`;
  } else {
    const stringValue = text(value);
    const preserve = /^\s|\s$/.test(stringValue) ? " xml:space=\"preserve\"" : "";
    newOpeningTag = newOpeningTag.replace(/>$/, ` t=\"inlineStr\">`);
    body = `<is><t${preserve}>${escapeXml(stringValue)}</t></is>`;
  }
  return xml.replace(cellPattern, `${newOpeningTag}${body}</c>`);
}

function clearCell(xml, address) {
  const cellPattern = new RegExp(
    "<c\\b[^>]*\\br=\"" + address + "\"[^>]*(?:/>|>[\\s\\S]*?</c>)",
  );
  const match = xml.match(cellPattern);
  if (!match) throw new Error("Template cell " + address + " was not found");
  const openingTag = match[0].match(/^<c\b[^>]*>/)[0];
  return xml.replace(cellPattern, openingTag.replace(/>$/, "/>"));
}

function writeString(xml, address, value) {
  return replaceCell(xml, address, value, "string");
}

function writeNumber(xml, address, value) {
  return replaceCell(xml, address, value, "number");
}

function removeColumns(xml, columns, newDimension) {
  const columnPattern = columns.join("|");
  const cellPattern = new RegExp(
    "<c\\b[^>]*?\\br=\"(?:" + columnPattern + ")\\d+\"[^>]*?(?:/>|>[\\s\\S]*?</c>)",
    "g",
  );
  xml = xml.replace(cellPattern, "");
  xml = xml.replace(/<dimension\b[^>]*ref=\"[^\"]*\"\s*\/>/, `<dimension ref=\"${newDimension}\"/>`);
  xml = xml.replace(/<cols>([\\s\\S]*?)<\/cols>/, (full, body) => {
    const trailingColumns = /<col\b[^>]*\bmin=\"25\"[^>]*\bmax=\"16384\"[^>]*\/>/;
    return `<cols>${body.replace(trailingColumns, "")}</cols>`;
  });
  return xml;
}

const nationalityEnglish = {
  "利比里亚": "LIBERIA",
  "中国": "CHINA",
  "瓦努阿图": "VANUATU",
  "马来西亚": "MALAYSIA",
  "泰国": "THAILAND",
  "菲律宾": "PHILIPPINES",
  "乌克兰": "UKRAINE",
  "印度": "INDIA",
  "印度尼西亚": "INDONESIA",
  "巴拿马": "PANAMA",
  "中国香港": "HONG KONG, CHINA",
};

const portEnglish = {
  "南沙": "NANSHA",
  "南沙新港": "NANSHA NEW PORT",
  "泰国林查班": "LAEM CHABANG",
  "林查班": "LAEM CHABANG",
  "蛇口": "SHEKOU",
  "博多": "FUKUOKA",
  "巴生港": "PORT KLANG",
  "胡志明市": "HO CHI MINH CITY",
  "丹戎帕拉帕斯": "TANJUNG PELEPAS",
  "天津": "TIANJIN",
  "香港": "HONG KONG",
  "新加坡": "SINGAPORE",
};

const countryEnglish = {
  "中国": "CHINA",
  "日本": "JAPAN",
  "马来西亚": "MALAYSIA",
  "泰国": "THAILAND",
  "越南": "VIETNAM",
  "新加坡": "SINGAPORE",
  "中国香港": "HONG KONG, CHINA",
};

const englishPort = value => portEnglish[text(value).trim()] || text(value).trim();
const englishCountry = value => countryEnglish[text(value).trim()] || text(value).trim();

const vessel = payload.vessel || {};
const voyage = payload.voyage || {};
const crew = payload.crew || {};
const extra = voyage.extra || {};
const nationality = text(vessel.nationality);
const nationalityEn = nationalityEnglish[nationality] || nationality;
const previousPort = text(voyage.previous_port);
const nextPort = text(voyage.next_port);
const localPort = text(extra.declaration_port || "南沙新港");
const arrivalSerial = excelSerial(voyage.arrival_time);

const zip = await JSZip.loadAsync(await fs.readFile(templatePath));
let workbookXml = await zip.file("xl/workbook.xml").async("string");
let generalXml = await zip.file("xl/worksheets/sheet5.xml").async("string");
let cargoXml = await zip.file("xl/worksheets/sheet6.xml").async("string");

for (const sideCell of [
  "Z10", "Z11", "Z12", "Z13", "Z14", "Z15",
]) {
  generalXml = clearCell(generalXml, sideCell);
}

if (formType === "general") {
  generalXml = writeString(generalXml, "I6", vessel.chinese_name);
  generalXml = writeString(generalXml, "I7", vessel.english_name);
  generalXml = writeString(generalXml, "F8", nationality);
  generalXml = writeString(generalXml, "F9", nationalityEn);
  generalXml = clearCell(generalXml, "I9");
  generalXml = writeString(generalXml, "I10", vessel.imo ? `IMO:${vessel.imo}` : "");
  generalXml = writeString(generalXml, "B11", vessel.nationality_certificate_no);
  if (vessel.gross_tonnage !== null && vessel.gross_tonnage !== undefined && vessel.gross_tonnage !== "") {
    generalXml = writeNumber(generalXml, "E12", vessel.gross_tonnage);
  }
  if (vessel.net_tonnage !== null && vessel.net_tonnage !== undefined && vessel.net_tonnage !== "") {
    generalXml = writeNumber(generalXml, "L12", vessel.net_tonnage);
  }
  generalXml = writeString(generalXml, "M8", crew.captain);
  generalXml = writeString(generalXml, "S6", localPort);
  generalXml = writeString(generalXml, "S7", englishPort(localPort));
  if (arrivalSerial !== null) generalXml = writeNumber(generalXml, "X6", arrivalSerial);
  generalXml = writeString(generalXml, "W8", previousPort);
  generalXml = writeString(generalXml, "W9", englishPort(previousPort));
  generalXml = writeString(generalXml, "G18", previousPort);
  generalXml = writeString(generalXml, "G19", englishPort(previousPort));
  generalXml = writeString(generalXml, "B19", englishPort(previousPort));
  generalXml = writeString(generalXml, "M18", localPort);
  generalXml = writeString(generalXml, "M19", englishPort(localPort));
  generalXml = writeString(generalXml, "T18", nextPort);
  generalXml = writeString(generalXml, "T19", englishPort(nextPort));
  generalXml = writeNumber(generalXml, "H24", crew.total_count ?? 0);
  generalXml = writeNumber(generalXml, "N24", crew.passenger_count ?? 0);
  generalXml = writeString(generalXml, "W25", crew.nationality_distribution);
  generalXml = writeString(generalXml, "R26", vessel.call_sign);
  generalXml = writeString(generalXml, "R27", vessel.mmsi);
  generalXml = writeString(generalXml, "R28", vessel.shipping_company);
  generalXml = writeString(
    generalXml,
    "R29",
    [voyage.inbound_voyage_no, voyage.outbound_voyage_no].filter(Boolean).join("/"),
  );
  generalXml = removeColumns(generalXml, ["Y", "Z", "AA", "AB"], "A1:X37");
}

if (formType === "cargo") {
  cargoXml = writeString(cargoXml, "C6", vessel.chinese_name);
  cargoXml = writeString(cargoXml, "C7", vessel.english_name);
  cargoXml = writeString(cargoXml, "C8", nationality);
  cargoXml = writeString(cargoXml, "C9", nationalityEn);
  cargoXml = writeString(cargoXml, "E8", crew.captain);
}

zip.file("xl/worksheets/sheet5.xml", generalXml);
zip.file("xl/worksheets/sheet6.xml", cargoXml);

const activeName = formType === "cargo" ? "Cgo Inb" : "Gen Inb";
workbookXml = workbookXml.replace(
  /<(x:)?workbookView([^>]*)activeTab="\d+"([^>]*)\/>/,
  '<$1workbookView$2activeTab="0"$3/>',
);
if (!/<(x:)?workbookView\b/.test(workbookXml)) {
  workbookXml = workbookXml.replace(
    /<(x:)?workbook([^>]*)>/,
    '$&<$1bookViews><$1workbookView activeTab="0"/></$1bookViews>',
  );
}
const targetSheetMatch = workbookXml.match(
  new RegExp(`<x:sheet\\b[^>]*name="${activeName}"[^>]*\\s*/>`),
);
if (targetSheetMatch) {
  const withoutTarget = workbookXml.replace(targetSheetMatch[0], "");
  workbookXml = withoutTarget.replace("<x:sheets>", `<x:sheets>${targetSheetMatch[0]}`);
}
workbookXml = workbookXml.replace(
  /<(x:)?workbookView([^>]*)activeTab="\d+"([^>]*)\/>/,
  '<$1workbookView$2activeTab="0"$3/>',
);
const targetSheetMatch2 = workbookXml.match(
  new RegExp("<(?:x:)?sheet\\b[^>]*name=\"" + activeName + "\"[^>]*\\s*/>"),
);
if (targetSheetMatch2) {
  const withoutTarget2 = workbookXml.replace(targetSheetMatch2[0], "");
  workbookXml = withoutTarget2.replace(
    /<(?:x:)?sheets>/,
    match => match + targetSheetMatch2[0],
  );
}
zip.file("xl/workbook.xml", workbookXml);

await fs.writeFile(outputPath, await zip.generateAsync({
  type: "nodebuffer",
  compression: "DEFLATE",
  compressionOptions: { level: 6 },
}));
