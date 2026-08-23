from __future__ import annotations

import re
from datetime import datetime
from typing import Any


VESSEL_FIELDS = {
    "imo": ["IMO", "IMO编号", "国际海事组织编号"],
    "chinese_name": ["船舶中文船名", "中文船名", "船名（中文）"],
    "english_name": ["船舶英文船名", "英文船名", "船名（英文）"],
    "nationality": ["船旗国/地区", "船旗国", "船舶国籍", "国籍"],
    "call_sign": ["呼号", "船舶呼号"],
    "shipping_company": ["船舶所属公司中文名称", "船公司", "所属公司"],
    "net_tonnage": ["净吨位"],
    "gross_tonnage": ["总吨位"],
    "mmsi": ["通讯号码MMSI", "通讯号码（MMSI）", "通讯号码 (MMSI)", "MMSI", "船舶MMSI"],
}

VESSEL_EXTRA_FIELDS = {
    "ship_system_no": ["船舶编号"],
    "maritime_ship_no": ["海事船舶编号"],
    "customs_code": ["海关代码"],
    "maritime_authority": ["所属海事局"],
    "registry_port": ["船籍港"],
    "ship_category": ["船舶种类"],
    "supervision_type": ["船舶监管类型"],
    "ship_classification": ["船舶分类"],
    "ship_type": ["船舶类型"],
    "registry_certificate_no": ["登记证书"],
    "record_date": ["备案日期"],
    "nationality_certificate_no": ["船舶国籍证书编号"],
    "nationality_certificate_issue_date": ["国籍证书签发日期"],
    "build_date": ["建造日期"],
    "build_yard": ["建造船厂"],
    "initial_registration_no": ["船舶初始登记号"],
    "ship_identification_no": ["船舶识别号"],
    "register_no": ["船舶登记号"],
    "operation_nature": ["运营性质"],
    "sea_river_flag": ["海船内河船标识"],
    "route_nature": ["航线性质"],
    "navigation_area": ["航区"],
    "ship_length": ["船长"],
    "beam": ["船宽"],
    "speed": ["船速", "最大航速"],
    "ship_height": ["船高"],
    "deadweight": ["载重吨"],
    "minimum_safe_manning": ["最低安全配员人数"],
    "summer_draft": ["夏季满载吃水(米)", "夏季满载吃水（米）"],
    "main_engine_power": ["船舶主机功率(kw)", "船舶主机功率（kw）"],
    "hull_material": ["船体材料代码"],
    "propeller_type": ["推进器种类"],
    "main_engine_type": ["主机种类"],
    "teu_total": ["TEU合计"],
    "owner_company_english": ["船舶所属公司英文名称"],
    "owner_company_org_code": ["船舶所属公司组织机构代码"],
    "owner_company_type": ["船舶所属公司性质"],
    "owner_company_customs_code": ["船舶所属公司海关编码"],
    "operator_customs_code": ["船舶运营企业海关编码"],
    "operator": ["船舶经营人"],
}

VOYAGE_FIELDS = {
    "inbound_voyage_no": ["进港航次号", "入港航次号", "进境航次号", "入境航次号"],
    "outbound_voyage_no": ["出港航次号", "出境航次号"],
    "arrival_time": ["抵港时间", "靠泊时间", "预抵时间"],
    "departure_time": ["离港时间", "离泊时间"],
    "berth": ["预抵泊位", "泊位"],
    "previous_port": ["上一港", "发航港"],
    "previous_port_country": ["上一港国家/地区", "上一港国家／地区", "发航港国家/地区", "发航港国家／地区"],
    "previous_port_departure_time": ["上港离港时间", "发航时间"],
    "next_port": ["下一港"],
    "next_port_country": ["下一港国家/地区", "下一港国家／地区"],
    "route": ["航线"],
    "entry_type": ["进口标识", "进出口标识"],
}

