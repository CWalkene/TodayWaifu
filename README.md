# TodayWaifu

<p align="center">
  <img src="./ICON.png" width="160" alt="TodayWaifu ICON">
</p>

<p align="center">GSCore / GsUID 用的鸣潮「今日老婆」插件。</p>

> 本项目采用 **GNU General Public License v3.0（GPLv3）** 开源。

## 功能

- `今日老婆` / `娶婆娘`：抽鸣潮女角色；可配置概率抽群友。
- `今日老公`：抽鸣潮男角色，需要先开启配置。
- `老婆列表` / `老公列表`：查看本群当天记录。
- `抢老婆`：抢别人当天抽到的鸣潮角色老婆。
- `娶群友`：只抽群友，不依赖 XWUID。
- 主人命令：创建、上传、查看、删除自定义老婆图片。

## 依赖说明

插件本体是独立 GSCore / GsUID 插件。鸣潮角色图片默认读取本地 XutheringWavesUID（XWUID）资源，也可以在控制台切换到图库接口。

| 功能 | 是否依赖 XWUID | 说明 |
| --- | --- | --- |
| 今日老婆 | 本地模式依赖；图库模式不依赖 | 本地模式读取鸣潮角色图片，图库模式从接口获取。 |
| 今日老公 | 仅本地模式可用 | 图库模式下禁止使用今日老公，会直接提示。 |
| 抢老婆 | 是 | 抢的是别人当天抽到的鸣潮角色老婆。 |
| 老婆列表 / 老公列表 | 间接依赖 | 记录来自抽取结果。 |
| 今日老婆概率抽群友 | 否 | 使用 GSCore 群成员缓存和 QQ 头像。 |
| 娶群友 | 否 | 使用 GSCore 群成员缓存和 QQ 头像。 |

## 安装位置

把插件目录放到 GSCore 的插件目录里，例如：

```text
gsuid_core/gsuid_core/plugins/TodayWaifu
```

目录结构大概是：

```text
gsuid_core/
├── gsuid_core/
│   └── plugins/
│       └── TodayWaifu/
│           ├── __init__.py
│           ├── config_default.py
│           ├── daily_wife_config.py
│           ├── role_id_map.txt
│           └── README.md
└── data/
    └── XutheringWavesUID/
        ├── custom_role_pile/
        └── resource/
            └── role_pile/
```

安装后重启 GSCore，或者按你的部署方式刷新插件。

## 鸣潮角色图片

角色图片有两种数据源，可在控制台配置 `DailyWifeImageSource`：

- `local`：读取本地 XWUID 图片资源。
- `gallery`：读取 XWUID 图库接口并下载图片发送。

本地模式默认会查找：

```text
gsuid_core/data/XutheringWavesUID/custom_role_pile
gsuid_core/data/XutheringWavesUID/resource/role_pile
```

如果路径不在默认位置，在控制台配置：

```text
DailyWifeCustomRolePilePath
```

图库模式使用以下配置：

```text
DailyWifeGalleryApiUrl
DailyWifeGalleryUsername
DailyWifeGalleryPassword
```

图库模式下强制禁止 `今日老公` / `老公列表`，会直接提示“图库模式下禁止使用今日老公”。

角色 ID 和角色名由插件内置 `role_id_map.txt` 决定；也可以用 `DailyWifeRoleMapPath` 指定自己的对照表。

## 自定义老婆图片

以下命令需要 GSCore 主人权限。

```text
老婆创建达妮娅老婆
老婆上传图片达妮娅
老婆图片列表达妮娅
老婆删除图片达妮娅 abcd1234
老婆删除达妮娅
老婆删除确认
老婆删除取消
```

自定义上传内容保存在 GSCore / 早柚核心的数据目录：

```text
gsuid_core/data/TodayWaifu/custom_role_pile/
gsuid_core/data/TodayWaifu/custom_role_map.txt
```

