"""状态页指标：向 Core 控制台暴露「今日」各分类的聚合计数。

计数走一次数据库聚合查询并缓存，记录写入只把快照标记为过期而不立即重算，
使重算频率与写入频率解耦：否则高峰期每次写入都会让控制台的下一轮轮询
触发一次全表聚合，读放大随写入量线性增长。
"""
from __future__ import annotations

import time
import asyncio

from PIL import Image

from gsuid_core.status.plugin_status import register_status

from .shared import (
    HELP_ICON_PATH,
    STATUS_MIN_RECOMPUTE_SECONDS,
    DailyWifeRecord,
    _today_key,
    _load_wife_data,
    _daily_bucket_name,
)
from .payloads import DailyContext

# 聚合快照的进程内状态，四者语义互相独立：
# `_STATUS_INFLIGHT` 让并发读取共享同一次查询（单飞合并，避免缓存未命中时的惊群）；
# `_STATUS_CACHE` 与计算时的日期键绑定，跨日必须整体失效而不能沿用前一日数值；
# `_STATUS_COMPUTED_AT` 取单调时钟，不受系统时间回拨影响；
# `_STATUS_STALE` 仅表示期间出现过写入，是否重算由最小重算间隔决定。
_STATUS_INFLIGHT: asyncio.Task[dict[str, int]] | None = None
_STATUS_CACHE: tuple[str, dict[str, int]] | None = None
_STATUS_COMPUTED_AT = 0.0
_STATUS_STALE = True


def _is_countable_daily_record(raw: object) -> bool:
    # 被抢与被送出的记录已归属他人（stolen_from / gifted_from），标记为安全的
    # 记录不计入图库口径（safe）——三者若一并统计，会让「今日老婆」等数字
    # 高于实际持有量；名称缺失的占位记录同理不计。
    if not isinstance(raw, dict):
        return False
    name = raw.get('name')
    if not isinstance(name, str) or not name.strip():
        return False
    return not (raw.get('stolen_from') or raw.get('gifted_from') or raw.get('safe'))


def _daily_record_count(day_data: object, bucket_name: str) -> int:
    # 按桶名统计已 hydrate 的旧结构数据，状态指标本身已改走下方的一次聚合查询，
    # 此函数不再处于热路径，故对缺失或形状不符的层级一律返回 0 而不抛异常，
    # 避免历史快照中的个别脏记录导致整块状态页不可用。
    if not isinstance(day_data, dict):
        return 0

    count = 0
    for context in day_data.values():
        if not isinstance(context, dict):
            continue
        bucket = context.get(bucket_name)
        if not isinstance(bucket, dict):
            continue
        count += sum(1 for raw in bucket.values() if _is_countable_daily_record(raw))
    return count


async def _today_data() -> DailyContext:
    """兼容旧的状态读取辅助函数；指标本身使用下方的一次聚合查询。

    旧结构以 `days[日期]` 为入口，这里保持同一读取口径；升级过程中磁盘上
    可能残留形状不符的历史快照，故各层级缺失时降级为空桶，由调用方按
    「今日无记录」处理，而不是让类型错误冒泡到状态页。
    """
    data = await _load_wife_data()
    days = data.get('days')
    # 保留旧数据结构的 days.get(_today_key()) 兼容口径。
    today = days.get(_today_key()) if isinstance(days, dict) else {}
    return today if isinstance(today, dict) else {}


async def _today_record_counts() -> dict[str, int]:
    """一次数据库聚合产出全部状态指标，避免每个回调各自 hydrate 全部上下文。

    结果按日期缓存：缓存未命中时才发起聚合，命中且未过期时直接复用，
    因此控制台轮询的数据库开销与轮询频率无关，只与最小重算间隔有关。
    """
    global _STATUS_INFLIGHT, _STATUS_CACHE, _STATUS_COMPUTED_AT, _STATUS_STALE
    day = _today_key()
    cached = _STATUS_CACHE
    if cached is not None and cached[0] == day:
        # 写入刚提交但尚未达到最小重算间隔时，先返回上一次的聚合结果：
        # 高峰期若每次写入都重算，控制台的每一轮轮询都会打一次数据库聚合，
        # 读放大随写入量线性增长，故以短暂的数据滞后换取稳定的读开销。
        # 没有任何写入时不因时间流逝重算——数值不会自行变化，重算纯属空转。
        fresh_enough = (
            not _STATUS_STALE
            or time.monotonic() - _STATUS_COMPUTED_AT < STATUS_MIN_RECOMPUTE_SECONDS
        )
        if fresh_enough:
            return cached[1]

    task = _STATUS_INFLIGHT
    if task is None:
        bucket_names = (
            _daily_bucket_name('wife'),
            _daily_bucket_name('loli'),
            _daily_bucket_name('shota'),
            _daily_bucket_name('husband'),
        )

        async def load() -> dict[str, int]:
            return await DailyWifeRecord.count_daily_records(day, bucket_names)

        task = asyncio.create_task(load())
        _STATUS_INFLIGHT = task
    try:
        # 并发调用方共享同一 task：仅首个调用方创建查询，其余仅等待结果。
        counts = await task
    finally:
        # 仅在等待结束时清除在途标记（含 task 抛异常的情形）；查询失败时
        # 缓存与时间戳保持原样，下次调用重新发起聚合，避免把一次瞬时故障
        # 固化成长期空结果。
        if task.done() and _STATUS_INFLIGHT is task:
            _STATUS_INFLIGHT = None
    _STATUS_CACHE = (day, counts)
    _STATUS_COMPUTED_AT = time.monotonic()
    _STATUS_STALE = False
    return counts


def invalidate_status_cache() -> None:
    """在每日记录成功提交后把聚合快照标记为过期。

    这里**只标记、不丢弃**：快照仍可继续服务落在最小重算间隔内的读取，
    而真正的重算由 `_today_record_counts` 按 `STATUS_MIN_RECOMPUTE_SECONDS`
    的最小间隔决定，避免高峰期每次写入都让网页控制台的下一次轮询触发一次
    全表聚合；丢弃快照则会让紧随写入之后的读取立刻穿透到数据库。
    """
    global _STATUS_STALE
    _STATUS_STALE = True


async def _today_record_count(kind: str) -> int:
    counts = await _today_record_counts()
    return int(counts.get(_daily_bucket_name(kind), 0))


async def get_today_wife_count() -> int:
    return await _today_record_count('wife')


async def get_today_loli_count() -> int:
    return await _today_record_count('loli')


async def get_today_shota_count() -> int:
    return await _today_record_count('shota')


async def get_today_husband_count() -> int:
    return await _today_record_count('husband')


# 注册发生在导入期：图标必须在进程启动阶段即可读，故此处直接解码并转 RGBA，
# 让状态页注册所需的数据与格式要求一次性满足，避免运行期再处理图像。
register_status(
    Image.open(HELP_ICON_PATH).convert('RGBA'),
    'TodayWaifu',
    {
        '今日老婆': get_today_wife_count,
        '今日萝莉': get_today_loli_count,
        '今日正太': get_today_shota_count,
        '今日老公': get_today_husband_count,
    },
)
