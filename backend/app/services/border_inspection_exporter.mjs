import fs from "node:fs/promises";
import JSZip from "jszip";

const [, , templatePath, outputPath, payloadPath] = process.argv;
const payload = JSON.parse(await fs.readFile(payloadPath, "utf8"));

function text(value) {
  return value === null || value === undefined ? "" : String(value);
}

function berthPhase(value) {
  const match = text(value).trim().match(/([一二三四五六七八九十百]+期|\d+期)/);
  return match ? match[1] : text(value);
}

function escapeXml(value) {
  return text(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function replaceCell(xml, address, value) {
  const selfClosingPattern = new RegExp(
    "<c\\b(?=[^>]*\\br=\"" + address + "\"[^>]*)[^>]*/>",
  );
  const fullCellPattern = new RegExp(
    "<c\\b(?=[^>]*\\br=\"" + address + "\"[^>]*)[^>]*>[\\s\\S]*?</c>",
  );
  const cellPattern = selfClosingPattern.test(xml) ? selfClosingPattern : fullCellPattern;
  const match = xml.match(cellPattern);
  if (!match) {
    throw new Error("边检手续表模板未找到单元格 " + address);
  }

  const original = match[0];
  let openingTag = original.match(/^<c\b[^>]*>/)[0];
  openingTag = openingTag.replace(/\s+t="[^"]*"/, "");
  openingTag = openingTag.replace(/\/>$/, ">");
  openingTag = openingTag.replace(/>$/, ' t="inlineStr">');
  const valueText = text(value);
  const preserve = /^\s|\s$/.test(valueText) ? ' xml:space="preserve"' : "";
  const body = "<is><t" + preserve + ">" + escapeXml(valueText) + "</t></is>";
  return xml.replace(cellPattern, openingTag + body + "</c>");
}

const workbook = await JSZip.loadAsync(await fs.readFile(templatePath));
let sheetXml = await workbook.file("xl/worksheets/sheet1.xml").async("string");

const vessel = payload.vessel || {};
const voyage = payload.voyage || {};
const crew = payload.crew || {};
const changes = payload.changes || {};

const values = {
  A2: "大船船名：中文：" + text(vessel.chinese_name) + "                英文：" + text(vessel.english_name),
  A3: "船舶公司名称（请填写完整准确名称）：" + text(vessel.shipping_company),
  B5: "",
  D5: "",
  B6: text(voyage.previous_port),
  D6: text(voyage.next_port),
  B7: "中国籍海员证： " + text(crew.chinese_seaman) + " 本；  外国籍护照： " + text(crew.foreign_passport) +
    " 本；  外国籍海员证： " + text(crew.foreign_seaman) + " 本；港澳台证件： " + text(crew.hk_macao_taiwan) + " 本（张）",
  B8: crew.female_count ? "有" : "无",
  D8: text(crew.female_count || 0),
  B9: changes.has_current ? "有" : "无",
  D9: text(changes.current_summary),
  B10: changes.has_domestic ? "有" : "无",
  D10: text(changes.domestic_summary),
  B12: text(voyage.port_sequence),
  B13: berthPhase(voyage.berth),
  B15: text(changes.other_summary),
};

for (const [address, value] of Object.entries(values)) {
  sheetXml = replaceCell(sheetXml, address, value);
}

workbook.file("xl/worksheets/sheet1.xml", sheetXml);
await fs.writeFile(outputPath, await workbook.generateAsync({
  type: "nodebuffer",
  compression: "DEFLATE",
  compressionOptions: { level: 6 },
}));
