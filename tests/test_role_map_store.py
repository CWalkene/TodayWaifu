# 角色对照表读写的契约：分节隔离、旧格式宽容解析、原子落盘与迁移幂等。
#
# f7fd76c 将三份内置 TXT 合并为单个分节 JSON，并让用户自定义表也从 TXT 迁移到 JSON。
# 这次改动同时引入四类风险，本文件逐条锁定：分节选错会让老公表读到老婆角色；
# 旧 TXT 与扁平等价配置必须继续可用（老用户数据不能因升级失效）；写文件必须原子
# （上传会频繁重写而读取方随时可能并发读取）；迁移必须幂等且失败时保留原文件。
import sys
import json
import tempfile
import unittest
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_store():
    # role_map_store 只依赖标准库，可按文件路径独立加载而不必导入 TodayWaifu 包（该包会
    # 连带拉起 gsuid_core）；先登记进 sys.modules 再 exec_module，使模块内任何按
    # 模块名回查自身的逻辑都能取到已初始化的实例。
    path = ROOT / 'TodayWaifu' / 'role_map_store.py'
    spec = importlib.util.spec_from_file_location('todaywaifu_role_map_store', path)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load role map store module')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


store = _load_store()


class BuiltinRoleMapTests(unittest.TestCase):
    def test_builtin_json_carries_all_three_sections(self) -> None:
        # 三个分节都必须非空：合并为单文件后，任一节的遗漏都不会报错，只会在对应模式下
        # 表现为「没有可用角色」，且用户无从判断是配置问题还是数据缺失。
        payload = json.loads((ROOT / 'role_id_map.json').read_text(encoding='utf-8'))
        self.assertEqual(payload['version'], 1)
        for section in store.ROLE_MAP_SECTIONS:
            self.assertIsInstance(payload[section], dict, section)
            self.assertTrue(payload[section], section)

    def test_builtin_json_replaces_the_removed_txt_files(self) -> None:
        # 旧 TXT 不得残留：两个真值源并存时，改一份而读另一份会出现「改了配置不生效」，
        # 是该次重构要消除的主要隐患。
        for legacy in ('wife_role_id_map.txt', 'husband_role_id_map.txt', 'nte_role_id_map.txt'):
            self.assertFalse((ROOT / legacy).exists(), legacy)

    def test_each_section_is_readable_by_mode(self) -> None:
        # 除各节能读到本职角色外，还须验证跨节不串：老公表不得包含只出现在老婆表的 ID，
        # 否则分节选择退化为读取整份文件，模式隔离形同虚设。
        text = (ROOT / 'role_id_map.json').read_text(encoding='utf-8')
        wife = store.loads_role_map(text, 'wife')
        husband = store.loads_role_map(text, 'husband')
        nte = store.loads_role_map(text, 'nte')
        self.assertEqual(wife['1102'], '散华')
        self.assertEqual(husband['1104'], '凌阳')
        self.assertEqual(nte['1003'], '早雾')
        self.assertNotIn('1102', husband)
        self.assertNotIn('1104', wife)

    def test_unknown_section_does_not_fall_back_to_whole_file(self) -> None:
        # 请求的分节不存在时必须返回空表，而不是回落到整个文件：回落会把其它模式的
        # 角色混入当前模式的候选池，用户可抽到不属于该玩法的角色。
        text = (ROOT / 'role_id_map.json').read_text(encoding='utf-8')
        self.assertEqual(store.loads_role_map(text, 'nonexistent'), {})


