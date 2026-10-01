# 普通老婆图库响应解析的契约：脏数据必须被逐条丢弃，接口异常必须显式报错。
#
# 上游图库混有其他作品数据，因此解析器按「能用的留下、不能用的跳过」处理；但整份响应都
# 无可用角色时必须抛错而非返回空候选——返回空会让上层报「没有可用角色」，把接口故障
# 掩盖成正常的空结果。另外本模块不注册命令，普通老婆只能经「今日老婆」入口抽取。
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'TodayWaifu' / 'normal_wife.py'


def _load_parser():
    # 以语法树拼装被测函数，而非导入整个模块：normal_wife 经 shared 间接依赖 gsuid_core，
    # 直接导入会要求框架环境；此处仅需 RoleCandidate 与 urlparse 两个名字。
    source = MODULE.read_text(encoding='utf-8')
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == '_parse_normal_gallery_candidates'
    )
    module = ast.Module(
        body=[
            ast.ImportFrom(module='__future__', names=[ast.alias('annotations')], level=0),
            ast.ImportFrom(module='typing', names=[ast.alias('Any')], level=0),
            ast.parse(
                'class RoleCandidate:\n'
                '    def __init__(self, name, role_ids, images):\n'
                '        self.name = name\n'
                '        self.role_ids = role_ids\n'
                '        self.images = images\n'
            ).body[0],
            ast.ImportFrom(module='urllib.parse', names=[ast.alias('urlparse')], level=0),
            function,
        ],
        type_ignores=[],
    )
    namespace = {}
    exec(compile(ast.fix_missing_locations(module), str(MODULE), 'exec'), namespace)
    return namespace['_parse_normal_gallery_candidates']


class NormalWifeGalleryTests(unittest.TestCase):
    def setUp(self) -> None:
        # 逐条用例重建解析函数：构建过程会向命名空间写入被测函数，复用易掩盖上一条用例
        # 对命名空间的意外影响。
        self.parse = _load_parser()

    def test_parses_https_images_and_deduplicates_urls(self) -> None:
        # 同一 URL 重复上报与明文 HTTP 地址必须被剔除：明文 HTTP 在协议端可能被拒或被
        # 压缩转发，重复项会让该图片在随机选图时获得更高权重。
        candidates = self.parse(
            {
                'roles': [
                    {
                        'role_ids': ['ceshi'],
                        'name': '老婆',
                        'images': [
                            {'url': 'https://example.test/a.webp'},
                            {'url': 'https://example.test/a.webp'},
                            {'url': 'http://example.test/unsafe.webp'},
                        ],
                    }
                ]
            }
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].name, '老婆')
        self.assertEqual(candidates[0].role_ids, ('ceshi',))
        self.assertEqual(candidates[0].images, ('https://example.test/a.webp',))

    def test_uses_role_id_when_name_is_missing(self) -> None:
        # 角色名缺失时以首个标识代称：文案模板需要非空角色名，留空会输出残缺提示。
        candidates = self.parse(
            {
                'roles': [
                    {
                        'role_ids': ['ceshi'],
                        'images': [{'url': 'https://example.test/a.webp'}],
                    }
                ]
            }
        )
        self.assertEqual(candidates[0].name, 'ceshi')

    def test_rejects_empty_or_invalid_gallery(self) -> None:
        # 四类输入分别对应结构缺失、空列表、无图片、图片为相对路径：全部必须抛
        # RuntimeError。若改为返回空候选，调用方会把它当作「本群没有可用角色」，
        # 用户看到的是无角色可抽而非接口异常。
        for payload in (
            {},
            {'roles': []},
            {'roles': [{'role_ids': ['ceshi'], 'images': []}]},
            {'roles': [{'role_ids': ['ceshi'], 'images': [{'url': '/local.webp'}]}]},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(RuntimeError):
                    self.parse(payload)

    def test_normal_wife_has_no_standalone_command(self) -> None:
        # 本模块以源码文本为断言对象：普通老婆不设独立指令，只能经「今日老婆」入口抽取，
        # 以保证每日唯一性与数据库记录共用同一套流程；一旦在此注册触发器，
        # 同一用户会出现两条互不感知的抽取路径。
        source = MODULE.read_text(encoding='utf-8')
        self.assertNotIn("on_fullmatch", source)
        self.assertNotIn("on_command", source)
        self.assertNotIn("on_prefix", source)


if __name__ == '__main__':
    unittest.main()
