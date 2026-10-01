"""状态指标的注册与兼容计数口径的回归测试。

状态页是插件对外可见的运行面貌，两组问题都不会以异常形式暴露：状态模块若未被插件入口
导入，指标注册不会发生而状态页只是缺项；计数口径若把已被抢走、已送出或标记为安全的
记录一并计入，「今日老婆」等数字会高于实际持有量。两者均为静默失效，故以本文件固定。

本文件同时锁定兼容路径 `_daily_record_count` 的存在：状态指标已改走一条带索引的聚合
查询，但该辅助函数仍服务于已 hydrate 的旧结构数据，删除它会破坏依赖方。
"""
import ast
import unittest
from typing import Any
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_status_helpers() -> dict[str, Any]:
    # 只抽取口径判定相关的两个函数执行：status.py 依赖 PIL 与 gsuid_core，整模块导入
    # 在无 Core 的测试环境中会失败。此处刻意不注入任何替换实现，须测真实源码。
    status_path = ROOT / 'TodayWaifu' / 'status.py'
    tree = ast.parse(status_path.read_text(encoding='utf-8-sig'))
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {'_is_countable_daily_record', '_daily_record_count'}
    ]
    module = ast.Module(body=functions, type_ignores=[])
    ast.fix_missing_locations(module)
    globals_dict: dict[str, Any] = {'Any': Any}
    exec(compile(module, str(status_path), 'exec'), globals_dict)
    return globals_dict


class StatusSourceTests(unittest.TestCase):
    def test_status_module_is_loaded_by_plugin_entry(self) -> None:
        # 子模块导入即注册触发器与状态指标，导入行缺失时功能静默消失：不会报错，
        # 只是状态页少一项。因此对入口源码本身做断言，而不是断言运行期行为。
        source = (ROOT / 'TodayWaifu' / '__init__.py').read_text(encoding='utf-8-sig')
        self.assertIn('from . import status', source)

    def test_status_registers_three_daily_metrics(self) -> None:
        # 指标名是控制台展示与外部引用的契约，改名或漏注册都会让既有面板出现空缺；
        # 这里逐项断言名字与各自的回调函数，缺一即失败。
        source = (ROOT / 'TodayWaifu' / 'status.py').read_text(encoding='utf-8-sig')
        self.assertIn('register_status(', source)
        self.assertIn("'今日老婆': get_today_wife_count", source)
        self.assertIn("'今日萝莉': get_today_loli_count", source)
        self.assertIn("'今日老公': get_today_husband_count", source)
        self.assertIn("days.get(_today_key())", source)

    def test_daily_record_count_counts_today_original_records(self) -> None:
        # 夹具刻意混合多种边界记录，用于固定口径：stolen_by（记录仍归本人）、
        # stolen_from / gifted_from（已归属他人）、safe（不计入图库口径）、
        # 名称为空的占位记录，以及跨群与跨桶的分布。计数只排除 stolen_from /
        # gifted_from / safe 与空名，因此 wives 应为 3（被抢走与被送出的两条不计，
        # divorced 不影响计数）、lolis 应为 1（safe 与空名不计）、husbands 应为 1；
        # 任一排除条件被放宽都会让数字高于实际持有量。
        helpers = _load_status_helpers()
        count = helpers['_daily_record_count']

        day_data = {
            'qqgroup:g1': {
                'wives': {
                    'u1': {'name': '今汐'},
                    'u2': {'name': '长离', 'stolen_by': 'u3'},
                    'u3': {'name': '长离', 'stolen_from': 'u2'},
                    'u4': {'name': '吟霖', 'gifted_from': 'u5'},
                    'u5': {'name': ''},
                },
                'lolis': {'u1': {'name': '萝莉图abc'}},
                'husbands': {'u1': {'name': '忌炎'}},
            },
            'qqgroup:g2': {
                'wives': {'u6': {'name': '珂莱塔', 'divorced': True}},
                'lolis': {'u2': {'name': '萝莉图def', 'safe': True}},
            },
        }

        self.assertEqual(count(day_data, 'wives'), 3)
        self.assertEqual(count(day_data, 'lolis'), 1)
        self.assertEqual(count(day_data, 'husbands'), 1)


if __name__ == '__main__':
    unittest.main()
