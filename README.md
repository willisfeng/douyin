# 抖音直播云端录制器

在 GitHub Actions 上 24 小时持续监控录制抖音直播，无需登录。

## 原理

1. 无头 Chromium 打开直播间页面 `https://live.douyin.com/{roomId}`
2. 每 15 秒检测一次页面内嵌 script 中的 `flv_pull_url` 数据
3. 开播瞬间提取最高画质（FULL_HD1 > HD1 > SD1 > SD2）的 FLV 流地址
4. ffmpeg `-c copy` 直接下载，不转码，原画质原音质
5. 下播自动停止，继续等待下一场

## 24h 覆盖机制

公共仓库无限额度。安排 5 个工作流实例轮流运行，每 5 小时启动一个：

| UTC 启动 | 泰国时间 | 覆盖时段 |
|----------|---------|---------|
| 00:00 | 07:00 | 07:00~12:30 |
| 05:00 | 12:00 | 12:00~17:30 |
| 10:00 | 17:00 | 17:00~22:30 |
| 15:00 | 22:00 | 22:00~03:30 |
| 20:00 | 01:00 | 01:00~06:30 |

相邻实例有 30 分钟重叠，确保无缝切换。每个实例跑 5.5 小时（留 30 分钟给上传+清理）。

## 使用方法

### 1. 配置房间
编辑 [`rooms.txt`](rooms.txt)，一行一个房间ID：

```
12345678901 # 主播名字
23456789012
```

### 2. 推送代码到 GitHub
新建公共仓库 → push → 自动开始跑

### 3. 手动触发
Actions → `24h连续监控` → Run workflow → 立刻启动一次

### 4. 下载录制
每次运行后 Actions 页面 → Summary → Artifacts，保留 90 天

文件名格式: `{roomId}_{FULL_HD1|HD1}_{YYYYMMDD_HHMMSS}.mp4`

## 文件结构

```
├── recorder.py              # 核心录制器
├── check_live.py            # 开播检测器
├── rooms.txt                # 房间ID配置
├── requirements.txt         # Python依赖
└── .github/workflows/
    └── continuous.yml       # 24h连续监控工作流
```
