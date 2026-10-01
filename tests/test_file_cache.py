# 本模块守护 `TodayWaifu/file_cache.py` 的三条不变量：本地文件缓存以 (路径, mtime_ns,
# 大小) 判定命中，URL 缓存按「归一化后的 URL」内容寻址，TTL 清理只驱逐常规文件。
#
# 缓存层本身即为 0 点高峰卡死（46fd929）而引入：原先每次出图都要重新读盘或重新下载图库
# 图片，零点突发时磁盘与网络同时被压满。本文件锁定引入该层时所依赖的判据不被削弱——命中
# 判据必须包含 mtime，缓存键必须由归一化 URL 派生（签名轮换不得导致重复落盘，4de7304），
# 且 TTL 清理不得触碰并发写入中的临时文件，否则原子替换会落空。
#
# 因此断言普遍先篡改外部状态（替换 Path.read_bytes、回拨 mtime），以确认实现依据的是这些
# 判据本身，而非偶然的调用次序或文件系统恰好未发生变化。
#
# 被测模块不依赖 gsuid_core 与包内其它模块，故用 importlib 按文件路径独立加载，避免整套
# 测试被框架的导入环境绑住。
import os
import time
import unittest
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "TodayWaifu" / "file_cache.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("todaywaifu_file_cache", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load file_cache module")
    module = importlib.util.module_from_spec(spec)
    # 每个测试各自加载一份模块，从而获得彼此隔离的内存缓存；共享 sys.modules 中的单例
    # 会让上一个测试写入的条目影响本测试的淘汰与命中判断。
    spec.loader.exec_module(module)
    return module


class FileBytesCacheTests(unittest.TestCase):
    def test_hit_returns_same_bytes_without_reread(self) -> None:
        """命中缓存时只允许廉价的 stat 校验，不得再次读取文件内容。"""
        cache = _load_module()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "a.png"
            path.write_bytes(b"first")
            self.assertEqual(cache.read_file_bytes_cached(path), b"first")
            # stat 校验 mtime 是廉价系统调用，允许保留；只有 read_bytes 才是需要消除的
            # 磁盘读取，因此这里只拦截 read_bytes，命中时它必须不被调用。
            original_read = Path.read_bytes

            def fail_read(self, *args, **kwargs):
                if self == path:
                    raise AssertionError("命中缓存时不应再次 read_bytes")
                return original_read(self, *args, **kwargs)

            try:
                # 只在补丁生效的窗口内做第二次读取，避免失败分支中的其它读盘被误判为回归。
                Path.read_bytes = fail_read
                self.assertEqual(cache.read_file_bytes_cached(path), b"first")
            finally:
                # 即使断言失败也必须还原：Path.read_bytes 是全局补丁，泄漏会让后续测试
                # 以难以定位的方式失败。
                Path.read_bytes = original_read

    def test_mtime_change_invalidates_cache(self) -> None:
        cache = _load_module()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "b.png"
            path.write_bytes(b"old")
            self.assertEqual(cache.read_file_bytes_cached(path), b"old")
            # 间隔必须大于文件系统 mtime 的分辨率，否则两次写入落在同一时间戳上，
            # 测试会因环境差异而偶发通过（即真失效却未触发失效）。
            time.sleep(0.02)
            path.write_bytes(b"new-content-longer")
            self.assertEqual(cache.read_file_bytes_cached(path), b"new-content-longer")

    def test_lru_eviction_respects_max_entries(self) -> None:
        cache = _load_module()
        with TemporaryDirectory() as directory:
            # 写入量刻意超出上限 8 条：断言只要求不越界，从而给未来调整淘汰策略留出空间，
            # 同时防止缓存无上限增长耗尽核心进程内存。
            for index in range(cache.LOCAL_BYTES_CACHE_MAX_ENTRIES + 8):
                p = Path(directory) / f"{index}.png"
                p.write_bytes(b"x")
                cache.read_file_bytes_cached(p)
            # 断言的是内部字典规模：单看内存占用无法区分「未淘汰」与「淘汰后又写入」
            self.assertLessEqual(
                len(cache._LOCAL_BYTES_CACHE), cache.LOCAL_BYTES_CACHE_MAX_ENTRIES
            )


class UrlCacheTests(unittest.TestCase):
    def test_write_then_read_round_trip(self) -> None:
        cache = _load_module()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            url = "https://example.com/a.png"
            # 未写入前必须返回 None：调用方以此区分「缓存未命中」与「读到空数据」。
            self.assertIsNone(cache.read_url_cache(root, url))
            self.assertTrue(cache.write_url_cache(root, url, b"image-bytes"))
            self.assertEqual(cache.read_url_cache(root, url), b"image-bytes")

    def test_content_addressed_same_url_same_path(self) -> None:
        cache = _load_module()
        # 缓存键由 URL 派生且必须稳定：同 URL 命中同一文件，不同 URL 落不同文件；
        # 键一旦不稳定，磁盘会为同一张图片积累多份副本。
        p1 = cache.url_hash_cache_path(Path("/tmp/x"), "https://a/1.png")
        p2 = cache.url_hash_cache_path(Path("/tmp/x"), "https://a/1.png")
        p3 = cache.url_hash_cache_path(Path("/tmp/x"), "https://a/2.png")
        self.assertEqual(p1, p2)
        self.assertNotEqual(p1, p3)

    def test_expired_files_are_removed_but_temp_files_are_kept(self) -> None:
        cache = _load_module()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            expired = root / 'expired'
            fresh = root / 'fresh'
            temporary = root / '.expired.tmp'
            expired.write_bytes(b'old')
            fresh.write_bytes(b'new')
            temporary.write_bytes(b'tmp')
            # 只回拨 mtime 而不真正等待：TTL 由文件时间戳判定，等待真实时间会让测试变慢
            # 且不稳定。临时文件同样回拨，用以确认它因后缀被跳过，而非因为「还年轻」。
            old_time = time.time() - 100
            os.utime(expired, (old_time, old_time))
            os.utime(temporary, (old_time, old_time))
            removed = cache.clear_expired_files(root, 50)
            # 仅删除 1 个即证明新鲜文件与 `.tmp` 都被保留：`write_url_cache` 正依赖后者
            # 完成原子替换，误删会让写入端读到半截内容甚至写入失败。
            self.assertEqual(removed, 1)
            self.assertFalse(expired.exists())
            self.assertTrue(fresh.exists())
            self.assertTrue(temporary.exists())

        cache = _load_module()
        with TemporaryDirectory() as directory:
            # 空数据写缓存必须视为失败且不落盘：零字节文件会被读端当作命中而返回空图，
            # 因此这里要求返回 False，由调用方继续走网络路径。
            self.assertFalse(cache.write_url_cache(Path(directory), "https://a", b""))


if __name__ == "__main__":
    unittest.main()
