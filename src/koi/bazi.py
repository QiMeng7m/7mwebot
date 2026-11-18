"""八字 / 八卦 / 天圆地方 / 四象 — 娱乐向命理计算。

计算刻意保持简化：不查真太阳时、不处理节气交接，只根据公历生日
生成一套稳定、好玩的四柱与卦象。所有文案都标注「娱乐向」。
"""

from __future__ import annotations

import hashlib
import re
from datetime import date

STEMS = "甲乙丙丁戊己庚辛壬癸"
BRANCHES = "子丑寅卯辰巳午未申酉戌亥"
ZODIAC = "鼠牛虎兔龙蛇马羊猴鸡狗猪"

STEM_ELEMENT = {
    "甲": "木", "乙": "木", "丙": "火", "丁": "火",
    "戊": "土", "己": "土", "庚": "金", "辛": "金",
    "壬": "水", "癸": "水",
}
BRANCH_ELEMENT = {
    "子": "水", "丑": "土", "寅": "木", "卯": "木",
    "辰": "土", "巳": "火", "午": "火", "未": "土",
    "申": "金", "酉": "金", "戌": "土", "亥": "水",
}
ELEMENTS = ("木", "火", "土", "金", "水")

ELEMENT_TO_QUADRANT = {
    "木": "青龙", "金": "白虎", "火": "朱雀", "水": "玄武", "土": "玄武",
}

SHICHEN_TO_HOUR = {
    "子": 0, "丑": 2, "寅": 4, "卯": 6, "辰": 8, "巳": 10,
    "午": 12, "未": 14, "申": 16, "酉": 18, "戌": 20, "亥": 22,
}

TRIGRAMS = ("乾", "兑", "离", "震", "巽", "坎", "艮", "坤")
TRIGRAM_SYMBOLS = ("☰", "☱", "☲", "☳", "☴", "☵", "☶", "☷")
TRIGRAM_ELEMENT = {
    "乾": "金", "兑": "金", "离": "火", "震": "木",
    "巽": "木", "坎": "水", "艮": "土", "坤": "土",
}

# 上卦为行，下卦为列。
HEXAGRAMS: list[list[str]] = [
    ["乾为天", "天泽履", "天火同人", "天雷无妄",
     "天风姤", "天水讼", "天山遁", "天地否"],
    ["泽天夬", "兑为泽", "泽火革", "泽雷随",
     "泽风大过", "泽水困", "泽山咸", "泽地萃"],
    ["火天大有", "火泽睽", "离为火", "火雷噬嗑",
     "火风鼎", "火水未济", "火山旅", "火地晋"],
    ["雷天大壮", "雷泽归妹", "雷火丰", "震为雷",
     "雷风恒", "雷水解", "雷山小过", "雷地豫"],
    ["风天小畜", "风泽中孚", "风火家人", "风雷益",
     "巽为风", "风水涣", "风山渐", "风地观"],
    ["水天需", "水泽节", "水火既济", "水雷屯",
     "水风井", "坎为水", "水山蹇", "水地比"],
    ["山天大畜", "山泽损", "山火贲", "山雷颐",
     "山风蛊", "山水蒙", "艮为山", "山地剥"],
    ["地天泰", "地泽临", "地火明夷", "地雷复",
     "地风升", "地水师", "地山谦", "坤为地"],
]

# 洛书九宫：4 9 2 / 3 5 7 / 8 1 6
PALACE_GRID = ((4, 9, 2), (3, 5, 7), (8, 1, 6))
PALACE_TRIGRAM = {
    1: "坎", 2: "坤", 3: "震", 4: "巽", 5: "中",
    6: "乾", 7: "兑", 8: "艮", 9: "离",
}

TRIGRAM_ADVICE = {
    "乾": "宜主动、宜定方向，今天适合把话说清楚。",
    "兑": "宜沟通、宜社交，别把开心憋在心里。",
    "离": "宜表达、宜展示，灵感比计划更值钱。",
    "震": "宜启动、宜行动，先迈出第一步。",
    "巽": "宜顺势、宜变通，别跟风向硬碰。",
    "坎": "宜静观、宜蓄力，今天少做高风险决定。",
    "艮": "宜收尾、宜停顿，把边界守好。",
    "坤": "宜合作、宜承接，稳住基本盘最重要。",
}

