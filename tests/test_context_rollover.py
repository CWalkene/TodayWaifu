"""日期翻转路径的上下文回收契约：翻转必须即时生效，且不得破坏互斥与写入时效。

`_CONTEXT_REGISTRY` 与兼容缓存按 `(day, bot, group)` 缓存整群记录快照，原实现只依赖
间隔一小时的维护循环回收。零点到凌晨 1 点之间因此同时驻留「两天 × 全部活跃群」的数据，
数千群规模下会带来数百 MB 的额外占用与 GC 压力，且正好叠加在零点高峰上（316eed0）。

本文件锁定该修复的三条不变量：非当天快照被立即回收；翻转时仍在使用的锁不被回收，否则
同一分片的两个协程会各持一把锁同时进入临界区；已被回收快照的滞后 hydrate 不得回写。
后两条属并发正确性约束，仅靠外部行为观察难以复现，故以显式断言固定。
"""
import ast
import asyncio
import unittest
import dataclasses
from typing import Any
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'TodayWaifu'
REPOSITORY = PLUGIN / 'daily_repository.py'
STORE = PLUGIN / 'daily_store.py'


# 被测模块带相对导入，无法在测试进程内直接 import；改为按 AST 抽取所需语句后编译执行，
# 并在其前补上 `from __future__ import annotations`，使注解保持惰性求值，不触发对
# 未注入名称的解析。
def _exec_nodes(nodes: list[ast.stmt], globals_dict: dict[str, Any]) -> None:
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.Module(body=[future, *nodes], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(REPOSITORY), 'exec'), globals_dict)


def _load_repository() -> dict[str, Any]:
    """只抽取 ContextRegistry 及其键值类型，避免拉起整套插件运行时依赖。

    仅注入被抽取类真正引用的 asyncio 与 dataclass；其余名称依赖惰性注解保持未解析状态。
    """
    tree = ast.parse(REPOSITORY.read_text(encoding='utf-8'))
    wanted = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name in {'ContextKey', 'ContextSnapshot', 'ContextRegistry'}
    ]
    globals_dict: dict[str, Any] = {'asyncio': asyncio, 'dataclass': dataclasses.dataclass}
    _exec_nodes(wanted, globals_dict)
    return globals_dict


repository = _load_repository()
ContextKey = repository['ContextKey']
ContextRegistry = repository['ContextRegistry']


