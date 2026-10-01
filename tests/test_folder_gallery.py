# 本模块守护 `TodayWaifu/folder_gallery.py` 的图库契约：只在一级子目录中按整名匹配角色
# 目录（路径穿越因此在比较阶段即被挡下），扫描递归收集图片并跳过隐藏目录与非图片后缀，
# 结果以绝对路径返回。图库目录由用户自行组织，这些输入都不受控，故用例中的大小写混写、
# 非图片文件、隐藏目录与 `../` 前缀均为真实输入的样本；而相对路径一旦依赖当前工作目录
# 就会指错文件，扫描还须在目录缺失时完成初始化。
import sys
import tempfile
import unittest
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / 'TodayWaifu' / 'folder_gallery.py'


def _load_module():
    spec = importlib.util.spec_from_file_location('todaywaifu_folder_gallery', MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load folder_gallery module')
    module = importlib.util.module_from_spec(spec)
    # 必须显式登记进 sys.modules：该模块日后若引入包内相对导入，缺少登记会让
    # `from .x import y` 在加载期失败，而问题只在测试环境暴露。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FolderGalleryTests(unittest.TestCase):
    def test_scans_role_folders_recursively_and_ignores_other_files(self) -> None:
        gallery = _load_module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'pgr_wife'
            # 刻意混入非图片、伪装成图片的隐藏目录与嵌套皮肤目录：断言结果只保留 2 张图，
            # 既锁定扩展名过滤，也锁定「隐藏目录一律不当作角色图库」的约定。
            (root / '露西亚' / '皮肤').mkdir(parents=True)
            (root / '露西亚' / 'a.PNG').write_bytes(b'a')
            (root / '露西亚' / '皮肤' / 'b.jpg').write_bytes(b'b')
            (root / '露西亚' / 'note.txt').write_text('ignore', encoding='utf-8')
            (root / '.cache').mkdir()
            (root / '.cache' / 'hidden.png').write_bytes(b'c')

            rows = gallery.scan_named_role_directories(root, {'.png', '.jpg'})

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], '露西亚')
            self.assertEqual(len(rows[0][1]), 2)
            # 抽取结果会被缓存或跨进程传递，相对路径一旦依赖当前工作目录就会指错文件，
            # 因此这里锁定返回绝对路径这一契约。
            self.assertTrue(all(Path(path).is_absolute() for path in rows[0][1]))

    def test_creates_missing_root_and_returns_empty_gallery(self) -> None:
        gallery = _load_module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'missing'
            # 首次使用时用户尚未建立图库目录，扫描须承担初始化职责：返回空图库而非抛错，
            # 否则首次抽签会以异常中断，而非落到「暂无可用图片」的提示分支。
            self.assertEqual(gallery.scan_named_role_directories(root, {'.png'}), ())
            self.assertTrue(root.is_dir())

    def test_finds_only_existing_direct_role_directory(self) -> None:
        gallery = _load_module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'pgr_wife'
            role_dir = root / '露西亚'
            role_dir.mkdir(parents=True)

            # 前后空白来自用户输入或配置项，查表时必须归一化，否则与已存在的目录擦肩而过。
            self.assertEqual(
                gallery.find_named_role_directory(root, ' 露西亚 '),
                role_dir,
            )
            # `../露西亚` 不拼接、不规范化为真实路径，只在目录名层做整名比较；这样路径穿越
            # 在比较阶段即被挡下，无需依赖后续校验。最后一句同时确认该调用不会顺带创建目录：
            # 上传白名单流程依赖「查询无副作用」这一点。
            self.assertIsNone(gallery.find_named_role_directory(root, '不存在'))
            self.assertIsNone(gallery.find_named_role_directory(root, '../露西亚'))
            self.assertFalse((root / '不存在').exists())


if __name__ == '__main__':
    unittest.main()