ELEMENT_ADVICE = {
    "木": "木旺：适合生长、学习与长期布局。",
    "火": "火旺：适合表达、社交与推进曝光。",
    "土": "土旺：适合稳定、收尾与处理琐事。",
    "金": "金旺：适合决断、整理与划清界限。",
    "水": "水旺：适合思考、蓄力与灵活变通。",
}

# ── 五维评分 ────────────────────────────────────────────────────

SCORE_KEYS: tuple[tuple[str, str], ...] = (
    ("love", "爱情"), ("career", "事业"), ("study", "学习"),
    ("wealth", "财富"), ("social", "人际"),
)

ELEMENT_TO_SCORE = {
    "水": "love", "金": "career", "木": "study",
    "土": "wealth", "火": "social",
}

ZODIAC_SCORE = {
    "鼠": {"study": 4, "wealth": 3},
    "牛": {"career": 5, "study": 2},
    "虎": {"career": 4, "social": 4},
    "兔": {"love": 5, "social": 3},
    "龙": {"career": 5, "wealth": 3},
    "蛇": {"study": 4, "love": 2},
    "马": {"social": 5, "career": 2},
    "羊": {"love": 4, "study": 3},
    "猴": {"social": 4, "wealth": 3},
    "鸡": {"career": 4, "study": 3},
    "狗": {"love": 3, "social": 4},
    "猪": {"wealth": 4, "love": 3},
}

QUADRANT_SCORE = {
    "青龙": "study", "白虎": "career",
    "朱雀": "social", "玄武": "love",
}

SCORE_COMMENTS = {
    "love": {
        "high": "桃花有动静，适合主动表达。",
        "mid": "关系平稳，多聊两句会更顺。",
        "low": "别急着推进，先把自己过好。",
    },
    "career": {
        "high": "事业在线，适合争取机会。",
        "mid": "稳中有进，按计划推进就好。",
        "low": "先守住基本盘，别做高风险决定。",
    },
    "study": {
        "high": "学习状态好，适合啃硬骨头。",
        "mid": "按节奏来，每天一点就够。",
        "low": "注意力容易散，先做最简单的一件事。",
    },
    "wealth": {
        "high": "财运有亮点，适合整理资产。",
        "mid": "收支平稳，别冲动消费。",
        "low": "钱包偏紧，今天少做非必要支出。",
    },
    "social": {
        "high": "人缘在线，适合组局或求助。",
        "mid": "社交平稳，保持自己的节奏。",
        "low": "少凑热闹，安静做事更划算。",
    },
}


def _score_tier(score: int) -> str:
    if score >= 78:
        return "high"
    if score >= 52:
        return "mid"
    return "low"


def score_comment(key: str, score: int) -> str:
    return SCORE_COMMENTS.get(key, {}).get(_score_tier(score), "")


def score_bar(score: int) -> str:
    filled = max(1, min(10, int(round(int(score) / 10))))
    return "█" * filled + "░" * (10 - filled)


def scores(chart: dict, seed: str = "") -> dict[str, int]:
    """Stable five-dimension scores from the birth chart."""
    counts = chart["elements"]
    result = {key: 50 for key, _label in SCORE_KEYS}

    for element, count in counts.items():
        key = ELEMENT_TO_SCORE[element]
        result[key] += int(count) * 7

    result[ELEMENT_TO_SCORE[chart["day_master_element"]]] += 10
    for key, bonus in ZODIAC_SCORE.get(chart["zodiac"], {}).items():
        result[key] += bonus
    quadrant_key = QUADRANT_SCORE.get(chart["quadrant"])
    if quadrant_key:
        result[quadrant_key] += 4

    for key, _label in SCORE_KEYS:
        result[key] += _stable(f"{seed}:{key}") % 21 - 10
    return {
        key: max(20, min(96, int(value)))
        for key, value in result.items()
    }


