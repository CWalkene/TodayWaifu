"""状态聚合的降频与 COUNT 化回归测试。

`daily_store` 每次保存都会调用 `invalidate_status_cache()`，而旧实现下失效即等于下一次
读取必然重算，且 `count_daily_records` 是「全天所有群的全表扫描 + 逐行 `json.loads`」。
只要网页控制台的状态页开着轮询，高峰期就退化为每次轮询触发一次全表扫描。
提交 e877248 同时修复了两侧：聚合下推到带索引的 COUNT 查询，失效改为「只标记过期」，
重算时机则由最小间隔 `STATUS_MIN_RECOMPUTE_SECONDS` 决定。

本文件守护三条契约：未过期或未写入时不得重算；超过最小间隔后必须重算；COUNT 查询的
判定列必须与旧实现（排除 stolen_from / gifted_from / safe）口径一致。
"""
import ast
import asyncio
import unittest
from typing import Any
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _is_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )
PLUGIN = ROOT / 'TodayWaifu'
STATUS = PLUGIN / 'status.py'
MODELS = PLUGIN / 'models.py'


class _FakeClock:
    def __init__(self) -> None:
        self.now = 5000.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeRecord:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def count_daily_records(self, day: str, buckets: tuple[str, ...]) -> dict[str, int]:
        self.calls.append(day)
        return {'wife': 1, 'loli': 2, 'shota': 0, 'husband': 0}


def _load_status_globals(clock: _FakeClock, calls: list[str]) -> dict[str, Any]:
    # 只抽出与状态缓存相关的函数单独执行：status.py 还依赖 PIL 与 gsuid_core，
    # 整模块导入会在无 Core 的测试环境中失败。注入可控时钟是为了让「最小重算间隔」
    # 可被精确跨越，而不必真实等待 30 秒。
    tree = ast.parse(STATUS.read_text(encoding='utf-8'))
    wanted = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {'_today_record_counts', 'invalidate_status_cache'}
    ]
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.Module(body=[future, *wanted], type_ignores=[])
    ast.fix_missing_locations(module)
    globals_dict: dict[str, Any] = {
        'asyncio': asyncio,
        'time': clock,
        '_today_key': lambda: '2026-09-24',
        '_daily_bucket_name': lambda kind: kind,
        'DailyWifeRecord': _FakeRecord(calls),
        'STATUS_MIN_RECOMPUTE_SECONDS': 30.0,
        '_STATUS_INFLIGHT': None,
        '_STATUS_CACHE': None,
        '_STATUS_COMPUTED_AT': 0.0,
        '_STATUS_STALE': True,
    }
    exec(compile(module, str(STATUS), 'exec'), globals_dict)
    return globals_dict


class StatusThrottleTests(unittest.TestCase):
    def test_repeated_reads_hit_the_cache(self) -> None:
        # 缓存的基本职责：TTL 内的重复读取必须复用同一份聚合结果，否则状态页轮询
        # 会退化成每次访问都打一次数据库。
        clock, calls = _FakeClock(), []

        async def run() -> None:
            g = _load_status_globals(clock, calls)
            counts = g['_today_record_counts']
            first = await counts()
            second = await counts()
            self.assertEqual(first, second)
            self.assertEqual(len(calls), 1, '未过期时不应重复查询')

        asyncio.run(run())

    def test_write_invalidation_does_not_recompute_immediately(self) -> None:
        """最小重算间隔必须吸收写入抖动，使该间隔内的轮询不触发聚合查询。

        写入与轮询的速率互不相关：零点高峰每秒可能发生数十次写入，而控制台轮询同样
        密集。若失效立即导致重算，二者会相乘为「每次轮询一次全表扫描」。
        """
        clock, calls = _FakeClock(), []

        async def run() -> None:
            g = _load_status_globals(clock, calls)
            counts, invalidate = g['_today_record_counts'], g['invalidate_status_cache']
            await counts()
            self.assertEqual(len(calls), 1)

            for _ in range(20):
                invalidate()
                await counts()
            self.assertEqual(len(calls), 1, '最小重算间隔内不应重算')

        asyncio.run(run())

    def test_recomputes_once_the_minimum_interval_elapsed(self) -> None:
        # 降频不得演变为永久陈旧：标记过期后，一旦跨越最小间隔就必须真正重算，
        # 否则状态页数字会停留在进程启动时的快照上。此处以 29 秒与 31 秒分别
        # 卡在间隔两侧，验证边界是「大于等于间隔才重算」。
        clock, calls = _FakeClock(), []

        async def run() -> None:
            g = _load_status_globals(clock, calls)
            counts, invalidate = g['_today_record_counts'], g['invalidate_status_cache']
            await counts()
            invalidate()
            clock.advance(29.0)
            await counts()
            self.assertEqual(len(calls), 1)

            clock.advance(2.0)
            await counts()
            self.assertEqual(len(calls), 2, '超过最小间隔后必须重算')

        asyncio.run(run())

    def test_no_recompute_when_nothing_was_written(self) -> None:
        # 时间流逝本身不构成重算理由：无写入时数据不可能变化，重算纯属浪费。
        # 该断言同时固定了「失效标记」而非「TTL」才是重算的触发条件。
        clock, calls = _FakeClock(), []

        async def run() -> None:
            g = _load_status_globals(clock, calls)
            counts = g['_today_record_counts']
            await counts()
            clock.advance(3600.0)
            await counts()
            self.assertEqual(len(calls), 1, '没有写入就不该重算，哪怕过了很久')

        asyncio.run(run())

    def test_invalidate_only_marks_stale(self) -> None:
        # 失效只置位标记、保留快照，是为了让间隔内的读取仍能返回上一次结果。
        # 若失效同时清空快照，间隔内的读取将无值可返，等于没有降频。
        clock, calls = _FakeClock(), []

        async def run() -> None:
            g = _load_status_globals(clock, calls)
            await g['_today_record_counts']()
            self.assertIsNotNone(g['_STATUS_CACHE'])
            g['invalidate_status_cache']()
            self.assertIsNotNone(g['_STATUS_CACHE'], 'invalidate 只标记过期，不丢弃快照')
            self.assertTrue(g['_STATUS_STALE'])

        asyncio.run(run())


