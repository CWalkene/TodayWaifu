from __future__ import annotations

from pathlib import Path

from gsuid_core.data_store import get_res_path

# BASE_DIR 指向插件仓库根目录，用于定位随插件分发的只读资源；用户数据一律经
# data_root() 落到 Core 的资源目录，两者不可混用，否则升级会覆盖用户数据。
BASE_DIR = Path(__file__).parent.parent
# 内置角色对照表：升级时随插件一同替换，因此只能读、不能在上层写回。
ROLE_MAP_JSON_PATH = BASE_DIR / 'role_id_map.json'
LEGACY_ROLE_MAP_PATH = BASE_DIR / 'role_id_map.txt'
HELP_ICON_PATH = BASE_DIR / 'ICON.png'
PGR_WIFE_DIR_NAME = 'pgr_wife'
LOLI_IMAGE_DIR_NAME = 'loli_images'
ROLE_QUOTES_FILE_NAME = 'role_quotes.json'
# 随插件分发的内置台词库（含鸣潮、异环、战双角色），与 ICON.png / role_id_map.json 同级
BUNDLED_ROLE_QUOTES_PATH = BASE_DIR / ROLE_QUOTES_FILE_NAME


def data_root() -> Path:
    """用户数据根目录，由 Core 按部署环境解析，插件不得假设其具体位置。"""
    return get_res_path('TodayWaifu')


def role_upload_map() -> Path:
    # 自定义角色对照表与内置表分离：写入用户目录才能跨升级保留，且避免污染只读资源。
    return data_root() / 'custom_role_map.json'


def role_upload_root() -> Path:
    # 图片按角色 ID 分目录存放，删除角色时整目录移除即可，不会牵连其它角色。
    return data_root() / 'custom_role_pile'


def pgr_root() -> Path:
    return data_root() / PGR_WIFE_DIR_NAME


def loli_root() -> Path:
    return data_root() / LOLI_IMAGE_DIR_NAME


def role_quotes_path() -> Path:
    """只读随插件分发的台词库。

    曾把内置库播种到 data 并优先读那份，结果是升级后的新库永远被旧副本挡住
    （#27 播种的自创台词一直压过 #30 的官方原文），故不再读 data 副本。
    """
    return BUNDLED_ROLE_QUOTES_PATH