不会写进 XWUID 的 `custom_role_pile`，避免和 XWUID 面板图混在一起。

## 命令

| 命令 | 说明 | 配置 |
| --- | --- | --- |
| `今日老婆` | 抽鸣潮老婆；可配置概率抽群友 | `DailyWifeEnableGroupMember` |
| `娶婆娘` | `今日老婆` 别名 | 同上 |
| `老婆列表` | 查看本群今日老婆记录 | 无 |
| `今日老公` | 抽鸣潮老公 | `DailyWifeHusbandEnabled`；图库模式不可用 |
| `老公列表` | 查看本群今日老公记录 | 无 |
| `娶群友` | 只抽群友 | `DailyWifeMarryGroupMemberEnabled` |
| `抢老婆 @对方` | 抢别人当天老婆 | `DailyWifeRobEnabled` |
| `抢今日老婆 @对方` | `抢老婆` 别名 | 同上 |
| `抢婆娘 @对方` | `抢老婆` 别名 | 同上 |

Debug 模式开启后，主人可以无限抽、指定角色名，且不写入列表：

```text
今日老婆 达妮娅
今日老公 忌炎
```

配置项：`DailyWifeDebugMode`。

## 常用配置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `DailyWifeImageSource` | `local` | 图片数据源；`local` 本地资源，`gallery` 图库接口。 |
| `DailyWifeCustomRolePilePath` | 空 | 自定义本地角色图片目录，本地模式生效。 |
| `DailyWifeRoleMapPath` | 空 | 自定义角色 ID 对照表，本地模式生效。 |
| `DailyWifeGalleryApiUrl` | `https://img.xlinxc.cn/api/xwuid/roles` | 图库接口地址，图库模式生效。 |
| `DailyWifeGalleryUsername` | 空 | 图库账号。 |
| `DailyWifeGalleryPassword` | 空 | 图库密码。 |
| `DailyWifeSendText` | 开启 | 发图片前是否带文字。 |
| `DailyWifeAtUser` | 开启 | 发送结果时是否艾特触发者。 |
| `DailyWifeShowRoleId` | 关闭 | 是否显示角色 ID。 |
| `DailyWifeDebugMode` | 关闭 | 主人调试模式。 |
| `DailyWifeEnableGroupMember` | 关闭 | `今日老婆` 是否有概率抽群友。 |
| `DailyWifeGroupMemberProbability` | `0.1` | 抽群友概率。 |
| `DailyWifeMarryGroupMemberEnabled` | 关闭 | 是否启用 `娶群友`。 |
| `DailyWifeHusbandEnabled` | 关闭 | 是否启用 `今日老公`。 |
| `DailyWifeRobEnabled` | 开启 | 是否启用抢老婆。 |
| `DailyWifeRobSuccessRate` | `0.5` | 抢老婆成功概率。 |

## 常见问题

### `今日老婆` 没图片

先确认 XWUID 资源目录存在：

```text
gsuid_core/data/XutheringWavesUID/custom_role_pile
gsuid_core/data/XutheringWavesUID/resource/role_pile
```

没有这些目录时，鸣潮角色相关功能无法正常发图。

### 群友能抽，鸣潮角色不能抽？

群友功能只依赖 GSCore 群成员缓存和 QQ 头像；鸣潮角色功能依赖 XWUID 本地图片。

### `今日老公` / `娶群友` 没反应？

先在控制台开启：

```text
DailyWifeHusbandEnabled
DailyWifeMarryGroupMemberEnabled
```

### 抢不了别人老婆？

常见原因：抢老婆功能关闭、对方今天没抽、自己已经抢过一次、抢的是自己，或对方抽到的是群友。

## 致谢

- [CWalkene](https://github.com/CWalkene)：感谢提供 PR 和改进建议。

## 开源协议

GPLv3。你可以使用、修改和分发，但需要保留许可证与版权声明；分发修改版时按 GPLv3 继续开放对应源码。
