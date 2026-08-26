import json
import re
from collections import Counter
from datetime import datetime


PORTS = {
    "HAIPHONG": "海防",
    "海防": "海防",
    "LAEM CHABANG": "林查班",
    "林查班": "林查班",
    "PORT KLANG": "巴生港",
    "巴生港": "巴生港",
    "FUKUOKA": "博多",
    "博多": "博多",
    "HONG KONG": "香港",
    "HONGKONG": "香港",
    "蛇口": "蛇口",
    "SHEKOU": "蛇口",
    "ZHOUSHAN": "舟山",
    "上海": "上海",
    "外港": "外港",
}

PORT_COUNTRIES = {
    "海防": "越南",
    "林查班": "泰国",
    "巴生港": "马来西亚",
    "博多": "日本",
    "香港": "中国香港",
}

COUNTRIES = {
    "CN": "中国",
    "中国": "中国",
    "TH": "泰国",
    "泰国": "泰国",
    "THAILAND": "泰国",
    "VN": "越南",
    "越南": "越南",
    "VIETNAM": "越南",
    "MY": "马来西亚",
    "马来西亚": "马来西亚",
    "MALAYSIA": "马来西亚",
    "JP": "日本",
    "日本": "日本",
    "JAPAN": "日本",
    "SG": "新加坡",
    "新加坡": "新加坡",
    "SINGAPORE": "新加坡",
    "HK": "中国香港",
    "中国香港": "中国香港",
    "香港": "中国香港",
}


def fmt_time(value: datetime | None) -> str:
    return value.strftime("%m.%d/%H%M") if value else "[待人工填写]"


def normalize_port(value: str | None, country: str | None) -> str:
    if not value:
        return "[待人工填写]"
    raw = value.strip()
    upper = raw.upper()
    country_raw = (country or "").strip()
    bracketed_code = re.search(r"[（(]\s*([A-Za-z]{2,3})\s*[）)]", country_raw)
    country_code = re.fullmatch(r"[A-Za-z]{2,3}", country_raw)
    country_key = (bracketed_code.group(1) if bracketed_code else country_code.group(0) if country_code else country_raw)
    country_label = COUNTRIES.get(country_key.upper(), COUNTRIES.get(country_key, country_raw))
    for key, label in PORTS.items():
        if key.upper() in upper:
            port_label = label
            break
    else:
        if country_label == "越南" and "HAI" in upper:
            port_label = "海防"
        else:
            port_label = raw
    if not country_label:
        country_label = PORT_COUNTRIES.get(port_label, "")
    foreign = country_label and country_label not in {"中国", "中国香港"}
    if foreign and not port_label.startswith(country_label):
        return f"{country_label}{port_label}"
    return port_label


def format_crew_change(changes) -> str:
    """Return the compact wording shared by forecast and border forms."""
    if isinstance(changes, bool):
        return "有船员更动" if changes else "无船员更动"
    people = list(changes or [])
    if not people:
        return "无船员更动"
    counts = {"up": 0, "down": 0}
    order = []
    for person in people:
        direction = getattr(person, "direction", "")
        if direction in counts:
            counts[direction] += 1
            if direction not in order:
                order.append(direction)
    detail = []
    for direction in order:
        detail.append(f"{counts[direction]}{'上' if direction == 'up' else '下'}")
    return f"本港{''.join(detail)}" if detail else "本港有船员更动"


def berth_text(value: str | None) -> str:
    if not value:
        return "[待人工填写]"
    raw = value.strip()
    match = re.search(r"([一二三123])期.*?([0-9一二三四五六七八九十]+)号", raw)
    if match:
        phase = {"1": "一期", "2": "二期", "3": "三期", "一": "一期", "二": "二期", "三": "三期"}[match.group(1)]
        return f"南沙{phase}{match.group(2)}号泊位"
    return raw


def generate_forecast(vessel, voyage, crew, crew_changes) -> tuple[str, list[str]]:
    missing = []
    nationality = vessel.nationality or "[待人工填写]"
    if not vessel.nationality:
        missing.append("船舶国籍")
    imo = vessel.imo or "[待人工填写]"
    if not vessel.imo or not re.fullmatch(r"\d{7}", vessel.imo):
        missing.append("7位IMO")
    chinese_name = vessel.chinese_name or "[待人工填写]"
    if not vessel.chinese_name:
        missing.append("船舶中文名")
    previous = normalize_port(voyage.previous_port, voyage.previous_port_country)
    next_port = normalize_port(voyage.next_port, voyage.next_port_country)
    if voyage.previous_port and previous == voyage.previous_port:
        missing.append("上一港正式中文名")
    if not voyage.previous_port:
        missing.append("上一港")
    berth = berth_text(voyage.berth)
    entry = voyage.entry_type
    if not entry:
        entry = "入港" if (voyage.previous_port_country or "").upper() in {"CN", "中国"} else "入境"
    count = len(crew)
    nat = Counter((m.nationality or "待人工填写") for m in crew)
    gender = Counter((m.gender or "待人工填写") for m in crew)
    if not crew:
        missing.append("船员名单")
    if nat and set(nat) == {"中国"}:
        nat_text = "全中国人"
    else:
        nat_text = "、".join(f"{key}人{value}名" for key, value in nat.items()) or "待人工填写"
    female_count = gender.get("女", 0)
    female_text = "无女性" if female_count == 0 else f"其中女性{female_count}名"
    crew_text = format_crew_change(crew_changes)
    inspection_text = "系统中控" if getattr(voyage, "customs_inspection", False) else "系统允许放行"
    forecast_notes = [inspection_text]
    if crew_changes:
        change_text = format_crew_change(crew_changes)
        if change_text != "无船员更动":
            forecast_notes.append(change_text)
    opening_note = f"（{'，'.join(forecast_notes)}）"
    subject = f"{nationality}籍“{chinese_name}/{vessel.english_name or '[待人工填写]'}”"
    first = (
        f"{opening_note}广州港中联大船{entry}信息预报：{subject}，计划于{fmt_time(voyage.arrival_time)}靠泊{berth}"
        f"【该轮于{fmt_time(voyage.previous_port_departure_time)}从{previous}驶来，"
        f"计划于{fmt_time(voyage.departure_time)}离泊，航线：{previous}-南沙-{next_port}，"
        f"IMO：{imo}，船员共{count}名，{nat_text}，{female_text}，无枪弹，{crew_text}"
        f"（该轮途经{previous}-南沙-{next_port}，船员身体状况正常）】广州港中联"
    )
    phase = "一期" if "一期" in (voyage.berth or "") else "二期" if "二期" in (voyage.berth or "") else "三期" if "三期" in (voyage.berth or "") else "待确认码头"
    short_change = crew_text.removeprefix("本港") if crew_changes else ""
    change_suffix = f"，{short_change}" if short_change else ""
    second = f"{phase}，{entry}，{chinese_name}，{fmt_time(voyage.arrival_time)}--{fmt_time(voyage.departure_time)}，{nationality}籍，{previous}-南沙-{next_port}{change_suffix}，{imo}。"
    return f"{first}\n\n{second}", sorted(set(missing))
