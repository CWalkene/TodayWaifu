<div align="center">

<img src="./ICON.png" width="160" alt="TodayWaifu ICON">

# TodayWaifu

_基于 [早柚核心（GsCore）](https://github.com/Genshin-bots/gsuid_core) 的多游戏「今日老婆」娱乐插件_

[![License: GPLv3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![GsCore](https://img.shields.io/badge/GsCore-%E6%97%A9%E6%9F%9A%E6%A0%B8%E5%BF%83-8f5db7)](https://github.com/Genshin-bots/gsuid_core)
[![Python](https://img.shields.io/badge/Python-%E2%89%A53.11-3776ab)](pyproject.toml)

每天零点一过，全群一起抽今日老婆 —— 还能抢、能送、能娶群友。

<a href="https://count.getloli.com/"><img src="https://count.getloli.com/get/@TodayWaifu?theme=moebooru" alt="TodayWaifu 访问计数"></a>

[交流 Q 群 (798949533)](https://qm.qq.com/q/pJVt8HNwrg) | [问题反馈](https://github.com/MimoKit/TodayWaifu/issues)

</div>

TodayWaifu 为 GsCore 提供群娱乐互动玩法：鸣潮、异环、战双、萝莉、正太各玩各的，当天结果全天固定，可抢、可送、可离婚。安装后发送 `今日老婆帮助` 查看全部玩法。

## 特性

- **多游戏同抽** —— 各游戏独立开抽，互不干扰
- **全天固定** —— 每天一抽，当日结果全员一致
- **群互动** —— 抢老婆、送老婆、娶群友、离婚
- **图源灵活** —— 每个功能可独立选择本地图片或远程图库
- **出图不卡** —— 图片走独立投递队列，命令秒回

## 快速开始

```mermaid
flowchart LR
    A[克隆或 core 安装] --> B[重启 GsCore]
    B --> C[发送 今日老婆帮助]
    C --> D[开抽]
```

1. 部署好 [GsCore](https://github.com/Genshin-bots/gsuid_core)，将本仓库克隆到插件目录，或向 bot 发送 `core安装插件TodayWaifu`：

```bash
cd gsuid_core/gsuid_core/plugins
git clone https://github.com/MimoKit/TodayWaifu
```

2. 重启 GsCore，发送 `今日老婆帮助` 获取可视化帮助图 —— 全部指令与玩法都在里面。

## 配置

在 **GsCore 网页控制台** 配置。图片来源按功能独立开关：

| 配置项 | 作用范围 | 默认 |
| --- | --- | --- |
| `DailyWifeImageSource` | 今日老婆 / 老公 | `local` |
| `DailyWifeNteImageSource` | 今日异环 | `gallery` |
| `DailyWifePgrImageSource` | 今日战双 | `gallery` |
| `DailyLoliImageSource` | 今日萝莉 | `gallery` |

- **`local`** —— 只用本地图库（`XutheringWavesUID`、`NTEUID` 等），不请求远程接口
- **`gallery`** —— 走远程图库接口，接口不可用时回退本地

各默认值与升级前行为一致。图库启用令牌鉴权后填写 `DailyWifeGalleryToken`（进 [Q 群](https://qm.qq.com/q/pJVt8HNwrg) 获取）。

> [!WARNING]
> 远程图库会拉取线上图片，部分内容可能存在风控风险，请自行评估，风险由部署者承担。

## 环境要求

- [GsCore](https://github.com/Genshin-bots/gsuid_core) 最新版，Python `>= 3.11`

## 开源协议

[GPLv3](LICENSE)，仅供学习交流，严禁商用。感谢 [An](https://github.com/An-Sun110) 提供图库服务器支持、[CWalkene](https://github.com/CWalkene) 的修改建议。

<br/>

## Star History

<a href="https://www.star-history.com/?repos=MimoKit%2FTodayWaifu&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=MimoKit/TodayWaifu&type=date&theme=dark&legend=top-left&sealed_token=iGSy87OqFTUvED8ayYLjTFrw_W7IlBP5_jY6Q_ua8FnsJDLS0SoSUjqMvyUKaRF42CC16rhG0iVTRvAzrovXVw-AHeca_zndYF3RwQfVhE2KWan11v5JC8XjvW3z3hkkpqPEmH0CxEBpKjsWtwBTMlL_Xi16v4ig4KgoEph17U9LAGBNMDGUbsyMoz8M" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=MimoKit/TodayWaifu&type=date&legend=top-left&sealed_token=iGSy87OqFTUvED8ayYLjTFrw_W7IlBP5_jY6Q_ua8FnsJDLS0SoSUjqMvyUKaRF42CC16rhG0iVTRvAzrovXVw-AHeca_zndYF3RwQfVhE2KWan11v5JC8XjvW3z3hkkpqPEmH0CxEBpKjsWtwBTMlL_Xi16v4ig4KgoEph17U9LAGBNMDGUbsyMoz8M" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=MimoKit/TodayWaifu&type=date&legend=top-left&sealed_token=iGSy87OqFTUvED8ayYLjTFrw_W7IlBP5_jY6Q_ua8FnsJDLS0SoSUjqMvyUKaRF42CC16rhG0iVTRvAzrovXVw-AHeca_zndYF3RwQfVhE2KWan11v5JC8XjvW3z3hkkpqPEmH0CxEBpKjsWtwBTMlL_Xi16v4ig4KgoEph17U9LAGBNMDGUbsyMoz8M" />
 </picture>
</a>
