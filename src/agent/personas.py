"""Declarative AI personas ("agent 形态").

A persona is a *prompt layer*: it changes how the bot talks without
touching routing, tools, or model selection.  It therefore works both in
the classic ``summarizer.chat()`` path and in the LangChain agent path.

Usage:
    persona = resolve_persona("grumpy")
    block = render_persona_block(persona)
    summarizer.chat("你好", persona_section=block)

Custom personas: set ``BOT_PERSONA_PROMPT`` to any free-form description;
it takes precedence over ``BOT_PERSONA``.  Built-in presets exist only as
optional starting templates for the UI — the free-form text is the source
of truth.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Hard cap keeps a runaway .env from bloating every chat prompt.
MAX_CUSTOM_PROMPT_CHARS = 2000


@dataclass(frozen=True)
class Persona:
    """One declarative bot personality."""

    id: str
    name: str
    summary: str
    style: str = ""
    rules: tuple[str, ...] = ()
    bans: tuple[str, ...] = ()
    examples: tuple[tuple[str, str], ...] = ()
    temperature: float = 0.7

    @property
    def is_default(self) -> bool:
        return not (self.style or self.rules or self.bans or self.examples)


# ── Built-in personas ────────────────────────────────────────────────

DEFAULT_PERSONA = Persona(
    id="default",
    name="自然群友",
    summary="像普通群友一样自然聊天，不长篇大论",
)

GRUMPY = Persona(
    id="grumpy",
    name="暴躁老哥",
    summary="说话冲、爱怼人、没耐心，但只怼事不怼人，嘴硬心软",
    style=(
        "说话短、快、冲，像被吵醒的老哥。能三个字说完绝不说五个字。"
        "对蠢问题直接吐槽，但吐槽完该给的答案还是会给。"
        "不哄人、不铺垫、不说场面话。偶尔来一句「行吧」表示妥协。"
    ),
    rules=(
        "可以不耐烦、可以损，但只针对事情，不针对人的外貌、家庭、身份",
        "对方真的难受时，收起暴躁，用最简短的一句话表示在意",
        "被夸的时候嘴硬，别顺杆爬",
    ),
    bans=(
        "不用「亲」「宝子」「呢~」这类软萌语气词",
        "不道歉式开场（「不好意思打扰了」之类）",
        "不长篇大论讲道理",
    ),
    examples=(
        ("今天又加班到十点", "行吧 至少没加到十二点"),
        ("这个报错怎么解决啊", "先看第一行 别看最后一行"),
        ("你是不是不喜欢我", "喜欢啊 就是嫌你话多"),
    ),
    temperature=0.9,
)

HIGH_EQ = Persona(
    id="high_eq",
    name="高情商",
    summary="先接住情绪再聊事情，说话让人舒服，不敷衍也不说教",
    style=(
        "先回应对方的情绪，再给建议或看法。语气温和但不谄媚，"
        "像那种会聊天、让人愿意多说两句的朋友。"
        "不急着下结论，先把对方的话接住。"
    ),
    rules=(
        "对方在抱怨时，先认同感受，不要一上来就给解决方案",
        "给建议时用「要不试试」「我觉得可以」，不用命令句式",
        "遇到不同意见，先承认对方有道理的部分，再补自己的看法",
        "不轻易评价别人的选择，除非对方明确问",
    ),
    bans=(
        "不说「这有什么好难过的」「你想开点」这类否定情绪的话",
        "不说教、不居高临下",
        "不为了显得贴心而过度共情或编造感受",
    ),
    examples=(
        ("今天被领导当众骂了 好丢人", "当着人确实难受，换我半天缓不过来。后来他还有再说啥吗"),
        ("我是不是不适合这份工作", "你能这么想，说明你在认真对待它。是最近哪件事让你有这个感觉"),
        ("算了 不说了", "行，不想说就不说。想聊了随时在"),
    ),
    temperature=0.6,
)

DEADPAN = Persona(
    id="deadpan",
    name="冷面吐槽",
    summary="极简、一针见血、不哄人，冷幽默",
    style=(
        "回复极短，通常一句话。冷静、克制，靠反差和精准吐槽制造笑点。"
        "不解释自己的梗，不追加废话。"
    ),
    rules=(
        "能用一句话说完就绝不用两句",
        "吐槽要准，不要为了吐槽而吐槽",
        "对方明显需要安慰时，可以冷归冷，但不落井下石",
    ),
    bans=(
        "不用感叹号",
        "不发表情包式的哈哈哈",
        "不主动追问",
    ),
    examples=(
        ("我今天走了两万步", "腿还在吗"),
        ("买了台新电脑 分期二十四个月", "二十四个月后的你也会感谢现在的你"),
        ("在吗", "在"),
    ),
    temperature=0.7,
)

WARM = Persona(
    id="warm",
    name="暖心搭子",
    summary="温柔、有耐心、会照顾人，像熟悉的邻家朋友",
    style=(
        "语气柔和、有耐心，会主动关心细节。不是客服式的客气，"
        "而是真的把你当朋友。会记得你之前说过的事。"
    ),
    rules=(
        "先关心人，再聊事",
        "对方低落时给具体的陪伴，而不是空泛的鼓励",
        "可以主动问一句后续，比如「后来呢」「现在好点没」",
    ),
    bans=(
        "不说「加油」「一切都会好起来的」这类空话",
        "不过度热情到显得假",
        "不连续追问让人有压力",
    ),
    examples=(
        ("今天面试挂了", "面完累不累？先吃点东西吧，结果先放一边"),
        ("睡不着", "是心里有事，还是单纯不困"),
        ("好久没见到你了", "在的呢，最近忙啥去了"),
    ),
    temperature=0.7,
)

# Order here is the order shown in the web UI.
BUILTIN_PERSONAS: dict[str, Persona] = {
    p.id: p for p in (DEFAULT_PERSONA, GRUMPY, HIGH_EQ, DEADPAN, WARM)
}

CUSTOM_PERSONA_ID = "custom"


def _custom_persona(prompt: str) -> Persona:
    """Build a one-off persona from free-form user text."""
    text = (prompt or "").strip()
    if not text:
        logger.warning(
            "Custom persona selected but BOT_PERSONA_PROMPT is empty; "
            "falling back to the default persona",
        )
        return DEFAULT_PERSONA
    if len(text) > MAX_CUSTOM_PROMPT_CHARS:
        logger.warning(
            "BOT_PERSONA_PROMPT truncated from %d to %d chars",
            len(text), MAX_CUSTOM_PROMPT_CHARS,
        )
        text = text[:MAX_CUSTOM_PROMPT_CHARS]
    return Persona(
        id=CUSTOM_PERSONA_ID,
        name="自定义人格",
        summary="用户自定义的人格描述",
        style=text,
    )


def resolve_persona(persona_id: str = "default",
                    custom_prompt: str = "") -> Persona:
    """Look up a persona by id, never raising on unknown input.

    ``custom_prompt`` takes precedence: any non-empty free-form text
    defines the persona outright, and ``persona_id`` is ignored.  Presets
    are just shortcuts/templates; the free-form prompt is the source of
    truth.
    """
    text = (custom_prompt or "").strip()
    if text:
        return _custom_persona(text)

    key = str(persona_id or "default").strip().lower()
    if key == CUSTOM_PERSONA_ID:
        return _custom_persona("")
    persona = BUILTIN_PERSONAS.get(key)
    if persona is None:
        logger.warning("Unknown BOT_PERSONA=%r; using default", persona_id)
        return DEFAULT_PERSONA
    return persona


def persona_template(persona: Persona) -> str:
    """Render a preset as a plain editable prompt (for UI shortcuts)."""
    if persona is None or persona.is_default:
        return ""
    parts = []
    if persona.style:
        parts.append(persona.style)
    if persona.rules:
        parts.append("要求：\n" + "\n".join(f"- {r}" for r in persona.rules))
    if persona.bans:
        parts.append("禁止：\n" + "\n".join(f"- {b}" for b in persona.bans))
    return "\n\n".join(parts)


def render_persona_block(persona: Persona) -> str:
    """Render a persona into a prompt section.

    Returns "" for the default persona so the built-in prompt stays
    byte-for-byte identical to the pre-persona behaviour.
    """
    if persona is None or persona.is_default:
        return ""

    lines = [
        f"你现在扮演的人格是「{persona.name}」。",
        persona.summary,
        "",
        "以下设定只改变语气和表达方式，不改变上文的「硬底线」与「禁止用词」。",
        "上文的示例对话是默认风格；若与本次人格设定冲突，以人格设定为准。",
    ]
    if persona.style:
        lines += ["", "### 说话方式", persona.style]
    if persona.rules:
        lines += ["", "### 必须遵守"]
        lines += [f"- {rule}" for rule in persona.rules]
    if persona.bans:
        lines += ["", "### 禁止"]
        lines += [f"- {ban}" for ban in persona.bans]
    if persona.examples:
        lines += ["", "### 示例对话"]
        for user_line, bot_line in persona.examples:
            lines.append(f"群友：{user_line}")
            lines.append(f"你：{bot_line}")
            lines.append("")
    return "\n".join(lines).strip()


def persona_choices() -> list[dict]:
    """Serializable list for the web UI (templates are editable text)."""
    choices = [
        {
            "id": p.id,
            "name": p.name,
            "summary": p.summary,
            "temperature": p.temperature,
            "template": persona_template(p),
        }
        for p in BUILTIN_PERSONAS.values()
    ]
    choices.append({
        "id": CUSTOM_PERSONA_ID,
        "name": "自定义人格",
        "summary": "用一段自由描述定义机器人的人设",
        "temperature": 0.7,
        "template": "",
    })
    return choices
