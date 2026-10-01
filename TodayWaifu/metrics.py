"""插件侧可观测性：把高峰期真正关键的几个数字暴露出来。

框架只会在命令排队超过 5 秒时打一条 `queue_wait` 警告，看不到插件内部
「有多少下载在飞」「缓存里堆了多少群上下文」「图库是否已熔断」。
这里把这些指标定期打进日志，卡顿时可以直接判断瓶颈在哪一层：

- `inflight_image_downloads` 高 + `context_cache_entries` 正常
  → 瓶颈在图库网络，看 `gallery_circuit_open`
- `context_cache_entries` 接近「活跃群数 × 2」
  → 日期翻转回收没生效
- `inflight_candidate_loads` 长期不为 0
  → 候选列表加载卡住，所有抽签都在等它

判读方式建立在指标之间的相对关系上，而非绝对阈值：各群活跃度与部署规模差异过大，
单点数值无法设定通用上限，比值与趋势才具备可比性。采集过程不触碰网络与数据库，
因此在故障状态下仍可安全调用。
"""
from __future__ import annotations

from gsuid_core.logger import logger

from .state import (
    _MEMBER_CACHE,
    _SOURCE_CACHE,
    _IMAGE_INFLIGHT,
    CANDIDATE_CACHE,
    _CONTEXT_REGISTRY,
    _CANDIDATE_INFLIGHT,
    _DAILY_CONTEXT_CACHE,
    _PGR_CANDIDATE_CACHE,
)
from .executor import blocking_executor_workers
from .constants import LOG_PREFIX


def collect_metrics() -> dict[str, int | bool]:
    """采集当前运行时指标（只读，不产生副作用）。

    函数内导入各被观测模块，使指标采集不参与它们的导入链：观测代码不得成为插件启动
    的依赖，也避免模块间循环导入。读取的全是缓存大小、在飞任务数与熔断状态，均无锁
    无等待，因此可在维护循环中按固定间隔调用而不干扰业务路径。
    """
    from .gallery import gallery_circuit_state
    from .senders import image_delivery_backlog
    from .file_cache import cached_url_count
    from .daily_store import pending_write_count

    circuit_open, retry_after = gallery_circuit_state()
    return {
        'image_delivery_backlog': image_delivery_backlog(),
        'pending_db_writes': pending_write_count(),
        'cached_image_urls': cached_url_count(),
        'inflight_image_downloads': len(_IMAGE_INFLIGHT),
        'inflight_candidate_loads': len(_CANDIDATE_INFLIGHT),
        'candidate_cache_entries': len(CANDIDATE_CACHE),
        'context_cache_entries': len(_CONTEXT_REGISTRY.cache),
        'compat_context_cache_entries': len(_DAILY_CONTEXT_CACHE),
        'source_cache_entries': _SOURCE_CACHE.size,
        'pgr_cache_entries': _PGR_CANDIDATE_CACHE.size,
        'member_cache_entries': _MEMBER_CACHE.size,
        'blocking_executor_workers': blocking_executor_workers(),
        'gallery_circuit_open': circuit_open,
        'gallery_circuit_retry_after': int(retry_after),
    }


def log_metrics() -> dict[str, int | bool]:
    """把指标打进日志并返回，供维护循环与测试使用。

    返回值使调用方与测试无需解析日志文本即可断言指标，同时日志保留单行汇总，便于在
    事故现场按时间轴比对相邻采样，识别指标是持续恶化还是瞬时抖动。
    """
    metrics = collect_metrics()
    summary = ', '.join(f'{key}={value}' for key, value in metrics.items())
    logger.info(f'{LOG_PREFIX} 运行时指标: {summary}')
    return metrics
