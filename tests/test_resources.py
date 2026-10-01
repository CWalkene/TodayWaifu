# 随包图片资源的完整性契约：帮助图素材必须可被 PIL 正常解码，且尺寸与色彩模式不变。
#
# 这两项并非审美偏好：尺寸改动人会直接改变帮助图排版，色彩模式退化为 RGB 会让依赖透明
# 通道的叠加层出现黑底。432f6e7 的故障是文件名引用与被引用方不一致（bj.jpg 改名后帮助图
# 背景 404），故此处以「能打开并 verify()」而非仅比较文件是否存在来守护同类问题。
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "ICON.png": ((1024, 1024), "RGBA"),
}


class ResourceIntegrityTests(unittest.TestCase):
    def test_help_resources_remain_readable_with_compatible_dimensions(self) -> None:
        # 先 verify() 再重新打开：verify() 会消耗解码器状态，同一句柄上继续读取尺寸不可靠；
        # 这一步同时能捕获截断或编码损坏的文件，而文件存在性检查对此完全无感。
        for relative, (size, mode) in EXPECTED.items():
            path = ROOT / relative
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                self.assertEqual(image.size, size, relative)
                self.assertEqual(image.mode, mode, relative)


if __name__ == "__main__":
    unittest.main()
