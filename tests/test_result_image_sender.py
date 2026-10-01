# 抽取结果图片发送链路的契约：旧入口保留转发、AI 摘要在入队时刻生成、图片在构造消息段前先下载。
#
# 31c225d 把「下载 + 编码 + 发送」整体迁入插件自有队列，命令协程仅入队即返回，以归还
# Core 的命令并发额度（此前 200 并发下一台机器上所有插件的命令都要等 11.4 秒）。
# 这次解耦留下三处易被破坏的边界，本文件逐条锁定：rob/gift 的旧入口必须仍转发到共享
# 实现（调用方零改动）、AI 摘要必须在入队前生成（后台 worker 已无请求上下文）、
# 萝莉远程图片必须在构造消息段前下载为字节（否则回到在事件循环上编码大图的旧路径）。
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ResultImageSenderTests(unittest.TestCase):
    def test_rob_and_gift_legacy_senders_delegate_to_shared_sender(self) -> None:
        # 两个旧入口在解耦后仍须只做转发：调用点保持签名不变是「调用方零改动」的前提；
        # 一旦在包装层内自行入队或发送，两条链路会各自维护一份投递语义并逐渐分叉。
        # 断言调用列表完全等于单个目标，意味着包装函数内不得再引入任何其它函数调用。
        for filename, wrapper in (
            ("rob.py", "_send_rob_result_image"),
            ("gift.py", "_send_gift_result_image"),
        ):
            with self.subTest(filename=filename):
                tree = ast.parse((ROOT / "TodayWaifu" / filename).read_text(encoding="utf-8"))
                function = next(
                    node
                    for node in tree.body
                    if isinstance(node, ast.AsyncFunctionDef) and node.name == wrapper
                )
                calls = [
                    node
                    for node in ast.walk(function)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                ]
                self.assertEqual([call.func.id for call in calls], ["_send_daily_result_image"])

    def test_shared_sender_keeps_kind_specific_result_selection(self) -> None:
        # 共享入口按 kind 分流：萝莉/正太需排除角色信息并走 loli_style 分支，
        # 其余类型携带角色名。分流条件若写成恒真或恒假，两类抽取中必有一类输出错内容。
        source = (ROOT / "TodayWaifu" / "senders.py").read_text(encoding="utf-8")
        self.assertIn("async def _send_daily_result_image(", source)
        self.assertIn("if kind != 'loli':", source)
        self.assertIn("_send_role_image(bot, role, image, text, user_id, is_group, kind)", source)
        self.assertIn("_send_loli_result_image(bot, image, text, user_id, is_group, kind)", source)

    def test_result_senders_inject_ai_readable_summary(self) -> None:
        """AI 调用工具时需拿到文字摘要，否则只能看到图片资源 ID、答不出"抽到了谁"。"""
        source = (ROOT / "TodayWaifu" / "senders.py").read_text(encoding="utf-8")
        self.assertIn("def _ai_return_draw(", source)

        # 摘要在**入队时刻**注入：后台 worker 发送时已经没有请求上下文了
        role_fn = source[
            source.index("async def _send_role_image("):
            source.index("async def _send_daily_result_image(")
        ]
        self.assertIn("_ai_return_draw(kind, role.name, text)", role_fn)

        loli_fn = source[
            source.index("async def _send_loli_result_image("):source.index("async def _send_local_image(")
        ]
        self.assertIn("_ai_return_draw(kind, '', text)", loli_fn)

    def test_loli_sender_downloads_remote_images_before_building_segment(self) -> None:
        # 远程图片必须在此处下载为字节后交给 _image_message，而不是把 URL 原样透传：
        # 后者会走框架在事件循环上同步编码大图的路径（de7fd79 已因此改过一次），
        # 卡顿会随并发图片数线性累加，波及 Core 内所有插件。
        source = (ROOT / "TodayWaifu" / "senders.py").read_text(encoding="utf-8")
        function_start = source.index("async def _deliver_loli_result_image(")
        function_end = source.index("async def _send_local_image(", function_start)
        function = source[function_start:function_end]

        self.assertIn("image_ref = await _acquire_gallery_image(image)", function)
        self.assertNotIn(
            "if image.startswith(('http://', 'https://')):\n            image_ref = image",
            function,
        )


if __name__ == "__main__":
    unittest.main()
