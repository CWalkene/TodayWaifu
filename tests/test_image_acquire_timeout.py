"""图库图片获取的超时契约：命令协程尽快归还 Core 的命令并发额度，且底层下载不被掐断。

提交 df77257 定位到零点卡顿的一个根因：框架 `bot.py` 的 `_process` 先 `await sem.acquire()`
（CommandSemaphore，默认 25）再 `create_task(_safe_run(ctx))`，额度直到命令协程结束才归还。
而当时的图库重试链最坏可达 95 秒（20+5+20+5+20+5+20），只要 25 个抽签命令同时卡在等图上，
`_process` 便停止消费队列，该 bot 上所有插件的命令一并停滞。

由此引入 `IMAGE_ACQUIRE_TIMEOUT_SECONDS` 与 `_acquire_gallery_image`，本文件守护其三条约束：

1. 超时上限必须小（常量级断言），否则额度仍会被长期占用；
2. 超时只能放弃「等待」，底层下载任务须继续跑完并落盘 —— 这正是 `asyncio.shield` 的用途。
   直接以 `wait_for` 包裹会把取消沿 `await task` 传播下去，中断下载：既令已付出的网络
   开销失效，也使磁盘缓存永远暖不起来；
3. 异常类型须继承 `RuntimeError`，以复用既有的「回退本地图片」分支而非穿透到框架。

命令路径的接线同样属于契约：角色图与萝莉图的投递实现（31c225d 将其改名为 `_deliver_*`）
必须经由有界获取，且不得再直接 `await` 无上限的下载。
"""
import ast
import asyncio
import unittest
from typing import Any
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SENDERS = ROOT / 'TodayWaifu' / 'senders.py'


def _node(tree: ast.Module, name: str) -> ast.stmt:
    # 按 AST 定位顶层定义而非切片源码文本：senders.py 中同名片段与相邻函数会随格式化
    # 变动，文本切片得到的边界并不可靠，AST 查找则在定义被改名时直接失败
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    raise AssertionError(f'{name} not found in {SENDERS.name}')


def _exec_nodes(nodes: list[ast.stmt], globals_dict: dict[str, Any]) -> None:
    # 补入 __future__ annotations：senders.py 的签名使用 PEP 604 注解写法，缺少该导入时
    # 摘出的片段会在函数定义阶段即求值注解并失败，与超时行为无关
    future = ast.ImportFrom(
        module='__future__',
        names=[ast.alias(name='annotations')],
        level=0,
    )
    module = ast.Module(body=[future, *nodes], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(SENDERS), 'exec'), globals_dict)


def _load_helpers(timeout: float, download: Any) -> dict[str, Any]:
    # 以注入的下载实现与可控超时组装被测对象：超时分支因此无需真实网络与真实等待即可
    # 复现，download 由各用例提供，用以区分「下载被取消」与「下载继续」
    tree = ast.parse(SENDERS.read_text(encoding='utf-8'))
    globals_dict: dict[str, Any] = {
        'asyncio': asyncio,
        'IMAGE_ACQUIRE_TIMEOUT_SECONDS': timeout,
        '_download_image': download,
    }
    _exec_nodes([_node(tree, '_ImageAcquireTimeout')], globals_dict)
    _exec_nodes([_node(tree, '_acquire_gallery_image')], globals_dict)
    return globals_dict


