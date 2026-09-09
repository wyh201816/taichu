# 太初仓库地图

> 更新日期：2026-09-05

太初是面向个人作者的单本玄幻长篇 AI 写作工作台。本文件只回答两件事：仓库每个区域负责什么，以及想找某类资料应该去哪里。

## 最常用入口

| 我想做什么 | 去哪里 |
|---|---|
| 查看 Codex 必须遵守的项目规则 | `AGENTS.md` |
| 修改当前前端视觉、布局或交互 | `DESIGN.md` |
| 查看当前入口页状态与历史点云备份位置 | `docs/前端风格/7-11入口页状态说明.md` |
| 查看前端主题的精确实现 | `web/src/components/theme/`、`web/src/app/globals.css` |
| 查看全部项目级 Skills | `.agents/索引.md` |
| 查看 Skill 编写规则 | `.agents/skills/rule.md` |
| 查看某个 Skill | `.agents/skills/{名称}/SKILL.md` |
| 查看项目资料分类与命名规则 | `docs/rule.md` |
| 生成或阅读实现学习资料 | `docs/学习资料/说明.md`、`docs/学习资料/` |
| 查看后端和系统架构学习资料 | `docs/学习资料/7-10太初系统架构图.md` |
| 查看已实现的第一版 Tool、子 Agent 能力边界与后续调优项 | `docs/学习资料/7-13工具与子智能体能力层技术设计.md` |
| 查看通用 Agent Runtime 已实现后端边界与后续技术设计 | `docs/学习资料/7-13通用智能体运行时编排技术设计.md` |
| 查看通用 Agent 全链路、上下文、能力调用、恢复与纠偏排查地图 | `docs/学习资料/7-20通用Agent运行链路上下文与能力调用排查地图.md` |
| 查看当前真实 LangGraph 图、节点、边与状态转移 | `docs/学习资料/8-24当前LangGraph图节点边与状态转移报告.md` |
| 查看当前模型请求中系统提示词（System Prompt）、工具、会话、约束的生成、编码与多轮更新链路 | `docs/学习资料/8-24模型请求上下文生成编码与多轮更新真实链路.md` |
| 查看通用写作智能体当前 37 条固定评测基准 | `tests/fixtures/evaluations/general_writing_agent_benchmark/suite.json` |
| 查看 18 条多步骤与 8 条异常恢复如何形成 Opik Dataset、Experiment、评分、Trace 和前端结果 | `docs/学习资料/9-01通用写作智能体Opik评测完整链路.md` |
| 查看 Opik 的 Dataset、Experiment、Trace 页面入口与具体使用方法 | `docs/学习资料/9-01Opik评测页面查看与使用指南.md` |
| 查看通用 Agent 后续阶段任务包 | `docs/任务包/太初-通用Agent后续推进任务包-20260717/` |
| 查看独立代码与架构审查报告 | `docs/reviews/` |
| 查看审查结论生成的可执行迁移任务包 | `docs/tasks/` |
| 查看多轮讨论形成的未来功能计划 | `docs/已讨论功能/说明.md`、`docs/已讨论功能/` |
| 查看已落地并通过首轮全量门禁的 Graph RAG 分层评测与自动回归体系 | `docs/学习资料/8-19Graph RAG质量评测与回归体系设计.md` |
| 查看知识沉淀智能体效果评估设计 | `docs/学习资料/7-11知识沉淀智能体效果评估方案.md` |
| 核对真实后端代码分层 | `src/taichu/api/`、`src/taichu/application/`、`src/taichu/domain/`、`src/taichu/infrastructure/` |
| 查看本地数据态目录结构 | `project_assets/readme.md` |
| 查看当前结构化字段和状态 | `src/taichu/domain/models/` |
| 查看 API 输入输出 | `src/taichu/api/schemas/` |
| 查看存储与检索契约 | `src/taichu/application/contracts/` |
| 追溯已经废弃或被替代的旧方案 | `docs/旧历史/说明.md`、`docs/旧历史/` |
| 查看测试与评测样本 | `tests/`、`tests/fixtures/evaluations/` |
| 安全探测 Right Code 模型名称与协议 | `.agents/scripts/probe_rightcode_models.py` |
| 探测本地嵌入并增量更新 Milvus 多跳图索引 | `scripts/probe_embedding_models.py`、`scripts/update_vector_graph_index.py` |

