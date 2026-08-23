import re
from datetime import datetime


def _clean(value):
    return re.sub(r"^[\s|]+|[\s|]+$", "", str(value or "")).replace("（", "(").replace("）", ")")


def _datetime(value):
    if not value:
        return None
    match = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\s*(\d{1,2}):?(\d{2})", value)
    if not match:
        return None
    return datetime(*[int(x) for x in match.groups()])


def recognize_screenshot(path: str):
    from rapidocr_onnxruntime import RapidOCR

    result, _ = RapidOCR()(path)
    if not result:
        return {"fields": {}, "rows": [], "missing_fields": ["截图文字"]}
    items = []
    for box, text, confidence in result:
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        items.append((min(ys), min(xs), _clean(text), float(confidence)))
    items.sort()
    rows = []
    for y, x, text, confidence in items:
        if rows and abs(rows[-1][0] - y) <= 12:
            rows[-1][1].append((x, text, confidence))
        else:
            rows.append((y, [(x, text, confidence)]))
    token_rows = []
    for _, cells in rows:
        cells.sort()
        token_rows.append([text for _, text, _ in cells])

    def value_for(label, exclude=()):
        for tokens in token_rows:
            for index, token in enumerate(tokens):
                if label in token and not any(item in token for item in exclude) and index + 1 < len(tokens):
                    return tokens[index + 1]
        return None

    fields = {
        "ship_system_no": value_for("船舶编号"),
        "inbound_voyage_no": value_for("进港航次号"),
        "outbound_voyage_no": value_for("出港航次号"),
        "english_name": value_for("船舶英文"),
        "chinese_name": value_for("船舶中文"),
        "mmsi": value_for("通讯号码"),
        "berth": value_for("预抵泊位"),
        "arrival_time": _datetime(value_for("抵港时间")),
        "departure_time": _datetime(value_for("离港时间")),
        "entry_type": value_for("进口标识"),
        "previous_port_country": value_for("上一港国家/地区"),
        "previous_port": value_for("上一港", exclude=("国家/地区",)),
        "previous_port_departure_time": _datetime(value_for("发航时间")),
        "next_port_country": value_for("下一港国家/地区"),
        "next_port": value_for("下一港", exclude=("国家/地区",)),
        "route": value_for("航线"),
    }
    fields = {key: value for key, value in fields.items() if value not in (None, "")}
    missing = [key for key in ("english_name", "chinese_name", "arrival_time", "departure_time", "berth", "previous_port", "next_port") if key not in fields]
    return {"fields": fields, "rows": token_rows, "missing_fields": missing}
