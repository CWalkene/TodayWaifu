"""三种老婆文本必须共用同一套构造函数，且都带上角色台词与署名。

背景：pgr.py 曾自行拼接模板，只输出「你今天的战双老婆是XXX。」，既不调用
get_role_quote 也无署名行，导致战双老婆永远看不到台词；鸣潮与异环走 daily.py 的
_build_text 因此不受影响（1edf2c4）。异环虽然一直走公共路径，但当时没有任何测试
锁定该行为，同类的旁路改写仍可能复发，故本文件同时锁住三者的文本来源与开关语义。

台词内容本身来自 role_quotes.json，属于数据资产而非代码契约，这里的断言只检查
「台词是否被带上」这一结构性事实，不校验具体句子。
"""

import ast
import unittest
from typing import Any
from pathlib import Path
from dataclasses import dataclass

ROOT = Path(__file__).resolve().parents[1]
DAILY_PATH = ROOT / 'TodayWaifu' / 'daily.py'
PGR_PATH = ROOT / 'TodayWaifu' / 'pgr.py'
QUOTES_MODULE = ROOT / 'TodayWaifu' / 'role_quotes.py'
BUNDLED_QUOTES = ROOT / 'role_quotes.json'

# 真台词库就在插件根；pgr.py 曾因为绕开公共构造函数而漏掉台词
QUOTES_FILE = BUNDLED_QUOTES if BUNDLED_QUOTES.is_file() else None


# 以下三个假对象替代生产数据类型：被测函数只读取 name / role_ids / images 与两个模板
# 字段，用真实类型会连带引入图库、配置与事件依赖，使断言从「文本构造」偏移到运行时装配。
@dataclass
class _FakeRole:
    name: str
    role_ids: tuple[str, ...]
    images: tuple[str, ...]


@dataclass
class _FakeKindMetadata:
    text_template_key: str
    text_template_default: str


@dataclass
class _FakeRecord:
    name: str
    role_ids: tuple[str, ...]

    def to_role(self) -> _FakeRole:
        return _FakeRole(self.name, self.role_ids, ('https://example.test/a.png',))


def _load_role_quotes_module() -> dict[str, Any]:
    # 台词模块依赖 resource_paths 定位打包资源，测试环境无该上下文，故剔除相对导入并由
    # 注入的 role_quotes_path 直接指向仓库内的内置台词库。
    tree = ast.parse(QUOTES_MODULE.read_text(encoding='utf-8-sig'))
    tree.body = [
        node
        for node in tree.body
        if not (isinstance(node, ast.ImportFrom) and (node.module == 'resource_paths' or node.level == 1))
    ]
    globals_dict: dict[str, Any] = {'role_quotes_path': lambda: QUOTES_FILE}
    exec(compile(tree, str(QUOTES_MODULE), 'exec'), globals_dict)
    return globals_dict


def _extract_function(path: Path, name: str, globals_dict: dict[str, Any]) -> Any:
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )
    # 抽取出的片段脱离原模块，需补回 future import，注解才保持惰性求值。
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.Module(body=[future, function], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), 'exec'), globals_dict)
    return globals_dict[name]


# 模板按模式的真实默认值给出：模板串本身不是被测对象，但它是「文本里出现角色名」的
# 前提，缺失会让断言因无关原因失败。
TEMPLATES = {
    'wife': ('DailyWifeTextTemplate', '你今天的老婆是{name}'),
    'nte': ('DailyWifeNteTextTemplate', '你今天的异环老婆是{name}。'),
    'pgr': ('DailyWifePgrTextTemplate', '你今天的战双老婆是{name}。'),
}


