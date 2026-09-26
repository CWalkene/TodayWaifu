"""写合并：同一瞬间的写入要合成一条多值 upsert，且**一行都不能丢**。

GsCore 默认 SQLite，所有写排一个进程级单写者闸门（实测约 250 写/秒）。
零点高峰逐条提交会把闸门压满，而命令协程在等写时仍占着 Core 的命令额度。
"""
import ast
import sys
import types
import asyncio
import tempfile
import unittest
import functools
import importlib.util
from typing import Any
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'TodayWaifu'
STORE = PLUGIN / 'daily_store.py'


def _load_coalescer(record_cls: Any) -> dict[str, Any]:
    """抽出写合并相关定义单独跑（daily_store 依赖 gsuid_core）。"""
    tree = ast.parse(STORE.read_text(encoding='utf-8'))
    wanted: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == '_WriteBatch':
            wanted.append(node)
        elif isinstance(node, ast.AsyncFunctionDef) and node.name in {
            '_flush_write_batch',
            'flush_pending_writes',
        }:
            wanted.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in {
            '_consume_batch_exception',
            '_current_batch',
            'pending_write_count',
        }:
            wanted.append(node)
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.Module(body=[future, *wanted], type_ignores=[])
    ast.fix_missing_locations(module)
    globals_dict: dict[str, Any] = {
        'asyncio': asyncio,
        'logger': __import__('logging').getLogger('test'),
        'DailyWifeRecord': record_cls,
        '_PENDING_BATCH': None,
        'RoleRecordValue': dict,
    }
    exec(compile(module, str(STORE), 'exec'), globals_dict)
    return globals_dict


def _load_models_with_sqlite() -> tuple[Any, Any, Any]:
    """Load models.py against tiny Core stubs and a real async SQLite database."""
    from sqlmodel import Field, SQLModel
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    maker_box: dict[str, Any] = {}

    def with_session(fn: Any) -> Any:
        @functools.wraps(fn)
        async def wrapped(cls: Any, *args: Any, **kwargs: Any) -> Any:
            async with maker_box['maker']() as session:
                try:
                    result = await fn(cls, session, *args, **kwargs)
                    await session.commit()
                    return result
                except BaseException:
                    await session.rollback()
                    raise

        return wrapped

    def with_read_session(fn: Any) -> Any:
        @functools.wraps(fn)
        async def wrapped(cls: Any, *args: Any, **kwargs: Any) -> Any:
            async with maker_box['maker']() as session:
                return await fn(cls, session, *args, **kwargs)

        return wrapped

    class BaseModel(SQLModel):
        id: int | None = Field(default=None, primary_key=True)
        bot_id: str = Field(index=True)
        user_id: str = Field(index=True)

    class _Logger:
        def info(self, message: str) -> None:
            pass

    class _Site:
        def register_admin(self, cls: Any) -> Any:
            return cls

    class _PageSchema:
        def __init__(self, **kwargs: Any) -> None:
            pass

    def _decorator(**kwargs: Any) -> Any:
        def apply(fn: Any) -> Any:
            return fn

        return apply

    core_modules = {
        'gsuid_core': types.ModuleType('gsuid_core'),
        'gsuid_core.logger': types.ModuleType('gsuid_core.logger'),
        'gsuid_core.server': types.ModuleType('gsuid_core.server'),
        'gsuid_core.webconsole': types.ModuleType('gsuid_core.webconsole'),
        'gsuid_core.webconsole.mount_app': types.ModuleType('gsuid_core.webconsole.mount_app'),
        'gsuid_core.utils': types.ModuleType('gsuid_core.utils'),
        'gsuid_core.utils.database': types.ModuleType('gsuid_core.utils.database'),
        'gsuid_core.utils.database.startup': types.ModuleType('gsuid_core.utils.database.startup'),
        'gsuid_core.utils.database.base_models': types.ModuleType('gsuid_core.utils.database.base_models'),
    }
    core_modules['gsuid_core.logger'].logger = _Logger()
    core_modules['gsuid_core.server'].on_core_start_before = _decorator
    mount_app = core_modules['gsuid_core.webconsole.mount_app']
    mount_app.PageSchema = _PageSchema
    mount_app.GsAdminModel = object
    mount_app.site = _Site()
    core_modules['gsuid_core.utils.database.startup'].exec_list = []
    base_models = core_modules['gsuid_core.utils.database.base_models']
    base_models.BaseModel = BaseModel
    base_models.engine = None
    base_models.with_session = with_session
    base_models.with_read_session = with_read_session

    old_modules = {name: sys.modules.get(name) for name in core_modules}
    sys.modules.update(core_modules)
    temp = tempfile.TemporaryDirectory()
    engine = create_async_engine(f'sqlite+aiosqlite:///{Path(temp.name) / "atomicity.db"}')
    maker_box['maker'] = async_sessionmaker(engine, expire_on_commit=False)
    module_name = f'todaywaifu_models_atomic_{id(temp)}'
    spec = importlib.util.spec_from_file_location(module_name, PLUGIN / 'models.py')
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load models module')
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    async def initialize() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    async def dispose() -> None:
        await engine.dispose()
        temp.cleanup()
        sys.modules.pop(module_name, None)
        for name, old in old_modules.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old

    return module, initialize, dispose