`docs/学习资料/` 解释具体实现主题，`docs/已讨论功能/` 保存未来计划，`docs/旧历史/` 保存已经废弃的方案，`docs/reviews/` 保存基于真实代码与验证证据形成的独立审查报告，`docs/tasks/` 保存由已证实问题生成的可执行任务包。这些资料都不能替代当前代码、测试、`AGENTS.md` 和数据目录说明；学习资料涉及未落地内容时必须明确标记。

## 仓库目录

```text
Taichu/
├── AGENTS.md                 # Codex 项目硬规则
├── myreadme.md               # 开发者仓库地图
├── DESIGN.md                 # 当前前端唯一设计规则
├── .env.example              # 环境变量示例
├── .gitignore                # Git 忽略规则
├── .python-version           # Python 版本提示
├── pyproject.toml            # Python 项目与 uv 依赖
├── uv.lock                   # Python 依赖锁文件，应提交 Git
├── start.bat                 # Windows 一键启动入口
├── .agents/                  # Codex Skills、开发工作流与维护脚本
├── .codex/                   # Codex 客户端与多 Agent 协作配置
├── docs/                     # 学习资料、审查报告、实施任务、未来计划和废弃方案归档
├── project_assets/           # 当前单本小说的数据态资产
├── scripts/                  # 可显式运行的向量探测与索引维护命令
├── src/                      # FastAPI 后端代码
├── tests/                    # 后端测试和评测夹具
└── web/                      # Next.js 前端代码
```

仓库不使用 `.gitkeep` 保存空目录，需要的目录由业务代码或开发脚本按需创建。

## 运行方式

首次准备依赖：

```powershell
uv sync
cd web
npm install
```

日常启动双击根目录 `start.bat`。它调用 `.agents/scripts/start.ps1`，依次启动或复用 MongoDB、Milvus、本地 BGE Reranker 与 Qwen Embedding，再完成固定端口清理和前后端就绪检查：

- 前端：`http://localhost:3000`
- 后端：`http://127.0.0.1:8000`
- MongoDB：`mongodb://127.0.0.1:27017`
- Milvus：`http://127.0.0.1:19530`
- Milvus 健康检查：`http://127.0.0.1:9091/healthz`
- 本地 Embedding：`http://127.0.0.1:8011/v1`
- 本地 BGE Reranker：`http://127.0.0.1:8012`

## Milvus Vector Graph RAG

当前多跳召回使用 Milvus 团队开源的 `vector-graph-rag 0.2.2`。系统把实体、关系和来源 passage 存在同一 Milvus 实例的三组集合中，通过 ID 引用扩展子图，不再维护第二个图数据库。

- 正文 Markdown 默认切成约 1000 字符的子块，目标重叠 200 字符；下一片起点优先对齐段落，其次对齐完整句首，并保留章节、子块序号、字符区间和来源引用。
- 每张 MongoDB `lifecycle=confirmed` 知识卡形成一个完整 passage，不再拆成身份、摘要和类型字段三个向量。
- 两类 passage 一起抽取实体和关系，因此可以沿“正文事实 → 桥接实体 → 知识卡设定”或反向路径完成多跳召回。
- `retrieve_story_context` 是生产唯一的语义相关性检索 Tool，统一返回正文、知识卡、关系链与可追溯证据；最终答案仍由高层编排 Agent 生成。
- Milvus 仍是可删除、可重建的派生层；Markdown 与 MongoDB 的事实源地位不变。日常语料维护按稳定来源键增量同步，不执行集合级全量重建。
- 正文章节和知识卡分别形成稳定来源；新增或内容变化的来源执行整源替换，事实源中消失的来源同步删除，内容哈希未变化的来源直接跳过。
- 每个来源成功后立即推进来源清单；中断后重跑只处理尚未成功或已经变化的来源，不重复处理已完成来源。