def daily_scores(chart: dict, day: str, seed: str = "") -> dict[str, int]:
    base = scores(chart, seed)
    result = {}
    for key, _label in SCORE_KEYS:
        drift = _stable(f"{seed}:{day}:{key}") % 19 - 9
        result[key] = max(15, min(99, base[key] + drift))
    return result


def score_label(key: str) -> str:
    return dict(SCORE_KEYS).get(key, key)



def _stable(seed: str) -> int:
    return int(hashlib.sha256(str(seed).encode("utf-8")).hexdigest()[:12], 16)


def _gz(index: int) -> tuple[str, str]:
    index = int(index) % 60
    return STEMS[index % 10], BRANCHES[index % 12]


def parse_birth(text: str) -> dict | None:
    """Parse 1995-08-15 / 1995年8月15日 / 1995-08-15 08:30 / 辰时."""
    raw = str(text or "").strip()
    if not raw:
        return None

    # 纯 8 位数字：19950815
    compact = re.search(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)", raw)
    if compact:
        year, month, day = (int(compact.group(i)) for i in (1, 2, 3))
    else:
        match = re.search(
            r"(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})",
            raw,
        )
        if not match:
            return None
        year, month, day = (int(match.group(i)) for i in (1, 2, 3))

    try:
        date(year, month, day)
    except ValueError:
        return None

    hour = None
    minute = 0
    shichen = re.search(r"([子丑寅卯辰巳午未申酉戌亥])时", raw)
    if shichen:
        hour = SHICHEN_TO_HOUR[shichen.group(1)]
    else:
        clock = re.search(r"(\d{1,2})\s*[:：]\s*(\d{1,2})", raw)
        if clock:
            hour = int(clock.group(1))
            minute = int(clock.group(2))
        else:
            point = re.search(r"(\d{1,2})\s*[点时]", raw)
            if point:
                hour = int(point.group(1))
    if hour is not None and not (0 <= hour <= 23):
        hour = None
    if 0 <= minute <= 59 and hour is not None:
        pass
    else:
        minute = 0

    return {
        "year": year, "month": month, "day": day,
        "hour": hour, "minute": minute,
    }