# 三个用例共同约束超时语义：异常类型、超时后的下载行为、以及未超时的正常返回路径
class ImageAcquireTimeoutTests(unittest.TestCase):
    def test_timeout_raises_a_runtime_error_so_fallback_branches_still_catch_it(self) -> None:
        # 回退本地图片的分支以 `except RuntimeError` 捕获；超时异常若不继承 RuntimeError
        # 会穿透到框架，用户看到的是未处理异常而非「已回退本地图」的降级结果
        tree = ast.parse(SENDERS.read_text(encoding='utf-8'))
        source = ast.unparse(_node(tree, '_ImageAcquireTimeout'))
        self.assertIn('RuntimeError', source, '超时异常必须继承 RuntimeError，否则回退本地图的分支接不住')

    def test_command_gives_up_waiting_but_the_download_keeps_warming_the_cache(self) -> None:
        """超时只放弃「等待」，底层下载任务必须继续跑完并落盘。

        该不变量决定了超时是否值得付出：取消等待而保留下载，超时后的下一个请求可直接
        命中磁盘缓存；若连同下载一起取消，则每次超时都重做一遍网络往返，缓存始终为空。
        """
        finished: list[str] = []

        async def inner(url: str) -> bytes:
            # 下载耗时被刻意放大到远超超时值：完成记录即「任务是否被取消」的唯一判据
            await asyncio.sleep(0.25)
            finished.append(f'downloaded:{url}')
            return b'image-bytes'

        async def download(url: str) -> bytes:
            # 与 gallery._download_image 同构：内部是独立 Task，取消等待者不会取消它
            return await asyncio.ensure_future(inner(url))

        async def run() -> None:
            helpers = _load_helpers(0.05, download)
            acquire = helpers['_acquire_gallery_image']
            timeout_error = helpers['_ImageAcquireTimeout']

            with self.assertRaises(timeout_error):
                await acquire('https://gallery.test/a.png')

            # 命令协程已抛出超时并返回，此刻下载必须仍在进行：若该断言失败，说明取消
            # 已传播到底层任务，后续的落盘断言也就无从成立，缓存将永远无法预热
            self.assertEqual(finished, [])
            await asyncio.sleep(0.35)
            self.assertEqual(finished, ['downloaded:https://gallery.test/a.png'])

        asyncio.run(run())

    def test_fast_download_returns_bytes_without_timing_out(self) -> None:
        # 未超时的正常路径必须原样返回字节，不得被有界获取改写为异常或降级结果
        async def download(url: str) -> bytes:
            return b'fast'

        async def run() -> None:
            helpers = _load_helpers(1.0, download)
            data = await helpers['_acquire_gallery_image']('https://gallery.test/b.png')
            self.assertEqual(data, b'fast')

        asyncio.run(run())


# 接线契约：超时机制只有真正接入命令路径才有意义；仅实现 _acquire_gallery_image 而调用方
# 继续直接 await 下载，额度占用问题会原样保留
class SenderWiringTests(unittest.TestCase):
    def test_role_and_loli_senders_use_the_bounded_acquisition(self) -> None:
        source = SENDERS.read_text(encoding='utf-8')
        role_fn = source[
            source.index('async def _deliver_role_image('):source.index('async def _deliver_daily_result_image(')
        ]
        loli_fn = source[
            source.index('async def _deliver_loli_result_image('):source.index('_deliver_shota_result_image = ')
        ]

        # 超时取消不能顺着 await 传下去掐断下载，必须用 shield 保护
        acquire_fn = source[
            source.index('async def _acquire_gallery_image('):source.index('async def _deliver_role_image(')
        ]
        self.assertIn('asyncio.shield(_download_image(image_url))', acquire_fn)

        # 命令路径上不允许再直接 await 无上限的下载
        # 前两条断言确认两条投递路径改用有界获取，后两条否定断言防止旧写法回流：
        # 只要有一处绕过，该路径的命令协程就会重新长期占用命令并发额度
        self.assertIn('image: bytes = await _acquire_gallery_image(image_url)', role_fn)
        self.assertIn('image_ref = await _acquire_gallery_image(image)', loli_fn)
        self.assertNotIn('await _download_image(', role_fn)
        self.assertNotIn('await _download_image(', loli_fn)

    def test_acquire_timeout_is_a_small_bounded_value(self) -> None:
        # 上限放宽即失去意义：额度在协程返回前不予归还，超时值偏大时 25 个并发命令
        # 仍可长时间占满 Core 的命令信号量，队列积压与跨插件卡顿会复现
        constants = (ROOT / 'TodayWaifu' / 'constants.py').read_text(encoding='utf-8')
        self.assertIn('IMAGE_ACQUIRE_TIMEOUT_SECONDS', constants)
        tree = ast.parse(constants)
        value = next(
            node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == 'IMAGE_ACQUIRE_TIMEOUT_SECONDS'
        )
        self.assertLessEqual(value, 10.0, '超时上限必须足够小，否则命令额度还是会被长期占用')
        self.assertGreater(value, 0.0)


if __name__ == '__main__':
    unittest.main()
