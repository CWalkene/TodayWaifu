"""图库拒绝请求时的错误提示：必须按原因给出可操作的指引。

图库（twf-gallery）在拒绝时会返回 401/403/429，但**具体原因不同**：

  - 令牌缺失 / 错误 / 已被注销  -> 应提示去控制台更新令牌
  - IP 被临时封禁（多次失败）   -> 应提示联系管理员，改令牌没用
  - 请求过于频繁（限流）        -> 应提示稍后重试，改令牌也没用
  - 今日配额用尽                -> 应提示明天再试

单看状态码无法区分，因此需要读响应体里的 `error` 字段。若一律提示「检查令牌」，
用户遇到限流时会反复去改一个本来正确的配置，排查方向完全跑偏。

另外 429 不能被当作上游故障喂给熔断器：一次限流就会把图库误熔断，
之后所有请求快速失败并回退本地，用户看到「图库挂了」而真实原因是自己请求太快。
"""
import io
import ast
import json
import unittest
from pathlib import Path
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'TodayWaifu'
GALLERY = PLUGIN / 'gallery.py'


def _load_auth_error_reason():
    """抽出 `_auth_error_reason` 单独执行。

    gallery.py 依赖 gsuid_core（本测试环境未安装），无法整包导入；
    该函数只用到 json 与 HTTPError，因此按源码抽出后单独加载。
    """
    source = GALLERY.read_text(encoding='utf-8')
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == '_auth_error_reason':
            module = ast.Module(body=[node], type_ignores=[])
            namespace = {'json': json, 'HTTPError': HTTPError}
            exec(compile(module, str(GALLERY), 'exec'), namespace)  # noqa: S102
            return namespace['_auth_error_reason']
    raise AssertionError('gallery.py 里找不到 _auth_error_reason')


auth_error_reason = _load_auth_error_reason()


def _http_error(code: int, body: dict | None = None) -> HTTPError:
    payload = b'' if body is None else json.dumps(body).encode('utf-8')
    return HTTPError('https://twfapi.xlinxc.cn/api/xwuid/roles', code, 'err', {}, io.BytesIO(payload))


class AuthErrorReasonTests(unittest.TestCase):
    def test_missing_token_403_tells_user_to_check_token(self) -> None:
        msg = auth_error_reason(_http_error(403, {'error': 'token_required'}), what='请求图库接口')
        self.assertIn('令牌', msg)
        self.assertIn('DailyWifeGalleryToken', msg)

    def test_ip_ban_is_reported_as_ban_not_token_problem(self) -> None:
        """IP 被封时改令牌无用，必须明确说是封禁。"""
        msg = auth_error_reason(_http_error(403, {'error': 'ip_banned'}), what='请求图库接口')
        self.assertIn('IP', msg)
        self.assertIn('封禁', msg)
        self.assertNotIn('DailyWifeGalleryToken', msg)

    def test_rate_limited_says_retry_later(self) -> None:
        msg = auth_error_reason(_http_error(429, {'error': 'rate_limited'}), what='请求图库接口')
        self.assertIn('频繁', msg)
        self.assertNotIn('DailyWifeGalleryToken', msg)

    def test_crawl_detected_is_treated_as_throttle(self) -> None:
        msg = auth_error_reason(_http_error(429, {'error': 'crawl_detected'}), what='请求图库接口')
        self.assertIn('频繁', msg)

    def test_quota_exceeded_mentions_quota(self) -> None:
        msg = auth_error_reason(_http_error(429, {'error': 'quota_exceeded'}), what='请求图库接口')
        self.assertIn('配额', msg)

    def test_revoked_token_401_mentions_token_update(self) -> None:
        msg = auth_error_reason(_http_error(401), what='请求图库接口')
        self.assertIn('DailyWifeGalleryToken', msg)

    def test_429_without_body_still_says_retry_later(self) -> None:
        """图库若没给 error 字段，仅凭 429 也要给出正确指引。"""
        msg = auth_error_reason(_http_error(429), what='请求图库接口')
        self.assertIn('频繁', msg)

    def test_403_without_body_falls_back_to_token_hint(self) -> None:
        msg = auth_error_reason(_http_error(403), what='请求图库接口')
        self.assertIn('DailyWifeGalleryToken', msg)

    def test_malformed_body_does_not_raise(self) -> None:
        """响应体不是 JSON 时必须优雅降级，不能把解析异常抛给用户。"""
        err = HTTPError('https://h/x', 403, 'err', {}, io.BytesIO(b'<html>not json</html>'))
        msg = auth_error_reason(err, what='下载图片')
        self.assertIn('下载图片', msg)

    def test_empty_body_does_not_raise(self) -> None:
        msg = auth_error_reason(_http_error(403), what='下载图片')
        self.assertIn('下载图片', msg)

    def test_what_prefix_is_used(self) -> None:
        """不同调用点要能区分（列表 vs 图片），便于用户定位。"""
        self.assertIn('下载图片', auth_error_reason(_http_error(403), what='下载图片'))
        self.assertIn('请求图库接口', auth_error_reason(_http_error(403), what='请求图库接口'))

    def test_all_messages_are_chinese_and_actionable(self) -> None:
        for err in (_http_error(401), _http_error(403, {'error': 'ip_banned'}), _http_error(429)):
            msg = auth_error_reason(err, what='请求图库接口')
            self.assertTrue(any('\u4e00' <= ch <= '\u9fff' for ch in msg), msg)
            self.assertTrue(msg.endswith('。') or msg.endswith('。'), msg)


