"""TodayWaifu 的高峰期状态仓储协调层。

按 `(日期, Bot, 群)` 维度把上下文拆分为独立分片，各分片自带锁、快照与在飞任务，
使不同群之间的读写互不阻塞；分片数量随活跃群数增长，故回收策略由 `ContextRegistry`
显式承担，而非依赖进程级缓存。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .payloads import DailyContext


# 上下文分片的复合键。冻结后可作为字典键使用，并要求三者同时相等才算同一分片；
# 日期参与键，跨天的旧分片才能被识别为陈旧数据，而不必依赖时间戳比较。
@dataclass(frozen=True)
class ContextKey:
    day: str
    bot_id: str
    group_id: str

    @property
    def cache_key(self) -> str:
        return f'{self.day}:{self.bot_id}:{self.group_id}'


# 带代次号的上下文快照。值与代次绑定存放，读取方可先取快照再提交，提交时凭代次
# 判断期间是否已被他人刷新，从而避免以旧值覆盖新值。
@dataclass(frozen=True)
class ContextSnapshot:
    generation: int
    value: DailyContext


class ContextRegistry:
    """管理按上下文分片的锁、缓存和进行中的 hydrate 任务。

    锁一经创建即长期保留，只有维护循环在确认无人使用后才回收：翻转或淘汰路径若一并
    移除锁，同一分片的两个协程会各自新建锁对象，互斥随即失效。
    """

    def __init__(self) -> None:
        self.locks: dict[ContextKey, asyncio.Lock] = {}
        self.cache: dict[ContextKey, ContextSnapshot] = {}
        self.inflight: dict[ContextKey, asyncio.Task[DailyContext]] = {}
        self._generations: dict[ContextKey, int] = {}

    def lock_for(self, key: ContextKey) -> asyncio.Lock:
        lock = self.locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self.locks[key] = lock
        return lock

    def generation(self, key: ContextKey) -> int:
        return self._generations.get(key, 0)

    def put(self, key: ContextKey, value: DailyContext, generation: int | None = None) -> bool:
        """发布成功提交的快照；旧 generation 不能覆盖较新的快照。

        返回 `False` 表示本次提交已被更新的一次超越而丢弃，调用方据此区分「写入成功」
        与「写入过期」，无需回读缓存。传 `generation=None` 表示无条件发布，供初始化等
        不存在并发写入者的路径使用。
        """
        current = self.generation(key)
        if generation is not None and generation < current:
            return False
        next_generation = max(current, generation or 0) + 1
        self._generations[key] = next_generation
        self.cache[key] = ContextSnapshot(next_generation, value)
        return True

    def get(self, key: ContextKey) -> DailyContext | None:
        snapshot = self.cache.get(key)
        return snapshot.value if snapshot is not None else None

    def snapshot(self, key: ContextKey) -> ContextSnapshot | None:
        return self.cache.get(key)

    def invalidate(self, key: ContextKey) -> None:
        self._generations[key] = self.generation(key) + 1
        self.cache.pop(key, None)

    def drop_stale_days(self, current_day: str) -> int:
        """日期翻转时立即丢弃非当天的上下文快照，返回丢弃数量。

        快照按 `(day, bot, group)` 缓存整群记录。若只靠维护循环回收（默认间隔 1 小时），
        零点到凌晨 1 点之间内存里会同时驻留**两天 × 全部活跃群**的上下文。

        **只动快照，不动锁**：翻转瞬间可能仍有协程持有前一天 key 的锁，
        此时回收锁会让两个协程拿到不同的锁对象、同时进入临界区。
        锁留给维护循环在一小时后回收，那时已经没有协程还在用前一天的 key。
        """
        dropped = 0
        stale_keys = {key for key in self.cache if key.day != current_day}
        stale_keys.update(key for key in self.inflight if key.day != current_day)
        for key in stale_keys:
            self.cache.pop(key, None)
            # 递增而不是删除 generation，阻止进行中的旧 hydrate 回写快照。
            self._generations[key] = self.generation(key) + 1
            dropped += 1
        for key, task in tuple(self.inflight.items()):
            if task.done():
                self.inflight.pop(key, None)
        return dropped

    def prune(self, current_day: str) -> None:
        # 维护循环的完整回收路径，同时清理快照、代次与无人持有的锁。
        # 与 drop_stale_days 的差异在于本方法可安全回收锁：调用时已假定没有协程仍在
        # 使用旧日期的键，因此不得放在热路径的翻转检测中，否则会破坏互斥。
        for key in tuple(self.cache):
            if key.day != current_day:
                self.cache.pop(key, None)
                self._generations.pop(key, None)
        for key, task in tuple(self.inflight.items()):
            if task.done():
                self.inflight.pop(key, None)
        for key, lock in tuple(self.locks.items()):
            if lock.locked() or key in self.cache or key in self.inflight:
                continue
            self.locks.pop(key, None)
            self._generations.pop(key, None)
