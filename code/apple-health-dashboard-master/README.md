<div align="center">

# 🍎 Apple Health Dashboard

## Boji Health 本地数据库模式

当前定制版使用 Python 标准库提供本地服务，并通过 SQLite 按用户保存数据，无需安装额外依赖。

1. 双击 `启动.bat`，它会同时启动前端、后端并打开浏览器。
2. 浏览器打开 `http://127.0.0.1:4173/boji-health.html`。
3. 首次使用时注册用户名和密码；用户名是唯一键。
4. 后续打开页面时会自动恢复登录会话和该用户的数据。

数据库默认保存在项目根目录的 `data/boji_health.db`。备份该文件即可备份所有本地账户与记录。不要继续使用普通静态文件服务器启动本页面，因为登录和数据库 API 由 `server.py` 提供。

### 统一记录元数据

饮食、力量训练和花销记录保留原有字段，另有统一的 `meta` 对象：

```json
{
  "schemaVersion": 1,
  "recordType": "food",
  "ownerUsername": "用户名",
  "entryMethod": "manual",
  "valueSource": "knowledge_base",
  "sourceRef": {"sheet": "工作表名称", "cell": "D15"},
  "confidence": "high",
  "createdAt": "2026-10-22T04:00:00Z",
  "updatedAt": "2026-10-22T04:00:00Z",
  "version": 1
}
```

`entryMethod` 记录数据如何进入系统（手动、记账同步或生成测试记录），`valueSource` 记录营养/消耗/金额的依据。缺失的历史创建时间和来源坐标保留为 `null`，不会编造。生成的测试记录仍参与现有统计及 Agent 上下文，不做过滤。后端按登录会话校正 `ownerUsername`，并在单条内容发生变化时增加 `version`。旧数据库可运行 `python migrate_record_metadata.py` 补齐结构；脚本会先创建一份数据库备份，重复执行不会重复添加元数据。

### 记录级数据库与迁移

本地服务启动时会自动运行版本化迁移（也可在本目录手动执行 `python migrate_records.py`）。迁移前将已有数据库备份为 `data/boji_health.db.pre-records-*.bak`，保留 `user_data.data_json` 中的旧记录，并逐账户核对饮食、训练、支出条数及热量、蛋白质、花销合计。校验失败会回滚新记录；迁移成功后 `PRAGMA user_version` 为 `2`。建议另外妥善保存备份文件，其中包含个人健康及账户数据。

新数据层由通用 `records` 表、`food_records`、`workout_records`、`exercise_sets`、`expense_records` 明细表和 `record_events` 审计表组成。饮食与支出通过记录 ID 关联；每条记录有独立 `version`，通过 SQLite 外键、`CHECK` 和索引保证基础完整性。

- `GET/POST /api/records/{food|workout|expense}`：按登录用户读取或新增单条记录。新增请求为 `{"record":{...}}`。
- `GET/PATCH/DELETE /api/records/{类型}/{记录ID}`：读取、修改或软删除。修改请求为 `{"expectedVersion":1,"changes":{...}}`；删除请求为 `{"expectedVersion":1}`。版本不匹配返回 HTTP `409` 和 `currentVersion`。
- `GET /api/records/{类型}/{记录ID}/events`：查看该记录的创建、修改、删除事件。
- `GET /api/records/summary?days=7`（或 `30`）：服务端汇总与证据记录 ID；默认包含演示记录。仅当显式使用 `includeDemo=0` 时排除演示记录。

前端对饮食、力量训练和花销只调用上述逐条写入接口；`/api/data` 继续保存个人资料、设置和聊天，不再覆盖记录明细。Agent 不接收整份账户记录，而是在回答时按需调用当前账户的只读工具。

### 指标字典与分析层

`metric_definitions.py` 定义版本化指标 ID、单位、公式、来源、缺失规则和估算标记；`analytics.py` 从新记录表计算 7/30 天指标与证据记录 ID。`GET /api/analytics/metrics` 返回字典，`GET /api/analytics?days=7`（或 `30`）返回分析报告；可显式传 `includeDemo=0` 排除演示记录，默认仍包含。首页与 Agent 共用这套服务端计算，旧 `/api/records/summary` 保留兼容。

分析报告中的 `value: null` 表示没有足够记录或资料，不能解释为实际摄入、训练或花销为零；`recordedDays` 只表示该日存在记录，不保证完整记录了三餐或全部支出。目标热量和营养建议沿用原界面公式 `formulaVersion: 1`，属于估算，不是医学诊断。每个指标通过 `evidenceKey` 指向报告中的记录 ID，`sourceBreakdown` 区分知识库、AI 和公式估算等来源。

