"""TodayWaifu/image_shrink.py 的单元测试：不依赖 gsuid_core，importlib 独立加载。"""
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
            self.assertEqual(image.format, 'WEBP')
            self.assertLessEqual(max(image.size), 1920)

    def test_alpha_channel_is_kept(self) -> None:
        raw = _noise_png_rgba(2400)
        self.assertGreater(len(raw), LIMIT)
        result = shrink.shrink_image_bytes(raw, LIMIT)
        self.assertLessEqual(len(result), LIMIT)
        with Image.open(io.BytesIO(result)) as image:
            self.assertIn(image.mode, ('RGBA', 'LA'))

    def test_broken_payload_is_returned_untouched(self) -> None:
        raw = b'not an image at all' * 200000
        self.assertIs(shrink.shrink_image_bytes(raw, LIMIT), raw)

    def test_animated_gif_is_returned_untouched(self) -> None:
        frames = [Image.new('P', (600, 600), index) for index in range(2)]
        buffer = io.BytesIO()
        frames[0].save(buffer, save_all=True, append_images=frames[1:], format='GIF')
        raw = buffer.getvalue() * 400
        self.assertIs(shrink.shrink_image_bytes(raw, LIMIT), raw)

    def test_zero_threshold_disables_compression(self) -> None:
        raw = _noise_image(800)
        self.assertIs(shrink.shrink_image_bytes(raw, 0), raw)

    def test_threshold_boundary_keeps_small_payload(self) -> None:
        raw = _noise_image(200)
        self.assertLessEqual(len(raw), LIMIT)
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
        self.assertNotEqual(
            shrink.shrink_cache_path(root, raw, LIMIT),
            shrink.shrink_cache_path(root, raw, LIMIT * 2),
        )

    def test_uncompressible_payload_is_not_written(self) -> None:
        raw = b'not an image at all' * 200000
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertIs(shrink.shrink_image_cached(raw, LIMIT, root), raw)
            self.assertEqual(list(root.iterdir()), [])

    def test_small_payload_skips_cache_entirely(self) -> None:
        raw = _noise_image(200)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertIs(shrink.shrink_image_cached(raw, LIMIT, root), raw)
            self.assertEqual(list(root.iterdir()), [])

    def test_unwritable_cache_still_returns_compressed(self) -> None:
        raw = _noise_image(2000)
        with TemporaryDirectory() as directory:
            blocker = Path(directory) / 'file'
            blocker.write_bytes(b'')
            # 缓存目录是个普通文件：写入失败，但仍应返回压缩结果
            result = shrink.shrink_image_cached(raw, LIMIT, blocker / 'sub')
            self.assertLessEqual(len(result), LIMIT)


if __name__ == '__main__':
    unittest.main()
