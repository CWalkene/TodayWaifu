# gs_wuwa_daily_wife

GSCore / GsUID 版鸣潮“今日老婆”插件。

## 插件目录

`gs_wuwa_daily_wife`

## 使用方法

固定触发命令：

```text
今日老婆
娶群友
```

插件禁用 GSCore 强制前缀继承，直接发送 `今日老婆` 或 `娶群友` 即可触发。

触发 `今日老婆` 后，插件会按当天日期、用户 ID 和当前群号固定随机一个结果；同一个用户在不同群会分别固定，不再跨群同步。

默认会随机一个鸣潮角色，然后：
2. 去 `gsuid_core/data/XutheringWavesUID/custom_role_pile/<数字ID>/`；
3. 随机取一张图片发送。

如果在控制台开启 `DailyWifeEnableGroupMember`，群聊触发时会按 `DailyWifeGroupMemberProbability` 的概率改为抽取本群已记录成员，并发送该成员头像、名称和 QQ 号。

`娶群友` 命令会直接从本群已记录成员里抽取群友，不走鸣潮角色池。

如果开启 `DailyWifeMasterUnlimited`，GSCore 主人触发 `今日老婆` 或 `娶群友` 时不会固定当天结果，可以重复随机抽取。

例如随机到“灯灯”，对照表里是 `1504：灯灯`，就会从：

```text
gsuid_core/data/XutheringWavesUID/custom_role_pile/1504/
```

里面取图发送。

## 控制台配置

- `DailyWifeCustomRolePilePath`：自定义图片目录，留空自动查找；
- `DailyWifeRoleMapPath`：自定义角色 ID 对照表，留空使用插件内置；
- `DailyWifeSendText`：是否发送“你今天的老婆是xxx”；
- `DailyWifeShowRoleId`：是否显示角色 ID；
- `DailyWifeTextTemplate`：文字模板；
- `DailyWifeEnableGroupMember`：是否启用群成员老婆，默认关闭，关闭时只抽鸣潮角色；
- `DailyWifeGroupMemberProbability`：群成员抽取概率，范围 `0~1`，例如 `0.1` 为 10%；
- `DailyWifeMasterUnlimited`：主人无限抽老婆，默认开启，开启后 GSCore 主人不会固定当天结果。

> 群成员数据来自 GSCore 已记录的本群用户缓存；如果缓存里没有成员，会自动回退为抽鸣潮角色。

## 图片目录要求

每个角色一个数字 ID 文件夹，文件夹里放图片：

```text
custom_role_pile/
├─ 1504/
│  ├─ 1.png
│  └─ 2.jpg
├─ 1203/
│  └─ encore.webp
```

支持：`.jpg`、`.jpeg`、`.png`、`.webp`、`.gif`、`.bmp`。