生产查询先在 Passage Collection 中分别执行中文 BM25 Top 30 和 HNSW Dense Top 30，再由 Milvus 原生 `RRFRanker(k=60)` 融合成 Passage Top 30。系统从这批相关 Passage 已有的 `entity_ids/relation_ids` 取得图种子，执行一次有全局预算和 Hub 限流的查询感知 Graph Expansion；随后通过关系的 `passage_ids` 回取 Graph Passage，与 RRF Passage 合并去重。`BAAI/bge-reranker-v2-m3` 只调用一次，对全部合并候选统一评分；Top 10 是检索指标与追踪边界，不是第二次图检索入口。HNSW 参数固定为 `M=24`、`efConstruction=300`、`efSearch=150`。

上下文装配最多选择 3 份互补证据。正文仍以约 1000 字符的子块完成细粒度定位；直接事实题优先投影覆盖“主体—谓词—客体”的最小原文句窗，因果、过程和共同经历等需要场景语义的问题保留更宽的同章父级邻域。知识卡按查询相关字段和关系投影压缩。所有投影进入模型前仍由 Markdown 或 MongoDB 权威回读校验；邻域和投影不参与第一阶段 BM25、Dense、RRF 或图扩展。

运行组件：

| 组件 | 版本或模型 | 位置 |
|---|---|---|
| Milvus Standalone | `milvusdb/milvus:v2.6.9` | Docker Compose，入口 `127.0.0.1:19530` |
| Vector Graph RAG | `vector-graph-rag 0.2.2` | 由 `uv.lock` 固定 |
| Qwen Embedding | `Qwen3-Embedding-4B-Q4_K_M.gguf`，2560 维 | `E:\Taichu\Models\Qwen3-Embedding-4B\` |
| BGE Reranker | `BAAI/bge-reranker-v2-m3` | Hugging Face TEI 1.9 CUDA 服务，入口 `127.0.0.1:8012` |
| llama.cpp | `b10066` Windows Vulkan x64 | `E:\Taichu\Runtime\llama.cpp\b10066` |

索引构建阶段的实体/关系抽取面向 LangChain `BaseChatModel`，通过 `with_structured_output` 把三元组 Schema 作为模型 API 原生结构化输出契约发送；供应商协议转换、用量审计和降级策略只由基础设施层的 `GatewayChatModel` 适配。`.env` 中的 `VECTOR_GRAPH_LLM_MODEL` 只负责选择该建模模型，当前使用 `deepseek-v4-pro`。生产查询阶段不再调用 LLM 做实体抽取或关系重排：图种子来自 RRF Passage 元数据，关系和全部 Passage 统一交给本地 BGE 查询感知评分。本地 Qwen Embedding 只负责向量化。

## 阶段 04 运行时记忆与上下文压缩

通用写作助手以一个侧栏“新对话”窗口对应一个 `conversation_id（会话标识）`；窗口内每次用户请求各自产生一个 `run_id（运行标识）` 和递增的 `request_index（请求序号）`。只要用户没有点击“新对话”，后续请求始终续接同一会话。旧运行会按共同 `task_id（任务标识）` 归并，避免把同一窗口错误拆成多条最近对话。

`ContextAssembler（上下文组装器）` 从第一次请求开始生成“稳定记忆、长期记忆、历史对话、工作记忆、当前请求”五层上下文，模型输入固定按 `System Prompt → 长期记忆 → 历史对话 → 工作记忆 → 当前请求` 拼接。稳定记忆就是 `system` 角色中的身份、基本准则和静态能力索引；跨任务用户偏好维护在 `project_assets/source/workspace/long_term_memory.md`，按当前请求召回并可在后续阶段重新召回，因此不并入 System Prompt。运行工作记忆由 LangGraph 官方 `MongoDBStore` 保存到 MongoDB 的 `langgraph_store` 集合，并按会话 namespace 隔离；Runtime 自动写入并按请求序号过期，不再维护 JSON 记忆仓储或 JSON 词法索引。总预算不足时依次收缩长期记忆、历史对话和可重建的工作记忆，System Prompt 与当前请求保持完整，仍无法容纳时明确拒绝。

外层运行图直接使用 LangGraph 官方 `MongoDBSaver`，检查点与节点中间写入分别保存在 MongoDB 的 `langgraph_checkpoints`、`langgraph_checkpoint_writes` 集合；`conversation_id` 是 LangGraph `thread_id`，同一会话中的每个 `run_id` 是该线程上的一次图运行。异常或重启后恢复会话线程的当前请求，不重跑已成功图节点。澄清、写入授权和副作用人工核对在同一运行中通过 LangGraph `interrupt()` 暂停，并以 `Command(resume=...)` 接续，不再为人工回答另建接续运行。运行状态由 LangGraph `astream(..., version="v2")` 产生，FastAPI 仅把业务投影转成网页可订阅的 NDJSON；工作台直接订阅状态流，不再轮询任务。写入型 Tool 的副作用对账日志独立保存在 `project_assets/derived/general_agent_effects/`，不再与框架检查点混存。工作台只读展示自动运行记忆及其来源，不提供单条记忆的手动新增、修改或删除入口；作者只通过正常对话或人工介入节点影响 Agent。任务监控可查看本次记忆数量、压缩状态、Token 估算和恢复差异。当前行为以对应源码与测试为准。

### 日常检查

正常情况下只需运行 `start.bat`，不需要重复下载。也可以手动检查：

```powershell
Invoke-WebRequest http://127.0.0.1:9091/healthz -UseBasicParsing
Invoke-RestMethod http://127.0.0.1:8011/health
Invoke-RestMethod http://127.0.0.1:8011/v1/models
docker ps --filter name=taichu-milvus
Invoke-RestMethod http://127.0.0.1:8012/health
uv run python scripts/probe_embedding_models.py
uv run python scripts/update_vector_graph_index.py --dry-run
```

### 索引增量更新与专项评测

```powershell
# 只核对章节数、正文片段数、知识卡数和当前语料快照，不写 Milvus
uv run python scripts/update_vector_graph_index.py --dry-run

