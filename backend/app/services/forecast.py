import json
import re
from collections import Counter
from datetime import datetime


PORTS = {
    "HAIPHONG": "越南海防",
    "海防": "越南海防",
    "HONG KONG": "香港",
    "HONGKONG": "香港",
    "ZHOUSHAN": "舟山",
    "上海": "上海",
    "外港": "外港",
}


def fmt_time(value: datetime | None) -> str:
    return value.strftime("%m.%d/%H%M") if value else "[待人工填写]"


def normalize_port(value: str | None, country: str | None) -> str:
    if not value:
        return "[待人工填写]"
    raw = value.strip()
    upper = raw.upper()
    for key, label in PORTS.items():
        if key.upper() in upper:
            return label
    if country and country.upper() in {"VN", "越南"} and "HAI" in upper:
        return "越南海防"
    return raw


def berth_text(value: str | None) -> str:
    if not value:
        return "[待人工填写]"
    raw = value.strip()
    match = re.search(r"([一二三123])期.*?([0-9一二三四五六七八九十]+)号", raw)
    if match:
        phase = {"1": "一期", "2": "二期", "3": "三期", "一": "一期", "二": "二期", "三": "三期"}[match.group(1)]
        return f"南沙{phase}{match.group(2)}号泊位"
    return raw


def generate_forecast(vessel, voyage, crew, crew_change: bool) -> tuple[str, list[str]]:
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
    crew_text = "无船员更动" if not crew_change else "有船员更动"
    subject = f"{nationality}籍“{chinese_name}/{vessel.english_name or '[待人工填写]'}”"
    first = (
        f"广州港中联大船{entry}信息预报：{subject}，计划于{fmt_time(voyage.arrival_time)}靠泊{berth}"
        f"【该轮于{fmt_time(voyage.previous_port_departure_time)}从{previous}驶来，"
        f"计划于{fmt_time(voyage.departure_time)}离泊，航线：{previous}-南沙-{next_port}，"
        f"IMO：{imo}，船员共{count}名，{nat_text}，{female_text}，无枪弹，{crew_text}"
        f"（该轮途经{previous}-南沙-{next_port}，船员身体状况正常）】广州港中联"
    )
    phase = "一期" if "一期" in (voyage.berth or "") else "二期" if "二期" in (voyage.berth or "") else "三期" if "三期" in (voyage.berth or "") else "待确认码头"
    second = f"{phase}，{entry}，{chinese_name}，{fmt_time(voyage.arrival_time)}--{fmt_time(voyage.departure_time)}，{nationality}籍，{previous}-南沙-{next_port}，{imo}。"
    return f"{first}\n\n{second}", sorted(set(missing))
