"""TodayWaifu 的目标用户解析（@ / 富文本 / 纯文本三种上报形态）。

各适配器上报 @ 目标的形态并不统一：有的给出 `at_list` 数字 QQ 号，有的只在
消息段里放富文本 mention，QQ 官方机器人等平台甚至没有数字 QQ 号而改用 openid。
本模块按「结构化的 @ 字段 → 消息段 → 原始文本」的优先级依次尝试，命中即返回，
全部失败则返回 None 交由上层给出提示，不做任何猜测性回退。
"""
from __future__ import annotations

import re
from typing import Iterator

from gsuid_core.models import Event, Message


def _normalise_target_user_id(value: object) -> str:
    """从异构的上报字段中提取可用的用户标识，无法判定时返回空串。

    返回值既可能是数字 QQ 号，也可能是 openid，或整段未解析的富文本
    （含 `CQ:at` / `<at>`），因此这里不校验格式，仅做形态归一；
    空串表示「该字段不可用」，调用方据此继续尝试下一来源。
    """
    if isinstance(value, bool) or value is None:
        return ''
    if isinstance(value, Message):
        value = value.data
    if isinstance(value, dict):
        # 不同平台的字段名不一致，按常见程度依次取第一个非空值。
        for field in ('user_id', 'qq', 'openid', 'open_id', 'id', 'data'):
            user_id = _normalise_target_user_id(value.get(field))
            if user_id:
                return user_id
        return ''
    text = str(value).strip()
    # 适配器常把缺失值序列化为这些占位串：它们不是有效用户标识，
    # 尤其 'all' 表示 @全体成员，若当作目标会把整群成员误判为用户。
    if not text or text.lower() in {'none', 'true', 'false', 'all'}:
        return ''
    return text


def _target_user_id_from_text(text: str) -> str | None:
    """从纯文本中提取目标用户标识，无匹配时返回 None。

    各模式的匹配结果都必须是同一段文本里最可信的那一个：顺序即优先级，
    越靠前的模式越具体。最后两条数字模式只做长度约束而不做语义校验，
    因为开放平台可能给出非数字标识，宁可交给上层按未找到用户处理，
    也不要在这里臆造一个标识。
    """
    text = str(text or '').strip()
    if not text:
        return None

    patterns = (
        r'\[CQ:at,[^\]]*qq=([0-9A-Za-z_-]{5,})',
        r'<at[^>]*(?:id|qq|user_id)=["\']?([0-9A-Za-z_-]{5,})',
        r'(?:qq=|qq:|QQ=|QQ:|@)\s*([0-9A-Za-z_-]{5,})',
        r'\b(\d{5,40})\b',
        # QQ 官方机器人等平台无数字 QQ 号，使用 openid（形如 16 位以上十六进制）
        r'\b([0-9A-Fa-f]{16,})\b',
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def _self_user_ids(ev: Event) -> set[str]:
    # 平台把机器人自身上报为 openid/实例名时，@机器人 需被排除避免误认目标。
    # 三个字段在不同 Core 版本中择一存在，故全部收集而非只取其一；
    # 任一为空或缺失都不影响其余字段参与排除。
    ids: set[str] = set()
    for attr in ('bot_self_id', 'self_id', 'real_bot_id'):
        value = getattr(ev, attr, None)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            ids.add(text)
    return ids


def _iter_event_messages(ev: Event) -> Iterator[object]:
    """遍历事件中承载消息段的字段。

    仅 `content` 是消息段列表：Core 的 Event/MessageReceive 字段固定（msgspec Struct），
    `ev.message` / `ev.original_message` 等属性并不存在（见 Core 的 Event 字段说明）。
    在此之上使用 `getattr` 风格的惰性默认值并无意义，缺失该字段时事件本身即无效。
    """
    for item in ev.content or ():
        yield item


def _get_event_target_user_id(ev: Event) -> str | None:
    """解析"抢/送老婆"等命令的目标用户，兼容 @、富文本与纯文本三种上报形态。

    依次尝试「结构化 @ 字段 → 消息段 → 原始文本」，返回首个可用的非自身标识；
    三种形态皆无结果时返回 None，由调用方提示用户显式 @ 目标，而不是回退到
    发送者本人——那会让「送老婆」等命令在解析失败时静默作用于自己。
    """
    self_ids = _self_user_ids(ev)
    for value in (ev.at_list, ev.at):
        if value is not None:
            # at_list 已是集合形态：一次命令只服务一个目标，取任一元素即可。
            if isinstance(value, (list, tuple, set)):
                value = next(iter(value), None)

            user_id = _normalise_target_user_id(value)
            if user_id:
                # 结构化字段偶尔直接承载整段富文本而非纯标识，此时转入文本解析，
                # 并保留自身排除，避免 @机器人 被解析成目标。
                if 'CQ:at' in user_id or '<at' in user_id:
                    parsed = _target_user_id_from_text(user_id)
                    if parsed and parsed not in self_ids:
                        return parsed
                    continue
                if user_id in self_ids:
                    continue
                return user_id

    # 结构化字段不可用时退到消息段：形态因适配器而异，故 Message 与 dict 两条路径并存。
    for item in _iter_event_messages(ev):
        if isinstance(item, Message) and item.type in {'at', 'mention_user', 'mention'}:
            user_id = _normalise_target_user_id(item.data)
            if user_id and user_id not in self_ids:
                return user_id
        if isinstance(item, dict) and item.get('type') in {'at', 'mention_user', 'mention'}:
            user_id = _normalise_target_user_id(item.get('data'))
            if user_id and user_id not in self_ids:
                return user_id

    # 最后尝试原始文本：部分适配器只上报拼接后的纯文本，误判风险最高，
    # 因此仅在更可靠的两类来源均无结果时使用。
    for text in (ev.text, ev.raw_text):
        if text:
            user_id = _target_user_id_from_text(str(text))
            if user_id and user_id not in self_ids:
                return user_id

    return None
