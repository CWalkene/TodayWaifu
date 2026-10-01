"""本地候选缓存的回归测试：全量目录扫描必须被 TTL 缓存包住，且失败结果不得入缓存。

本地候选来自 `rglob` 全量扫盘，代价随图库规模增长。图库不可用时每一次发送失败都会经
`_find_local_role_image` 回退至此；若缺少缓存，图库一挂便以「每次发送一次全量扫盘」的
频率耗尽插件线程池，回退路径本身演变为新的过载源。

本文件同时锁定缓存的失效方向：上传或删除图片必须主动清空缓存，否则新图要等到 TTL 到期
才可见；扫描失败（多为目录暂时不可用）不得写入缓存，否则目录恢复后仍持续返回失败结果。
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'TodayWaifu'
ROLES = PLUGIN / 'roles.py'


class LocalCandidateCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = ROLES.read_text(encoding='utf-8')

    def _body(self, start_marker: str, end_marker: str) -> str:
        """取出函数体并剥掉 docstring（说明文字里会提到 rglob 等旧细节）。"""
        block = self.source[self.source.index(start_marker):self.source.index(end_marker)]
        function = ast.parse(block).body[0]
        return '\n'.join(
            ast.unparse(node)
            for node in function.body
            if not (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            )
        )

    def test_local_candidates_are_cached_with_ttl(self) -> None:
        # 缓存键带 role_mode：各模式的目录根不同，共用键会让一个模式的结果被另一个模式读到。
        # TTL 到期才重扫，因此用户新增图片的可见延迟上界即 CACHE_TTL_SECONDS。
        body = self._body('def _load_local_candidates(', 'def _scan_local_candidates(')
        self.assertIn("cache_key = f'local:{role_mode}'", body)
        self.assertIn('CANDIDATE_CACHE.get(cache_key)', body)
        self.assertIn('CACHE_TTL_SECONDS', body)
        self.assertIn('CANDIDATE_CACHE[cache_key] = (now, result)', body)

    def test_the_scan_itself_is_separate_so_the_cache_can_wrap_it(self) -> None:
        # 扫描与缓存分层，缓存函数体内不得直接扫盘：否则缓存一旦被绕过或失效，
        # 调用方将无法通过单独调用 `_scan_local_candidates` 强制刷新。
        self.assertIn('def _scan_local_candidates(', self.source)
        body = self._body('def _load_local_candidates(', 'def _scan_local_candidates(')
        self.assertNotIn('rglob', body)
        self.assertNotIn('_collect_role_candidates(', body)

    def test_failures_are_not_cached(self) -> None:
        """目录暂时不可用等失败结果不得写入缓存，否则目录恢复后仍持续返回失败。"""
        body = self._body('def _load_local_candidates(', 'def _scan_local_candidates(')
        self.assertIn('if result[0]:', body, '只缓存成功结果')

    def test_upload_invalidates_the_cache(self) -> None:
        """上传或删除图片必须使本地候选缓存失效，否则新图要等到 TTL 到期才出现。"""
        invalidation = (PLUGIN / 'invalidation.py').read_text(encoding='utf-8')
        self.assertIn('CANDIDATE_CACHE.clear()', invalidation)

    def test_scan_keeps_its_original_behaviour(self) -> None:
        # 拆出扫描函数只为便于加缓存，行为必须逐项保持不变：角色对照表、资源根与候选
        # 收集三个环节任一丢失，都会使候选集静默变空而非报错。
        body = self._body('def _scan_local_candidates(', 'def _load_nte_local_candidates(')
        for marker in ('_resolve_role_map_path', '_resolve_role_pile_root', '_collect_role_candidates'):
            self.assertIn(marker, body, marker)
        # 返回结构不能变（用原始源码判断，ast.unparse 会给元组补括号）：
        # (None, 原因) 表示扫描失败，((), None) 表示确实无角色，调用方据此区分提示文案
        raw = self.source[
            self.source.index('def _scan_local_candidates('):self.source.index('def _load_nte_local_candidates(')
        ]
        self.assertIn('return candidates, None', raw)


if __name__ == '__main__':
    unittest.main()
