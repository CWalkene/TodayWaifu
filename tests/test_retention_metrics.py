"""旧数据清理与运行时指标：表不能无限增长，卡顿要能看到瓶颈在哪一层。

表行数按「群 × 用户 × 桶 × 天数」增长，不设保留期会随运行天数线性膨胀，并拖慢按天查询
与状态页聚合（489e63e）；指标采集则用于定位零点高峰的瓶颈层级——框架只在命令排队超过
5 秒时打一条 queue_wait 警告，看不到插件内部有多少下载在飞、缓存里堆了多少群上下文。
本文件锁定两处关键取舍：清理必须可关闭且以 ISO 日期字符串做比较；指标采集必须严格只读，
否则观测动作本身会改变被观测对象的状态。
"""
import ast
import unittest
from typing import Any
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'TodayWaifu'


def _constants() -> dict[str, Any]:
    # 以受限求值读取常量而非导入 constants 模块：后者会连带拉起 GsCore 依赖；
    # 求值环境清空 __builtins__ 并只接受字面量与算式，避免执行任意代码。
    tree = ast.parse((PLUGIN / 'constants.py').read_text(encoding='utf-8'))
    values: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.targets[0], ast.Name):
            continue
        try:
            code = compile(ast.Expression(node.value), '<constants>', 'eval')
            values[node.targets[0].id] = eval(code, {'__builtins__': {}}, {})
        except (NameError, TypeError, ValueError, SyntaxError):
            continue
    return values


class RetentionTests(unittest.TestCase):
    def test_retention_default_is_bounded(self) -> None:
        # 默认值必须既非 0（否则等于默认不清理，表无限增长）也非过大：上界同时约束
        # 「保留期长于一年」这种实质等于不清理的取值。
        retention = _constants()['DAILY_RECORD_RETENTION_DAYS']
        self.assertGreater(retention, 0, '默认必须清理，否则表随天数无限增长')
        self.assertLessEqual(retention, 365)

    def test_retention_is_user_configurable_including_disabled(self) -> None:
        # 保留期须可配且显式支持 0：默认删掉用户想长期保留的历史记录不可接受，
        # 故必须给出「永久保留」这一退出路径，且该语义需在配置说明中写明。
        config = (ROOT / 'config_default.py').read_text(encoding='utf-8')
        self.assertIn("'DailyWifeRecordRetentionDays'", config)
        self.assertIn('设为 0 表示永久保留', config)

    def test_cleanup_skips_when_retention_is_disabled(self) -> None:
        # 非正数须整体短路并直接返回，而不是以负天数计算裁剪点：后者会算出未来日期，
        # 把全部历史记录一次删空。
        shared = (PLUGIN / 'shared.py').read_text(encoding='utf-8')
        fn = shared[
            shared.index('async def _prune_old_daily_records('):shared.index('def _record_retention_days(')
        ]
        self.assertIn('if retention_days <= 0:', fn)
        self.assertIn('return', fn)

    def test_cleanup_uses_an_iso_cutoff_day(self) -> None:
        # 裁剪点取「本机当天减去保留天数」并序列化为 ISO 日期：day 列即按该格式写入，
        # 两侧格式必须严格一致，否则字符串比较会得到错误的大小关系。
        # 以本机当天而非 UTC 计算，是为与记录写入时使用的日期口径保持同源。
        shared = (PLUGIN / 'shared.py').read_text(encoding='utf-8')
        fn = shared[
            shared.index('async def _prune_old_daily_records('):shared.index('def _record_retention_days(')
        ]
        self.assertIn('timedelta(days=retention_days)', fn)
        self.assertIn('.isoformat()', fn)
        self.assertIn('DailyWifeRecord.delete_before(cutoff)', fn)

    def test_cleanup_runs_inside_the_maintenance_loop(self) -> None:
        # 清理必须挂在周期维护循环上：只提供函数而无调用点，表仍会无限增长，
        # 且因删除逻辑「看起来存在」而难以发现。
        shared = (PLUGIN / 'shared.py').read_text(encoding='utf-8')
        loop = shared[
            shared.index('async def _cache_maintenance_once('):shared.index('async def _cache_maintenance_loop(')
        ]
        self.assertIn('await _prune_old_daily_records()', loop)

    def test_delete_before_compares_iso_day_strings(self) -> None:
        # 以字符串小于比较而非日期函数删除：ISO 日期（YYYY-MM-DD）定宽，字典序与时间序
        # 一致，因此可直接走索引；一旦 day 列改为其它日期格式，该比较会静默删错范围。
        source = (PLUGIN / 'models.py').read_text(encoding='utf-8')
        fn = source[source.index('async def delete_before('):source.index('async def get_record(')]
        self.assertIn('delete(cls)', fn)
        self.assertIn('cls.day < cutoff_day', fn)

    def test_retention_falls_back_to_the_constant(self) -> None:
        # 配置读取失败（空值、非数字）须回落到常量而非抛错或按 0 处理：
        # 抛错会让整个维护循环中断，按 0 处理则会静默变成永久保留，两种后果都不可见。
        shared = (PLUGIN / 'shared.py').read_text(encoding='utf-8')
        fn = shared[shared.index('def _record_retention_days('):shared.index('async def _cache_maintenance_once(')]
        self.assertIn("int(_cfg('DailyWifeRecordRetentionDays'))", fn)
        self.assertIn('return DAILY_RECORD_RETENTION_DAYS', fn)


