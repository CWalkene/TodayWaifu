# 每日对象类型元数据表的契约：桶名、配置键与默认文案必须单一真值源且彼此自洽。
#
# 抽卡、抢、送与配置引用守卫四条流程共用同一张类型表。这些字段原先散落在各流程代码中，
# 任一处漏改即产生两类静默故障：配置项写了却不生效（键名拼写或归属不一致），以及默认文案
# 与键对不上。d7461ab 还发现 `normal` 引用了 config_default 中不存在的键，每次取值都退化到
# 兜底分支并输出告警；因此这里逐字段锁定桶名与配置键的归属。
#
# 未知类型回退到 wife 的行文见 `test_unknown_kind_keeps_legacy_wife_fallback`：该回退是历史
# 兼容路径，一旦改为抛错，仍携带未登记类型的旧数据在升级后会直接读取失败。
import sys
import unittest
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "TodayWaifu" / "kind_metadata.py"


def _load_module():
    # 以模块名登记进 sys.modules 是加载成功的必要条件：dataclass 在装饰期会回调
    # sys.modules[cls.__module__]，不登记时该查询返回 None，执行即抛 AttributeError。
    # 按文件路径独立加载则使其不依赖 gsuid_core。
    spec = importlib.util.spec_from_file_location("todaywaifu_kind_metadata", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load kind_metadata module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DailyKindMetadataTests(unittest.TestCase):
    def test_known_kinds_have_centralized_metadata(self) -> None:
        # 逐字段覆盖各类型：任一字段被改成相邻类型的值（例如把 shota 的桶写成 wives）
        # 都不会有异常，只会在运行时表现为「抽到的角色串台」，故必须在此显式核对。
        kinds = _load_module()
        self.assertEqual(set(kinds.DAILY_KIND_METADATA), {"wife", "husband", "loli", "shota", "nte", "pgr", "normal"})
        self.assertEqual(kinds.daily_kind_metadata("wife").bucket, "wives")
        self.assertEqual(kinds.daily_kind_metadata("husband").title, "老公")
        self.assertEqual(kinds.daily_kind_metadata("loli").rob_enabled_key, "DailyLoliRobEnabled")
        self.assertEqual(kinds.daily_kind_metadata("shota").bucket, "shotas")
        self.assertEqual(kinds.daily_kind_metadata("shota").title, "正太")
        self.assertEqual(kinds.daily_kind_metadata("shota").text_template_default, "你今天的正太来啦！")
        self.assertEqual(kinds.daily_kind_metadata("nte").bucket, "nte_wives")
        self.assertEqual(kinds.daily_kind_metadata("nte").role_mode, "nte")
        self.assertEqual(kinds.daily_kind_metadata("pgr").bucket, "pgr_wives")
        self.assertEqual(kinds.daily_kind_metadata("pgr").text_template_key, "DailyWifePgrTextTemplate")
        self.assertEqual(kinds.daily_kind_metadata("normal").bucket, "normal_wives")
        self.assertEqual(kinds.daily_kind_metadata("normal").role_mode, "normal")
        self.assertEqual(kinds.daily_kind_metadata("normal").text_template_key, "DailyWifeNormalTextTemplate")
        self.assertEqual(
            kinds.daily_kind_metadata("husband").gift_success_default,
            "你把今天的老公{name}送给了对方！",
        )

    def test_unknown_kind_keeps_legacy_wife_fallback(self) -> None:
        # 返回同一对象（is 而非 ==）而非等价副本：调用方可能依赖恒等性做缓存或比较，
        # 且回退必须是既有记录可读的历史兼容路径，不得改为抛错。
        kinds = _load_module()
        self.assertIs(kinds.daily_kind_metadata("unknown"), kinds.DAILY_KIND_METADATA["wife"])

    def test_metadata_includes_templates_and_config_keys(self) -> None:
        # 萝莉与正太都填有抢/送配置键，其键名必须与 config_default 中的实际键名逐字一致：
        # 键名写错不会报错，只会让 `_rob_enabled` / `_gift_enabled` 落到配置兜底值，
        # 开关随之静默失效。
        kinds = _load_module()
        loli = kinds.daily_kind_metadata("loli")
        self.assertEqual(loli.rob_success_rate_key, "DailyLoliRobSuccessRate")
        self.assertEqual(loli.rob_success_default, "抢萝莉成功！你把对方今天的萝莉抢过来了！")
        self.assertEqual(loli.gift_enabled_key, "DailyLoliGiftEnabled")
        self.assertEqual(loli.gift_success_key, "DailyLoliGiftSuccessTemplate")
        shota = kinds.daily_kind_metadata("shota")
        self.assertEqual(shota.rob_enabled_key, "DailyShotaRobEnabled")
        self.assertEqual(shota.gift_enabled_key, "DailyShotaGiftEnabled")
        self.assertEqual(shota.rob_success_default, "抢正太成功！你把对方今天的正太抢过来了！")
        self.assertEqual(shota.gift_success_default, "你把今天的正太送给了对方！")


if __name__ == "__main__":
    unittest.main()
