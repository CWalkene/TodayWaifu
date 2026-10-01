# 交互命令注册与配置键的耦合契约。
#
# 抢、送、离婚三条交互链路的入口分散在 rob/gift/divorce 与 config_default 两处：命令触发器
# 在插件模块中声明，而开关与文案模板在配置项中声明，两侧仅靠字符串键名耦合。任一侧改名都会
# 静默失效——命令仍在但读不到配置，或配置存在却无入口触发，用户侧只表现为「功能没反应」。
#
# 本文件以源码文本断言把两侧的键名与命令别名钉在一起，并锁定每日记录落库与离婚状态标记的
# 归属（daily_store 为唯一真值源，daily_state.py 曾是缺少 'normal' 的重复实现，已随 d7461ab
# 删除）。
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class InteractionConfigSourceTests(unittest.TestCase):
    def test_help_appearance_config_page_exists(self) -> None:
        # 帮助图外观配置同时需要在 config_default 声明与 help.py 消费，且外观参数必须
        # 作为 column 传入渲染；缺任一环节，控制台的配置项要么不出现，要么调整后无效果。
        # 调用方直接读整个文件文本，故分组字典（APPEARANCE_CONFIG_DEFAULT）中的项同样计入。
        config_source = (ROOT / 'config_default.py').read_text(encoding='utf-8')
        daily_config_source = (ROOT / 'daily_wife_config.py').read_text(encoding='utf-8')
        help_source = (ROOT / 'TodayWaifu' / 'help.py').read_text(encoding='utf-8')
        for key in (
            'DailyWifeHelpBannerBgUpload',
            'DailyWifeHelpBgUpload',
            'DailyWifeHelpIconUpload',
            'DailyWifeHelpColumn',
        ):
            self.assertIn(key, config_source)
            self.assertIn(key, help_source)
        self.assertIn("DailyWifeShowConfig = StringConfig(", daily_config_source)
        self.assertIn("'今日老婆外观配置'", daily_config_source)
        self.assertIn('column=column', help_source)

    def test_new_interaction_configs_exist(self) -> None:
        # 遍历所有字典字面量收集键名，而非只匹配 CONFIG_DEFAULT：配置项可能被拆到多个
        # 分组字典中，只扫顶层会把已存在的键误判为缺失。
        source = (ROOT / 'config_default.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        keys = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key in node.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        keys.add(key.value)
        expected = {
            # 萝莉图库地址已并入统一的 DailyWifeApiUrl
            'DailyWifeApiUrl',
            'DailyHusbandRobEnabled',
            'DailyHusbandRobSuccessTemplate',
            'DailyHusbandGiftEnabled',
            'DailyHusbandGiftSuccessTemplate',
            'DailyLoliRobEnabled',
            'DailyLoliRobSuccessRate',
            'DailyLoliRobSuccessTemplate',
            'DailyLoliGiftEnabled',
            'DailyLoliGiftSuccessTemplate',
        }
        self.assertTrue(expected.issubset(keys), expected - keys)


class InteractionSourceTests(unittest.TestCase):
    def test_rob_and_gift_register_husband_and_loli_commands(self) -> None:
        # 别名是用户实际输入的入口，遗漏某一别名不会有任何报错，只是该说法静默失效；
        # 因此逐条列举而非只检查主命令。
        rob_source = (ROOT / 'TodayWaifu' / 'rob.py').read_text(encoding='utf-8')
        gift_source = (ROOT / 'TodayWaifu' / 'gift.py').read_text(encoding='utf-8')
        for word in ('抢老公', '抢今日老公', '抢萝莉', '抢今日萝莉'):
            self.assertIn(word, rob_source)
        for word in (
            '送老公', '送今日老公', '同意送老公', '拒绝送老公',
            '送萝莉', '送今日萝莉', '同意送萝莉', '拒绝送萝莉',
        ):
            self.assertIn(word, gift_source)
        for word in ('接受老婆赠送', '拒绝老婆赠送', '接受老公赠送', '拒绝老公赠送', '接受萝莉赠送', '拒绝萝莉赠送'):
            self.assertIn(word, gift_source)

    def test_loli_daily_record_is_persisted(self) -> None:
        # 萝莉记录必须写入 context['lolis'] 并走统一的记录序列化：只存在于内存时，
        # 进程重启或跨模块读取都会丢记录，用户当天会重复抽到不同萝莉。
        source = (ROOT / 'TodayWaifu' / 'loli.py').read_text(encoding='utf-8')
        self.assertIn("context['lolis']", source)
        self.assertIn('_record_to_dict', source)

    def test_result_sender_calls_pass_kind_argument(self) -> None:
        # 86333ee 之前，抢、送两条链路的调用点都漏传了 kind，而 `_send_rob_result_image` /
        # `_send_gift_result_image` 的该参数没有默认值，命令一执行即抛 TypeError，用户侧
        # 表现为抢或送完全没有回复。kind 决定结果图走角色图还是萝莉/正太渲染分支，故必须
        # 逐调用点显式传入。
        rob_source = (ROOT / 'TodayWaifu' / 'rob.py').read_text(encoding='utf-8')
        gift_source = (ROOT / 'TodayWaifu' / 'gift.py').read_text(encoding='utf-8')
        self.assertIn('ev.group_id is not None,\n        kind,', rob_source)
        self.assertIn('ev.group_id is not None,\n        kind,', gift_source)

    def test_loli_gift_request_does_not_include_image_id_name(self) -> None:
        # 萝莉记录的 name 形如「萝莉图<内容标识>」（见 `_loli_record_name`），若照搬老婆追加
        # `role.name` 的写法，请求文案会把内部图片标识暴露给用户，并重复「萝莉」前缀。
        gift_source = (ROOT / 'TodayWaifu' / 'gift.py').read_text(encoding='utf-8')
        self.assertIn("item_text = title if kind == 'loli' else f'{title}{role.name}'", gift_source)

    def test_divorce_module_registers_one_unified_command(self) -> None:
        # 离婚入口按对象类型拆成多个协程，共用一个 SV，别名则按类型分组；任一类型漏注册，
        # 该类型记录将无法解除，用户只能等次日自然重置。
        init_source = (ROOT / 'TodayWaifu' / '__init__.py').read_text(encoding='utf-8')
        shared_source = (ROOT / 'TodayWaifu' / 'shared.py').read_text(encoding='utf-8')
        divorce_source = (ROOT / 'TodayWaifu' / 'divorce.py').read_text(encoding='utf-8')
        self.assertIn('from . import divorce', init_source)
        self.assertIn("divorce_sv = SV('今日老婆-离婚'", shared_source)
        for word in (
            '离婚',
            '老婆离婚',
            '离婚老婆',
            '离婚老公',
            '离婚萝莉',
            '离婚群友',
            '群友离婚',
            '异环老婆离婚',
            '战双老婆离婚',
        ):
            self.assertIn(word, divorce_source)
        self.assertIn('async def divorce_wife(', divorce_source)
        self.assertIn('async def divorce_husband(', divorce_source)
        self.assertIn('async def divorce_loli(', divorce_source)

    def test_natural_command_aliases_are_registered(self) -> None:
        # 自定义角色与萝莉图片管理各有一组口语化别名，帮助图另需登记入口命令；
        # 别名与帮助文案不同步时，用户无法从帮助中得知可用说法。
        custom_source = (ROOT / 'TodayWaifu' / 'custom_role.py').read_text(encoding='utf-8')
        loli_source = (ROOT / 'TodayWaifu' / 'loli.py').read_text(encoding='utf-8')
        help_source = (ROOT / 'TodayWaifu' / 'help.py').read_text(encoding='utf-8')
        for word in ('创建老婆', '上传老婆图片', '查看老婆图片', '删除老婆图片', '确认删除老婆', '取消删除老婆'):
            self.assertIn(word, custom_source)
        for word in ('上传萝莉图片', '查看萝莉图片'):
            self.assertIn(word, loli_source)
        self.assertIn('老婆帮助', help_source)

    def test_divorce_marks_selected_daily_record_with_divorced_state(self) -> None:
        # 离婚状态判定与整批标记都住在 daily_store（daily_state.py 曾是重复实现，已删除）。
        # 判定与标记必须落在同一模块，否则两处对状态的理解会各自演化，出现「显示已离婚但
        # 抽签仍视为占用」之类的矛盾。
        daily_store_source = (ROOT / 'TodayWaifu' / 'daily_store.py').read_text(encoding='utf-8')
        divorce_source = (ROOT / 'TodayWaifu' / 'divorce.py').read_text(encoding='utf-8')
        self.assertIn("raw.get('divorced')", daily_store_source)
        self.assertIn("return 'divorced'", daily_store_source)
        self.assertIn('ALL_DAILY_RECORD_KINDS', daily_store_source)
        self.assertIn("context['safe_wives']", daily_store_source)
        self.assertIn("record['divorced'] = True", divorce_source)


    def test_rob_rejects_when_robber_already_has_active_item(self) -> None:
        # 已有占用对象时抢必须被拒绝，否则同一用户会同时持有两条当日记录，
        # 离婚只能解除其一，另一条将无法通过命令清理。
        rob_source = (ROOT / 'TodayWaifu' / 'rob.py').read_text(encoding='utf-8')
        self.assertIn('robber_data = context[bucket].get(robber_id)', rob_source)
        self.assertIn("f'你今天已经有{title}了，先离婚再抢吧~'", rob_source)


if __name__ == '__main__':
    unittest.main()
