"""按主机熔断的轻量断路器。

图库不可用时，若不设熔断，每个用户的抽签都会各自发起完整重试链，使已经过载的
线程池与上游同时承受叠加压力（重试风暴）。连续失败达到阈值后直接快速失败一段时间，
可将上游负载立刻降至零、为其留出恢复窗口，同时让插件立即降级到本地图库，
而不是让每个请求继续同步等待。

本模块只依赖标准库，可独立加载（测试用 importlib 直接加载）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from collections.abc import Callable


@dataclass
class _BreakerState:
    consecutive_failures: int = 0
    open_until: float = 0.0
    tripped_count: int = 0


class CircuitBreaker:
    """按 key（通常为主机名）熔断；冷却期内 `allow()` 返回 False。

    按 key 而非全局隔离，是为了避免单个主机不可用牵连其它主机。
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # 阈值下限取 1：若允许 0，则任何一次失败都会立刻熔断，退化为「禁用上游」。
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self.clock = clock
        self._states: dict[str, _BreakerState] = {}

    def _state(self, key: str) -> _BreakerState:
        state = self._states.get(key)
        if state is None:
            state = _BreakerState()
            self._states[key] = state
        return state

    def allow(self, key: str) -> bool:
        """是否放行本次请求。冷却结束后放行**一个**探测请求（半开）。

        判定成功后一并清空 `open_until`（而非仅放行本次），使后续请求恢复正常通过；
        代价是探测请求失败前可能存在一小段全部放行的窗口，这是为省去额外状态机的
        有意取舍。
        """
        state = self._states.get(key)
        if state is None or state.open_until <= 0.0:
            return True
        if self.clock() >= state.open_until:
            state.open_until = 0.0
            return True
        return False

    def is_open(self, key: str) -> bool:
        """纯查询，不产生半开副作用。

        供可观测性使用：若这里复用 `allow()`，仅查询指标就会提前解除半开状态。
        """
        state = self._states.get(key)
        return state is not None and state.open_until > self.clock()

    def retry_after(self, key: str) -> float:
        """距离冷却结束还有多少秒；未熔断时为 0。"""
        state = self._states.get(key)
        if state is None or state.open_until <= 0.0:
            return 0.0
        return max(0.0, state.open_until - self.clock())

    def record_success(self, key: str) -> None:
        state = self._state(key)
        # 成功即清零连续计数并解除熔断：失败次数必须连续累积才有统计意义，
        # 否则长期运行下的零星失败会逐渐逼近阈值并造成误熔断。
        state.consecutive_failures = 0
        state.open_until = 0.0

    def record_failure(self, key: str) -> None:
        state = self._state(key)
        state.consecutive_failures += 1
        if state.consecutive_failures >= self.failure_threshold:
            state.open_until = self.clock() + self.cooldown_seconds
            state.tripped_count += 1

    def tripped_count(self, key: str) -> int:
        state = self._states.get(key)
        return state.tripped_count if state is not None else 0

    def reset(self) -> None:
        self._states.clear()
