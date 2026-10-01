# 抽群友候选过滤的契约：发起者本人与机器人自身必须被排除在候选之外。
#
# 该回归由 8f88d61 修复：此前候选名单未过滤 ev.user_id 与 ev.bot_self_id，用户抽到的
# 「群友」可能就是自己或机器人账号。过滤口径还须覆盖代抽场景——显式传入 exclude_user_id
# 时以被代替的用户为准，否则代抽会把被代替者重新抽回。
import ast
import random
import asyncio
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]


class MemberCandidate:
    def __init__(self, name: str, user_id: str, avatar: str):
        self.name = name
        self.user_id = user_id
        self.avatar = avatar


def _load_module_functions() -> dict:
    # 按函数名跨文件收集而非固定导入路径：bc6ab41 将 shared.py 拆成多个职责单一的子模块后，
    # 目标函数所在文件会随职责调整而迁移，按名收集可避免测试与模块划分方式耦合。
    wanted = {'_pick_group_member', '_loli_enabled'}
    body: list[ast.stmt] = [
        ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
    ]
    for path in sorted((ROOT / 'TodayWaifu').glob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        body.extend(
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted
        )
    # 函数体引用的模块级名字不会随函数体一起抽取，须在此补齐，否则 exec 时抛 NameError；
    # 同时以桩替身切断对 gsuid_core 与配置系统的依赖。
    namespace = {
        'Event': object,
        'MemberCandidate': MemberCandidate,
        'random': random,
        'logger': SimpleNamespace(debug=lambda *a: None, warning=lambda *a: None),
        'LOG_PREFIX': '[TEST]',
        '_cfg_bool': lambda key, default=False: default,
    }
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, 'test_fns', 'exec'), namespace)
    return namespace


class MarryMemberSelfExclusionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # 每条用例重建命名空间而非共用：用例会向其中注入 AsyncMock 与替身函数，
        # 复用同一份命名空间会让上一条用例的注入残留，掩盖注入失效。
        self.ns = _load_module_functions()
        self.pick_fn = self.ns['_pick_group_member']

    async def test_pick_group_member_excludes_caller_and_bot(self) -> None:
        # 名单中同时放入两个应被排除的 ID 与一个合法候选：过滤一旦失效，命中哪个取决于
        # 洗牌结果，故以具体 user_id 固定断言，使「娶到自己」的回归必然可见。
        ev = SimpleNamespace(
            user_id="123456",
            bot_self_id="999999",
            group_id="888888",
            bot_id="onebot",
        )
        candidates = (
            MemberCandidate(name="Self", user_id="123456", avatar="avatar_self"),
            MemberCandidate(name="Bot", user_id="999999", avatar="avatar_bot"),
            MemberCandidate(name="Friend", user_id="654321", avatar="avatar_friend"),
        )

        self.ns['_load_group_member_candidates'] = AsyncMock(return_value=candidates)
        self.ns['_resolve_member_candidate_avatar'] = lambda m: asyncio.sleep(0, m)

        picked = await self.pick_fn(ev, random.Random(42))
        self.assertIsNotNone(picked)
        self.assertEqual(picked.user_id, "654321")
        self.assertEqual(picked.name, "Friend")

    async def test_pick_group_member_returns_none_when_only_caller_and_bot_present(self) -> None:
        # 无第三方候选时必须返回 None，而不是放宽过滤退而选择自身：调用方依赖 None
        # 回落到角色图库，若此处改为返回发起者，用户会看到自己成为自己的老婆。
        ev = SimpleNamespace(
            user_id="123456",
            bot_self_id="999999",
            group_id="888888",
            bot_id="onebot",
        )
        candidates = (
            MemberCandidate(name="Self", user_id="123456", avatar="avatar_self"),
            MemberCandidate(name="Bot", user_id="999999", avatar="avatar_bot"),
        )

        self.ns['_load_group_member_candidates'] = AsyncMock(return_value=candidates)
        self.ns['_resolve_member_candidate_avatar'] = lambda m: asyncio.sleep(0, m)

        picked = await self.pick_fn(ev, random.Random(42))
        self.assertIsNone(picked)

    async def test_pick_group_member_respects_custom_exclude_user_id(self) -> None:
        # 代抽路径传入的 exclude_user_id 必须覆盖 ev.user_id：该参数指向被代替的用户，
        # 若被忽略，代抽会把被代替者重新抽给自己，等同代抽指令失效。
        ev = SimpleNamespace(
            user_id="caller",
            bot_self_id="999999",
            group_id="888888",
            bot_id="onebot",
        )
        candidates = (
            MemberCandidate(name="Target", user_id="777777", avatar="avatar_target"),
            MemberCandidate(name="Friend", user_id="654321", avatar="avatar_friend"),
        )

        self.ns['_load_group_member_candidates'] = AsyncMock(return_value=candidates)
        self.ns['_resolve_member_candidate_avatar'] = lambda m: asyncio.sleep(0, m)

        picked = await self.pick_fn(ev, random.Random(42), exclude_user_id="777777")
        self.assertIsNotNone(picked)
        self.assertEqual(picked.user_id, "654321")


class DailyLoliConfigTests(unittest.TestCase):
    # 默认值曾为 False，使未显式开启该配置的部署完全无法抽到今日萝莉；8f88d61 将其改为
    # True 并以此固定为兼容契约，改动默认值等同于默认关闭一项既有功能。
    def test_loli_enabled_defaults_to_true(self) -> None:
        ns = _load_module_functions()
        self.assertTrue(ns['_loli_enabled']())


if __name__ == "__main__":
    unittest.main()
