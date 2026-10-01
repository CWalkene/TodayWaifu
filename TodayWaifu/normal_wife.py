"""普通老婆（跨作品动漫角色）图库的加载与缓存。

本模块只负责把远程图库响应转成候选角色，不注册任何命令：普通老婆由「今日老婆」共用同一
套抽取与每日唯一性流程，仅在开关开启时替换候选池，因此这里不设独立指令入口。
"""
from __future__ import annotations

from urllib.error import URLError, HTTPError
from urllib.parse import urlparse

from .shared import (
    LOG_PREFIX,
    CACHE_TTL_SECONDS,
    DEFAULT_GALLERY_BASE_URL,
    RoleCandidate,
    _cfg,
    logger,
    _fetch_gallery_payload_from_url_sync,
)
from .executor import run_blocking
from .payloads import GalleryPayload
from .source_cache import AsyncSourceCache

# 容量上限按「配置过的接口地址个数」估算而非按角色数：缓存以地址为键，用户在控制台反复
# 修改统一地址时旧键会残留，有界 LRU 可避免其无限增长
_NORMAL_GALLERY_CACHE = AsyncSourceCache[GalleryPayload](CACHE_TTL_SECONDS, max_entries=4)


def prune_normal_gallery_cache() -> None:
    # 主动清理过期项：定时维护路径调用，使长期空闲的实例不必等到下次读取才腾出内存
    _NORMAL_GALLERY_CACHE.prune()


def invalidate_normal_gallery_cache() -> None:
    # 失效用于配置变更后立即生效；进行中的加载不被取消，仅使其结果不再写入缓存
    _NORMAL_GALLERY_CACHE.invalidate()


def _normal_gallery_api_url() -> str:
    url = str(_cfg('DailyWifeApiUrl') or DEFAULT_GALLERY_BASE_URL).strip().rstrip('/')
    # 兼容用户按旧文档填写完整接口地址（含 /ceshi/ 段）或以 /roles 结尾的情况；其余地址补全
    # 为默认路径，避免拼出重复段
    if '/ceshi/' in url or url.endswith('/roles'):
        return url
    return f'{url}/api/ceshi/roles'


def _parse_normal_gallery_candidates(
    payload: GalleryPayload,
) -> tuple[RoleCandidate, ...]:
    # 空列表与结构缺失同样视为失败：返回空候选会让上层报「没有可用角色」，掩盖接口异常
    roles_data = payload.get('roles')
    if not isinstance(roles_data, list) or not roles_data:
        raise RuntimeError('普通老婆图库没有返回可用角色。')

    candidates: list[RoleCandidate] = []
    # 逐条跳过而非整体报错：图库混有其他作品数据时，单个格式异常的条目不应导致整次抽取失败
    for item in roles_data:
        if not isinstance(item, dict):
            continue
        role_ids_data = item.get('role_ids')
        if not isinstance(role_ids_data, list):
            continue
        # 过滤空白标识后重建为元组：标识会写入记录并参与二手判定，不能含空串
        role_ids = tuple(
            str(role_id).strip()
            for role_id in role_ids_data
            if str(role_id).strip()
        )
        if not role_ids:
            continue

        # 缺 name 时以首个标识代称：文案模板需要非空角色名，留空会输出残缺提示
        name = str(item.get('name') or role_ids[0]).strip()
        images_data = item.get('images')
        if not name or not isinstance(images_data, list):
            continue
        images: list[str] = []
        for image_item in images_data:
            if not isinstance(image_item, dict):
                continue
            image_url = str(image_item.get('url') or '').strip()
            parsed = urlparse(image_url)
            # 仅接受完整 HTTPS 地址：明文 HTTP 图片在协议端可能被拒或被压缩转发，且相对路径
            # 无法在发送阶段解析；去重保证同一角色的图片不因重复上报获得更高权重
            if parsed.scheme == 'https' and parsed.netloc and image_url not in images:
                images.append(image_url)
        # 无可用图片的角色不进候选池：抽中它只会得到空结果
        if images:
            candidates.append(RoleCandidate(name, role_ids, tuple(images)))

    if not candidates:
        raise RuntimeError('普通老婆图库没有返回有效的 HTTPS 图片。')
    return tuple(candidates)


async def _load_normal_wife_candidates() -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    api_url = _normal_gallery_api_url()
    # 地址为空表示配置被显式清空：此处不静默回退默认地址，避免用户以为已停用却仍在访问远程
    if not api_url:
        return None, '未配置普通老婆图库接口。'

    try:
        payload = await _NORMAL_GALLERY_CACHE.get(
            api_url,
            lambda: run_blocking(_fetch_gallery_payload_from_url_sync, api_url),
        )
        candidates = _parse_normal_gallery_candidates(payload)
        return candidates, None
    except HTTPError as exc:
        # 401/403 直接指向令牌问题，可给出明确处置建议；其余状态码没有可操作的归因，如实带上
        # 状态码回报即可，避免把限流等临时故障误报成配置错误
        if exc.code in {401, 403}:
            message = '图库访问令牌无效或未配置。'
        else:
            message = f'请求普通老婆图库失败，HTTP {exc.code}。'
        logger.warning(f'{LOG_PREFIX} {message}')
        return None, message
    except (RuntimeError, URLError, TimeoutError, OSError) as exc:
        # 失败以 (None, 原因) 返回而非外抛：调用方需要在开关开启但图库不可用时据此提示用户，
        # 而不是让整个「今日老婆」命令以异常结束
        message = str(exc) or '读取普通老婆图库失败。'
        logger.warning(f'{LOG_PREFIX} 读取普通老婆图库失败: {message}')
        return None, message