@unittest.skipUnless(QUOTES_FILE is not None, '缺少内置台词库，跳过文本构造测试')
class DailyWifeQuoteTextTests(unittest.TestCase):
    def setUp(self) -> None:
        quotes_mod = _load_role_quotes_module()
        self.get_role_quote = quotes_mod['get_role_quote']

        def fake_kind_metadata(mode: str) -> _FakeKindMetadata:
            key, default = TEMPLATES.get(mode, TEMPLATES['wife'])
            return _FakeKindMetadata(key, default)

        # 台词开关打开、ID 行关闭，专注验证「台词是否被带上」
        self.quote_on = {
            'RoleCandidate': _FakeRole,
            '_cfg_bool': lambda key, default=False: True if key == 'DailyWifeSendRoleQuote' else bool(default),
            '_daily_kind_metadata': fake_kind_metadata,
            '_cfg': lambda key: False,
            'get_role_quote': self.get_role_quote,
        }
        self.build_text = _extract_function(DAILY_PATH, '_build_text', dict(self.quote_on))

    def _pgr_result_text(self, record: _FakeRecord, user_id: str = ''):  # noqa: ANN202
        # 每次重新抽取并绑定当次 globals：pgr 的结果函数通过模块级名称调用 _build_text，
        # 复用同一份 globals 字典可让测试替换的桩生效。
        globals_dict = dict(self.quote_on)
        globals_dict['WifeRecord'] = _FakeRecord
        globals_dict['_build_text'] = self.build_text
        fn = _extract_function(PGR_PATH, '_pgr_result_text', globals_dict)
        return fn(record, user_id)

    def test_wife_text_has_quote(self) -> None:
        text = self.build_text(_FakeRole('折枝', ('1105',), ('x.png',)), 'wife', '123456')
        self.assertIn('折枝', text)
        self.assertIn('「', text)
        self.assertIn('——折枝', text)

    def test_nte_text_has_quote(self) -> None:
        """异环走的是 daily 的公共路径，必须同样带台词。"""
        text = self.build_text(_FakeRole('早雾', ('1003',), ('x.png',)), 'nte', '123456')
        self.assertIn('早雾', text)
        self.assertIn('「', text)
        self.assertIn('——早雾', text)

    def test_pgr_text_has_quote(self) -> None:
        """战双曾漏掉台词：pgr 现在必须复用 _build_text。

        该断言同时覆盖署名行，而署名行的缺失正是 1edf2c4 之前用户可感知的症状。
        """
        text = self._pgr_result_text(_FakeRecord('露西亚', ('1001',)))
        self.assertIsNotNone(text)
        assert text is not None
        self.assertIn('露西亚', text)
        self.assertIn('「', text)
        self.assertIn('——露西亚', text)

    def test_pgr_reuses_shared_text_builder(self) -> None:
        """守卫：pgr.py 不得再自己拼模板。

        行为断言只覆盖当前实现路径；本项直接检查源码，使「另起一套模板」的写法即使
        恰好产出相同文本也会被拦下。
        """
        source = PGR_PATH.read_text(encoding='utf-8')
        self.assertIn('from .daily import _build_text', source)
        body = source[source.index('def _pgr_result_text(') : source.index('async def _send_daily_pgr_wife(')]
        self.assertIn('_build_text(', body)

    def test_quote_switch_off_drops_the_quote_line(self) -> None:
        # 关闭开关后文本中不得残留任何引号，否则说明台词行来自未被开关控制的旁路。
        globals_dict = dict(self.quote_on)
        globals_dict['_cfg_bool'] = lambda key, default=False: False
        build_text = _extract_function(DAILY_PATH, '_build_text', globals_dict)
        text = build_text(_FakeRole('折枝', ('1105',), ('x.png',)), 'wife', '123456')
        self.assertIn('折枝', text)
        self.assertNotIn('「', text)

    def test_pgr_text_respects_send_text_switch(self) -> None:
        # 战双走公共构造函数后仍须独立响应 DailyWifeSendText：若直接返回构造结果，关闭
        # 文本发送的部署会照常推送文本，与配置语义不符。
        globals_dict = dict(self.quote_on)
        globals_dict['WifeRecord'] = _FakeRecord
        globals_dict['_build_text'] = self.build_text
        globals_dict['_cfg_bool'] = lambda key, default=False: (
            False if key == 'DailyWifeSendText' else bool(default)
        )
        fn = _extract_function(PGR_PATH, '_pgr_result_text', globals_dict)
        self.assertIsNone(fn(_FakeRecord('露西亚', ('1001',))))


if __name__ == '__main__':
    unittest.main()
