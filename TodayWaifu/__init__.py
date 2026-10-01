# ruff: noqa: E402, F401, I001
"""TodayWaifu - 鸣潮今日老婆 GsCore 插件

内层包入口：声明插件，并通过子模块导入触发各命令注册。业务逻辑全部位于本包
各子模块中，入口自身不含实现。

其后的子模块导入均为**有意的副作用导入**（导入即注册 `@sv.on_xxx` 触发器）。
顺序上存在两项不可交换的约束：

1. `Plugins(...)` 必须先于任何触发器注册 —— 触发器需要绑定到已声明的插件实例，
   否则注册目标不存在。
2. 子模块之间的依赖方向为单向：`shared` 是最底层（提供 SV 实例、数据模型与工具
   函数），其余模块均从 `shared` 取用；`help` 必须在 `daily` 之前，`daily` 的
   命令注册依赖 `help` 已提供 `register_help`。

因此本文件显式关闭 E402 / F401 / I001 —— 自动排序会打乱上述加载顺序，导入失败
或命令优先级错乱，故这两条 lint 规则在此处不适用。
"""
from gsuid_core.sv import Plugins

Plugins(
    name='TodayWaifu',
    disable_force_prefix=True,
    allow_empty_prefix=True,
)

# 导入顺序即为命令加载顺序，且不可重排（详见模块文档字符串）
from . import shared       # 公共层：SV 实例、数据模型、工具函数
from . import help         # 帮助命令 + register_help（须在 daily 之前）
from . import normal_wife  # 普通老婆远程图库
from . import daily        # 每日抽取 / 列表 / 娶群友 / 老公
from . import pgr          # 战双本地图库抽取
from . import rob          # 抢老婆
from . import gift         # 送老婆
from . import divorce      # 离婚
from . import loli         # 萝莉 / 下载
from . import shota        # 今日正太（远程图库）
from . import custom_role  # 自定义老婆
from . import status       # core状态统计
