"""零点高峰突发压测：在真机 Core 上复现「全员同时抽老婆」的并发链路。

本脚本依赖真实的 gsuid_core 运行环境，无法在插件仓库内独立执行。用法：

    cd <gsuid_core 仓库根>
    .venv/bin/python plugins/TodayWaifu/tests/burst_benchmark.py <plugin_parent_dir> [并发数] [warm] [db]

`plugin_parent_dir` 是**包含** `TodayWaifu` 包的那一层目录（通常是 `plugins/`）。
可选参数：`warm` 先灌满磁盘缓存，以复现 23:50 预热完成之后的 00:00 突发；
`db` 把真实的一次 `upsert_record` 写库计入命令路径。

它取代了此前的 `peak_benchmark.py`。后者只验证内存中 `AsyncSourceCache` 的并发
合并（100 个协程请求同一 key，`loader_calls=1`），并未复现零点链路，却输出了
「问题已优化」的结论，是早期多轮修复未能命中根因的认知来源之一。

压测覆盖四项指标，其中前两项才是命令相互阻塞的直接成因：

  1. `slot_occupancy_*`  命令协程持有 Core `CommandSemaphore` 额度的时长。
     框架直至协程**结束**才归还额度，故该值等价于「一条命令会阻塞其他命令多久」。
  2. `command_slot_wait_*` 一条**无关命令**取得额度所需的等待时间。
     额度耗尽时框架 `_process` 停止消费队列，该 bot 上全部插件一并受阻。
  3. `competing_probe_*`  其他插件经 `asyncio.to_thread` 执行阻塞 IO 的等待时长，
     用于量化插件占用 Core 默认线程池所引发的跨插件饥饿。
  4. `all_images_delivered_seconds` 用户视角的完成时刻（吞吐量），
     用于确认延迟优化未以牺牲吞吐为代价。

使用 `db` 时必须让 `prepare_db()` 复刻框架真实的 SQLite 初始化（WAL +
`synchronous=NORMAL`）。否则实测落在 `journal_mode=delete` + `synchronous=FULL`
之下，每次提交都触发 fsync，写入延迟被高估数倍。

真机 4 核、200 并发、冷缓存、0.5s/张图库的实测基线：

    指标                   修复前        修复后       修复后+预热
    命令占用额度 max       2081.6 ms     0.1 ms       0.2 ms
    无关命令等待额度 max   11410.8 ms    0.0 ms       0.0 ms
    命令延迟 p99           13033.4 ms    0.0 ms       0.2 ms
    其他插件阻塞 IO 等待   493.0 ms      17.9 ms      13.1 ms
    全部 200 张图送达      13.10 s       12.87 s      0.43 s

带上真实写库（`db`）后，剩余的瓶颈是框架的 SQLite 单写者闸门：

    指标                   修复前        修复后
    命令占用额度 max       2123.9 ms     195.8 ms
    无关命令等待额度 max   11455.2 ms    825.8 ms
    全部 200 张图送达      13.10 s       12.93 s
"""
from __future__ import annotations

import sys
import json
import time
import asyncio
import tempfile
import threading
from types import SimpleNamespace
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from concurrent.futures import ThreadPoolExecutor

PORT = 8899
IMAGE_DELAY = 0.5          # 单张图片的模拟网络耗时；须大于零，否则并发争用不可观测
COMMAND_SEMAPHORE = 25     # 对齐框架 CommandSemaphore 默认值，偏离将改变额度耗尽的临界并发
COMPETING_PROBE_INTERVAL = 0.05


class _Handler(BaseHTTPRequestHandler):
    payload = b'\x89PNG\r\n\x1a\n' + b'Z' * (256 * 1024)

    def do_GET(self) -> None:
        time.sleep(IMAGE_DELAY)
        body = type(self).payload
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


