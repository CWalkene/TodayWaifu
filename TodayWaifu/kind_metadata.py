from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DailyKindMetadata:
    bucket: str
    title: str
    role_mode: str
    text_template_key: str
    text_template_default: str
    rob_enabled_key: str
    rob_success_rate_key: str
    rob_success_key: str
    rob_success_default: str
    gift_enabled_key: str
    gift_success_key: str
    gift_success_default: str


# `nte` / `pgr` 的空字符串表示该模式不参与抢/送：`_send_rob_daily` 与
# `_send_gift_daily` 由 `_rob_enabled` / `_gift_enabled` 返回 False 直接放行，
# 永远不会读到这些 key。守卫测试（tests/test_config_references.py）会跳过空值。
DAILY_KIND_METADATA = {
    "wife": DailyKindMetadata(
        bucket="wives",
        title="老婆",
        role_mode="wife",
        text_template_key="DailyWifeTextTemplate",
        text_template_default="你今天的老婆是{name}",
        rob_enabled_key="DailyWifeRobEnabled",
        rob_success_rate_key="DailyWifeRobSuccessRate",
        rob_success_key="DailyWifeRobSuccessTemplate",
        rob_success_default="抢老婆成功！你把对方今天的老婆{name}抢过来了！",
        gift_enabled_key="DailyWifeGiftEnabled",
        gift_success_key="DailyWifeGiftSuccessTemplate",
        gift_success_default="你把今天的老婆{name}送给了对方！",
    ),
    "husband": DailyKindMetadata(
        bucket="husbands",
        title="老公",
        role_mode="husband",
        text_template_key="DailyHusbandTextTemplate",
        text_template_default="你今天的老公是{name}",
        rob_enabled_key="DailyHusbandRobEnabled",
        rob_success_rate_key="DailyWifeRobSuccessRate",
        rob_success_key="DailyHusbandRobSuccessTemplate",
        rob_success_default="抢老公成功！你把对方今天的老公{name}抢过来了！",
        gift_enabled_key="DailyHusbandGiftEnabled",
        gift_success_key="DailyHusbandGiftSuccessTemplate",
        gift_success_default="你把今天的老公{name}送给了对方！",
    ),
    "nte": DailyKindMetadata(
        bucket="nte_wives",
        title="异环老婆",
        role_mode="nte",
        text_template_key="DailyWifeNteTextTemplate",
        text_template_default="你今天的异环老婆是{name}。",
        rob_enabled_key="",
        rob_success_rate_key="",
        rob_success_key="",
        rob_success_default="",
        gift_enabled_key="",
        gift_success_key="",
        gift_success_default="",
    ),
    "pgr": DailyKindMetadata(
        bucket="pgr_wives",
        title="战双老婆",
        role_mode="pgr",
        text_template_key="DailyWifePgrTextTemplate",
        text_template_default="你今天的战双老婆是{name}。",
        rob_enabled_key="",
        rob_success_rate_key="",
        rob_success_key="",
        rob_success_default="",
        gift_enabled_key="",
        gift_success_key="",
        gift_success_default="",
    ),
    "loli": DailyKindMetadata(
        bucket="lolis",
        title="萝莉",
        role_mode="wife",
        text_template_key="",
        text_template_default="",
        rob_enabled_key="DailyLoliRobEnabled",
        rob_success_rate_key="DailyLoliRobSuccessRate",
        rob_success_key="DailyLoliRobSuccessTemplate",
        rob_success_default="抢萝莉成功！你把对方今天的萝莉抢过来了！",
        gift_enabled_key="DailyLoliGiftEnabled",
        gift_success_key="DailyLoliGiftSuccessTemplate",
        gift_success_default="你把今天的萝莉送给了对方！",
    ),
    "shota": DailyKindMetadata(
        bucket="shotas",
        title="正太",
        role_mode="shota",
        text_template_key="DailyShotaTextTemplate",
        text_template_default="你今天的正太来啦！",
        rob_enabled_key="DailyShotaRobEnabled",
        rob_success_rate_key="DailyShotaRobSuccessRate",
        rob_success_key="DailyShotaRobSuccessTemplate",
        rob_success_default="抢正太成功！你把对方今天的正太抢过来了！",
        gift_enabled_key="DailyShotaGiftEnabled",
        gift_success_key="DailyShotaGiftSuccessTemplate",
        gift_success_default="你把今天的正太送给了对方！",
    ),
    "normal": DailyKindMetadata(
        bucket="normal_wives",
        title="普通老婆",
        role_mode="normal",
        text_template_key="DailyWifeNormalTextTemplate",
        text_template_default="你今天的老婆是来自{role_id}的{name}！",
        # 普通老婆与丈夫图库一样没有抢/送入口（_load_candidates 不处理这两个 role_mode），
        # 故 rob/gift 三件套留空，由 _rob_enabled / _gift_enabled 短路放行。
        rob_enabled_key="",
        rob_success_rate_key="",
        rob_success_key="",
        rob_success_default="",
        gift_enabled_key="",
        gift_success_key="",
        gift_success_default="",
    ),
}


def daily_kind_metadata(kind: str) -> DailyKindMetadata:
    return DAILY_KIND_METADATA.get(kind, DAILY_KIND_METADATA["wife"])
