"""船舶吨税单价规则与吨税申请说明文字。"""

import re
from datetime import date
from decimal import Decimal


PREFERENTIAL_COUNTRIES = frozenset({
    "朝鲜", "韩国", "日本", "香港", "澳门", "越南", "菲律宾", "泰国", "新加坡", "马来西亚",
    "孟加拉国", "印度", "斯里兰卡", "蒙古", "也门", "巴基斯坦", "以色列", "黎巴嫩", "塞浦路斯", "阿曼",
    "伊朗", "印度尼西亚", "芬兰", "挪威", "丹麦", "瑞典", "俄罗斯", "乌克兰", "克罗地亚", "保加利亚",
    "罗马尼亚", "格鲁吉亚", "波兰", "德国", "荷兰", "比利时", "英国大不列颠及北爱尔兰（包括泽西岛、百慕大、根西岛、开曼群岛、马恩岛、直布罗陀附属地）",
    "法国", "希腊", "意大利", "卢森堡", "马耳他", "土耳其", "拉脱维亚", "立陶宛", "葡萄牙", "斯洛文尼亚",
    "斯洛伐克", "爱沙尼亚", "阿尔巴尼亚", "捷克", "匈牙利", "奥地利", "西班牙", "爱尔兰", "阿根廷", "智利",
    "巴西", "秘鲁", "古巴", "墨西哥", "加拿大", "美国", "南非", "突尼斯", "摩洛哥", "阿尔及利亚", "苏丹共和国",
    "埃塞俄比亚", "肯尼亚", "刚果", "加纳", "埃及", "新西兰", "巴哈马", "中国", "利比里亚", "巴拿马", "安提瓜和巴布达",
})

TONNAGE_TIERS = (
    ("不超过2000净吨", lambda value: value <= 2000),
    ("超过2000净吨，但不超过10000净吨", lambda value: 2000 < value <= 10000),
    ("超过10000净吨，但不超过50000净吨", lambda value: 10000 < value <= 50000),
    ("超过50000净吨", lambda value: value > 50000),
)

# 图片表格列顺序为：1年、90日、30日。
RATE_TABLE = {
    "普通税率": ((12.6, 4.2, 2.1), (24.0, 8.0, 4.0), (27.6, 9.2, 4.6), (31.8, 10.6, 5.3)),
    "优惠税率": ((9.0, 3.0, 1.5), (17.4, 5.8, 2.9), (19.8, 6.6, 3.3), (22.8, 7.6, 3.8)),
}
_DURATION_INDEX = {365: 0, 90: 1, 30: 2}


def normalize_country_name(value: str | None) -> str:
    raw = re.sub(r"[\u200b-\u200d\ufeff]", "", str(value or "")).strip()
    raw = re.sub(r"^[（(]\s*[A-Za-z0-9]+\s*[）)]", "", raw).strip()
    if "/" in raw:
        raw = raw.split("/")[-1].strip()
    aliases = {"中国香港": "香港", "中国澳门": "澳门", "中国内地": "中国", "中华人民共和国": "中国"}
    return aliases.get(raw, raw)


def is_preferential_country(nationality: str | None, preferential_countries=None) -> bool:
    country = normalize_country_name(nationality)
    countries = PREFERENTIAL_COUNTRIES if preferential_countries is None else preferential_countries
    normalized_countries = {normalize_country_name(item) for item in countries}
    return country in normalized_countries


def _tier_for(net_tonnage: int | float | str) -> tuple[int, str]:
    try:
        value = Decimal(str(net_tonnage))
    except Exception as exc:
        raise ValueError("船舶净吨位不能为空且必须为数字") from exc
    if value < 0:
        raise ValueError("船舶净吨位不能为负数")
    for index, (label, matches) in enumerate(TONNAGE_TIERS):
        if matches(value):
            return index, label
    raise ValueError("无法判断船舶净吨位阶梯")


def calculate_tonnage_quote(nationality: str | None, net_tonnage: int | float | str | None, duration_days: int | None, preferential_countries=None) -> dict:
    if net_tonnage in (None, ""):
        raise ValueError("船舶净吨位未填写")
    try:
        duration = int(duration_days or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("购买时长必须为30、90或365天") from exc
    if duration not in _DURATION_INDEX:
        raise ValueError("购买时长必须选择30天、90天或一年")
    tier_index, tier_label = _tier_for(net_tonnage)
    preferential = is_preferential_country(nationality, preferential_countries)
    rate_name = "优惠税率" if preferential else "普通税率"
    display_rate_name = "优惠税率" if preferential else "原价税率"
    unit_price = Decimal(str(RATE_TABLE[rate_name][tier_index][_DURATION_INDEX[duration]]))
    total = Decimal(str(net_tonnage)) * unit_price
    return {
        "tax_type": display_rate_name,
        "rate_table_type": rate_name,
        "preferential": preferential,
        "country": normalize_country_name(nationality),
        "tier_index": tier_index,
        "tier": tier_label,
        "duration_days": duration,
        "duration_text": "一年" if duration == 365 else f"{duration}天",
        "unit_price": unit_price,
        "total_amount": total,
    }


def decimal_text(value: Decimal | int | float | str | None) -> str:
    if value is None:
        return ""
    text = format(Decimal(str(value)).quantize(Decimal("0.1")), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def build_tonnage_text(vessel, voyage, purchase_date: date, quote: dict) -> str:
    vessel_name = vessel.chinese_name or "待填写船名"
    english_name = vessel.english_name or "待填写英文船名"
    voyage_no = voyage.inbound_voyage_no or "待填写进港航次"
    start = f"{purchase_date.month}.{purchase_date.day}"
    duration = quote["duration_text"]
    return (
        f"{vessel_name}/{english_name}/{voyage_no},{start}起购买吨税{duration}，"
        f"船籍：{quote.get('country') or vessel.nationality or '待填写'}，净吨*{quote['tax_type']}="
        f"{decimal_text(vessel.net_tonnage)}*{decimal_text(quote['unit_price'])}={decimal_text(quote['total_amount'])}"
    )