def start_server() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(('127.0.0.1', PORT), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


async def competing_probe(samples: list[float], stop: asyncio.Event) -> None:
    """以默认 executor 执行阻塞 IO，采样其他插件被线程池饥饿的程度。

    生产环境中插件与框架共用 Core 的默认线程池，图片下载长期占用该池时，
    其他插件的同步 IO 只能排在其后；该采样是本插件对跨插件可用性影响的观测口径。

    探针以固定间隔循环，故采样值上升直接对应线程池排队，而非探针自身开销。
    """
    while not stop.is_set():
        started = time.perf_counter()
        await asyncio.to_thread(time.sleep, 0.01)
        samples.append(time.perf_counter() - started)
        await asyncio.sleep(COMPETING_PROBE_INTERVAL)


async def command_slot_probe(waits: list[float], stop: asyncio.Event, sem: asyncio.Semaphore) -> None:
    """采样一条无关命令取得 Core 命令并发额度的等待时长。

    额度被占满时框架 `_process` 会停止消费队列，因此该等待时长反映的是该 bot 上
    所有插件的可用性，而非本插件自身的命令延迟；这是判断「是否会导致整个 Core 阻塞」的依据。

    探针与压测协程共用同一个信号量，从而与真实命令争抢同一份额度。
    """
    while not stop.is_set():
        started = time.perf_counter()
        async with sem:
            waits.append(time.perf_counter() - started)
        await asyncio.sleep(COMPETING_PROBE_INTERVAL)


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * ratio))
    return ordered[index]


async def prepare_db() -> object:
    """把框架的数据库层指向临时 SQLite，并建好 dailywiferecord 表。

    GsCore 默认 `db_type` 即为 SQLite，且所有写入都要排一个**进程级单写者闸门**，
    因此命令路径中的那次 upsert 必须一并压测；若绕过数据库，占用额度的时长会被低估。
    """
    from sqlmodel import SQLModel
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from TodayWaifu.TodayWaifu import models as M
    from gsuid_core.utils.database import base_models as BM

    db_path = Path(tempfile.mkdtemp()) / 'bench.db'
    # 必须复刻框架真实的 SQLite 初始化（base_models.py:165-226）：WAL + synchronous=NORMAL。
    # 仅创建引擎而不复现该步骤时，实测落在 journal_mode=delete + synchronous=FULL 之下，
    # 每次提交都触发 fsync，写入延迟被高估数倍，据此得出的结论会失真。
    BM._enable_sqlite_wal(str(db_path))
    engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}')
    event.listens_for(engine.sync_engine, 'connect')(BM._set_sqlite_connect_pragmas)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    BM.engine = engine
    BM.async_maker = maker
    BM._db_type = 'sqlite'
    BM._db_initialized = True
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: SQLModel.metadata.create_all(c, tables=[M.DailyWifeRecord.__table__])
        )
    return M


