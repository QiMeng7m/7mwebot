"""星盘玩法的纯规则：签等级、欧气、段位、星象、星座换算。

本模块不碰数据库、不发消息，方便单测和后续调参。
"""

from __future__ import annotations

import hashlib
import random
from datetime import date

# ── 签等级 ──────────────────────────────────────────────────────

SIGN_LEVELS: list[dict] = [
    {"name": "上上签", "emoji": "🟣", "weight": 2,
     "min_luck": 90, "max_luck": 100},
    {"name": "大吉", "emoji": "🟢", "weight": 5,
     "min_luck": 75, "max_luck": 89},
    {"name": "中吉", "emoji": "🔵", "weight": 18,
     "min_luck": 55, "max_luck": 74},
    {"name": "小吉", "emoji": "🟡", "weight": 40,
     "min_luck": 40, "max_luck": 54},
    {"name": "末吉", "emoji": "🟠", "weight": 25,
     "min_luck": 25, "max_luck": 39},
    {"name": "凶", "emoji": "⚫", "weight": 10,
     "min_luck": 10, "max_luck": 24},
]

LEVEL_BY_NAME = {level["name"]: level for level in SIGN_LEVELS}

LEVEL_ADVICE: dict[str, str] = {
    "上上签": "顺势而为，今天适合把最想做的事往前推。",
    "大吉": "状态在线，主动一点会有回报。",
    "中吉": "稳中求进，别急着一次做完。",
    "小吉": "小步慢走，先完成一件小事。",
    "末吉": "保守一点，先守住基本盘。",
    "凶": "今天不宜硬冲，养精蓄锐也是赢。",
}


def level_emoji(name: str) -> str:
    return LEVEL_BY_NAME.get(name, {}).get("emoji", "")


def pick_level(roll: float) -> dict:
    """Pick a signature level from a ``[0, 1)`` roll."""
    total = sum(level["weight"] for level in SIGN_LEVELS)
    target = max(0.0, min(0.999999, float(roll))) * total
    cursor = 0.0
    for level in SIGN_LEVELS:
        cursor += level["weight"]
        if target < cursor:
            return level
    return SIGN_LEVELS[-1]


def roll_level(rng: random.Random) -> dict:
    return pick_level(rng.random())


def roll_luck(level: dict, rng: random.Random) -> int:
    return int(rng.randint(int(level["min_luck"]), int(level["max_luck"])))


def streak_bonus(streak_days: int) -> int:
    return min(max(0, int(streak_days)) * 2, 20)


# ── 段位 ────────────────────────────────────────────────────────

RANKS: list[dict] = [
    {"name": "咸鱼", "threshold": 0, "luck_bonus": 0},
    {"name": "小虾", "threshold": 15, "luck_bonus": 1},
    {"name": "鲫鱼", "threshold": 50, "luck_bonus": 2},
    {"name": "鲤鱼", "threshold": 120, "luck_bonus": 3},
    {"name": "锦鲤", "threshold": 300, "luck_bonus": 5},
    {"name": "欧皇", "threshold": 700, "luck_bonus": 8},
    {"name": "天选之子", "threshold": 1500, "luck_bonus": 12},
    {"name": "锦鲤王", "threshold": 3000, "luck_bonus": 20},
]


def rank_for(fortune: int) -> dict:
    """Current rank plus the next threshold and remaining fortune."""
    fortune = max(0, int(fortune or 0))
    current = RANKS[0]
    for rank in RANKS:
        if fortune >= rank["threshold"]:
            current = rank
        else:
            break
    index = RANKS.index(current)
    nxt = RANKS[index + 1] if index + 1 < len(RANKS) else None
    return {
        **current,
        "next": nxt,
        "to_next": max(0, nxt["threshold"] - fortune) if nxt else 0,
    }


def rank_luck_bonus(fortune: int) -> int:
    return int(rank_for(fortune)["luck_bonus"])


# ── 每日星象 ────────────────────────────────────────────────────