# 按来源增量更新实体、关系和 passage
uv run python scripts/update_vector_graph_index.py

```

增量更新以正文章节和已确认知识卡的稳定 `source_key` 为同步边界。来源新增或内容哈希变化时整源替换，来源消失时删除其 passage 及无引用图数据，未变化来源跳过。每个来源成功后立即写入 `project_assets/generated/milvus_vector_graph/source_manifest.json`，因此失败重试只继续未完成来源；整次运行摘要仍写入 `active_manifest.json`。两份清单都不保存正文、知识卡或向量。

只有集合 Schema、嵌入维度或索引协议发生不兼容变化时，才通过专门迁移执行一次性集合重建；普通正文和知识卡更新不得 `drop` Milvus collections。

### 全新机器的下载方式

Milvus 使用仓库内的官方 Standalone 组件编排。一般由 `start.bat` 自动拉取并启动；也可手动执行：

```powershell
docker compose -f infra/milvus/docker-compose.yml up -d
docker compose -f infra/reranker/docker-compose.yml up -d
```

Qwen 模型优先从官方 Hugging Face 仓库按提交号下载：

```powershell
uvx --from huggingface_hub hf download `
  Qwen/Qwen3-Embedding-4B-GGUF `
  Qwen3-Embedding-4B-Q4_K_M.gguf `
  --revision f4602530db1d980e16da9d7d3a70294cf5c190be `
  --local-dir E:\Taichu\Models\Qwen3-Embedding-4B
```

本机 Hugging Face 直连当时不可用，因此实际从 Qwen 官方魔搭仓库下载同一个文件：

```powershell
New-Item -ItemType Directory -Force E:\Taichu\Models\Qwen3-Embedding-4B
curl.exe --ssl-no-revoke --fail --location `
  --output E:\Taichu\Models\Qwen3-Embedding-4B\Qwen3-Embedding-4B-Q4_K_M.gguf `
  https://www.modelscope.cn/models/Qwen/Qwen3-Embedding-4B-GGUF/resolve/master/Qwen3-Embedding-4B-Q4_K_M.gguf
Get-FileHash -Algorithm SHA256 `
  E:\Taichu\Models\Qwen3-Embedding-4B\Qwen3-Embedding-4B-Q4_K_M.gguf
```