async def run(n_draws: int, plugin_parent: str, warm: bool = False, with_db: bool = False) -> dict[str, float]:
    sys.path.insert(0, plugin_parent)
    from TodayWaifu.TodayWaifu import gallery as G, senders as S

    cache_root = Path(tempfile.mkdtemp())
    G._gallery_image_cache_root = lambda: cache_root
    G._gallery_api_url = lambda: f'http://127.0.0.1:{PORT}/api/roles'
    S._download_image = G._download_image

    # 生产环境由 on_core_start_before 钩子启动投递 worker；压测进程没有该钩子，
    # 且发图链路已改为后台 worker 投递，故需在此手动补启动，否则送达时刻永远等不到。
    has_workers = hasattr(S, 'start_image_delivery_workers')
    if has_workers:
        S.start_image_delivery_workers()

    if with_db:
        from gsuid_core.models import Event
        from TodayWaifu.TodayWaifu import daily_store as store

        await prepare_db()
        events = [
            Event(bot_id='bot1', user_id=f'u{i}', group_id='g1', real_bot_id='bot1')
            for i in range(n_draws)
        ]
        record_value = {'name': '今汐', 'role_ids': ['1304'], 'image': 'x', 'record_type': 'role'}

    sent: list[int] = []

    class FakeBot:
        def __init__(self) -> None:
            self.ev = SimpleNamespace(user_type='group')

        async def send(self, message: object, *args: object, **kwargs: object) -> None:
            sent.append(len(message) if isinstance(message, list) else len(str(message)))

    bot = FakeBot()
    urls = [f'http://127.0.0.1:{PORT}/img/unique_{i}.png' for i in range(n_draws)]
    roles = [
        SimpleNamespace(name=f'角色{i}', role_ids=(str(1000 + i),), images=(urls[i],))
        for i in range(n_draws)
    ]

    if warm:
        # 复现 23:50 预热完成之后的 00:00：先把图片写入磁盘缓存，使突发阶段
        # 只度量命中路径；预热耗时不计入任何指标，避免与冷缓存口径混淆。
        async def warm_one(url: str) -> None:
            try:
                await G._download_image(url)
            except Exception:
                pass

        await asyncio.gather(*(warm_one(u) for u in urls))
        print(f'# 预热完成，缓存文件 {len(list(cache_root.iterdir()))} 个', file=sys.stderr)

    # 默认 executor 容量对齐真实机器（min(32, cpu+4)）：线程数放宽会掩盖线程池饥饿，
    # 收窄则会把本机 CPU 数量误当成框架缺陷。
    loop = asyncio.get_running_loop()
    workers = min(32, (len(__import__('os').sched_getaffinity(0)) or 1) + 4)
    loop.set_default_executor(ThreadPoolExecutor(max_workers=workers))

    sem = asyncio.Semaphore(COMMAND_SEMAPHORE)
    latencies: list[float] = []
    occupancies: list[float] = []
    probe_samples: list[float] = []
    slot_waits: list[float] = []
    stop = asyncio.Event()

    async def draw(index: int) -> None:
        started = time.perf_counter()
        async with sem:                       # 命令协程在此持有 Core 的并发额度
            # 自进入临界区起计时：持锁时长决定其他命令的排队深度，是 Core 过载的直接度量；
            # 包含额度等待的外层耗时只反映本命令的用户体验，两者不可混用。
            entered = time.perf_counter()
            if with_db:
                await store._save_daily_record(events[index], 'wives', f'u{index}', record_value)
            await S._send_role_image(
                bot, roles[index], urls[index], '文字', index, True, 'wife'
            )
            occupancies.append(time.perf_counter() - entered)
        latencies.append(time.perf_counter() - started)

    probe_a = asyncio.create_task(competing_probe(probe_samples, stop))
    probe_b = asyncio.create_task(command_slot_probe(slot_waits, stop, sem))

    started = time.perf_counter()
    await asyncio.gather(*(draw(i) for i in range(n_draws)))
    drain = time.perf_counter() - started

    stop.set()
    await asyncio.gather(probe_a, probe_b, return_exceptions=True)

    # 等待**全部图片真正送达**：内联发送与后台 worker 发送的命令协程结束时刻不同，
    # 唯有用户视角的完成时刻可跨版本比较；180s 上限用于避免投递回归时压测永久挂起。
    while len(sent) < n_draws and time.perf_counter() - started < 180:
        await asyncio.sleep(0.05)
    deliver_seconds = time.perf_counter() - started

    return {
        'draws': n_draws,
        'warm_cache': warm,
        'with_db_write': with_db,
        'default_executor_threads': workers,
        'drain_seconds': round(drain, 2),
        'all_images_delivered_seconds': round(deliver_seconds, 2),
        'slot_occupancy_p50_ms': round(percentile(occupancies, 0.50) * 1000, 1),
        'slot_occupancy_p99_ms': round(percentile(occupancies, 0.99) * 1000, 1),
        'slot_occupancy_max_ms': round(max(occupancies) * 1000, 1),
        'draw_p50_ms': round(percentile(latencies, 0.50) * 1000, 1),
        'draw_p99_ms': round(percentile(latencies, 0.99) * 1000, 1),
        'draw_max_ms': round(max(latencies) * 1000, 1),
        'competing_probe_max_ms': round(max(probe_samples or [0]) * 1000, 1),
        'competing_probe_p99_ms': round(percentile(probe_samples, 0.99) * 1000, 1),
        'command_slot_wait_max_ms': round(max(slot_waits or [0]) * 1000, 1),
        'messages_sent': len(sent),
        'cache_files_after_drain': len(list(cache_root.iterdir())),
    }


def main() -> int:
    plugin_parent = sys.argv[1]
    n_draws = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    flags = set(sys.argv[3:])
    server = start_server()
    try:
        result = asyncio.run(run(n_draws, plugin_parent, 'warm' in flags, 'db' in flags))
    finally:
        server.shutdown()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
