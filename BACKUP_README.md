# 抖音录制系统 v3.0.0 — 备份说明

## 标签
`v3.0.0-stable` — 2026-05-14 16:18 (UTC+7)

## v3.0.0 核心改进

### 检测引擎
- ✅ #87 检测逻辑：`page.evaluate()` 直接调 `script:not([src])` + 结束关键词 + `<video>` 标签检查
- ✅ `_try_eval()` — page.evaluate 安全包装（30s 超时）
- ✅ `_safe_reload()` — 直接 try/except，不用 threading（防 greenlet 崩溃）
- ✅ `WATCHDOG_TIMEOUT = 180` 秒主循环看门狗
- ✅ `URLLIB_TIMEOUT = 30` 秒网络超时
- ✅ `PAGE_EVAL_TIMEOUT = 30000` 毫秒 JS 评估超时

### 资源优化
- ✅ **下播即关页面** — 检测到不在直播立刻关闭 Playwright 页面
- ✅ **自动重新打开** — 已关闭的页面下次循环自动开新标签页（数据最新）
- ✅ **去掉周期性全部 reload** — 之前每 5 分钟重载 21 个页面导致卡死
- ✅ **动态房间检测间隔 30 秒** — 新房间在 30 秒内被检测到
- ✅ **新房间立即录制** — 不等检测循环，添加后立刻 `is_live_page` + 获取流地址 + 录制

### 自续命
- ✅ 运行 **270 分钟（4.5 小时）** 后自动 dispatch 下一轮任务，确保 24 小时覆盖

### 转录
- ✅ `handle_room_end` 中实时转录（主播下播→停止录制→转录→上传 Release）
- ✅ `finally` 块扫描未处理 WAV 文件转录（取消后补救）
- ✅ `transcribe_orphan.py` — 模型只加载一次，处理所有文件
- ✅ `upload_orphan.py` — 打印下载 URL 到日志
- ✅ TXT 带时间戳 + 标点符号

### 上传
- ✅ 中文文件名 URL 编码（`urllib.parse.quote`）
- ✅ Release 作主要存储（永久），Artifact 作辅助（90 天）
- ✅ 实时上传（`upload_now()` — pure urllib.request，无 gh CLI 依赖）

### Cleanup Job（取消后补救）
- ✅ 独立的 `cleanup` job，`if: always() && needs.record.result == 'cancelled'`
- ✅ 下载已录制 Artifact → 扫描 WAV 转写 → 上传到 Release + Artifact
- ✅ 使用 `actions/cache/restore@v4`

### YAML 修复
- ✅ `continuous.yml` 语法正确（通过 `yaml.safe_load` 验证）
- ✅ `workflow_dispatch` + 6 个 cron（每 4.5 小时，30 分钟重叠）
- ✅ 定时覆盖：泰国 05:00, 09:30, 14:00, 18:30, 23:00, 03:30

## 文件清单
| 文件 | 说明 |
|------|------|
| `recorder.py` | 主程序（recorder_merged.py） |
| `transcriber.py` | SenseVoiceSmall 转写 |
| `transcribe_orphan.py` | Cleanup job 转录脚本（模型只加载一次） |
| `upload_orphan.py` | Cleanup job 上传脚本（日志显示下载 URL） |
| `requirements.txt` | Python 依赖 |
| `rooms.txt` | 26 个直播间 |
| `.github/workflows/continuous.yml` | 主工作流 + cleanup job |

## 已知注意事项
1. Cleanup job 首次运行需下载模型（~893MB），后续命中缓存
2. Cleanup job 的 SRT 文件上传可能因文件名含中文失败，TXT 正常
3. `handle_room_end` 转录独立于 cleanup job，即使 cleanup 失败也能在主任务内完成