VOYAGE_EXTRA_FIELDS = {
    "declaration_port": ["申报港口"],
    "pre_arrival_terminal": ["预抵码头"],
    "loading_terminal": ["装卸码头"],
    "customs_district_code": ["关区代码"],
    "quarantine_department": ["检疫部门"],
    "border_inspection_port": ["边检口岸"],
    "maritime_scene": ["海事现场"],
    "customs_business_type": ["海关业务类型"],
    "terminal_company": ["码头公司"],
    "arrival_anchorage": ["预抵锚地"],
    "arrival_anchorage_time": ["预抵锚地时间"],
    "captain_nationality": ["船长国籍/地区", "船长国籍／地区"],
    "captain_name": ["船长姓名"],
    "contact_phone": ["船舶联系人电话"],
    "first_draft": ["首吃水"],
    "stern_draft": ["尾吃水"],
    "arrival_draft": ["进港吃水"],
    "loading_draft": ["装载吃水"],
    "fresh_water": ["海淡水"],
    "max_speed": ["最大航速"],
    "passenger_count": ["旅客总数"],
    "tonnage_tax_verified": ["是否验核吨税电子信息"],
    "disease_area": ["是否疫区"],
    "incoming_pilot": ["进口引航"],
    "outgoing_pilot": ["出口引航"],
    "incoming_tug": ["进口拖轮"],
    "outgoing_tug": ["出口拖轮"],
}


def _all_labels() -> list[str]:
    groups = [VESSEL_FIELDS, VESSEL_EXTRA_FIELDS, VOYAGE_FIELDS, VOYAGE_EXTRA_FIELDS]
    return sorted({label for group in groups for labels in group.values() for label in labels}, key=len, reverse=True)


ALL_LABELS = _all_labels()
LABEL_TO_FIELD: dict[str, tuple[str, str]] = {}
for field_group, group in (("vessel", VESSEL_FIELDS), ("vessel_extra", VESSEL_EXTRA_FIELDS), ("voyage", VOYAGE_FIELDS), ("voyage_extra", VOYAGE_EXTRA_FIELDS)):
    for field, labels in group.items():
        for label in labels:
            LABEL_TO_FIELD[label] = (field_group, field)


def _clean_value(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\u3000", " ").strip())


def _match_label(line: str) -> tuple[str, str] | None:
    for label in ALL_LABELS:
        if line == label:
            return label, ""
        if line.startswith(label):
            rest = line[len(label):]
            if rest and rest[0] in " \t:：":
                return label, _clean_value(rest.lstrip(" \t:："))
    return None


def _parse_label_values(text: str) -> list[tuple[str, str]]:
    lines = [line.rstrip("\r") for line in str(text or "").splitlines()]
    result: list[tuple[str, str]] = []
    index = 0
    while index < len(lines):
        raw = lines[index].strip()
        if not raw:
            index += 1
            continue
        matched = _match_label(raw)
        if not matched:
            index += 1
            continue
        label, inline_value = matched
        if inline_value:
            result.append((label, inline_value))
            index += 1
            continue
        next_index = index + 1
        while next_index < len(lines) and not lines[next_index].strip():
            next_index += 1
        if next_index < len(lines):
            next_line = lines[next_index].strip()
            next_label = _match_label(next_line)
            if next_label and not next_label[1]:
                result.append((label, ""))
            else:
                result.append((label, _clean_value(next_line)))
                index = next_index
        else:
            result.append((label, ""))
        index += 1
    return result


def _strip_code(value: str) -> str:
    cleaned = _clean_value(value)
    return re.sub(r"^[\(（][^\)）]*[\)）]\s*", "", cleaned).strip()


def _display_text(value: str) -> str:
    """固定格式中的显示文本：去掉代码前缀和中文后的英文名称。"""
    cleaned = _strip_code(value)
    if re.search(r"[\u4e00-\u9fff]", cleaned):
        cleaned = re.sub(r"\s*[A-Za-z][A-Za-z ._/'-]*$", "", cleaned).strip()
    return cleaned


def _number(value: str) -> int | float | str | None:
    cleaned = value.replace(",", "").strip()
    if not cleaned:
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", cleaned)
    if not match:
        return value
    number = float(match.group(0))
    return int(number) if number.is_integer() else number


def _datetime(value: str) -> str | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    compact = re.fullmatch(r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})?", cleaned)
    if compact:
        groups = compact.groups()
        return f"{groups[0]}-{groups[1]}-{groups[2]}T{groups[3]}:{groups[4]}:{groups[5] or '00'}"
    cleaned = cleaned.replace("/", "-").replace("年", "-").replace("月", "-").replace("日", "")
    cleaned = cleaned.replace("T", " ")
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(cleaned.strip(), pattern)
            return parsed.strftime("%Y-%m-%dT%H:%M:%S" if "%S" in pattern else "%Y-%m-%dT%H:%M")
        except ValueError:
            continue
    return value


