"""图片上传鉴权边界的回归测试。

上传是写入共享图库的唯一入口，鉴权失效的后果不可逆：放行了未授权用户，任何人都能把
任意图片写入所有群共用的图库，既消耗磁盘也绕过内容审核；反之若解析过于严格，配置里
合法的白名单写法会被判为无效，表现为「配了却不生效」而没有任何报错。

本文件同时锁定字符串形式白名单的解析约定：Core 的配置项可能是字符串、列表或 None，
因此必须容忍逗号、空格与换行混合的分隔方式，且比较前去除首尾空白。
"""
import unittest
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / 'TodayWaifu' / 'upload_access.py'


def _load_module():
    # 每次重新执行模块文件而非复用导入结果：模块自身无状态，独立加载可避免与其它
    # 测试文件在同名模块上互相干扰。
    spec = importlib.util.spec_from_file_location('todaywaifu_upload_access', MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load upload_access module')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UploadAccessTests(unittest.TestCase):
    def test_whitelisted_user_can_upload(self) -> None:
        # 白名单命中的正向路径：主人名单为空时白名单必须独立生效，
        # 否则「只配白名单、不配主人」的部署会整体失去上传能力。
        access = _load_module()
        self.assertTrue(access.can_upload_images('10001', [], ['10001']))

    def test_master_bypasses_empty_whitelist(self) -> None:
        # 主人是兜底权限：白名单为空（或尚未配置）时主人仍须可用，
        # 否则全新部署在第一次配置白名单之前无法上传任何图片。
        access = _load_module()
        self.assertTrue(access.can_upload_images('10001', ['10001'], []))

    def test_unlisted_user_cannot_upload(self) -> None:
        # 拒绝路径同样重要：两份名单都不含该用户时必须返回 False。
        # 若此处误判为放行，鉴权即形同虚设，且不会有任何异常提示。
        access = _load_module()
        self.assertFalse(access.can_upload_images('10001', ['20002'], ['30003']))

    def test_string_whitelist_accepts_common_separators(self) -> None:
        # 配置以字符串写入时，用户惯用逗号、空格或换行分隔；三种写法都必须被识别。
        # 该用例同时覆盖「去空白后精确匹配」的策略：'10002' 与列表项之间不能因
        # 空白差异而被判为不匹配。
        access = _load_module()
        self.assertTrue(access.can_upload_images('10002', [], '10001, 10002\n10003'))


if __name__ == '__main__':
    unittest.main()
