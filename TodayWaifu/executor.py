"""TodayWaifu 专用阻塞 IO 线程池。

`asyncio.to_thread` 借用的是**整个进程共享**的默认 executor，CPython 默认线程数是
``min(32, cpu + 4)`` —— 4 核机器只有 8 个线程。而本插件高峰期同时在飞的下载与读盘
请求远超这个数（仅图片下载信号量就有 8 个槽），会让 Core 自身和其它插件的任务一起
排在同一个池后等待，表现为「整个 gscore 卡顿」。

因此所有阻塞 IO 一律走本模块自己的池子，实现故障隔离：插件过载时只耗尽自身线程，
不向外扩散。

本模块只依赖标准库，可独立加载（测试用 importlib 直接加载）。
"""
from __future__ import annotations

import atexit
import asyncio
from typing import TypeVar
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

T = TypeVar('T')

# 必须**大于**并发下载信号量（8），给读缓存与回退扫描留出余量。
# 若池容量小于信号量，信号量放行的请求会积压在池队列里，线程池取代网络成为吞吐上限：
# 真机压测中池=4 而信号量=8 时，排空时间由 13s 恶化到 25s。
# 12 = 8 个下载 + 4 个给 read_url_cache / 本地图回退扫描。
MAX_BLOCKING_WORKERS = 12

_EXECUTOR: ThreadPoolExecutor | None = None


def _executor() -> ThreadPoolExecutor:
    global _EXECUTOR
    if _EXECUTOR is None:
        _EXECUTOR = ThreadPoolExecutor(
            max_workers=MAX_BLOCKING_WORKERS,
            thread_name_prefix='twf-io',
        )
    return _EXECUTOR


async def run_blocking(func: Callable[..., T], *args: object) -> T:
    """在线程池里执行阻塞函数，不占用 Core 的默认 executor。

    调用方不得在事件循环线程上直接执行同步阻塞调用：那会冻结整个循环，令所有并发
    命令、心跳与超时回调一起停摆。经本函数投递到专用池，可保证事件循环始终可调度。
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor(), func, *args)


def shutdown_blocking_executor() -> None:
    """关闭线程池（Core 退出或插件重载时调用），可重复调用。

    置空全局引用后重新调用 `run_blocking` 会按需重建池子，因此重载场景无需额外的
    初始化钩子；`wait=False` 配合 `cancel_futures=True` 令关闭不阻塞调用方，代价是
    已提交但未开始的任务被丢弃。
    """
    global _EXECUTOR
    executor = _EXECUTOR
    _EXECUTOR = None
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


def blocking_executor_workers() -> int:
    """当前线程池的线程数（可观测性用）。

    上报的是配置值而非池内实际存活线程数，因为容量是否大于下载信号量才是需要持续
    核对的约束；后者可通过 `blocking_executor_workers` 指标与 `inflight_image_downloads`
    的对比来判断。
    """
    return MAX_BLOCKING_WORKERS


atexit.register(shutdown_blocking_executor)
