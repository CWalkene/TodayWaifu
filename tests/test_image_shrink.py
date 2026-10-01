"""图片压缩与压缩缓存的行为契约，覆盖「原样返回」与「缓存不再编码」两条关键边界。

本模块通过 importlib 独立加载 image_shrink.py，绕开对 gsuid_core 的依赖，使压缩逻辑
可脱离机器人框架单独验证。

守护的核心契约是**失败时原样返回**：压缩位于发送链路上，动图、解码失败与压不动
三类输入都必须退回原图，任何一处改为抛异常或返回部分数据，都会让用户侧消息发送
整体失败。另有两点历史约束：输出格式固定为 WebP 且保留 alpha（提交 d6d62e9），
以避免透明区域在 JPEG 下变黑；`assertIs` 断言的是对象同一性而非内容相等，因为
`shrink_image_cached` 以 `data is not raw` 判定是否值得落盘（提交 10c8156），
若实现改为返回等值副本，缓存写入判断即会失效。
"""
import io
import unittest
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / 'TodayWaifu' / 'image_shrink.py'
LIMIT = 2 * 1024 * 1024


def _load_module():
    spec = importlib.util.spec_from_file_location('todaywaifu_image_shrink', MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load image_shrink module')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shrink = _load_module()


def _noise_image(side: int) -> bytes:
    image = Image.effect_noise((side, side), 100).convert('RGB')
    buffer = io.BytesIO()
    image.save(buffer, format='JPEG', quality=95)
    return buffer.getvalue()


def _noise_png_rgba(side: int) -> bytes:
    image = Image.effect_noise((side, side), 100).convert('RGBA')
    image.putalpha(Image.radial_gradient('L').resize((side, side)))
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


class ImageShrinkTests(unittest.TestCase):
    def test_oversized_bytes_are_compressed(self) -> None:
        raw = _noise_image(2000)
        self.assertGreater(len(raw), LIMIT)

        result = shrink.shrink_image_bytes(raw, LIMIT)

        self.assertLessEqual(len(result), LIMIT)
        with Image.open(io.BytesIO(result)) as image:
            # 格式与边长上限均属对外契约：WebP 决定发送端可接受的类型，1920 上限决定
            # 传输量与编码耗时，二者任一放宽都会直接影响零点高峰的带宽与 CPU
            self.assertEqual(image.format, 'WEBP')
            self.assertLessEqual(max(image.size), 1920)

    def test_alpha_channel_is_kept(self) -> None:
        raw = _noise_png_rgba(2400)
        self.assertGreater(len(raw), LIMIT)
        result = shrink.shrink_image_bytes(raw, LIMIT)
        self.assertLessEqual(len(result), LIMIT)
        with Image.open(io.BytesIO(result)) as image:
            # 透明通道丢失会让立绘边缘出现黑底，故只接受携带 alpha 的模式
            self.assertIn(image.mode, ('RGBA', 'LA'))

    def test_broken_payload_is_returned_untouched(self) -> None:
        raw = b'not an image at all' * 200000
        # 断言对象同一性而非内容相等：实现以 `is raw` 判定「未压缩」，等值副本会导致
        # 缓存层误判为产生了新的压缩结果并写入磁盘
        self.assertIs(shrink.shrink_image_bytes(raw, LIMIT), raw)

    def test_animated_gif_is_returned_untouched(self) -> None:
        frames = [Image.new('P', (600, 600), index) for index in range(2)]
        buffer = io.BytesIO()
        frames[0].save(buffer, save_all=True, append_images=frames[1:], format='GIF')
        raw = buffer.getvalue() * 400
        # 逐帧压缩既无法收敛到阈值以内，又会丢失动画语义，故动图整体放行
        self.assertIs(shrink.shrink_image_bytes(raw, LIMIT), raw)

    def test_zero_threshold_disables_compression(self) -> None:
        raw = _noise_image(800)
        # 非正阈值是「不限制体积」的约定写法，用于保留发送原图的路径
        self.assertIs(shrink.shrink_image_bytes(raw, 0), raw)

    def test_threshold_boundary_keeps_small_payload(self) -> None:
        raw = _noise_image(200)
        self.assertLessEqual(len(raw), LIMIT)
        # 恰好达标时不得触发重编码：边界误判会让所有小图都多做一次无效的解码与编码
        self.assertIs(shrink.shrink_image_bytes(raw, LIMIT), raw)


class ShrinkCacheTests(unittest.TestCase):
    def test_second_call_hits_disk_without_reencoding(self) -> None:
        raw = _noise_image(2000)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = shrink.shrink_image_cached(raw, LIMIT, root)
            self.assertLessEqual(len(first), LIMIT)
            self.assertEqual(len(list(root.glob(f'{shrink.SHRINK_CACHE_PREFIX}*'))), 1)

            original = shrink.shrink_image_bytes

            # 以抛异常的替身替换编码入口，是证明「命中缓存即短路」的唯一可靠手段：
            # 断言返回值相等无法区分「读盘命中」与「重新编码得到同样结果」，而重新编码
            # 正是缓存所要消除的开销。替身在 finally 中还原，因为模块对象由全部测试共享。
            def fail(*args, **kwargs):
                raise AssertionError('命中压缩缓存时不应再次编码')

            try:
                shrink.shrink_image_bytes = fail
                self.assertEqual(shrink.shrink_image_cached(raw, LIMIT, root), first)
            finally:
                shrink.shrink_image_bytes = original

    def test_different_threshold_uses_different_cache_entry(self) -> None:
        raw = b'x' * 10
        root = Path('cache')
        # 缓存键必须包含阈值：阈值变化意味着目标体积不同，复用旧条目会让更严格的阈值
        # 拿到超出上限的结果，等于压缩整体失效
        self.assertNotEqual(
            shrink.shrink_cache_path(root, raw, LIMIT),
            shrink.shrink_cache_path(root, raw, LIMIT * 2),
        )

    def test_uncompressible_payload_is_not_written(self) -> None:
        raw = b'not an image at all' * 200000
        with TemporaryDirectory() as directory:
            root = Path(directory)
            # 原样返回的结果不得落盘：把「压不动」的原图复制进缓存既无命中价值，
            # 又会按原图体积持续膨胀磁盘占用
            self.assertIs(shrink.shrink_image_cached(raw, LIMIT, root), raw)
            self.assertEqual(list(root.iterdir()), [])

    def test_small_payload_skips_cache_entirely(self) -> None:
        raw = _noise_image(200)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            # 未超阈值的输入直接放行，连缓存目录都不应创建，避免为无收益的图片产生读写
            self.assertIs(shrink.shrink_image_cached(raw, LIMIT, root), raw)
            self.assertEqual(list(root.iterdir()), [])

    def test_unwritable_cache_still_returns_compressed(self) -> None:
        raw = _noise_image(2000)
        with TemporaryDirectory() as directory:
            blocker = Path(directory) / 'file'
            blocker.write_bytes(b'')
            # 将缓存根路径指向普通文件的子路径：写入必然失败，但降级路径仍须返回压缩结果，
            # 因为缓存仅承担加速职责，不可因磁盘异常而影响消息发送
            result = shrink.shrink_image_cached(raw, LIMIT, blocker / 'sub')
            self.assertLessEqual(len(result), LIMIT)


if __name__ == '__main__':
    unittest.main()