class ThrottleDoesNotTripBreakerTests(unittest.TestCase):
    """429 必须与 401/403 同类处理：直接抛出，不计入熔断失败。"""

    def setUp(self) -> None:
        self.source = GALLERY.read_text(encoding='utf-8')
        self.helper = self.source[
            self.source.index('def _http_get_with_retry('):self.source.index('def _auth_error_reason(')
        ]

    def test_429_is_in_the_no_retry_branch(self) -> None:
        marker = 'if exc.code in {401, 403, 429}:'
        self.assertIn(marker, self.helper, '429 必须走「不重试且不喂熔断器」的分支')

    def test_branch_raises_without_recording_failure(self) -> None:
        marker = 'if exc.code in {401, 403, 429}:'
        branch = self.helper[self.helper.index(marker):]
        self.assertIn('raise', branch[:120])
        self.assertNotIn('record_failure', branch[:120])


class AllEntryPointsReportFriendlyErrorsTests(unittest.TestCase):
    """三个拉取入口都必须把 401/403/429 转成中文提示。

    原先 `_fetch_gallery_payload_from_url_sync`（战双 / 测试图库走这条）会让
    HTTPError 直接冒泡，用户只看到 `HTTP Error 403: Forbidden`，无法判断
    是令牌问题还是 IP 被封 —— 排查方向完全跑偏。
    """

    def setUp(self) -> None:
        self.source = GALLERY.read_text(encoding='utf-8')

    def _function_body(self, name: str, next_name: str) -> str:
        start = self.source.index(f'def {name}(')
        end = self.source.index(f'def {next_name}(')
        return self.source[start:end]

    def test_url_entry_point_maps_auth_errors(self) -> None:
        body = self._function_body('_fetch_gallery_payload_from_url_sync', '_parse_role_candidates')
        self.assertIn('_auth_error_reason', body)
        self.assertIn('{401, 403, 429}', body)

    def test_default_entry_point_maps_auth_errors(self) -> None:
        body = self._function_body('_fetch_gallery_payload_sync', '_fetch_gallery_payload_from_url_sync')
        self.assertIn('_auth_error_reason', body)

    def test_image_download_maps_auth_errors(self) -> None:
        body = self._function_body('_download_image_sync', '_download_image')
        self.assertIn('_auth_error_reason', body)

    def test_loli_and_shota_map_auth_errors(self) -> None:
        for name in ('loli.py', 'shota.py'):
            source = (PLUGIN / name).read_text(encoding='utf-8')
            self.assertIn('_auth_error_reason', source, f'{name} 应复用统一错误映射')
            self.assertIn('{401, 403, 429}', source, f'{name} 应覆盖 429')


if __name__ == '__main__':
    unittest.main()
