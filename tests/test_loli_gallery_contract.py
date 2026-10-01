"""萝莉图库的解析契约与「当日图片固定」的持久化约定。

萝莉图库与每日老婆图库共用同一份 roles/images 载荷结构，但萝莉侧的记录必须落在
`context['lolis']` 中，且一旦选定就写死具体图片 URL —— 每日记录当天不再变化，抢与送都
直接复用该 URL。旧实现把随机接口地址写入 `role_ids=('接口',)`（见 `_is_legacy_remote_loli_record`），
每次读取都会跳到新图，与当日固定相冲突，因此这里锁定「解析出具体 URL 并持久化」。

解析失败一律抛 `RuntimeError` 而非静默降级：载荷契约漂移时宁可当天明确报错，也不把半成品
记录写进每日上下文，否则错误会在整天内被反复读取。
"""
import ast
import unittest
from typing import Any
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOLI_PATH = ROOT / 'TodayWaifu' / 'loli.py'


def _extract_function(name: str, globals_dict: dict[str, Any]) -> Any:
    # 按源码抽出单个函数执行，而非导入 loli 模块：该模块依赖 gsuid_core（测试环境未安装）。
    # 抽取而非在测试内重写实现，可保证断言始终指向线上代码本身。
    tree = ast.parse(LOLI_PATH.read_text(encoding='utf-8-sig'))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    future = ast.ImportFrom(
        module='__future__',
        names=[ast.alias(name='annotations')],
        level=0,
    )
    module = ast.Module(body=[future, function], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(LOLI_PATH), 'exec'), globals_dict)
    return globals_dict[name]


class LoliGalleryContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parse_urls = _extract_function(
            '_parse_loli_image_urls',
            {'Any': Any},
        )

    def test_parses_same_shape_as_daily_wife_gallery(self) -> None:
        # 重复 URL 必须去重且保留首次出现顺序：原样保留会让同一张图按出现次数获得更高的
        # 抽取权重（抽签在候选列表上均匀取一），顺序重排则使同一批上传得出不同结果。
        payload = {
            'roles': [
                {
                    'role_ids': ['loli'],
                    'images': [
                        {'url': 'https://img.example/loli/first.webp'},
                        {'url': 'https://img.example/loli/second.webp'},
                        {'url': 'https://img.example/loli/first.webp'},
                    ],
                }
            ]
        }

        self.assertEqual(
            self.parse_urls(payload),
            (
                'https://img.example/loli/first.webp',
                'https://img.example/loli/second.webp',
            ),
        )

    def test_rejects_old_direct_image_or_incomplete_payloads(self) -> None:
        # 覆盖旧版直传图片字符串的载荷、缺少 images/url 的残缺载荷，以及相对路径 URL。
        # 相对路径无法被发送器下载，若在此放行，故障会推迟到发送阶段才暴露，且可能已被
        # 写入当日记录；因此四种情形都必须在解析阶段拒绝。
        invalid_payloads = (
            {},
            {'roles': []},
            {'roles': [{'role_ids': ['loli']}]},
            {'roles': [{'role_ids': ['loli'], 'images': ['image.webp']}]},
            {
                'roles': [
                    {
                        'role_ids': ['loli'],
                        'images': [{'url': '/loli/image.webp'}],
                    }
                ]
            },
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                self.parse_urls(payload)

    def test_selected_url_is_persisted_and_reused_by_interactions(self) -> None:
        # 选定即写死具体 URL，而非保存接口地址（`image=custom_url` 是旧写法，会在每次读取
        # 时跳到新图）。抢操作必须从同一记录的 `image` 字段取图，才能保证被抢方与抢方看到
        # 的是同一张图。
        loli_source = LOLI_PATH.read_text(encoding='utf-8-sig')
        rob_source = (ROOT / 'TodayWaifu' / 'rob.py').read_text(encoding='utf-8-sig')
        self.assertIn("role_ids=('loli',)", loli_source)
        self.assertIn('image=image_url', loli_source)
        self.assertNotIn('image=custom_url', loli_source)
        self.assertIn("_daily_rng(ev, user_key, 'loli').choice", loli_source)
        self.assertIn('target_record.image,', rob_source)


if __name__ == '__main__':
    unittest.main()