class DropStaleDaysTests(unittest.TestCase):
    # 日期取字面量而非系统当天：翻转行为涉及跨日判定，若依赖真实时钟，测试会在零点前后
    # 出现与实现无关的漂移。
    def setUp(self) -> None:
        self.registry = ContextRegistry()
        self.yesterday = ContextKey('2026-09-23', 'bot1', 'group1')
        self.today = ContextKey('2026-09-24', 'bot1', 'group1')
        self.other_group_today = ContextKey('2026-09-24', 'bot1', 'group2')

    def test_drops_yesterday_and_keeps_today(self) -> None:
        # 三个 key 分别覆盖「前一天」「当天同群」「当天异群」：回收必须以完整 key 判定，
        # 既不能按天误伤当天快照，也不能跨群连带清除仍在使用的上下文。
        self.registry.put(self.yesterday, {'wives': {'u1': {'name': 'A'}}})
        self.registry.put(self.today, {'wives': {'u1': {'name': 'B'}}})
        self.registry.put(self.other_group_today, {'wives': {}})

        dropped = self.registry.drop_stale_days('2026-09-24')

        self.assertEqual(dropped, 1)
        self.assertIsNone(self.registry.get(self.yesterday), '上一天的快照必须被回收')
        self.assertIsNotNone(self.registry.get(self.today), '当天快照不能被误删')
        self.assertIsNotNone(self.registry.get(self.other_group_today))

    def test_keeps_locks_so_concurrent_callers_share_the_same_object(self) -> None:
        """翻转瞬间可能仍有协程持有前一天 key 的锁，回收锁会破坏互斥。

        锁被移除后，同一分片的两个协程会各自新建锁对象并同时进入临界区，随后互相覆盖
        对方的快照写入。锁交由维护循环在确认无人使用后回收。
        """
        lock_before = self.registry.lock_for(self.yesterday)
        self.registry.put(self.yesterday, {'wives': {}})

        self.registry.drop_stale_days('2026-09-24')

        self.assertIs(self.registry.lock_for(self.yesterday), lock_before, '锁不能在翻转时被回收')

    def test_generations_are_reset_so_stale_writes_cannot_republish(self) -> None:
        self.registry.put(self.yesterday, {'wives': {}})
        generation_before = self.registry.generation(self.yesterday)
        self.assertGreater(generation_before, 0)

        self.registry.drop_stale_days('2026-09-24')

        # 翻转时可能还有协程在 hydrate 上一天的快照；若只删除缓存而不递增 generation，
        # 该协程完成后会依据旧代际把过期快照重新发布回注册表。
        self.assertFalse(
            self.registry.put(self.yesterday, {'wives': {'u1': {}}}, generation_before),
            '滞后的 hydrate 必须被 generation 比较挡下',
        )
        self.assertIsNone(self.registry.get(self.yesterday))

    def test_no_op_when_nothing_is_stale(self) -> None:
        # 无过期项时必须返回 0：调用方以此判断是否需要记录回收日志，非零返回值也意味着
        # 一次无谓的全表扫描。
        self.registry.put(self.today, {'wives': {}})
        self.assertEqual(self.registry.drop_stale_days('2026-09-24'), 0)

    def test_clears_finished_inflight_tasks(self) -> None:
        # 已完成的 hydrate 任务不再需要被 generation 拦截，但若长期滞留在 inflight 中，
        # 该表会随日期推移持续增长，成为另一条缓慢泄漏路径。
        async def run() -> None:
            task: asyncio.Task[dict] = asyncio.ensure_future(asyncio.sleep(0, result={'wives': {}}))
            self.registry.inflight[self.yesterday] = task
            await task
            self.registry.drop_stale_days('2026-09-24')
            self.assertNotIn(self.yesterday, self.registry.inflight)

        asyncio.run(run())


class RolloverWiringTests(unittest.TestCase):
    # 以下四项以源码文本断言接线方式：翻转检测属于热路径上的横切逻辑，若被移到维护循环
    # 或换成重量级回收，行为测试不会失败，但零点内存与互斥约束会静默退化。
    def test_loading_a_context_triggers_the_rollover_check(self) -> None:
        source = STORE.read_text(encoding='utf-8')
        body = source[source.index('async def _load_daily_context('):source.index('async def _save_daily_records(')]
        self.assertIn('_roll_over_context_day(key.day)', body, '每次 hydrate 都要检查日期翻转')

    def test_rollover_is_a_single_cheap_comparison_on_the_hot_path(self) -> None:
        source = STORE.read_text(encoding='utf-8')
        fn = source[source.index('def _roll_over_context_day('):source.index('async def _load_daily_context(')]
        # 每次读取上下文都会经过该函数，同一天必须短路返回，不能每次都遍历缓存键
        self.assertIn('if _LAST_CONTEXT_DAY == day:', fn)
        self.assertIn('return 0', fn)

    def test_rollover_uses_drop_stale_days_not_full_prune(self) -> None:
        """必须调用只回收快照的 drop_stale_days，而非会连带回收锁与代次的 prune。

        prune 假定调用时已无协程使用旧日期键，与翻转瞬间的真实状态相反。
        """
        source = STORE.read_text(encoding='utf-8')
        fn = source[source.index('def _roll_over_context_day('):source.index('async def _load_daily_context(')]
        self.assertIn('_CONTEXT_REGISTRY.drop_stale_days(day)', fn)
        self.assertNotIn('_CONTEXT_REGISTRY.prune(', fn)

    def test_compat_cache_is_pruned_by_day_prefix(self) -> None:
        # 兼容缓存的键形如 `{day}:{bot}:{group}`，只能按日期前缀清理；按对象身份遍历会漏掉
        # 已被其他地方持有引用的旧键。
        source = STORE.read_text(encoding='utf-8')
        fn = source[source.index('def _roll_over_context_day('):source.index('async def _load_daily_context(')]
        self.assertIn("key.startswith(f'{day}:')", fn)


if __name__ == '__main__':
    unittest.main()
