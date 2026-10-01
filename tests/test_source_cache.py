"""异步来源缓存的回归测试：守护 TTL、失败短缓存、并发合并与有界 LRU 四条契约。

图库列表等上游资源在零点高峰会被大量并发命令同时请求，缓存一旦失去其中任一性质，
后果都不体现在返回值上而是体现在上游负载上：并发合并失效会把同一次列表请求放大数倍，
失败短缓存失效会让上游抖动升级为重试风暴，容量无界则会随来源种类增长持续占用内存。
三者都不会让功能报错，因此需要本文件以显式断言固定下来。
"""
import sys
import asyncio
import unittest
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_cache_class():
    # 以独立模块名加载：插件包依赖 gsuid_core，测试无法按包路径导入，只能直接执行
    # 模块文件；同时须与其它测试文件使用不同的模块名，否则 sys.modules 会复用先加载
    # 的那一份，跨文件共享类级状态。
    path = ROOT / 'TodayWaifu' / 'source_cache.py'
    spec = importlib.util.spec_from_file_location('todaywaifu_source_cache', path)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load source cache module')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.AsyncSourceCache


AsyncSourceCache = _load_cache_class()


class SourceCacheTests(unittest.TestCase):
    def test_concurrent_loads_share_one_task(self) -> None:
        # 并发合并是保护上游的唯一手段：八个并发请求只能对应一次真实加载。
        # 缓存命中后同样不得再次加载——命中路径若回落到 loader，缓存就只降延迟而
        # 不降低上游 QPS，来源限流的风险依旧存在。
        async def run() -> None:
            cache = AsyncSourceCache[int](ttl_seconds=60, max_entries=2)
            calls = 0

            async def load() -> int:
                nonlocal calls
                calls += 1
                await asyncio.sleep(0.01)
                return 42

            values = await asyncio.gather(*(cache.get('roles', load) for _ in range(8)))
            self.assertEqual(values, [42] * 8)
            self.assertEqual(calls, 1)
            self.assertEqual(await cache.get('roles', load), 42)
            self.assertEqual(calls, 1)

        asyncio.run(run())

    def test_failed_load_is_temporarily_cached(self) -> None:
        # 失败必须按更短的独立 TTL 缓存，且二次调用仍向调用方抛出：上游不可用时，
        # 每个并发命令各发一次请求会形成重试风暴；把异常转成空结果或吞掉又会掩盖故障。
        async def run() -> None:
            cache = AsyncSourceCache[int](ttl_seconds=60, error_ttl_seconds=60)
            calls = 0

            async def load() -> int:
                nonlocal calls
                calls += 1
                raise RuntimeError('source unavailable')

            with self.assertRaisesRegex(RuntimeError, 'source unavailable'):
                await cache.get('roles', load)
            with self.assertRaisesRegex(RuntimeError, 'source unavailable'):
                await cache.get('roles', load)
            self.assertEqual(calls, 1)

        asyncio.run(run())

    def test_lru_is_bounded_and_invalidation_clears_entries(self) -> None:
        # 容量必须有界，否则来源种类增长会让内存随条目累积；失效须能释放条目，
        # 否则上游数据变更后旧结果会一直驻留到 TTL 到期。
        # size 只统计成功条目，故这里同时是对缓存账目口径的断言。
        async def run() -> None:
            cache = AsyncSourceCache[int](ttl_seconds=60, max_entries=2)

            async def load(value: int) -> int:
                return value

            await cache.get('a', lambda: load(1))
            await cache.get('b', lambda: load(2))
            await cache.get('c', lambda: load(3))
            self.assertEqual(cache.size, 2)
            cache.invalidate('b')
            self.assertEqual(cache.size, 1)
            cache.invalidate()
            self.assertEqual(cache.size, 0)

        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
