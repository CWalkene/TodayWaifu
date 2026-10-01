"""图库磁盘缓存总容量上限的回归测试：仅按天过期无法约束签名 URL 带来的增长。

图库列表接口返回的图片 URL 带有短期签名，签名轮换即产生新的缓存键，同一张图片会在
磁盘上留下多份副本。提交 1356627 之前，清理策略只有「按天过期」（30 天 TTL、每轮上限
1000 个文件），缓存总量因此无界增长，长期运行会把磁盘占满。

本文件守护三条契约：容量裁剪按 mtime 从旧到新执行且不误删预算内的条目；裁剪必须同步
维护已缓存 URL 索引与「0 表示不限制」的配置语义；容量上限必须真被维护循环接线调用。
"""
import os
import sys
import time
import tempfile
import unittest
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'TodayWaifu'
FILE_CACHE = PLUGIN / 'file_cache.py'


def _load_file_cache():
    # 以独立模块名加载：多个测试文件会各自加载同一份 file_cache.py，共用名字会让
    # sys.modules 中的先加载者被复用，模块级缓存状态因此在文件之间串扰
    spec = importlib.util.spec_from_file_location('todaywaifu_file_cache_budget', FILE_CACHE)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load file_cache')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


file_cache = _load_file_cache()


def _write(root: Path, url: str, size: int, age_seconds: float = 0.0) -> Path:
    path = file_cache.url_hash_cache_path(root, url)
    path.write_bytes(b'x' * size)
    if age_seconds:
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))
    return path


class CacheBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        # 索引是模块级全局状态，跨用例残留会让「索引同步」类断言失去判别力
        file_cache.clear_cached_url_index()

    def test_no_eviction_when_under_budget(self) -> None:
        # 反向边界：预算内的缓存必须原样保留，否则每轮维护都会造成无谓回源
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'https://x/a.png', 100)
            self.assertEqual(file_cache.enforce_cache_size_budget(root, max_bytes=10_000), 0)

    def test_evicts_oldest_first_until_under_budget(self) -> None:
        # 淘汰顺序即有效性顺序：被反复访问的图片 mtime 较新，按 mtime 淘汰等价于近似
        # LRU，因此刚写入（新 mtime）的条目必须留存下来
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'https://x/old.png', 400, age_seconds=300)
            _write(root, 'https://x/mid.png', 400, age_seconds=200)
            _write(root, 'https://x/new.png', 400, age_seconds=100)

            removed = file_cache.enforce_cache_size_budget(root, max_bytes=800)

            self.assertEqual(removed, 1)
            self.assertFalse(file_cache.url_hash_cache_path(root, 'https://x/old.png').exists())
            self.assertTrue(file_cache.url_hash_cache_path(root, 'https://x/new.png').exists())
            self.assertLessEqual(file_cache.cache_dir_bytes(root), 800)

    def test_evicts_enough_files_for_a_large_overflow(self) -> None:
        # 单次维护须一次退到预算内：若只删一个文件就返回，容量裁剪永远追不上增量，
        # 磁盘仍会缓慢增长
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(10):
                _write(root, f'https://x/{i}.png', 100, age_seconds=100 - i)
            removed = file_cache.enforce_cache_size_budget(root, max_bytes=250)
            self.assertGreaterEqual(removed, 8)
            self.assertLessEqual(file_cache.cache_dir_bytes(root), 250)

    def test_eviction_keeps_the_cached_url_index_in_sync(self) -> None:
        """被淘汰的图必须从索引里移除，否则抽签会一直挑到已经不存在的图。

        索引只增不减时，`prefer_cached_urls` 会把已删除的 URL 当作命中并纳入抽签
        候选集，命中该 URL 的请求仍要走网络回源——恰是索引本应消除的冷启动下载。
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'https://x/old.png', 400, age_seconds=300)
            file_cache.read_url_cache(root, 'https://x/old.png')
            self.assertTrue(file_cache.is_url_cached('https://x/old.png'))

            file_cache.enforce_cache_size_budget(root, max_bytes=10)
            self.assertFalse(file_cache.is_url_cached('https://x/old.png'))

    def test_zero_budget_means_unlimited(self) -> None:
        # 0 是「关闭该策略」的哨兵值而非「预算为零」；若按字面理解，全量淘汰会一次性
        # 清空缓存，把用户显式关闭容量的意图变成误删
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'https://x/a.png', 400)
            self.assertEqual(file_cache.enforce_cache_size_budget(root, max_bytes=0), 0)
            self.assertTrue(file_cache.url_hash_cache_path(root, 'https://x/a.png').exists())

    def test_skips_temp_and_hidden_files(self) -> None:
        # 临时文件是写入过程中的半成品，隐藏文件为其它用途保留：两者既不计入总量也
        # 不可被淘汰，否则会删掉正在下载中的内容
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'https://x/a.png', 100)
            (root / '.a.png.123.tmp').write_bytes(b'y' * 5000)
            (root / '.hidden').write_bytes(b'z' * 5000)

            self.assertEqual(file_cache.enforce_cache_size_budget(root, max_bytes=1000), 0)
            self.assertTrue((root / '.a.png.123.tmp').exists())

    def test_missing_directory_is_a_noop(self) -> None:
        # 目录缺失在首次启动或用户清理缓存后属正常状态，清理动作不应因此抛出异常、
        # 中断整个后台维护循环
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                file_cache.enforce_cache_size_budget(Path(tmp) / 'nope', max_bytes=100), 0
            )

    def test_cache_dir_bytes_reports_total(self) -> None:
        # 该函数是容量预算的唯一观测量；统计口径漏项会让裁剪判断与指标同时失真
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'https://x/a.png', 100)
            _write(root, 'https://x/b.png', 250)
            self.assertEqual(file_cache.cache_dir_bytes(root), 350)


class WiringTests(unittest.TestCase):
    def test_maintenance_enforces_the_budget(self) -> None:
        # 逐段截取而非整文件匹配：只有落在维护循环体内，容量裁剪才会被周期性执行
        shared = (PLUGIN / 'shared.py').read_text(encoding='utf-8')
        loop = shared[
            shared.index('async def _cache_maintenance_once('):shared.index('async def _cache_maintenance_loop(')
        ]
        self.assertIn('enforce_cache_size_budget', loop)
        self.assertIn('_gallery_cache_max_bytes()', loop)

    def test_budget_is_configurable_including_unlimited(self) -> None:
        # 上限必须是可运营的配置项而非硬编码常量：不同部署的磁盘容量差异极大
        config = (ROOT / 'config_default.py').read_text(encoding='utf-8')
        self.assertIn("'DailyWifeGalleryCacheMaxMB'", config)
        self.assertIn('设为 0 表示不限制', config)

        # 配置读取的取值语义与裁剪逻辑共同构成「不限制」契约，任一侧改变都会误删缓存
        shared = (PLUGIN / 'shared.py').read_text(encoding='utf-8')
        fn = shared[shared.index('def _gallery_cache_max_bytes('):shared.index('def _record_retention_days(')]
        self.assertIn('max(0, max_mb)', fn, '负数或 0 必须落到「不限制」而不是误删')
        self.assertIn('GALLERY_CACHE_MAX_MB', fn, '配置缺失时要回退到常量')


if __name__ == '__main__':
    unittest.main()
