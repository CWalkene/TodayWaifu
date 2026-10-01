# 今日异环老婆（NTE）的接入契约：配置默认关闭、命令完成注册、候选池接线到本地目录。
#
# NTE 曾与鸣潮、战双共享「融合老婆」混合抽取（fc02294），该功能在 0b6469b 被整体移除，
# 故本文件同时锁定三件事：移除必须彻底（旧配置键与旧选取函数不得残留，否则控制台会出现
# 已废弃开关）、默认值必须保持关闭（升级不得擅自开启一项外部图库功能）、NTE 候选加载器
# 必须同时接线自定义立绘目录与默认立绘目录（缺失任一路径都会让部分角色无图可抽）。
import ast
import unittest
from typing import Any
from pathlib import Path
from tempfile import TemporaryDirectory
from dataclasses import dataclass

ROOT = Path(__file__).resolve().parents[1]


def _config_entries() -> list[tuple[str, str, str, ast.Call]]:
    # 以语法树枚举 config_default 中的配置项，而非导入该模块：导入会连带拉起 GsCore 配置
    # 注册流程，本文件只关心「键名、标题、默认值」三元组本身。
    source = (ROOT / 'config_default.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    entries: list[tuple[str, str, str, ast.Call]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and isinstance(value, ast.Call)
            ):
                continue
            title = ''
            description = ''
            if value.args and isinstance(value.args[0], ast.Constant):
                title = str(value.args[0].value)
            if len(value.args) > 1 and isinstance(value.args[1], ast.Constant):
                description = str(value.args[1].value)
            entries.append((key.value, title, description, value))
    return entries


def _bool_default(call: ast.Call) -> bool | None:
    # 第三个位置参数是布尔开关的默认值；参数不足或类型不是字面量 bool 时返回 None，
    # 由调用方据此区分「默认值为 False」与「无法判定默认值」。
    if len(call.args) < 3:
        return None
    value = call.args[2]
    if isinstance(value, ast.Constant) and isinstance(value.value, bool):
        return value.value
    return None


def _module_defining(name: str) -> Path:
    """返回定义 name 的 TodayWaifu 模块路径，避免测试与模块划分方式耦合。

    shared.py 已按职责拆分（bc6ab41），目标函数所在文件会随职责调整迁移，故按定义位置
    动态定位，而非写死导入路径。
    """
    for path in sorted((ROOT / 'TodayWaifu').glob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return path
    raise AssertionError(f'{name} 未在任何 TodayWaifu 模块中定义')


def _extract_function(name: str, globals_dict: dict[str, Any]):
    # 只抽取目标函数并注入替身全局名，使被测逻辑不依赖 gsuid_core 与真实配置系统；
    # 注入的全局名是函数体的隐式输入，缺失会直接抛 NameError。
    source_path = _module_defining(name)
    tree = ast.parse(source_path.read_text(encoding='utf-8-sig'))
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )
    module = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(source_path), 'exec'), globals_dict)
    return globals_dict[name]


@dataclass(frozen=True)
class _RoleCandidate:
    name: str
    role_ids: tuple[str, ...]
    images: tuple[str, ...]


class _Logger:
    def debug(self, _message: str) -> None:
        pass


