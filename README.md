<div align="center">

<img src="./ICON.png" width="140" alt="TodayWaifu">

# TodayWaifu

_Multi-game daily waifu plugin for GsCore (gsuid_core)_

<a href="LICENSE"><img alt="License" src="https://img.shields.io/github/license/MimoKit/TodayWaifu?style=for-the-badge&label=License&color=blue"></a>
<img alt="Python" src="https://img.shields.io/badge/Python-3.11%2B-3776ab?style=for-the-badge&logo=python&logoColor=white">
<a href="https://github.com/MimoKit/TodayWaifu/actions/workflows/test.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/MimoKit/TodayWaifu/test.yml?style=for-the-badge&label=Tests"></a>
<a href="https://github.com/MimoKit/TodayWaifu/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/MimoKit/TodayWaifu?style=for-the-badge&logo=github&color=8f5db7&label=Stars"></a>
<a href="https://github.com/Genshin-bots/gsuid_core"><img alt="GsCore" src="https://img.shields.io/badge/GsCore-%E6%97%A9%E6%9F%9A%E6%A0%B8%E5%BF%83-76bad9?style=for-the-badge"></a>

**[Quick Start](#-quick-start) · [Features](#-features) · [Tech Stack](#-tech-stack) · [Roadmap](#-roadmap)**

<!-- 截图/GIF 放这里 -->

<a href="https://count.getloli.com/"><img src="https://count.getloli.com/get/@TodayWaifu?theme=moebooru" alt="TodayWaifu 访问计数"></a>

[交流 Q 群 (798949533)](https://qm.qq.com/q/pJVt8HNwrg) · [问题反馈](https://github.com/MimoKit/TodayWaifu/issues)

</div>

## 💡 Concept

> 让全群在零点之后一起抽当天的老婆：结果全天固定，可抢、可送、可娶群友。
> 出图走异步队列不卡命令，图源在本地目录与远程图库之间按功能独立切换。
> 所有指令与玩法收敛在一张可视化帮助图里，发送 `今日老婆帮助` 即可查看。

---

## ✨ Features

| Feature | Description |
| --- | --- |
| 🎮 多游戏同抽 | 鸣潮、异环、战双、萝莉、正太独立开抽，互不干扰 |
| 📅 全天固定 | 每天一抽，当日结果全员一致、可查可回顾 |
| 🤝 群互动玩法 | 抢老婆、送老婆、娶群友、离婚一条龙 |
| 🖼 灵活图源 | 每个功能独立选择本地图库或远程图库接口 |
| ⚡ 异步出图 | 常驻投递队列消费图片，命令协程秒回不阻塞 |

---

## 🚀 Quick Start

```bash
cd gsuid_core/gsuid_core/plugins
git clone https://github.com/MimoKit/TodayWaifu
# 或直接向 bot 发送：core安装插件TodayWaifu
```

1. 部署好 [GsCore](https://github.com/Genshin-bots/gsuid_core)，执行上方命令并重启 GsCore
2. 发送 `今日老婆帮助` 获取可视化帮助图，一图看懂全部玩法

> [!TIP]
> 插件指令较多，玩法规则以帮助图为准，无需翻文档。

<details>
<summary><b>⚙️ 配置（图片来源 / 令牌）</b></summary>

<br>

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

</details>

---

## 🏗 Tech Stack

| 层级 | 技术 |
| --- | --- |
| 运行时 | Python 3.11+ · asyncio |
| 框架 | [GsCore (gsuid_core)](https://github.com/Genshin-bots/gsuid_core) 插件体系 |
| 图片处理 | Pillow · WebP 动态压缩 |
| 测试 | pytest · 独立契约测试（不依赖 Core 运行时） |
| 存储 | GsCore data 目录 · 磁盘缓存 |

<details>
<summary><b>📁 项目结构</b></summary>

<br>

```text
TodayWaifu/
├── TodayWaifu/            # 插件主体
│   ├── daily.py           #   每日触发器与分配
│   ├── gallery.py         #   本地 / 远程候选加载
│   ├── delivery.py        #   图片投递队列
│   ├── circuit_breaker.py #   图库断路器
│   ├── file_cache.py      #   磁盘缓存
│   └── ...                #   领域模型 / 互动玩法 / 帮助
├── tests/                 # 独立契约测试（pytest）
├── texture2d/             # 帮助图素材
├── config_default.py      # 控制台配置项声明
├── help.json              # 帮助图指令定义
├── role_id_map.json       # 角色映射表
└── pyproject.toml
```

</details>

---

## 🗺 Roadmap

- [x] 多游戏图源（鸣潮 / 异环 / 战双 / 萝莉 / 正太）
- [x] 图片来源按功能独立开关
- [x] 图片投递队列与插件重载自愈
- [x] 角色台词库 · WebP 动态压缩
- [ ] 零点高峰专项优化（专用执行器、下载与命令额度解耦）
- [ ] 玩法与帮助图持续扩展

---

## 🤝 Contributing

1. Fork 本仓库并创建特性分支 `git checkout -b feat/xxx`
2. 本地跑通 `python -m pytest tests/ -q`
3. 提交 [Pull Request](https://github.com/MimoKit/TodayWaifu/compare)

欢迎提 [Issue](https://github.com/MimoKit/TodayWaifu/issues) 或进 [交流 Q 群](https://qm.qq.com/q/pJVt8HNwrg) 讨论。

---

## 📄 License

MimoKit. Licensed under [GPL-3.0](LICENSE). 仅供学习交流，严禁商用。感谢 [An](https://github.com/An-Sun110) 提供图库服务器支持、[CWalkene](https://github.com/CWalkene) 的修改建议。

---

## Star History

<a href="https://www.star-history.com/?repos=MimoKit%2FTodayWaifu&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=MimoKit/TodayWaifu&type=date&theme=dark&legend=top-left&sealed_token=iGSy87OqFTUvED8ayYLjTFrw_W7IlBP5_jY6Q_ua8FnsJDLS0SoSUjqMvyUKaRF42CC16rhG0iVTRvAzrovXVw-AHeca_zndYF3RwQfVhE2KWan11v5JC8XjvW3z3hkkpqPEmH0CxEBpKjsWtwBTMlL_Xi16v4ig4KgoEph17U9LAGBNMDGUbsyMoz8M" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=MimoKit/TodayWaifu&type=date&legend=top-left&sealed_token=iGSy87OqFTUvED8ayYLjTFrw_W7IlBP5_jY6Q_ua8FnsJDLS0SoSUjqMvyUKaRF42CC16rhG0iVTRvAzrovXVw-AHeca_zndYF3RwQfVhE2KWan11v5JC8XjvW3z3hkkpqPEmH0CxEBpKjsWtwBTMlL_Xi16v4ig4KgoEph17U9LAGBNMDGUbsyMoz8M" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=MimoKit/TodayWaifu&type=date&legend=top-left&sealed_token=iGSy87OqFTUvED8ayYLjTFrw_W7IlBP5_jY6Q_ua8FnsJDLS0SoSUjqMvyUKaRF42CC16rhG0iVTRvAzrovXVw-AHeca_zndYF3RwQfVhE2KWan11v5JC8XjvW3z3hkkpqPEmH0CxEBpKjsWtwBTMlL_Xi16v4ig4KgoEph17U9LAGBNMDGUbsyMoz8M" />
 </picture>
</a>