饮食记录页可直接输入食物名称（知识库名称有自动补全）和重量；点击“查询营养”后先匹配本地知识库，未命中再使用 DeepSeek 估算。预览热量、营养和来源后才能确认保存；自定义食物需提供克数，按“份”录入仅适用于知识库标有每份重量的食物。可选备注支持组合餐拆分。查询失败不会生成饮食记录；如果组合餐保存中途失败，已成功的单项仍保留，剩余项可重试。记账同步饮食继续使用同一个查询工具。

### 配置 Chat Agent

复制项目根目录的 `.env.example` 为 `.env`，填写自己的 `DEEPSEEK_API_KEY`；也可以双击 `配置DeepSeek.bat` 在隐藏输入提示中配置。服务启动时读取项目根目录的 `.env`，已有系统环境变量优先。`.env` 位于网页静态目录之外并已被 Git 忽略，不会发送给浏览器或进入源码仓库。修改配置后重新运行 `启动.bat`。旧版 `data/deepseek.key` 可通过 `python configure_deepseek.py --migrate-legacy` 一次性迁移到 `.env`；迁移成功后旧文件会被移除。

### Agent 编排层

`POST /api/chat` 通过 DeepSeek 的工具调用机制按需使用 5 个只读工具：`get_metric`（7/30 天指标）、`get_daily_trend`（饮食/花销逐日趋势）、`get_workout_history`（7/30 天力量训练明细，最多最近 30 次）、`get_record`（单条记录核对）和 `lookup_food`（本地知识库查询）。后端从登录会话确定账户，模型不能传入用户名或直接执行数据库写入。工具调用最多 3 轮、总计 6 次；工具参数经服务端校验。首页和 Agent 默认都包含现有演示/测试记录。

回答必须符合 `agent_response.py` 定义的 JSON 结构；其中 `recordIds` 只能引用本轮工具实际返回的记录 ID。聊天界面展示依据、建议、证据 ID 和调用过的只读工具。此处的食物查询仅查本地知识库；饮食录入页的“知识库未命中 → AI 估算”仍由独立的 `/api/tools/food-nutrition` 接口负责。Agent 不会自行修改记录或执行建议。

> 本仓库现包含全新的中文健康管理界面 **Boji Health**。直接打开 `index.html` 会进入新版；原版深度分析仪表盘可通过 `index.html?legacy=1` 继续使用。

**A privacy-first, single-file HTML dashboard for analyzing your Apple Health export.**