llama.cpp 使用官方 GitHub 的固定版本 Vulkan 构建：

```powershell
New-Item -ItemType Directory -Force E:\Taichu\Runtime\llama.cpp
curl.exe --ssl-no-revoke --fail --location `
  --output E:\Taichu\Runtime\llama.cpp\llama-b10066-bin-win-vulkan-x64.zip `
  https://github.com/ggml-org/llama.cpp/releases/download/b10066/llama-b10066-bin-win-vulkan-x64.zip
Expand-Archive `
  E:\Taichu\Runtime\llama.cpp\llama-b10066-bin-win-vulkan-x64.zip `
  E:\Taichu\Runtime\llama.cpp\b10066
```

llama.cpp 依赖最新 Microsoft Visual C++ x64 运行库。本机已升级为 `14.51.36247.0`；若其他机器启动时报 `MSVCP140.dll`，安装[微软官方 x64 运行库](https://aka.ms/vc14/vc_redist.x64.exe)。

完整数据模型、多跳流程和索引约束见 `docs/已讨论功能/8-15Milvus向量图谱多跳召回决策.md`。

## 本机外部数据位置

以下内容在仓库外，不应重新复制到根目录：

| 内容 | 本机位置 | 说明 |
|---|---|---|
| MongoDB 数据 | `E:\Taichu\MongoDB\data\db` | 当前唯一结构事实存储位置 |
| MongoDB 日志 | `E:\Taichu\MongoDB\log` | MongoDB 本地运行日志 |
| Milvus 多跳图索引 | Docker 命名卷 `taichu_milvus_data`、`taichu_milvus_etcd`、`taichu_milvus_minio` | 可从正文 Markdown 与 MongoDB confirmed 卡按来源增量恢复的派生索引 |
| BGE Reranker 模型 | `E:\Taichu\Models\bge-reranker-v2-m3` | 本地重排模型权重，不是小说事实源 |
| Qwen Embedding 模型 | `E:\Taichu\Models\Qwen3-Embedding-4B` | 本地 GGUF 模型，不是小说事实源 |
| llama.cpp 运行时 | `E:\Taichu\Runtime\llama.cpp\b10066` | 本地模型服务程序 |
| Embedding 日志 | `E:\Taichu\Embedding\log` | 本地推理服务日志，不保存为知识事实 |
| 原小说导入资料 | `E:\Taichu\导入资料\太初原小说` | PDF、EPUB、TXT 原始导入包 |
| 旧知识 JSON 迁移备份 | `E:\Taichu\迁移备份\知识库-20260711-151915` | 88 张旧卡和迁移清单的只读备份 |
| 旧 LangGraph JSON 检查点备份 | `E:\Taichu\迁移备份\旧LangGraph-JSON检查点-20260829` | 迁移前自维护检查点的只读追溯备份；当前 Runtime 不读取或回退 |

原小说导入包只是外部导入材料。太初当前正文的文本事实源仍是 `project_assets/source/manuscripts/chapters/` 下的 Markdown。

## 数据边界

- Markdown 是唯一文本事实源。
- MongoDB `taichu.knowledge_cards` 是唯一结构事实源；默认事实查询只使用 `lifecycle=confirmed` 的记录。
- 旧知识迁移已备份 88 张 JSON：58 张有效卡导入为 `confirmed`，30 张已弃用重复卡只保留在 E 盘备份中。
- 迁移 `finalize` 已完成，`project_assets/source/knowledge/` 已删除；存储骨架和业务代码不得重新创建它。
- AI、Agent、评测和 Inbox 保存的 JSON/JSONL 仅是候选、运行、审计或工作区中间态；只有经过校验和作者确认的结构事实才能写入 MongoDB。
- Milvus 只保存可重建的实体、关系和来源 passage 索引；命中内容必须携带正文区间或知识卡来源引用，不能反向成为事实源。
- SQLite/FTS 已废弃，不参与后续架构决策；Milvus 向量、BM25、图索引及未来缓存都只能作为可重建派生层。

更完整的物理目录职责以 `project_assets/readme.md` 为准。