class _FakeRecord:
    """记录每次落库调用，用于断言合并行为。"""

    def __init__(self) -> None:
        self.upsert_calls: list[list[tuple[str, ...]]] = []
        self.delete_calls: list[list[tuple[str, ...]]] = []
        self.apply_calls: list[tuple[list[tuple[str, ...]], list[tuple[str, ...]]]] = []
        self.fail_next = False

    async def apply_rows(
        self,
        rows: list[tuple[str, ...]],
        deletes: list[tuple[str, ...]],
    ) -> None:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError('boom')
        self.apply_calls.append((list(rows), list(deletes)))
        if rows:
            self.upsert_calls.append(list(rows))
        if deletes:
            self.delete_calls.append(list(deletes))


def _key(group: str, user: str = 'u1') -> tuple[str, str, str, str, str]:
    return ('2026-09-24', 'bot1', group, 'wives', user)


class CoalescingTests(unittest.TestCase):
    def test_sqlite_flush_rolls_back_delete_when_upsert_fails(self) -> None:
        async def run() -> None:
            models, initialize, dispose = _load_models_with_sqlite()
            await initialize()
            try:
                seed_key = ('2026-09-24', 'bot1', 'g1', 'wives', 'u1')
                seed_value = {'name': '保留', 'image': 'keep.png', 'updated_at': 1}
                await models.DailyWifeRecord.upsert_rows([(*seed_key, seed_value)])

                original = models.DailyWifeRecord.__dict__['_upsert_rows']

                async def fail_upsert(cls: Any, session: Any, rows: Any) -> None:
                    raise RuntimeError('injected upsert failure')

                models.DailyWifeRecord._upsert_rows = classmethod(fail_upsert)
                try:
                    g = _load_coalescer(models.DailyWifeRecord)
                    batch = g['_current_batch']()
                    new_key = ('2026-09-24', 'bot1', 'g1', 'wives', 'u2')
                    batch.add(new_key, {'name': '新记录', 'updated_at': 2})
                    batch.drop(seed_key)
                    with self.assertRaises(RuntimeError):
                        await batch.ensure_task()
                finally:
                    models.DailyWifeRecord._upsert_rows = original

                self.assertEqual(
                    await models.DailyWifeRecord.get_record(*seed_key),
                    seed_value,
                )
                self.assertIsNone(
                    await models.DailyWifeRecord.get_record(
                        '2026-09-24', 'bot1', 'g1', 'wives', 'u2'
                    )
                )
            finally:
                await dispose()

        asyncio.run(run())

    def test_simultaneous_writes_share_one_transaction(self) -> None:
        record = _FakeRecord()

        async def run() -> None:
            g = _load_coalescer(record)
            batch = g['_current_batch']()
            for i in range(25):
                batch.add(_key(f'g{i}'), {'name': f'角色{i}'})
            await batch.ensure_task()

        asyncio.run(run())
        self.assertEqual(len(record.upsert_calls), 1, '25 个群同时写应只提交一次')
        self.assertEqual(len(record.upsert_calls[0]), 25, '一行都不能丢')

    def test_writes_arriving_after_the_snapshot_start_a_new_batch(self) -> None:
        """关键竞态：快照之后到达的写入必须开新批，不能被静默丢弃。"""
        record = _FakeRecord()

        async def run() -> None:
            g = _load_coalescer(record)
            current = g['_current_batch']
            flush = g['_flush_write_batch']

            first = current()
            first.add(_key('g1'), {'name': 'A'})
            task = first.ensure_task()
            # 让 flush 走到「摘除当前批」之后
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            # 此时应已开新批
            second = current()
            self.assertIsNot(second, first, '快照后必须开新批')
            second.add(_key('g2'), {'name': 'B'})
            await second.ensure_task()
            await task
            self.assertIsNot(flush, None)

        asyncio.run(run())
        self.assertEqual(len(record.upsert_calls), 2)
        self.assertEqual(record.upsert_calls[0][0][2], 'g1')
        self.assertEqual(record.upsert_calls[1][0][2], 'g2')

    def test_all_waiters_see_a_flush_failure(self) -> None:
        """一个批次失败必须让**所有**等待者都拿到异常，不能有人以为写成功了。"""
        record = _FakeRecord()
        record.fail_next = True

        async def run() -> None:
            g = _load_coalescer(record)
            batch = g['_current_batch']()
            batch.add(_key('g1'), {'name': 'A'})
            task = batch.ensure_task()
            with self.assertRaises(RuntimeError):
                await task
            with self.assertRaises(RuntimeError):
                await task

        asyncio.run(run())

    def test_later_value_wins_within_a_batch(self) -> None:
        record = _FakeRecord()

        async def run() -> None:
            g = _load_coalescer(record)
            batch = g['_current_batch']()
            batch.add(_key('g1'), {'name': '旧'})
            batch.add(_key('g1'), {'name': '新'})
            await batch.ensure_task()

        asyncio.run(run())
        self.assertEqual(len(record.upsert_calls[0]), 1)
        self.assertEqual(record.upsert_calls[0][0][5], {'name': '新'})

    def test_delete_then_write_same_key_keeps_the_write(self) -> None:
        record = _FakeRecord()

        async def run() -> None:
            g = _load_coalescer(record)
            batch = g['_current_batch']()
            batch.drop(_key('g1'))
            batch.add(_key('g1'), {'name': 'A'})
            await batch.ensure_task()

        asyncio.run(run())
        self.assertEqual(record.delete_calls, [])
        self.assertEqual(len(record.upsert_calls[0]), 1)

    def test_write_then_delete_same_key_keeps_the_delete(self) -> None:
        record = _FakeRecord()

        async def run() -> None:
            g = _load_coalescer(record)
            batch = g['_current_batch']()
            batch.add(_key('g1'), {'name': 'A'})
            batch.drop(_key('g1'))
            await batch.ensure_task()

        asyncio.run(run())
        self.assertEqual(record.upsert_calls, [])
        self.assertEqual(len(record.delete_calls[0]), 1)

    def test_deletes_and_writes_can_share_one_atomic_batch(self) -> None:
        record = _FakeRecord()

        async def run() -> None:
            g = _load_coalescer(record)
            batch = g['_current_batch']()
            batch.add(_key('g1'), {'name': 'A'})
            batch.drop(_key('g2'))
            await batch.ensure_task()

        asyncio.run(run())
        self.assertEqual(len(record.apply_calls), 1)
        rows, deletes = record.apply_calls[0]
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(deletes), 1)

    def test_pending_count_reports_the_open_batch(self) -> None:
        record = _FakeRecord()

        async def run() -> None:
            g = _load_coalescer(record)
            self.assertEqual(g['pending_write_count'](), 0)
            batch = g['_current_batch']()
            batch.add(_key('g1'), {'name': 'A'})
            batch.drop(_key('g2'))
            self.assertEqual(g['pending_write_count'](), 2)

        asyncio.run(run())

    def test_exception_is_consumed_when_nobody_awaits(self) -> None:
        """调用方被取消时不能留下 'exception was never retrieved' 告警。"""
        record = _FakeRecord()
        record.fail_next = True

        async def run() -> None:
            g = _load_coalescer(record)
            batch = g['_current_batch']()
            batch.add(_key('g1'), {'name': 'A'})
            task = batch.ensure_task()
            await asyncio.sleep(0.05)
            self.assertTrue(task.done())
            self.assertIsNotNone(task.exception())

        asyncio.run(run())


class WiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = STORE.read_text(encoding='utf-8')

    def test_all_three_write_paths_go_through_the_coalescer(self) -> None:
        for name in ('_save_daily_record', '_save_daily_records', '_delete_daily_record'):
            start = self.source.index(f'async def {name}(')
            body = self.source[start:self.source.index('\n\n\n', start)]
            self.assertIn('_submit_writes(', body, name)

    def test_submit_is_atomic_between_get_and_await(self) -> None:
        """`_current_batch` → `add` → `ensure_task` 之间不能有 await，否则会丢行。"""
        body = self.source[
            self.source.index('async def _submit_writes('):self.source.index('async def _save_daily_records(')
        ]
        before_await = body[:body.index('await batch.ensure_task()')]
        self.assertNotIn('await ', before_await.replace('async def', ''))

    def test_shutdown_flushes_pending_writes(self) -> None:
        shared = (PLUGIN / 'shared.py').read_text(encoding='utf-8')
        hook = shared[shared.index('async def _stop_blocking_executor_on_shutdown('):]
        self.assertIn('await flush_pending_writes()', hook)

    def test_models_expose_atomic_multi_context_apply(self) -> None:
        models = (PLUGIN / 'models.py').read_text(encoding='utf-8')
        self.assertIn('async def apply_rows(', models)
        self.assertIn('async def _upsert_rows(', models)
        self.assertIn('async def _delete_rows(', models)

    def test_flush_routes_both_operations_through_apply_rows(self) -> None:
        source = STORE.read_text(encoding='utf-8')
        start = source.index('async def _flush_write_batch(')
        end = source.index('\n\n\n', start)
        body = source[start:end]
        self.assertIn('await DailyWifeRecord.apply_rows(rows, deletes)', body)
        self.assertNotIn('DailyWifeRecord.delete_rows(', body)
        self.assertNotIn('DailyWifeRecord.upsert_rows(', body)


if __name__ == '__main__':
    unittest.main()
