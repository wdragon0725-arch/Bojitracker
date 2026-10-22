# BojiTracker｜个人饮食、力量训练与花销追踪

BojiTracker 是可在本机运行的个人数据助手。它把饮食、力量训练和日常花销保存在 SQLite 中，按账户隔离记录；提供营养估算、7/30 天指标分析和可选的 DeepSeek 对话 Agent。项目使用 Python 标准库提供前后端服务，无需另装 Python 包。

> 这是个人记录与分析工具，不是医疗设备。营养目标、训练消耗及 AI 回答均可能是估算，不能代替专业诊疗。

## 快速启动

1. 安装 Python 3，并确保 `python` 可在命令行执行。
2. Windows 双击 `code/apple-health-dashboard-master/启动.bat`；其他系统在该目录运行 `python server.py`。
3. 打开 <http://127.0.0.1:4173/boji-health.html>，注册账户后开始记录。
4. 如需 Agent 或知识库未命中时的 AI 营养估算，将 `.env.example` 复制为项目根目录的 `.env`，填入自己的 `DEEPSEEK_API_KEY`，然后重启服务。Windows 也可运行 `配置DeepSeek.bat`，在隐藏输入框中设置密钥。

不配置 API Key 也可以使用账户、记录、指标和本地食物知识库。`.env` 位于网页服务目录之外，已被 Git 忽略；不要把它、截图中的密钥或 `data/` 目录上传到公开仓库。

## 主要功能

- 独立账户和本地 SQLite 数据库；用户名唯一，记录按登录账户隔离。
- 饮食记录及克数换算：先查本地知识库，未命中时可选择由 DeepSeek 估算，并标记数据来源。
- 力量训练记录：动作、重量、组数、次数、RPE 与估算消耗。
- 每日记账；食物花销可关联饮食记录。
- 7/30 天服务端指标与分析，区分缺失记录、已记录值和估算值。
- 只读 Agent 工具：指标、饮食/花销趋势、力量训练明细、单条记录核对和食物知识库查询。Agent 不会自行修改记录。

## 数据与项目结构

| 路径 | 用途 |
| --- | --- |
| `code/apple-health-dashboard-master/` | 网页、Python 服务、数据库迁移和测试 |
| `knowledge-base/browser-bundle.js`、`knowledge-base/entities/` | 运行所需的精简知识库 |
| `tools/` | 自带参考工作簿的知识库构建与校验工具 |
| `.env.example` | 环境配置模板，不含真实密钥 |
| `data/` | 本机账户数据库、备份和导出文件；**不上传** |
| `reference/` | 本机原始参考材料；**不上传** |

服务首次启动会创建 `data/boji_health.db`。升级旧数据库时会执行版本化迁移，并在迁移前生成备份。请自行备份 `data/`，但不要公开分享。历史测试记录默认参与统计；需要排除时，可在分析 API 中显式使用 `includeDemo=0`。

知识库基于好人松松标注“可任意分享”的健身 Excel 整理，项目保留来源说明；公开仓库仅提供应用运行所需的精简数据，不包含原始 Excel、完整单元格快照或图片。该第三方资料不应被理解为本项目软件许可证覆盖的内容。详见 [知识库说明](knowledge-base/README.md)。原 Apple Health Dashboard 的 MIT 许可和作者署名保留在 [原项目许可证](code/apple-health-dashboard-master/LICENSE)。

更多接口、记录元数据及迁移细节见 [应用说明](code/apple-health-dashboard-master/README.md)。运行测试：

```powershell
cd code/apple-health-dashboard-master
python -m unittest discover -p 'test_*.py'
```

目前默认仅监听本机地址，不应直接暴露在公网；如需线上部署，必须另行完善 HTTPS、密钥管理、备份和访问控制。