class CountQueryTests(unittest.TestCase):
    @staticmethod
    def _count_body() -> str:
        """抽取 `count_daily_records` 的函数体源码，用于对实现方式做结构断言。

        剥离 docstring 是必需的：本测试以「函数体内不出现 payload / json.loads」为判据，
        而改写后的 docstring 恰好会解释为何不再解析 payload，若一并纳入匹配就会误伤。
        """
        tree = ast.parse(MODELS.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or node.name != 'DailyWifeRecord':
                continue
            for item in node.body:
                if isinstance(item, ast.AsyncFunctionDef) and item.name == 'count_daily_records':
                    return '\n'.join(
                        ast.unparse(stmt) for stmt in item.body if not _is_docstring(stmt)
                    )
        raise AssertionError('count_daily_records not found')

    def test_count_uses_sql_aggregation_not_a_payload_scan(self) -> None:
        # 断言实现方式而非结果，是因为旧实现在结果上完全正确，只在代价上不可接受；
        # 若有人以「更易读」为由改回逐行解析，功能测试不会报警，只有此处会。
        body = self._count_body()
        self.assertIn('func.count()', body)
        self.assertIn('.group_by(cls.bucket)', body)
        # 一旦 payload 重新出现在函数体内，就说明扫描与逐行解析又回来了
        self.assertNotIn('cls.payload', body)
        self.assertNotIn('json.loads', body)

    def test_count_filters_on_indexed_columns(self) -> None:
        body = self._count_body()
        # 过滤条件必须落在 name / origin 冗余列上才能走索引；origin 由写入路径的
        # _row_from_value 维护，因此该列与 payload 同步是查询正确性的前提
        self.assertIn("cls.name != ''", body)
        self.assertIn("cls.origin == 'self'", body)

    def test_origin_column_matches_the_old_exclusion_rules(self) -> None:
        """`origin == 'self'` 必须等价于旧实现的排除规则，否则计数会整体偏移。

        旧实现逐行检查 payload，排除被抢（stolen_from）、被赠送（gifted_from）与
        标记安全（safe）三类记录。冗余列只要漏掉其中任一条，状态页的「今日老婆」
        等数字就会高于实际持有量，且偏差只在特定交互发生后出现，极难察觉。
        """
        source = MODELS.read_text(encoding='utf-8')
        origin = source[source.index('def _record_origin('):source.index('def split_context_key(')]
        for marker in ('stolen_from', 'gifted_from', 'safe'):
            self.assertIn(marker, origin, marker)

    def test_min_recompute_interval_is_bounded(self) -> None:
        # 常量取值同时受两个方向约束：过小等于没有降频，过大则让状态页在写入后
        # 长时间显示陈旧数字。此处显式固化上下界，防止后续调参越过任一侧。
        tree = ast.parse((PLUGIN / 'constants.py').read_text(encoding='utf-8'))
        values: dict[str, Any] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                try:
                    code = compile(ast.Expression(node.value), '<constants>', 'eval')
                    values[node.targets[0].id] = eval(code, {'__builtins__': {}}, {})
                except (NameError, TypeError, ValueError, SyntaxError):
                    continue
        interval = values['STATUS_MIN_RECOMPUTE_SECONDS']
        self.assertGreaterEqual(interval, 5.0, '间隔太小等于没降频')
        self.assertLessEqual(interval, 300.0, '间隔太大会让状态页数字明显滞后')


if __name__ == '__main__':
    unittest.main()
