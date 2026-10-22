# Boji Health 知识库

本目录由 `tools/build_knowledge_base.py` 从参考 Excel 自动生成。原始工作簿未被修改。

公开源码仅附运行所需的精简知识库。下述 `source/sheets/`、`search/` 和 `media/` 属于本地构建产物，未随公开仓库上传；可在拥有参考工作簿时重新生成。

## 结构

- `manifest.json`：版本、来源校验值与覆盖统计。
- `catalog.json`：31 个工作表、语义实体和图片索引。
- `source/sheets/`：每个工作表的完整结构化快照，保留非空单元格、公式、缓存值、样式、合并区域、验证规则、图片锚点及来源坐标。
- `entities/`：供产品直接调用的食品、饮食方案、有氧、问答、训练计划、1RM、拉伸解剖和跨模块规则。
- `search/chunks.jsonl`：按工作表行生成的检索块，可直接导入全文检索或向量数据库。
- `media/`：按工作表映射的图片；`media/raw/` 保留工作簿压缩包中的全部原始媒体。
- `browser-bundle.js`：无需服务器即可由前端 `<script>` 引用的精简知识库。

## 来源与边界

来源：`【可任意分享】健身Excel超级套表（作者：B站好人松松）26年9月最新版.xlsx`
SHA-256：`211363d055795fe7c4c4151856c310b849ae008bc3eaac35e6093615f8e065e9`

知识库保留原作者表述和公式，并通过 `source.sheet` / `source.cell` 标注来源。食品总热量与原表未提供的营养素在 `application_foods` 中明确标为产品层估算，实际使用时应优先采用食品包装营养表。该资料不构成医疗建议。