def _hour_branch_index(hour: int) -> int:
    return ((int(hour) + 1) // 2) % 12


def _month_branch_index(month: int) -> int:
    # 1 月按丑月、2 月按寅月，依此类推；不处理节气。
    return {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6,
            7: 7, 8: 8, 9: 9, 10: 10, 11: 11, 12: 0}[int(month)]


def bazi(birth: dict) -> dict:
    year = int(birth["year"])
    month = int(birth["month"])
    day = int(birth["day"])
    hour = birth.get("hour")

    year_index = (year - 4) % 60
    year_stem, year_branch = _gz(year_index)

    # 五虎遁：由年干推月干
    month_base = {"甲": 2, "己": 2, "乙": 4, "庚": 4, "丙": 6, "辛": 6,
                  "丁": 8, "壬": 8, "戊": 0, "癸": 0}[year_stem]
    month_order = (month - 2) % 12
    month_stem = STEMS[(month_base + month_order) % 10]
    month_branch = BRANCHES[_month_branch_index(month)]

    # 日柱：以 2000-01-07 为甲子日的近似基准。
    days = (date(year, month, day) - date(2000, 1, 7)).days
    day_stem, day_branch = _gz(days % 60)

    hour_stem = hour_branch = None
    if hour is not None:
        hour_branch_index = _hour_branch_index(int(hour))
        hour_branch = BRANCHES[hour_branch_index]
        # 五鼠遁：由日干推时干
        hour_base = {"甲": 0, "己": 0, "乙": 2, "庚": 2, "丙": 4, "辛": 4,
                     "丁": 6, "壬": 6, "戊": 8, "癸": 8}[day_stem]
        hour_stem = STEMS[(hour_base + hour_branch_index) % 10]

    pillars = [
        ("年柱", year_stem, year_branch),
        ("月柱", month_stem, month_branch),
        ("日柱", day_stem, day_branch),
    ]
    if hour_stem:
        pillars.append(("时柱", hour_stem, hour_branch))

    counts = {element: 0 for element in ELEMENTS}
    for _name, stem, branch in pillars:
        counts[STEM_ELEMENT[stem]] += 1
        counts[BRANCH_ELEMENT[branch]] += 1
    dominant = max(ELEMENTS, key=lambda e: (counts[e], -ELEMENTS.index(e)))
    quadrant = ELEMENT_TO_QUADRANT[dominant]

    return {
        "pillars": pillars,
        "zodiac": ZODIAC[BRANCHES.index(year_branch)],
        "elements": counts,
        "dominant": dominant,
        "quadrant": quadrant,
        "day_master": day_stem,
        "day_master_element": STEM_ELEMENT[day_stem],
        "has_hour": hour is not None,
        "birth": birth,
    }


def hexagram(chart: dict, seed: str) -> dict:
    pillars = chart["pillars"]
    year_branch = BRANCHES.index(pillars[0][2])
    day_branch = BRANCHES.index(pillars[2][2])
    hour = chart["birth"].get("hour")
    hour_value = int(hour) if hour is not None else 12
    salt = _stable(seed)
    upper_index = (year_branch + chart["birth"]["month"] + salt) % 8
    lower_index = (day_branch + hour_value + salt // 8) % 8
    name = HEXAGRAMS[upper_index][lower_index]
    changing_line = (chart["birth"]["day"] + chart["birth"]["month"]
                     + salt // 64) % 6 + 1
    changed_upper, changed_lower = upper_index, lower_index
    if changing_line <= 3:
        changed_lower = (lower_index + 1) % 8
    else:
        changed_upper = (upper_index + 1) % 8
    return {
        "upper": TRIGRAMS[upper_index],
        "lower": TRIGRAMS[lower_index],
        "symbol": f"{TRIGRAM_SYMBOLS[upper_index]}{TRIGRAM_SYMBOLS[lower_index]}",
        "name": name,
        "number": upper_index * 8 + lower_index + 1,
        "changing_line": changing_line,
        "changed": HEXAGRAMS[changed_upper][changed_lower],
        "upper_advice": TRIGRAM_ADVICE[TRIGRAMS[upper_index]],
    }


def palace(chart: dict) -> list[str]:
    """天圆地方：洛书九宫，中宫放日主五行。"""
    lines = []
    for row in PALACE_GRID:
        cells = []
        for number in row:
            trigram = PALACE_TRIGRAM[number]
            if number == 5:
                cells.append(f"{chart['day_master']}({chart['day_master_element']})")
            else:
                cells.append(f"{trigram}{TRIGRAM_ELEMENT[trigram]}")
        lines.append("｜".join(cells))
    return lines


def today_ganzhi(day: date | None = None) -> tuple[str, str]:
    """Today's day pillar (approximate, same epoch as bazi)."""
    moment = day or date.today()
    days = (moment - date(2000, 1, 7)).days
    return _gz(days % 60)


def reading(chart: dict, cast: dict, today: tuple[str, str]) -> list[str]:
    dominant = chart["dominant"]
    upper = cast["upper"]
    day_stem, day_branch = today
    today_element = STEM_ELEMENT[day_stem]
    relation = {
        ("木", "火"): "相生", ("火", "土"): "相生",
        ("土", "金"): "相生", ("金", "水"): "相生",
        ("水", "木"): "相生",
        ("木", "土"): "相克", ("土", "水"): "相克",
        ("水", "火"): "相克", ("火", "金"): "相克",
        ("金", "木"): "相克",
    }.get((today_element, dominant), "平")

    lines = [
        f"日主 {chart['day_master']}{chart['day_master_element']}"
        f"｜{dominant}旺｜{chart['quadrant']}命",
        ELEMENT_ADVICE[dominant],
        f"本卦 {cast['name']}（{cast['symbol']}）",
        cast["upper_advice"],
        f"今日 {day_stem}{day_branch}（{today_element}），"
        f"与你的{dominant}呈「{relation}」",
    ]
    if relation == "相生":
        lines.append("今天顺势而为，做顺水推舟的事最省力。")
    elif relation == "相克":
        lines.append("今天阻力偏大，先做确定的小事，别硬碰。")
    else:
        lines.append("今天不咸不淡，按自己的节奏来就好。")
    lines.append("（娱乐向命理，不必当真）")
    return lines