def _entry_type(value: str) -> str:
    if "入境" in value:
        return "入境"
    if "入港" in value:
        return "入港"
    if "出境" in value:
        return "出境"
    if "出港" in value:
        return "出港"
    return value


def parse_fixed_text(text: str) -> dict[str, Any]:
    entries = _parse_label_values(text)
    vessel: dict[str, Any] = {"extra": {}}
    voyage: dict[str, Any] = {"extra": {}}
    recognized: list[dict[str, str]] = []
    ignored: list[dict[str, str]] = []
    vessel_hits = 0
    voyage_hits = 0

    for label, raw_value in entries:
        mapping = LABEL_TO_FIELD.get(label)
        if not mapping:
            ignored.append({"label": label, "value": raw_value})
            continue
        group, field = mapping
        value: Any = raw_value
        if group == "vessel" and field == "nationality":
            value = _display_text(value)
        elif group == "vessel" and field in {"net_tonnage", "gross_tonnage"}:
            value = _number(value)
        elif group == "voyage" and field in {"arrival_time", "departure_time", "previous_port_departure_time"}:
            value = _datetime(value)
        elif group == "voyage" and field in {"berth", "previous_port", "previous_port_country", "next_port", "next_port_country", "route"}:
            value = _display_text(value)
        elif group == "voyage" and field == "entry_type":
            value = _entry_type(_display_text(value))
        elif group == "voyage_extra" and field == "arrival_anchorage_time":
            value = _datetime(value)
        elif group == "vessel_extra" and field in {"record_date", "nationality_certificate_issue_date", "build_date"}:
            value = _datetime(value)
        elif group == "vessel_extra" and field in {"ship_length", "beam", "speed", "ship_height", "deadweight", "summer_draft", "main_engine_power", "teu_total"}:
            value = _number(value)
        elif group == "vessel_extra" and field not in {"ship_system_no", "maritime_ship_no", "record_date", "nationality_certificate_issue_date", "build_date", "owner_company_english", "owner_company_org_code", "owner_company_customs_code", "operator_customs_code"}:
            value = _display_text(value)
        elif group == "voyage_extra" and field not in {"arrival_anchorage_time", "contact_phone", "first_draft", "stern_draft", "arrival_draft", "loading_draft", "max_speed"}:
            value = _display_text(value)
        if value in (None, ""):
            continue
        if group == "vessel":
            vessel[field] = value
            vessel_hits += 1
        elif group == "vessel_extra":
            vessel["extra"][field] = value
            vessel_hits += 1
        elif group == "voyage":
            voyage[field] = value
            voyage_hits += 1
        else:
            voyage["extra"][field] = value
            voyage_hits += 1
        recognized.append({"label": label, "field": f"{group}.{field}", "value": str(value)})

    if "previous_port" not in voyage:
        for item in recognized:
            if item["label"] == "发航港":
                voyage["previous_port"] = item["value"]
                break
    if "previous_port_country" not in voyage:
        for item in recognized:
            if item["label"] == "发航港国家/地区":
                voyage["previous_port_country"] = _strip_code(item["value"])
                break

    if "mmsi" in vessel:
        vessel["mmsi"] = str(vessel["mmsi"])
    ship_system_no = str(vessel.get("extra", {}).get("ship_system_no") or "").strip().upper()
    imo_from_ship_no = re.fullmatch(r"UN(\d{7})", ship_system_no)
    if "imo" not in vessel and imo_from_ship_no:
        vessel["imo"] = imo_from_ship_no.group(1)
        recognized.append({"label": "船舶编号（去掉UN）", "field": "vessel.imo", "value": vessel["imo"]})
    kind = "voyage" if voyage_hits >= 2 else "vessel"
    return {
        "kind": kind,
        "kind_label": "航次信息（含船舶字段）" if kind == "voyage" else "船舶信息",
        "vessel": vessel,
        "voyage": voyage,
        "recognized": recognized,
        "ignored": ignored,
        "recognized_count": len(recognized),
        "ignored_count": len(ignored),
    }