class LegacyTextCompatTests(unittest.TestCase):
    def test_legacy_txt_still_parses_with_both_colon_styles(self) -> None:
        # 历史 TXT 由用户手工维护，全角与半角冒号混杂，注释行与空行也常见；
        # 解析须容忍这些形态并按内容而非后缀判断格式，否则老用户的对照表直接失效。
        parsed = store.loads_role_map('1102：散华\n1610: 秧秧·玄翎\n# 注释行\n\n')
        self.assertEqual(parsed, {'1102': '散华', '1610': '秧秧·玄翎'})

    def test_flat_json_is_used_when_no_section_is_requested(self) -> None:
        # 未指定分节时按扁平结构解析：用户上传的自定义表就是这一形态，
        # 若强制要求分节，所有既有自定义表都会读成空。
        self.assertEqual(store.loads_role_map('{"900001": "达妮娅"}'), {'900001': '达妮娅'})

    def test_sectioned_json_requested_flat_keeps_only_flat_entries(self) -> None:
        # 自定义老婆对照表是扁平结构；误配到分节文件上时不应把节名当角色名
        parsed = store.loads_role_map('{"version": 1, "wife": {"1102": "散华"}}')
        self.assertEqual(parsed, {})

    def test_broken_json_returns_empty_instead_of_raising(self) -> None:
        # 损坏或空内容一律返回空表：该函数处于读取热路径，抛错会中断抽取并让用户看到
        # 与真实原因无关的失败提示；返回空表则使调用方进入「无对照表」分支并给出可操作说明。
        self.assertEqual(store.loads_role_map('{not json'), {})
        self.assertEqual(store.loads_role_map(''), {})


class WriteAndMigrationTests(unittest.TestCase):
    def test_dumps_sorts_numeric_ids_and_round_trips(self) -> None:
        # 数字 ID 按数值、非数字键排在最后：若无稳定排序，每次重写都会产生大范围 diff，
        # 用户难以看清自己实际改动了哪一条。
        payload = store.dumps_role_map({'1000': '乙', '900': '甲', 'custom': '丙'})
        self.assertLess(payload.index('"900"'), payload.index('"1000"'))
        self.assertLess(payload.index('"1000"'), payload.index('"custom"'))
        self.assertEqual(store.loads_role_map(payload), {'900': '甲', '1000': '乙', 'custom': '丙'})

    def test_write_role_map_is_atomic_and_readable(self) -> None:
        # 就地写会让并发读取方解析到半截 JSON；此处除验证可回读，还断言目录中不残留
        # 临时文件，即写入路径必须自行清理临时产物。
        # 顺带覆盖「目标父目录不存在」的情形：上传首次落盘时该目录尚未创建。
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'nested' / 'custom_role_map.json'
            store.write_role_map(path, {'900001': '达妮娅'})
            self.assertEqual(store.loads_role_map(path.read_text(encoding='utf-8')), {'900001': '达妮娅'})
            self.assertEqual([item.name for item in path.parent.iterdir()], ['custom_role_map.json'])

    def test_legacy_txt_is_migrated_once_and_backed_up(self) -> None:
        # 迁移须备份原文件而非删除，且第二次调用必须短路返回 False：迁移在首次读取时
        # 触发，同一进程可能多次调用，非幂等会覆盖已迁移后的用户改动。
        with tempfile.TemporaryDirectory() as tmp:
            txt = Path(tmp) / 'custom_role_map.txt'
            js = Path(tmp) / 'custom_role_map.json'
            txt.write_text('900001：达妮娅\n900002：今汐\n', encoding='utf-8')

            self.assertTrue(store.migrate_legacy_text_map(txt, js))
            self.assertFalse(txt.exists())
            self.assertTrue((Path(tmp) / 'custom_role_map.txt.migrated.bak').is_file())
            self.assertEqual(
                store.loads_role_map(js.read_text(encoding='utf-8')),
                {'900001': '达妮娅', '900002': '今汐'},
            )
            # 幂等：JSON 已存在时不再迁移
            self.assertFalse(store.migrate_legacy_text_map(txt, js))

    def test_migration_is_skipped_when_no_legacy_file_exists(self) -> None:
        # 无旧文件时必须返回 False 且不产生空 JSON：写入一个空表会让后续读取认为
        # 「用户自定义表存在但为空」，从而跳过真正的迁移时机。
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(
                store.migrate_legacy_text_map(Path(tmp) / 'missing.txt', Path(tmp) / 'out.json')
            )
            self.assertFalse((Path(tmp) / 'out.json').exists())


if __name__ == '__main__':
    unittest.main()
