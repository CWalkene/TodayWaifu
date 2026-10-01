"""TodayWaifu 的消息发送兼容层。

存在理由：不同平台的 Bot 实现在提及语义与 hook 行为上并不一致，而各命令模块只应
表达「要发什么」。把平台差异收敛在这里，命令模块无需逐处判断私聊/群聊或适配器版本，
新增平台时也只需改动本模块。

本模块只做转发与降级，不产生业务文案。
"""
from __future__ import annotations

from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Message

from .payloads import SendMessage
from .constants import LOG_PREFIX


def _is_xwuid_group_activity_hook_error(exc: Exception) -> bool:
    # 三重条件缺一不可：按消息片段匹配是因为该异常没有稳定类型，只能靠特征识别；
    # 收窄到 AttributeError 可避免把同名信息出现在其它异常上时误判为兼容问题，
    # 那样会把本应暴露的真实错误悄悄降级掉。
    message = str(exc)
    return (
        isinstance(exc, AttributeError)
        and 'PluginHookManager' in message
        and 'group_activity_hooks' in message
    )


def _parse_send_options(
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> tuple[bool, dict[str, object] | None, bool]:
    # 位置参数与关键字参数并存是历史调用方造成的：旧调用按位置传，新调用用关键字。
    # 此处把两种形式归一，位置参数覆盖关键字参数，与 Python 自身的绑定优先级一致。
    options = dict(kwargs)
    at_sender = options.pop('at_sender', False)
    extra_metadata = options.pop('extra_metadata', None)
    wait_recall = options.pop('wait_recall', False)

    # 参数过多或存在未知关键字时抛 TypeError，而不是静默忽略：这类调用几乎都是
    # 代码缺陷，静默丢弃会让「@ 未生效」「撤回等待丢失」等问题难以定位。
    if len(args) > 3:
        raise TypeError(f'Bot.send expected at most 3 positional options, got {len(args)}')
    if len(args) >= 1:
        at_sender = args[0]
    if len(args) >= 2:
        extra_metadata = args[1]
    if len(args) >= 3:
        wait_recall = args[2]
    if options:
        unexpected = ', '.join(options)
        raise TypeError(f'Bot.send got unexpected keyword argument(s): {unexpected}')
    # 非字典的 metadata 一律丢弃：下游按字典取键，透传其它类型只会在更深处抛错。
    metadata = extra_metadata if isinstance(extra_metadata, dict) else None
    return bool(at_sender), metadata, bool(wait_recall)


async def _target_send_without_bot_hooks(
    bot: Bot,
    message: SendMessage,
    *args: object,
    **kwargs: object,
) -> list[str] | None:
    # 绕过 Bot.send 的 hook 链，直接调用底层 target_send：这是 hook 自身抛错时的
    # 降级路径。私聊与群聊的目标 ID 取自不同字段，必须按 user_type 分流。
    at_sender, extra_metadata, wait_recall = _parse_send_options(args, kwargs)
    ev = bot.ev
    target_type = ev.user_type
    target_id = ev.user_id if ev.user_type == 'direct' else ev.group_id
    return await bot.bot.target_send(
        message,
        target_type,
        target_id,
        ev.real_bot_id,
        bot.bot_self_id,
        ev.msg_id,
        at_sender,
        ev.user_id,
        ev.group_id,
        ev.task_id,
        ev.task_event,
        extra_metadata=extra_metadata,
        wait_recall=wait_recall,
    )


def _is_at_message(item: object) -> bool:
    return isinstance(item, Message) and item.type == 'at'


def _remove_private_mentions(message: SendMessage) -> SendMessage:
    # 私聊没有「@」语义，部分平台遇到 at 片段会直接发送失败或原样显示用户 ID。
    # 这里连带删除紧随 at 的换行：调用方普遍写成 [at(x), '\n', 正文]，
    # 只删 at 会在消息首行留下空行。
    items: list[object] = list(message) if isinstance(message, list) else [message]
    result: list[object] = []
    skip_linebreak = False
    for item in items:
        if _is_at_message(item):
            skip_linebreak = True
            continue
        if skip_linebreak and isinstance(item, str) and item in ('\n', '\r\n'):
            skip_linebreak = False
            continue
        skip_linebreak = False
        result.append(item)

    # 保持调用方传入的容器类型：单条消息传字符串，列表仍是列表，
    # 否则下游按列表遍历时会退化成逐字符处理。
    if isinstance(message, list):
        return result
    return result[0] if result else ''


def _adapt_mentions_for_platform(bot: Bot, message: SendMessage) -> SendMessage:
    if bot.ev.user_type == 'direct':
        return _remove_private_mentions(message)
    return message


async def _safe_send(
    bot: Bot,
    message: SendMessage,
    *args: object,
    **kwargs: object,
) -> list[str] | None:
    # 仅在特征明确的 hook 兼容错误上降级：其余 AttributeError 原样抛出，
    # 避免把真实缺陷掩盖成「发送成功但用户没收到」。
    adapted = _adapt_mentions_for_platform(bot, message)
    try:
        return await bot.send(adapted, *args, **kwargs)
    except AttributeError as exc:
        if not _is_xwuid_group_activity_hook_error(exc):
            raise
        logger.warning(f'{LOG_PREFIX} 检测到 XWUID BotHook 兼容问题，改用底层发送: {exc}')
        return await _target_send_without_bot_hooks(bot, adapted, *args, **kwargs)


async def _send_loli_text(bot: Bot, text: str, *args: object, **kwargs: object) -> list[str] | None:
    # 保留独立入口：萝莉/正太文本发送将来若需单独调整（如追加固定尾巴），
    # 调用方无需改动；同时避免调用方直接依赖 _safe_send 的私有名。
    return await _safe_send(bot, text, *args, **kwargs)


async def _send_shota_text(bot: Bot, text: str, *args: object, **kwargs: object) -> list[str] | None:
    return await _safe_send(bot, text, *args, **kwargs)