STAR_EVENTS: list[dict] = [
    {"name": "满月", "desc": "放生额外 +1 气运",
     "release_bonus": 1},
    {"name": "水星逆行", "desc": "转运费用 -20 香火",
     "reroll_discount": 20},
    {"name": "金星入宫", "desc": "今日求签欧气 +5",
     "sign_luck": 5},
    {"name": "木星高照", "desc": "观星香火 +15",
     "observe_bonus": 15},
    {"name": "土星回归", "desc": "放生费用 -10 香火",
     "release_discount": 10},
    {"name": "日食", "desc": "今日求签欧气 +10",
     "sign_luck": 10},
    {"name": "流星雨", "desc": "观星香火 +10",
     "observe_bonus": 10},
    {"name": "紫气东来", "desc": "今日香火收益 +10%",
     "merit_mult": 1.10},
]


def _stable_index(seed: str, size: int) -> int:
    digest = hashlib.sha256(str(seed).encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % max(1, int(size))


def event_for(chat_id: str, day: str) -> dict:
    return STAR_EVENTS[_stable_index(f"{chat_id}:{day}", len(STAR_EVENTS))]


# ── 星座 / MBTI ─────────────────────────────────────────────────

ZODIAC_SIGNS: list[str] = [
    "白羊座", "金牛座", "双子座", "巨蟹座", "狮子座", "处女座",
    "天秤座", "天蝎座", "射手座", "摩羯座", "水瓶座", "双鱼座",
]

# 日期区间（含首尾），用于「设置生日 10-24」。
_SIGN_RANGES: list[tuple[int, int, int, int, str]] = [
    (3, 21, 4, 19, "白羊座"),
    (4, 20, 5, 20, "金牛座"),
    (5, 21, 6, 21, "双子座"),
    (6, 22, 7, 22, "巨蟹座"),
    (7, 23, 8, 22, "狮子座"),
    (8, 23, 9, 22, "处女座"),
    (9, 23, 10, 23, "天秤座"),
    (10, 24, 11, 22, "天蝎座"),
    (11, 23, 12, 21, "射手座"),
    (12, 22, 1, 19, "摩羯座"),
    (1, 20, 2, 18, "水瓶座"),
    (2, 19, 3, 20, "双鱼座"),
]


def sign_from_date(month: int, day: int) -> str | None:
    month, day = int(month), int(day)
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    for start_m, start_d, end_m, end_d, name in _SIGN_RANGES:
        if start_m == end_m and start_m == month:
            if start_d <= day <= end_d:
                return name
        elif start_m < end_m and start_m == month and day >= start_d:
            return name
        elif start_m < end_m and end_m == month and day <= end_d:
            return name
        elif start_m > end_m:  # 跨年：摩羯 / 水瓶 / 双鱼
            if (month == start_m and day >= start_d) or \
                    (month == end_m and day <= end_d):
                return name
    return None


def normalize_sign(raw: str) -> str | None:
    """Accept 天蝎 / 天蝎座 / 天蝎座♏ and return the canonical name."""
    text = str(raw or "").strip()
    for name in ZODIAC_SIGNS:
        if text == name or text == name.replace("座", ""):
            return name
    return None


MBTI_TYPES: frozenset[str] = frozenset({
    "INTJ", "INTP", "ENTJ", "ENTP",
    "INFJ", "INFP", "ENFJ", "ENFP",
    "ISTJ", "ISFJ", "ESTJ", "ESFJ",
    "ISTP", "ISFP", "ESTP", "ESFP",
})


def normalize_mbti(raw: str) -> str | None:
    text = str(raw or "").strip().upper()
    return text if text in MBTI_TYPES else None


def mbti_affinity(mbti: str) -> str | None:
    """NT→白虎, NF→玄武, SJ→青龙, SP→朱雀。"""
    text = str(mbti or "").upper()
    if len(text) != 4:
        return None
    if text[1] == "N" and text[2] == "T":
        return "baihu"
    if text[1] == "N" and text[2] == "F":
        return "xuanwu"
    if text[1] == "S" and text[3] == "J":
        return "qinglong"
    if text[1] == "S" and text[3] == "P":
        return "zhuque"
    return None


def lucky_sign(chat_id: str, week: str) -> str:
    return ZODIAC_SIGNS[_stable_index(
        f"{chat_id}:{week}:lucky", len(ZODIAC_SIGNS))]


def moon_sign(sender_id: str) -> str:
    return ZODIAC_SIGNS[_stable_index(
        f"{sender_id}:moon", len(ZODIAC_SIGNS))]


def rising_sign(sender_id: str) -> str:
    return ZODIAC_SIGNS[_stable_index(
        f"{sender_id}:rising", len(ZODIAC_SIGNS))]


def today_minus_days(day: str, days: int = 1) -> str:
    moment = date.fromisoformat(day)
    return date.fromordinal(moment.toordinal() - int(days)).isoformat()


def week_start_ts(ts: int | float | None = None) -> int:
    """Local Monday 00:00 as a Unix timestamp."""
    import time
    now = int(time.time() if ts is None else ts)
    local = time.localtime(now)
    return now - local.tm_wday * 86400 - local.tm_hour * 3600 \
        - local.tm_min * 60 - local.tm_sec


# ── 星盘 28 宿 ──────────────────────────────────────────────────

QUADRANTS: dict[str, dict] = {
    "青龙": {
        "key": "qinglong", "action": "sign", "action_label": "求签",
        "stat": "香火收益", "per_star": 0.01, "set_bonus": 0.10,
        "set_label": "香火收益额外 +10%",
    },
    "白虎": {
        "key": "baihu", "action": "draw", "action_label": "开光",
        "stat": "开光保底", "per_star": 1, "set_bonus": 5,
        "set_label": "保底额外 -5 抽，传说符 +2%",
    },
    "朱雀": {
        "key": "zhuque", "action": "reroll", "action_label": "转运",
        "stat": "欧气", "per_star": 1, "set_bonus": 5,
        "set_label": "欧气额外 +5",
    },
    "玄武": {
        "key": "xuanwu", "action": "release", "action_label": "放生",
        "stat": "锦鲤池权重", "per_star": 0.01, "set_bonus": 0.08,
        "set_label": "池权重额外 +8%，放生 +2 气运",
    },
}

QUADRANT_BY_KEY = {v["key"]: k for k, v in QUADRANTS.items()}

STAR_COSTS = [80, 150, 250, 400, 600, 850, 1200]
STAR_FORTUNE_REQ = [5, 15, 30, 55, 90, 140, 200]
STAR_ACTION_REQ = {
    "qinglong": [3, 7, 15, 25, 40, 60, 90],
    "baihu": [3, 8, 15, 25, 40, 60, 85],
    "zhuque": [1, 3, 6, 10, 16, 24, 34],
    "xuanwu": [1, 3, 6, 10, 16, 24, 34],
}
STAR_TOTAL = 28
OUTER_STARS = 60

AWAKENINGS: dict[str, dict] = {
    "青龙": {"key": "qinglong", "name": "青龙·财神",
             "desc": "香火收益永久 +20%"},
    "白虎": {"key": "baihu", "name": "白虎·天机",
             "desc": "开光保底永久 -5，传说符 +2%"},
    "朱雀": {"key": "zhuque", "name": "朱雀·炎运",
             "desc": "每日欧气永久 +10"},
    "玄武": {"key": "xuanwu", "name": "玄武·锦鲤",
             "desc": "锦鲤池权重 +15%，放生气运翻倍"},
}
AWAKENING_BY_KEY = {v["key"]: v for v in AWAKENINGS.values()}

OUTER_COST_BASE = 1500
OUTER_COST_STEP = 100


def normalize_quadrant(raw: str) -> str | None:
    text = str(raw or "").strip()
    aliases = {
        "青龙": "青龙", "青": "青龙", "东": "青龙",
        "白虎": "白虎", "白": "白虎", "西": "白虎",
        "朱雀": "朱雀", "朱": "朱雀", "南": "朱雀",
        "玄武": "玄武", "玄": "玄武", "北": "玄武",
        "外圈": "外圈", "外": "外圈",
    }
    return aliases.get(text)


def star_cost(index: int) -> int:
    index = max(1, min(len(STAR_COSTS), int(index)))
    return STAR_COSTS[index - 1]


def star_fortune_req(index: int) -> int:
    index = max(1, min(len(STAR_FORTUNE_REQ), int(index)))
    return STAR_FORTUNE_REQ[index - 1]


def star_action_req(quadrant_key: str, index: int) -> int:
    reqs = STAR_ACTION_REQ.get(quadrant_key) or STAR_ACTION_REQ["qinglong"]
    index = max(1, min(len(reqs), int(index)))
    return reqs[index - 1]


def outer_star_cost(index: int) -> int:
    index = max(1, int(index))
    return OUTER_COST_BASE + (index - 1) * OUTER_COST_STEP


_STAR_OFFSET = {"qinglong": 0, "baihu": 7, "zhuque": 14, "xuanwu": 21}


def star_name(quadrant_key: str, index: int) -> str:
    offset = _STAR_OFFSET.get(quadrant_key)
    if offset is None:
        return f"外圈{int(index)}"
    idx = offset + max(1, min(7, int(index))) - 1
    return TWENTY_EIGHT_MANSIONS[idx % len(TWENTY_EIGHT_MANSIONS)]


def star_bonuses(quadrant_counts: dict[str, int],
                 awakening: str = "") -> dict:
    """Aggregate permanent bonuses from lit stars + awakening."""
    qing = int(quadrant_counts.get("qinglong", 0) or 0)
    bai = int(quadrant_counts.get("baihu", 0) or 0)
    zhu = int(quadrant_counts.get("zhuque", 0) or 0)
    xuan = int(quadrant_counts.get("xuanwu", 0) or 0)
    outer = int(quadrant_counts.get("outer", 0) or 0)

    bonuses = {
        "merit_mult": 1.0 + qing * 0.01 + outer * 0.002,
        "pity_reduction": bai * 1 + outer * 0.0,
        "legendary_bonus": 0.0,
        "luck_bonus": zhu * 1 + outer * 0.0,
        "pool_weight": 1.0 + xuan * 0.01 + outer * 0.002,
        "release_bonus": 0,
    }
    if qing >= 7:
        bonuses["merit_mult"] += 0.10
    if bai >= 7:
        bonuses["pity_reduction"] += 5
        bonuses["legendary_bonus"] += 0.02
    if zhu >= 7:
        bonuses["luck_bonus"] += 5
    if xuan >= 7:
        bonuses["pool_weight"] += 0.08
        bonuses["release_bonus"] += 2

    if awakening == "qinglong":
        bonuses["merit_mult"] += 0.20
    elif awakening == "baihu":
        bonuses["pity_reduction"] += 5
        bonuses["legendary_bonus"] += 0.02
    elif awakening == "zhuque":
        bonuses["luck_bonus"] += 10
    elif awakening == "xuanwu":
        bonuses["pool_weight"] += 0.15
        bonuses["release_bonus"] += 2
    return bonuses


# ── 开光 / 塔罗 22 张大阿卡纳 ───────────────────────────────────

TAROT_CARDS: list[dict] = [
    {"id": 0, "name": "愚者", "kind": "free_reroll", "value": 1,
     "text": "新的旅程从放下开始，今天适合轻装上阵。"},
    {"id": 1, "name": "魔术师", "kind": "merit", "value": 100,
     "text": "资源就在手边，敢用才会有结果。"},
    {"id": 2, "name": "女祭司", "kind": "fortune", "value": 3,
     "text": "答案在沉默里，先听一听自己的直觉。"},
    {"id": 3, "name": "皇后", "kind": "merit_boost", "value": 0.50,
     "text": "丰盛正在靠近，接住它。"},
    {"id": 4, "name": "皇帝", "kind": "fortune", "value": 10,
     "text": "秩序与掌控力回归，适合做决定。"},
    {"id": 5, "name": "教皇", "kind": "luck", "value": 15,
     "text": "跟随内心的秩序，今天不宜叛逆。"},
    {"id": 6, "name": "恋人", "kind": "fortune", "value": 3,
     "text": "关系里藏着机会，主动一点。"},
    {"id": 7, "name": "战车", "kind": "pool_boost", "value": 0.30,
     "text": "向前冲，但记得握紧缰绳。"},
    {"id": 8, "name": "力量", "kind": "merit", "value": 50,
     "text": "温柔比强硬更有力量。"},
    {"id": 9, "name": "隐者", "kind": "pity_boost", "value": 2,
     "text": "独处会给你答案，今天少凑热闹。"},
    {"id": 10, "name": "命运之轮", "kind": "fortune", "value": 10,
     "text": "轮子转起来了，顺势而为。"},
    {"id": 11, "name": "正义", "kind": "merit", "value": 50,
     "text": "该算的账会算清，保持坦荡。"},
    {"id": 12, "name": "倒吊人", "kind": "free_reroll", "value": 1,
     "text": "换个角度看，卡住的事会松动。"},
    {"id": 13, "name": "死神", "kind": "fortune", "value": 3,
     "text": "结束是为了新的开始。"},
    {"id": 14, "name": "节制", "kind": "luck", "value": 15,
     "text": "不过量，刚刚好就是最好。"},
    {"id": 15, "name": "恶魔", "kind": "merit", "value": 100,
     "text": "看清欲望，你就能驾驭它。"},
    {"id": 16, "name": "塔", "kind": "fortune", "value": 10,
     "text": "旧结构松动，别害怕变化。"},
    {"id": 17, "name": "星星", "kind": "pool_boost", "value": 0.30,
     "text": "希望还在，今晚值得一搏。"},
    {"id": 18, "name": "月亮", "kind": "pity_boost", "value": 2,
     "text": "情绪有迷雾，先别急着下判断。"},
    {"id": 19, "name": "太阳", "kind": "merit_boost", "value": 0.50,
     "text": "光出来了，今天适合被看见。"},
    {"id": 20, "name": "审判", "kind": "fortune", "value": 10,
     "text": "该做的决定，今天可以落了。"},
    {"id": 21, "name": "世界", "kind": "koi_body", "value": 3,
     "text": "一轮圆满。接下来 3 次开光保底 -2 抽。", "legendary": True},
]

TAROT_TOTAL = len(TAROT_CARDS)
TAROT_BY_ID = {card["id"]: card for card in TAROT_CARDS}


def tarot_set_reward(count: int) -> int:
    """气运奖励：集齐 22 张一次性的额外奖励。"""
    return 150 if int(count) >= TAROT_TOTAL else 0


# ── 星骰 ────────────────────────────────────────────────────────

DICE_PLANETS: list[dict] = [
    {"id": 1, "name": "太阳", "kind": "luck", "value": 5,
     "desc": "今日欧气 +5"},
    {"id": 2, "name": "月亮", "kind": "release", "value": 1,
     "desc": "下次放生 +1 气运"},
    {"id": 3, "name": "水星", "kind": "pity", "value": 1,
     "desc": "下次开光保底 -1 抽"},
    {"id": 4, "name": "金星", "kind": "pool", "value": 0.05,
     "desc": "下次锦鲤池权重 +5%"},
    {"id": 5, "name": "火星", "kind": "duel", "value": 5,
     "desc": "下次斗星胜利 +5 香火"},
    {"id": 6, "name": "木星", "kind": "merit", "value": 0.10,
     "desc": "今日香火收益 +10%"},
    {"id": 7, "name": "土星", "kind": "star_cost", "value": 0.05,
     "desc": "下次点星香火 -5%"},
]

DICE_ZODIAC = list(ZODIAC_SIGNS)
DICE_HOUSES = [f"第{i}宫" for i in range(1, 13)]


def planet_by_id(planet_id: int) -> dict | None:
    for planet in DICE_PLANETS:
        if int(planet["id"]) == int(planet_id):
            return planet
    return None


# ── 本命宿 / 生命数字 ───────────────────────────────────────────

TWENTY_EIGHT_MANSIONS: list[str] = [
    "角宿", "亢宿", "氐宿", "房宿", "心宿", "尾宿", "箕宿",
    "斗宿", "牛宿", "女宿", "虚宿", "危宿", "室宿", "壁宿",
    "奎宿", "娄宿", "胃宿", "昴宿", "毕宿", "觜宿", "参宿",
    "井宿", "鬼宿", "柳宿", "星宿", "张宿", "翼宿", "轸宿",
]


def noble_star(month: int, day: int) -> str:
    return TWENTY_EIGHT_MANSIONS[
        _stable_index(f"{int(month)}-{int(day)}:mansion",
                      len(TWENTY_EIGHT_MANSIONS))]


def life_number(month: int, day: int) -> int:
    def reduce_num(value: int) -> int:
        while value > 9:
            value = sum(int(ch) for ch in str(value))
        return max(1, value)
    return reduce_num(int(month) + int(day))


# ── 合盘 / 社交 ─────────────────────────────────────────────────

ELEMENT_OF_SIGN: dict[str, str] = {}
for _sign in ("白羊座", "狮子座", "射手座"):
    ELEMENT_OF_SIGN[_sign] = "火"
for _sign in ("金牛座", "处女座", "摩羯座"):
    ELEMENT_OF_SIGN[_sign] = "土"
for _sign in ("双子座", "天秤座", "水瓶座"):
    ELEMENT_OF_SIGN[_sign] = "风"
for _sign in ("巨蟹座", "天蝎座", "双鱼座"):
    ELEMENT_OF_SIGN[_sign] = "水"

ELEMENT_TO_FACTION = {
    "火": "zhuque", "土": "baihu", "风": "qinglong", "水": "xuanwu",
}


def compatibility(a: dict, b: dict) -> dict:
    """0~100 compatibility with a short reading. Deterministic."""
    score = 50
    a_sign = str(a.get("sign") or "")
    b_sign = str(b.get("sign") or "")
    a_mbti = str(a.get("mbti") or "")
    b_mbti = str(b.get("mbti") or "")

    if a_sign and a_sign == b_sign:
        score += 10
    a_elem = ELEMENT_OF_SIGN.get(a_sign)
    b_elem = ELEMENT_OF_SIGN.get(b_sign)
    if a_elem and b_elem:
        if a_elem == b_elem:
            score += 15
        elif {a_elem, b_elem} in ({"火", "风"}, {"土", "水"}):
            score += 10
        elif {a_elem, b_elem} in ({"火", "水"}, {"风", "土"}):
            score -= 5
        else:
            score += 3

    if a_mbti and b_mbti:
        score += sum(4 for x, y in zip(a_mbti, b_mbti) if x == y)
        if a_mbti == b_mbti:
            score += 8

    seed = "".join(sorted([a_sign, b_sign, a_mbti, b_mbti]))
    score += _stable_index(seed, 17) - 8
    score = max(0, min(100, score))
    if score >= 85:
        text = "非常合拍，适合一起搞事情。"
    elif score >= 70:
        text = "默契不错，多聊两句会更顺。"
    elif score >= 55:
        text = "关系平稳，保持各自节奏就好。"
    elif score >= 40:
        text = "需要磨合，别急着下结论。"
    else:
        text = "差异较大，适合当彼此的镜子。"
    return {"score": score, "text": text}


# ── 周运 ────────────────────────────────────────────────────────

WEEKLY_GOALS: list[dict] = [
    {"key": "sign", "label": "求签", "target": 3},
    {"key": "observe", "label": "观星", "target": 3},
    {"key": "draw", "label": "开光", "target": 3},
    {"key": "release", "label": "放生", "target": 2},
    {"key": "reroll", "label": "转运", "target": 1},
    {"key": "pool", "label": "投锦鲤池", "target": 1},
    {"key": "star", "label": "点亮星宿", "target": 2},
]


def weekly_goals(chat_id: str, week: str) -> list[dict]:
    """Deterministically pick 3 goals for a group/week."""
    start = _stable_index(f"{chat_id}:{week}:weekly", len(WEEKLY_GOALS))
    picked = [WEEKLY_GOALS[(start + i) % len(WEEKLY_GOALS)]
              for i in range(3)]
    return [dict(goal) for goal in picked]