class MetricsTests(unittest.TestCase):
    # 逐项覆盖零点高峰的瓶颈点：下载在飞数、候选加载在飞数、各类缓存规模、线程池占用
    # 与图库熔断状态。缺少任一项，对应层级的故障就只能靠猜测定位。
    EXPECTED = {
        'inflight_image_downloads',
        'inflight_candidate_loads',
        'candidate_cache_entries',
        'context_cache_entries',
        'compat_context_cache_entries',
        'source_cache_entries',
        'pgr_cache_entries',
        'member_cache_entries',
        'blocking_executor_workers',
        'gallery_circuit_open',
        'gallery_circuit_retry_after',
    }

    def test_metrics_module_covers_the_peak_bottlenecks(self) -> None:
        source = (PLUGIN / 'metrics.py').read_text(encoding='utf-8')
        for key in self.EXPECTED:
            self.assertIn(f"'{key}'", source, key)

    def test_metrics_are_read_only(self) -> None:
        # 采集不能改状态、不能触发网络或数据库：await 会引入挂起点，清除与弹出操作会
        # 直接改动被观测缓存（采集本身成为淘汰原因），日志调用则会在采集失败时递归。
        source = (PLUGIN / 'metrics.py').read_text(encoding='utf-8')
        collect = source[source.index('def collect_metrics('):source.index('def log_metrics(')]
        # 采集不能改状态、不能触发网络或数据库
        for forbidden in ('await ', '.clear()', '.pop(', 'logger.'):
            self.assertNotIn(forbidden, collect, forbidden)

    def test_metrics_are_logged_by_the_maintenance_loop(self) -> None:
        # 指标须由周期维护循环输出：仅提供采集函数而不落日志，故障时无人会主动调用，
        # 可观测性等于未启用。
        shared = (PLUGIN / 'shared.py').read_text(encoding='utf-8')
        loop = shared[
            shared.index('async def _cache_maintenance_once('):shared.index('async def _cache_maintenance_loop(')
        ]
        self.assertIn('log_metrics()', loop)

    def test_gallery_exposes_circuit_state(self) -> None:
        # 熔断状态必须经只读接口查询：allow() 会推进半开状态并消费一次探测机会，
        # 仅采集指标这一动作即可让冷却期失效，使断路器提前放行。
        source = (PLUGIN / 'gallery.py').read_text(encoding='utf-8')
        fn = source[source.index('def gallery_circuit_state('):source.index('def _retry_delay(')]
        self.assertIn('_HTTP_BREAKER.is_open(key)', fn)
        self.assertIn('_HTTP_BREAKER.retry_after(key)', fn)

    def test_metrics_docstring_explains_how_to_read_them(self) -> None:
        source = (PLUGIN / 'metrics.py').read_text(encoding='utf-8')
        # 指标没有解读说明就等于没有可观测性
        self.assertIn('瓶颈在图库网络', source)
        self.assertIn('日期翻转回收没生效', source)


if __name__ == '__main__':
    unittest.main()
