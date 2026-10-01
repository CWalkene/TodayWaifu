from __future__ import annotations

import time
import asyncio
from typing import Generic, TypeVar
from collections import OrderedDict
from dataclasses import dataclass
from collections.abc import Callable, Awaitable

T = TypeVar('T')
Loader = Callable[[], Awaitable[T]]


# 失败条目独立存放而不写入值缓存：值缓存命中即返回，无法表达「这次调用应当抛出」，
# 且异常需要自己的短 TTL，与成功结果的 TTL 解耦。
@dataclass(frozen=True)
class _ErrorEntry:
    expires_at: float
    error: BaseException


class AsyncSourceCache(Generic[T]):
    """为资源列表提供 TTL、失败短缓存、并发合并和有界 LRU 缓存。

    设计取舍集中在三点：成功结果按 TTL 到期，失败结果按更短的 TTL 缓存以免上游抖动
    演变为重试风暴；同一 key 的并发请求共享一个在途任务，使上游只承受一次真实请求；
    容量按 LRU 封顶，避免来源种类无限增长时内存随之膨胀。

    未提供显式的取消接口：调用方若在等待期间被取消，只会取消自己的等待，不影响共享
    任务，其余等待者仍能拿到结果。
    """

    def __init__(
        self,
        ttl_seconds: float,
        max_entries: int = 32,
        error_ttl_seconds: float = 15.0,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.error_ttl_seconds = error_ttl_seconds
        self.max_entries = max_entries
        # 值缓存与失败缓存共用 max_entries，各自的淘汰互不影响对方的推进顺序。
        self._values: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._errors: OrderedDict[str, _ErrorEntry] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[T]] = {}
        # 代次表用于识别「失效之前发起、失效之后才完成」的加载，防止陈旧结果写回。
        self._epochs: dict[str, int] = {}

    async def get(self, key: str, loader: Loader[T]) -> T:
        # 统一用单调时钟计时：系统时间被回拨时不会让已过期的条目重新变成有效。
        now = time.monotonic()
        cached = self._values.get(key)
        if cached is not None:
            expires_at, value = cached
            if expires_at > now:
                self._values.move_to_end(key)
                return value
            # 惰性淘汰：读取时发现过期即移除，无需后台清理任务。
            self._values.pop(key, None)

        # 失败缓存优先于在途合并：上游刚失败过，此处直接抛出可避免立刻重试。
        error_entry = self._errors.get(key)
        if error_entry is not None:
            if error_entry.expires_at > now:
                self._errors.move_to_end(key)
                raise error_entry.error
            self._errors.pop(key, None)

        task = self._inflight.get(key)
        if task is None:
            epoch = self._epochs.get(key, 0)
            async def load() -> T:
                try:
                    value = await loader()
                except BaseException as exc:
                    # 取消不是上游故障，不写入失败缓存；仅在代次未变时记录，避免把
                    # 已失效请求的异常留给后续调用者。
                    if not isinstance(exc, asyncio.CancelledError) and epoch == self._epochs.get(key, 0):
                        self._errors[key] = _ErrorEntry(
                            time.monotonic() + self.error_ttl_seconds,
                            exc,
                        )
                        self._errors.move_to_end(key)
                        while len(self._errors) > self.max_entries:
                            self._errors.popitem(last=False)
                    raise
                # 期间发生过 invalidate：结果已过期，直接返回给本次等待者但不写入缓存。
                if epoch != self._epochs.get(key, 0):
                    return value
                self._errors.pop(key, None)
                self._values[key] = (time.monotonic() + self.ttl_seconds, value)
                self._values.move_to_end(key)
                while len(self._values) > self.max_entries:
                    self._values.popitem(last=False)
                return value

            task = asyncio.create_task(load())
            self._inflight[key] = task

        try:
            # 合并点：并发调用在同一个 Task 上等待，加载次数不随并发数增长。
            return await task
        finally:
            # 仅在任务已结束且仍是当前项时移除，防止被后续新任务覆盖后误删其登记。
            if task.done() and self._inflight.get(key) is task:
                self._inflight.pop(key, None)

    def invalidate(self, key: str | None = None) -> None:
        """清除指定源或全部缓存；正在进行的加载不被取消。

        不取消在途加载是刻意选择：取消会波及所有共享该任务的等待者，而它们在失效
        之前就已发出请求。改为递增代次，使这些加载在写回时被识别并放弃缓存写入，
        从而兼顾「失效立即生效」与「已发出的请求仍能返回结果」。
        """
        if key is None:
            self._values.clear()
            self._errors.clear()
            for inflight_key in tuple(self._inflight):
                self._epochs[inflight_key] = self._epochs.get(inflight_key, 0) + 1
            return
        self._values.pop(key, None)
        self._errors.pop(key, None)
        self._epochs[key] = self._epochs.get(key, 0) + 1

    def prune(self) -> None:
        # 按需清理已过期的成功与失败条目：由调用方决定时机，避免常驻后台任务。
        now = time.monotonic()
        for key, (expires_at, _) in tuple(self._values.items()):
            if expires_at <= now:
                self._values.pop(key, None)
        for key, error in tuple(self._errors.items()):
            if error.expires_at <= now:
                self._errors.pop(key, None)

    @property
    def size(self) -> int:
        # 已缓存的成功条目数；失败条目与在途任务不计入，便于按容量上限做断言。
        return len(self._values)