Import the ZIP straight from your iPhone. Get instant visualizations, compare any metrics, run correlation analyses, and export AI-ready reports — all locally in your browser. No server, no upload, no tracking.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![No build step](https://img.shields.io/badge/build-none-brightgreen)](#)
[![Single file](https://img.shields.io/badge/single--file-HTML-blue)](#)
[![Works offline](https://img.shields.io/badge/works-offline-success)](#)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-support-ff5e5b?logo=ko-fi&logoColor=white)](https://ko-fi.com/711it)
[![PayPal](https://img.shields.io/badge/PayPal-tip-00457C?logo=paypal&logoColor=white)](https://paypal.me/711IT)

**🌐 [Live demo](https://pit711.github.io/apple-health-dashboard/)** &nbsp;·&nbsp; [Report a bug](https://github.com/pit711/apple-health-dashboard/issues/new?template=bug_report.yml)

</div>

---

## Why this exists

Apple Health collects years of incredibly detailed data about you — but the built-in app only shows you small slices at a time. Third-party tools mostly want you to upload your data to *their* servers. This project takes the opposite approach: **your health data never leaves your device**. The entire dashboard is one HTML file you can save, share, and run anywhere.

## ✨ Features

- **🔒 Local-first** — your health data never leaves your device. No server, no upload, no telemetry.
- **📈 30+ metrics auto-detected** — steps, heart rate, HRV, VO₂max, sleep, weight, blood pressure, workouts, and more
- **⚡ Handles huge exports** — streaming XML parser processes multi-GB files without crashing the browser
- **🧠 Smart deduplication** — when iPhone and Apple Watch both record the same metric, only the higher-priority source is counted (mirroring Apple Health's own internal logic)
- **🔍 8 analysis tabs** — Overview, Activity, Heart & Circulation, Body, Sleep, Compare, Correlation, Raw Data
- **🔀 Multi-metric overlay** — up to 4 metrics on one time axis, each with its own y-axis
- **📐 Correlation analysis** — Pearson coefficient with scatter plot and trend line for any metric pair
- **🖨️ Per-chart printing** — print any single chart for medical appointments
- **🤖 AI export** — produces compact JSON/CSV/Markdown reports (~100–300 KB for 12 months) sized to fit in Claude/ChatGPT context windows
- **📱 Fully responsive** — works on desktop, tablet, and mobile
- **🪶 Single HTML file** — no build, no install, just open

## 🚀 Quick Start

### Option 1: Use the live demo

Just go to **https://pit711.github.io/apple-health-dashboard/** and drop your export there. Nothing is uploaded — the page runs entirely in your browser.

### Option 2: Run it locally

1. Download [`index.html`](index.html) (or clone this repo)
2. Open it in any modern browser (Chrome, Firefox, Safari, Edge)
3. Save it anywhere — works offline after the first load

### Exporting your Apple Health data

On your iPhone:

1. Open the **Health** app
2. Tap your **profile picture** (top right)
3. Scroll down → **Export All Health Data**
4. Wait — it can take a few minutes
5. Share the ZIP to your computer (AirDrop, email, cloud)
6. Drag the ZIP onto the dashboard

## 🤖 Using the AI Export

The dashboard can produce a compact, structured summary of your data, sized specifically to fit inside a Claude or ChatGPT conversation.

1. Load your export in the dashboard
2. Click **"Export für KI"** (top right of the toolbar)
3. Choose period (12 months recommended), format (JSON recommended), and detail level
4. The dialog shows the estimated file size live — green means it fits comfortably
5. Download and drop the file into a Claude or ChatGPT chat with a prompt like:

> *Analyze my Apple Health data. Find trends, anomalies, and correlations between metrics. Suggest things I should pay attention to.*

The export includes summary statistics per metric, a daily/weekly/monthly time series, and all workouts in the period — plus a metadata block that tells the AI exactly how the data was aggregated.

## 🔧 How it works

Apple Health exports a `export.xml` inside a ZIP. For long-term users this XML can easily exceed 1 GB.

1. **Streams the ZIP** chunk by chunk via JSZip's `internalStream` API — never holding the full XML in memory
2. **Parses incrementally** with a stateful regex parser that handles tag boundaries across chunk edges
3. **Aggregates to daily buckets** keyed by metric type and source
4. **Deduplicates by source priority** (Watch > iPhone > others) for cumulative metrics
5. **Renders with Chart.js** — only the active tab's charts are drawn

### Why streaming matters

JavaScript's V8 engine has a maximum string length of ~512 MB. A naive parser that reads the whole XML as one string crashes with `RangeError: Invalid string length` on real-world exports. The streaming approach handles arbitrarily large files.

## 📋 Supported Metrics

| Category | Metrics |
|---|---|
| **Activity** | Steps, walking/running distance, cycling distance, flights climbed, exercise minutes, stand time |
| **Energy** | Active energy, basal energy |
| **Heart** | Heart rate (min/avg/max), resting HR, walking HR, HRV (SDNN), VO₂ max |
| **Vitals** | Blood pressure (sys/dia), oxygen saturation, respiratory rate, body temperature |
| **Body** | Weight, BMI, body fat %, lean body mass, height |
| **Sleep** | Sleep duration, sleep stages |
| **Other** | Blood glucose, dietary water/energy, mindful sessions, audio exposure, walking speed/step length |
| **Workouts** | All workout types with duration, energy, distance |

Missing a metric? [Open an issue](https://github.com/pit711/apple-health-dashboard/issues/new?template=new_metric.yml) — adding new ones takes about 30 seconds.

## 🌐 Browser Compatibility

| Browser | Minimum version |
|---|---|
| Chrome / Edge | 90+ |
| Firefox | 90+ |
| Safari (macOS / iOS) | 15+ |
| Chrome Android | recent |

## 🔒 Privacy

- **No network requests** for your data — everything happens in the browser
- **Two CDN dependencies** loaded on first open: Chart.js and JSZip (these don't see your data — they're libraries, not services)
- **No cookies, no localStorage, no analytics, no tracking**
- **Open source** — read the code and verify yourself

## 🧰 Tech Stack

- Vanilla JavaScript (no framework, no build step)
- [Chart.js 4](https://www.chartjs.org/) for visualizations
- [JSZip 3](https://stuk.github.io/jszip/) for streaming ZIP extraction
- Native `File.stream()` and `TextDecoder` APIs

## 🤝 Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

The most useful contributions:
- Adding new Apple Health metric types
- Improving mobile UX
- Translating the UI to additional languages
- Bug fixes for edge cases in the XML parser

## Disclaimer

Apple, Apple Health, and HealthKit are trademarks of Apple Inc., registered in the U.S. and other countries. This project is not affiliated with, endorsed by, or associated with Apple Inc.

This tool is not a medical device. Do not make medical decisions based on this data.

## 📜 License

[MIT](LICENSE) — do whatever you want, no warranty.

## ⚠️ Disclaimer

This is an analysis tool, not a medical device. Visualizations are based on data your devices recorded; accuracy depends on the sensors. Don't make medical decisions based on this dashboard alone — talk to your doctor.

## ☕ Support

This project is free and open-source. If it's useful to you, a small tip keeps it going:

- ☕ **Ko-fi:** https://ko-fi.com/711it
- 💸 **PayPal:** https://paypal.me/711IT

## 🙏 Acknowledgments

Built with [Chart.js](https://www.chartjs.org/) and [JSZip](https://stuk.github.io/jszip/). Inspired by the frustration of trying to look at three years of heart rate data in the iPhone Health app.
