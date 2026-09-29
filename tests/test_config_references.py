"""守卫：代码引用的配置键、以及每日记录桶必须真实存在。

两类退化都曾真实发生，且都被命令包装器吞掉、只在日志里留痕：

1. `daily_wife_config.py` 会删掉一批旧键（*GalleryApiUrl / *LoliApiUrl 等），
   但代码里还留着 `or _cfg('旧键')`。它们永远取不到值 —— `get_config` 走兜底
   分支返回假配置，而且每次调用都往日志里打一条 warning。
2. `TodayWaifu/kind_metadata.py` 的 `*_key` 字段是「间接」引用，字面量守卫扫不到。
   normal 模式曾在这里引用 4 个不存在的键（DailyWifeNormalRobEnabled 等）。
3. `_get_today_context` 曾漏建 `normal_wives` 桶，导致「普通老婆」整条链路
   `KeyError` 且用户收不到任何回复。

这里把三条都固化成契约。
"""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'TodayWaifu'
READERS = ('_cfg', '_cfg_bool', '_cfg_int', '_cfg_probability')


def _exec_module(path: Path) -> dict[str, object]:
    """求值一个无外部依赖的模块，返回它的全局命名空间。"""
    namespace: dict[str, object] = {}
    exec(  # noqa: S102 - 这些模块只含字面量定义，直接求值是刻意的
        compile(path.read_text(encoding='utf-8'), path.stem, 'exec'),
        namespace,
    )
    return namespace


def _literal_constant(path: Path, name: str) -> object:
    """取出模块级的字面量赋值。

    `constants.py` 顶部有相对 import，不能整文件 exec，故只按 AST 取需要的常量。
    """
    tree = ast.parse(path.read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and node.value is not None
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f'{path.name} 里没有字面量常量 {name}')


def _defined_keys() -> set[str]:
    tree = ast.parse((ROOT / 'config_default.py').read_text(encoding='utf-8-sig'))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == 'CONFIG_DEFAULT' and isinstance(node.value, ast.Dict):
                return {
                    key.value for key in node.value.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)
                }
    raise AssertionError('config_default.py 里没有 CONFIG_DEFAULT')


def _extract_context_builder():
    """抽出 `_get_today_context` 单独执行，只需 _today_key / _context_key 两个桩。"""
    path = PACKAGE / 'daily_store.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == '_get_today_context'
    )
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.Module(body=[future, function], type_ignores=[])
    ast.fix_missing_locations(module)

    constants = _literal_constant(PACKAGE / 'constants.py', 'ALL_DAILY_RECORD_KINDS')
    metadata = _exec_module(PACKAGE / 'kind_metadata.py')['DAILY_KIND_METADATA']
    namespace: dict[str, object] = {
        'ALL_DAILY_RECORD_KINDS': constants,
        '_daily_bucket_name': lambda kind: metadata[kind].bucket,
        '_today_key': lambda: '2026-01-01',
        '_context_key': lambda ev: 'onebot:G1',
    }
    exec(compile(module, str(path), 'exec'), namespace)  # noqa: S102
    return lambda: namespace['_get_today_context']({'days': {}}, object())


class ConfigReferenceTests(unittest.TestCase):
    def test_every_cfg_literal_is_a_defined_key(self) -> None:
        defined = _defined_keys()
        offenders: list[str] = []
        for path in sorted(PACKAGE.glob('*.py')):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                    continue
                if node.func.id not in READERS or not node.args:
                    continue
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    if first.value not in defined:
                        offenders.append(f'{path.name}:{node.lineno} -> {first.value}')
        self.assertEqual(offenders, [], '存在引用了未定义配置键的死引用')

    def test_legacy_keys_are_not_referenced_anywhere(self) -> None:
        """被 daily_wife_config 删除的旧键，代码里不应再出现。"""
        legacy = (
            'DailyWifeGalleryApiUrl',
            'DailyWifeNormalGalleryApiUrl',
            'DailyWifeLoliApiUrl',
            'DailyShotaGalleryApiUrl',
            'DailyWifePgrGalleryApiUrl',
            'DailyWifeRandomGalleryApiUrl',
        )
        source = '\n'.join(path.read_text(encoding='utf-8') for path in sorted(PACKAGE.glob('*.py')))
        for key in legacy:
            self.assertNotIn(key, source, f'{key} 已被删除，不应再被引用')

    def test_kind_metadata_keys_are_defined(self) -> None:
        """DailyKindMetadata 的 `*_key` 字段是间接引用，必须同样存在。

        空字符串表示该模式不参与抢/送（见 kind_metadata 顶部说明），跳过。
        """
        defined = _defined_keys()
        tree = ast.parse((PACKAGE / 'kind_metadata.py').read_text(encoding='utf-8'))
        offenders: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != 'DailyKindMetadata':
                continue
            for keyword in node.keywords:
                if keyword.arg is None or not keyword.arg.endswith('_key'):
                    continue
                if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                    key = keyword.value.value
                    if key and key not in defined:
                        offenders.append(f'kind_metadata.py:{node.lineno} {keyword.arg} -> {key}')
        self.assertEqual(offenders, [], 'DailyKindMetadata 引用了未定义的配置键')


class DailyContextBucketTests(unittest.TestCase):
    def test_every_record_kind_bucket_is_created(self) -> None:
        """`_get_today_context` 必须为 ALL_DAILY_RECORD_KINDS 的每个模式建好桶。

        调用方用 `context[bucket].get(...)` 直接下标，缺桶就 KeyError。
        """
        constants = _literal_constant(PACKAGE / 'constants.py', 'ALL_DAILY_RECORD_KINDS')
        metadata = _exec_module(PACKAGE / 'kind_metadata.py')['DAILY_KIND_METADATA']
        context = _extract_context_builder()()

        missing = [
            metadata[kind].bucket
            for kind in constants
            if metadata[kind].bucket not in context
        ]
        self.assertEqual(missing, [], f'_get_today_context 未创建这些记录桶: {missing}')

    def test_non_record_buckets_are_still_created(self) -> None:
        """marry_members / rob_attempts / safe_wives 不属于记录模式，但同样被直接下标。"""
        context = _extract_context_builder()()
        for bucket in ('marry_members', 'rob_attempts', 'safe_wives'):
            self.assertIn(bucket, context)


if __name__ == '__main__':
    unittest.main()