class NteConfigAndCommandTests(unittest.TestCase):
    def test_daily_nte_wife_is_disabled_by_default(self) -> None:
        # 开关必须唯一且默认关闭：重复键会让控制台出现两个同名开关，默认开启则等于对
        # 全部用户启用一项依赖外部图库的功能。唯一性同时约束「有且仅有一项」。
        matches = [
            entry
            for entry in _config_entries()
            if entry[0] == 'DailyWifeNteEnabled'
        ]
        self.assertEqual(len(matches), 1, '应有且仅有一个“今日异环老婆”布尔开关')
        self.assertFalse(_bool_default(matches[0][3]))

    def test_daily_nte_wife_command_is_registered(self) -> None:
        # 断言注册词本身而非仅检查文本出现：文本检查会被说明文字、日志或注释满足，
        # 命令却可能根本没挂到触发器上，用户侧表现为指令无响应。
        daily_source = (ROOT / 'TodayWaifu' / 'daily.py').read_text(encoding='utf-8')
        self.assertIn('今日异环老婆', daily_source)

        tree = ast.parse(daily_source)
        registered_words: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {'on_command', 'on_prefix', 'on_fullmatch'}:
                continue
            for arg in node.args[:1]:
                for value in ast.walk(arg):
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        registered_words.add(value.value)
        self.assertIn('今日异环老婆', registered_words)

    def test_nte_candidate_loader_is_wired_to_local_and_default_piles(self) -> None:
        # shared 已按职责拆分，NTE 加载器现位于 roles / paths 等模块，故扫描整个 TodayWaifu 包。
        # 以源码文本为断言对象：加载器的参数接线（自定义目录与默认目录）属于调用契约，
        # 漏传任一路径都能通过导入检查，却会让对应角色无图可抽。
        nte_functions: list[str] = []
        for path in sorted((ROOT / 'TodayWaifu').glob('*.py')):
            module_source = path.read_text(encoding='utf-8')
            tree = ast.parse(module_source)
            nte_functions.extend(
                ast.get_source_segment(module_source, node) or ''
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and (
                    node.name.lower().startswith('nte')
                    or node.name.lower().startswith('_nte')
                    or '_nte_' in node.name.lower()
                )
            )
        nte_source = '\n'.join(nte_functions).lower()
        self.assertTrue(nte_functions, 'TodayWaifu 包应提供 NTE 候选加载函数')
        self.assertIn('_collect_role_candidates', nte_source)
        self.assertIn('custom', nte_source, 'NTE 加载器应传入本地自定义立绘目录')
        self.assertIn('default', nte_source, 'NTE 加载器应传入默认立绘目录作为兜底')

    def test_mixed_wife_feature_is_removed(self) -> None:
        # 0b6469b 移除了融合老婆功能，此处的旧配置键与旧选取函数名不得以任何形式回归：
        # 残留的配置键会让控制台出现无效开关，残留的选取函数则是未接线的死代码。
        config = (ROOT / 'config_default.py').read_text(encoding='utf-8-sig')
        runtime = '\n'.join(
            (ROOT / 'TodayWaifu' / name).read_text(encoding='utf-8-sig')
            for name in ('shared.py', 'daily.py')
        )
        removed_symbols = (
            'DailyWifeNteMixedEnabled',
            'DailyWifeMixedWuwaEnabled',
            'DailyWifeMixedNteEnabled',
            'DailyWifeMixedPgrEnabled',
            '_load_wife_candidate_pools',
            '_pick_mixed_wife_record',
        )

        for symbol in removed_symbols:
            self.assertNotIn(symbol, config)
            self.assertNotIn(symbol, runtime)
        self.assertIn("'DailyWifeNteEnabled'", config)
        self.assertIn("'DailyWifePgrEnabled'", config)


class NteCandidateBehaviorTests(unittest.TestCase):
    def _collector(self):
        # 以真实文件系统而非 mock 驱动候选收集：_role_images 递归扫描目录，其与
        # rglob / 扩展名过滤的配合只有落到真实目录才能验证。
        return _extract_function(
            '_collect_role_candidates',
            {
                'Any': Any,
                'Path': Path,
                'RoleCandidate': _RoleCandidate,
                'IMAGE_EXTENSIONS': ('.png', '.jpg', '.jpeg', '.webp'),
                '_is_excluded_role': lambda _name: False,
                '_role_images': lambda role_dir: tuple(
                    str(path)
                    for path in sorted(role_dir.rglob('*'))
                    if path.is_file()
                    and path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp')
                ),
                'logger': _Logger(),
                'LOG_PREFIX': '[test]',
            },
        )

    def test_nte_local_custom_image_has_priority(self) -> None:
        # 默认立绘只作兜底，自定义目录有图时不得并入候选：一旦并入，随机选图会以一定概率
        # 展示插件内置图片，用户自行上传的立绘看上去「有时不生效」。
        collect = self._collector()
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            custom_root = root / 'custom'
            default_root = root / 'default'
            (custom_root / '1003').mkdir(parents=True)
            default_root.mkdir()
            custom_image = custom_root / '1003' / 'custom.png'
            default_image = default_root / 'role_pile_1003.png'
            custom_image.write_bytes(b'custom')
            default_image.write_bytes(b'default')

            candidates = collect({'1003': '早雾'}, custom_root, default_root)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].images, (str(custom_image),))

    def test_nte_default_image_is_used_when_custom_is_missing(self) -> None:
        # 自定义目录为空时必须回落到角色 ID 命名的默认立绘：该路径是未上传任何图片的
        # 部署唯一可用的图片来源，失效即等同于 NTE 功能整体不可用。
        collect = self._collector()
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            custom_root = root / 'custom'
            default_root = root / 'default'
            custom_root.mkdir()
            default_root.mkdir()
            default_image = default_root / 'role_pile_1003.png'
            default_image.write_bytes(b'default')

            candidates = collect({'1003': '早雾'}, custom_root, default_root)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].images, (str(default_image),))

if __name__ == '__main__':
    unittest.main()
