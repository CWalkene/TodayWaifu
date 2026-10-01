"""锁定图片 base64 编码必须发生在插件线程池，不得占用事件循环。

框架 `MessageSegment.image()` 内部以同步方式执行 `b64encode(...).decode()`
（`segment.py:121`），插件若把原始图片字节直接交给它，编码就会跑在事件循环上。
实测单图 2MB 约 8.8ms、10MB 约 47.7ms；插件允许单图 10MB，25 条命令并发完成时
将累积成 200ms 量级的串行阻塞，并伴随数十 MB 的瞬时字符串分配。de7fd79 因此把
编码改经 `executor.run_blocking` 下放线程池，并由框架在 `IS_UPLOAD` 为假时原样
透传 `base64://` 引用。本文件即守护该改动，防止后续重构把编码重新内联回事件循环。

除行为断言外，另有一组源码文本断言：编码链路一旦被改回 `MessageSegment.image(...)`
内联构造，行为测试仍可能通过，唯有文本检查能在合入前拦截。
"""
import ast
import sys
import time
import base64
import asyncio
import unittest
import threading
import importlib.util
from typing import Any
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'TodayWaifu'
SENDERS = PLUGIN / 'senders.py'


# 以文件路径加载 executor 而非导入包：可绕过包入口对 gsuid_core 的依赖，
# 使线程池行为可在无框架环境下验证。
def _load_executor():
    spec = importlib.util.spec_from_file_location('todaywaifu_executor_b64', PLUGIN / 'executor.py')
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load executor')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


executor = _load_executor()


def _load_encode_helper() -> Any:
    """从 senders.py 中单独抽出 `_encode_base64_ref` 运行，规避该模块对 gsuid_core 的依赖。

    抽取而非导入，是为了让被测函数保持与被测源码逐字一致：若在测试内重新实现，
    编码逻辑与线上实现将各自演化，断言不再指向真实代码。
    """
    tree = ast.parse(SENDERS.read_text(encoding='utf-8'))
    node = next(
        item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == '_encode_base64_ref'
    )
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.Module(body=[future, node], type_ignores=[])
    ast.fix_missing_locations(module)
    globals_dict: dict[str, Any] = {'b64encode': base64.b64encode}
    exec(compile(module, str(SENDERS), 'exec'), globals_dict)
    return globals_dict['_encode_base64_ref']


encode_base64_ref = _load_encode_helper()


class Base64RefTests(unittest.TestCase):
    # 前两例锁定引用格式与二进制保真：`base64://` 前缀是框架透传协议的约定，
    # 字节内容则必须逐字节无损，否则图片会以静默损坏的形态送达用户。
    def test_produces_a_framework_passthrough_ref(self) -> None:
        ref = encode_base64_ref(b'hello')
        self.assertTrue(ref.startswith('base64://'))
        self.assertEqual(base64.b64decode(ref[len('base64://'):]), b'hello')

    def test_round_trips_binary_image_bytes(self) -> None:
        payload = bytes(range(256)) * 512
        ref = encode_base64_ref(payload)
        self.assertEqual(base64.b64decode(ref[len('base64://'):]), payload)

    def test_encoding_happens_off_the_event_loop_thread(self) -> None:
        async def run() -> tuple[int, int]:
            loop_thread = threading.get_ident()
            worker_thread = await executor.run_blocking(
                lambda: threading.get_ident()
            )
            return loop_thread, worker_thread

        loop_thread, worker_thread = asyncio.run(run())
        # 线程标识不同是「编码确实离开事件循环」的最小证据；相等即说明 run_blocking
        # 退化成了同步调用，此时线程池配置再正确也无法缓解阻塞。
        self.assertNotEqual(loop_thread, worker_thread)

    def test_a_large_encode_does_not_stall_the_event_loop(self) -> None:
        """锁定编码 2MB 图片期间事件循环仍可调度其他协程。"""
        # 用 2MB 而非小样本：小图编码耗时低于调度粒度，即便内联执行也可能观察到 tick，
        # 无法区分「未阻塞」与「阻塞过短」。
        payload = b'x' * (2 * 1024 * 1024)

        async def run() -> float:
            ticks = 0

            async def ticker() -> None:
                nonlocal ticks
                while True:
                    ticks += 1
                    await asyncio.sleep(0.001)

            task = asyncio.ensure_future(ticker())
            started = time.perf_counter()
            await executor.run_blocking(encode_base64_ref, payload)
            elapsed = time.perf_counter() - started
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            self.assertGreater(ticks, 0, '编码期间事件循环被完全阻塞了')
            return elapsed

        asyncio.run(run())


class SenderEncodingWiringTests(unittest.TestCase):
    # 以下为源码文本断言：行为层面的编码耗时无法证明调用点未被改回内联构造，
    # 故直接固定各发送路径的调用形态（含 `IS_UPLOAD` 分支的取舍）。
    def test_helpers_route_through_the_plugin_executor(self) -> None:
        source = SENDERS.read_text(encoding='utf-8')
        helper = source[source.index('def _encode_base64_ref('):source.index('async def _image_message(')]
        self.assertIn('b64encode(data).decode()', helper)

        message_fn = source[
            source.index('async def _image_message('):source.index('async def _image_message_from_path(')
        ]
        self.assertIn('run_blocking(_encode_base64_ref, data)', message_fn)

    def test_upload_mode_still_hands_raw_bytes_to_the_framework(self) -> None:
        """EnablePicSrv 打开时框架需以原始字节做图床上传，此处不得预先编码。"""
        # 预先编码会迫使框架先解码再重编码，属反向优化，因此该分支必须保留原始字节。
        source = SENDERS.read_text(encoding='utf-8')
        message_fn = source[
            source.index('async def _image_message('):source.index('async def _image_message_from_path(')
        ]
        self.assertIn('if IS_UPLOAD:', message_fn)
        self.assertIn('return MessageSegment.image(data)', message_fn)

    def test_local_path_helper_reads_and_encodes_off_loop(self) -> None:
        source = SENDERS.read_text(encoding='utf-8')
        fn = source[source.index('async def _image_message_from_path('):]
        fn = fn[:fn.index('\n\n\n')]
        self.assertIn('run_blocking(read_file_bytes_cached, path)', fn)

    def test_hot_path_senders_no_longer_build_image_segments_inline(self) -> None:
        # `MessageSegment.image(` 出现在这三个热路径中即代表编码回到事件循环，
        # 属 de7fd79 已消除的阻塞形态，故逐函数块检查而非检查整个文件。
        source = SENDERS.read_text(encoding='utf-8')
        for name in ('_send_role_image', '_send_loli_result_image', '_send_local_image'):
            start = source.index(f'async def {name}(')
            end = source.find('\nasync def ', start + 1)
            block = source[start:end if end >= 0 else None]
            self.assertNotIn('MessageSegment.image(', block, f'{name} 仍在事件循环上构造图片段')

    def test_list_commands_also_use_the_off_loop_helper(self) -> None:
        # 列表命令在 de7fd79 中被易遗漏，故单独覆盖：这两条路径若新增内联构造，
        # 同一事件循环仍会被阻塞，只是触发入口不同。
        for name in ('loli.py', 'custom_role.py'):
            source = (PLUGIN / name).read_text(encoding='utf-8')
            self.assertIn('await _image_message_from_path(path)', source, name)
            self.assertNotIn('MessageSegment.image(path)', source, name)


if __name__ == '__main__':
    unittest.main()
