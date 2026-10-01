"""TodayWaifu 的缓存失效钩子。

各模块的候选、来源与状态缓存分布在多个模块中，任何一次数据变更（新建或删除自定义
角色、上传图片、退出关系）都必须让相关缓存同时失效，否则会出现「图库已更新但抽到的
仍是旧候选」。把失效动作收敛到本模块，使写入方只需调用一个入口，不必了解究竟存在
哪些缓存，也避免遗漏某个缓存而留下长期驻留的陈旧数据。

反向依赖（status、normal_wife）采用延迟导入：它们在导入期依赖本模块，顶层导入会成环。
"""
from __future__ import annotations

import sys

from . import state
from .state import _SOURCE_CACHE, CANDIDATE_CACHE, _PGR_CANDIDATE_CACHE


def _invalidate_status_cache() -> None:
    # 延迟导入 + 存在性判断：status 反向依赖 shared，顶层导入会成环；未加载时跳过。
    # 未加载即意味着该模块尚未建立自己的缓存，没有需要清理的对象，因此跳过是安全的。
    if f'{__package__}.status' not in sys.modules:
        return
    from . import status as status_module

    status_module.invalidate_status_cache()


def _invalidate_candidate_cache() -> None:
    # 先递增代次再清表：在途加载完成时会比对自己记录的代次，从而放弃写回，
    # 否则失效前发出、失效后才返回的请求会把陈旧候选重新灌回缓存。
    state._CANDIDATE_CACHE_GENERATION += 1
    CANDIDATE_CACHE.clear()
    # 来源缓存与候选缓存必须同时失效：来源列表是候选的上游，只清其一会让旧来源
    # 重新生成刚被删除的候选。
    _SOURCE_CACHE.invalidate()
    _PGR_CANDIDATE_CACHE.invalidate()
    _invalidate_status_cache()
    if f'{__package__}.normal_wife' not in sys.modules:
        return
    from . import normal_wife

    # 普通老婆图库目录在磁盘上被改动后，其内存缓存同样需要重建。
    normal_wife.invalidate_normal_gallery_cache()
