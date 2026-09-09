# Right Code 多模型、流式调用与模型监控实施报告

> 更新日期：2026-07-11  
> 文档状态：已完成任务的历史验收快照，不作为当前规则源；代码与接口定义以仓库当前实现为准。

## 本次任务目标

将太初原有的 DeepSeek 单后端调用链替换为统一的 Right Code 多模型能力，在不向前端暴露密钥、不混用旧 DeepSeek 官网配置的前提下，完成十模型目录、模型选择、普通与流式调用、结构化输出、调用遥测、Token 与费用统计、模型可用性检测以及中文模型监控页面。

## 实际协议探测结论

探测只从本机 `.env` 读取 `RIGHTCODE_API_KEY`，没有使用旧 `DEEPSEEK_API_KEY`，没有输出密钥、鉴权头、完整请求正文或完整上游错误响应。

| 显示名称 | 上游模型名 | 最终端点 | 最终协议 | 实测结果 |
| --- | --- | --- | --- | --- |
| GPT-5.6 Luna | `gpt-5.6-luna` | `https://www.right.codes/codex/v1/responses` | `openai_responses` | 普通、流式、正文、usage 成功 |
| GPT-5.6 Sol | `gpt-5.6-sol` | `https://www.right.codes/codex/v1/responses` | `openai_responses` | 流式、正文、usage 成功 |
| GPT-5.6 Terra | `gpt-5.6-terra` | `https://www.right.codes/codex/v1/responses` | `openai_responses` | 流式、正文、usage 成功 |
| DeepSeek V4 Flash | `deepseek-v4-flash` | `https://right.codes/deepseek/anthropic/v1/messages` | `anthropic_messages` | 普通、流式、正文、usage 成功 |
| DeepSeek V4 Pro | `deepseek-v4-pro` | `https://right.codes/deepseek/anthropic/v1/messages` | `anthropic_messages` | 普通、流式、JSON、usage 成功；默认模型 |
| Claude Opus 4.6 | `claude-opus-4-6` | `https://right.codes/claude-sale/v1/messages` | `anthropic_messages` | 普通、流式、JSON、usage 成功 |
| Claude Opus 4.7 | `claude-opus-4-7` | `https://right.codes/claude-sale/v1/messages` | `anthropic_messages` | 流式、正文、usage 成功 |
| Claude Opus 4.8 | `claude-opus-4-8` | `https://right.codes/claude-sale/v1/messages` | `anthropic_messages` | 流式、正文、usage 成功 |
| Claude Sonnet 4.6 | `claude-sonnet-4-6` | `https://right.codes/claude-sale/v1/messages` | `anthropic_messages` | 流式、正文、usage 成功 |
| Claude Sonnet 5 | `claude-sonnet-5` | `https://right.codes/claude-sale/v1/messages` | `anthropic_messages` | 流式、正文、usage 成功 |

### DeepSeek 渠道纠正记录

首次探测只覆盖了 Codex Responses 和 DeepSeek `/v1/responses`，因此曾过早将 DeepSeek 标记为不可用。根据 Right Code 后台展示的真实渠道重新核对后，确认 DeepSeek 提供独立的 OpenAI 格式 `/deepseek` 和 Anthropic 格式 `/deepseek/anthropic`。

为保留 system、user、assistant 角色边界并避免使用任务明确禁止的 Chat Completions，最终采用 Right Code DeepSeek Anthropic Messages 通道。DeepSeek V4 会先返回 `thinking` 内容块，过小的探测输出额度可能在推理阶段截断，因此 DeepSeek 显式检测使用 1024 Token 的输出额度，业务普通调用仍使用业务请求指定值或网关默认值。

## 已实现功能

### 统一配置与模型目录

- 产品级供应商统一为 `rightcode`。
- 默认模型为 `deepseek-v4-pro`，前端显示为“DeepSeek V4 Pro（默认）”。
- 后端维护十模型唯一目录，显示名称、稳定内部 ID、上游模型名、传输协议和端点配置分开保存。
- 新增配置：
  - `RIGHTCODE_API_KEY`
  - `RIGHTCODE_RESPONSES_BASE_URL`
  - `RIGHTCODE_CLAUDE_SALE_BASE_URL`
  - `RIGHTCODE_DEEPSEEK_ANTHROPIC_BASE_URL`
  - `RIGHTCODE_DEFAULT_MODEL_ID`
  - `RIGHTCODE_REQUEST_TIMEOUT_SECONDS`
  - `RIGHTCODE_MAX_RETRIES`
  - `RIGHTCODE_MODEL_PRICES_JSON`
- 删除旧 DeepSeek Provider、工厂分支和 `langchain-openai` 依赖。

### 统一 LLM 应用契约

- 新增不可变的消息、请求、响应、流事件、usage、cost 和模型 Profile 数据模型。
- system 与 user 消息在应用层和传输层保持角色分离。
- 每次调用显式携带 `model_id`，不使用可变全局当前模型。
- 同一网关同时支持文本和 JSON 响应模式。
- 并发调用根据请求模型独立路由，不互相覆盖。

### RightCodeLLMGateway

- GPT 路由到 OpenAI Responses 普通接口和 SSE 流。
- Claude 路由到 Claude Sale Anthropic Messages 普通接口和 SSE 流。
- DeepSeek 路由到 Right Code DeepSeek Anthropic Messages 普通接口和 SSE 流。
- Messages codec 支持 `thinking`、`text`、usage、停止原因和请求 ID 规范化。
- JSON 模式会去除模型可能附加的 Markdown 代码围栏，再交由业务 Schema 解析。
- 统一处理无密钥、无权限、余额不足、429、超时、5xx、空内容、非法 JSON、流式中断和客户端断开。
- 错误信息只保留中文安全摘要，不保存完整上游响应。

### 业务调用迁移

以下入口均通过统一 `LLMGatewayContract` 调用并传递模型 ID：

- 写作区：纯对话、续写、润色、设定、建议、证据、章节摘要、灵感、事实。
- 选区 AI。
- 独立章节摘要服务。
- 知识沉淀单章任务。
- 最多五章的知识沉淀批量任务。
- 知识抽取 Agent 的 LLM 节点和后处理节点。
- 语义评估裁判。

批量知识沉淀在任务启动时锁定模型 Profile，所有章节分支和后处理节点使用同一模型快照。知识沉淀只在完整响应结束后解析 JSON，不向用户显示半截结构化输出。

### 写作流式输出

- 新增 NDJSON 接口 `POST /api/writing-ai/runs/stream`。
- 事件包含运行开始、文本增量、usage、完成和失败。
- 服务端累计完整输出，结束时一次性保存运行结果和调用记录。
- 流式正文与最终持久化的 `raw_llm_output` 保持一致。
- 保留原非流式接口和历史记录能力。

### 用量、费用与调用遥测

- 新增统一 `LLMCallRecord`，记录模型快照、任务、章节、协议、状态、耗时、Token、费用类型、上游请求 ID 和脱敏错误。
- 调用记录追加写入 `project_assets/derived/llm_usage/calls.jsonl`，不写入知识库。
- 支持分页、时间、模型、任务类型和状态筛选。
- 支持按模型、任务类型聚合。
- 支持按小时或按天聚合 Token 趋势。
- 费用规则：
  - 上游明确返回费用时记录为“实际”。
  - 仅返回 Token 且本地配置价格时使用 `Decimal` 计算“预估”。
  - 没有实际费用且没有价格时金额为 `null`，记录为“不可计算”。
- 没有将 Right Code 网页价格写死为业务价格。

### 后端 API

- `GET /api/llm/models`
- `POST /api/llm/models/{model_id}/probe`
- `GET /api/llm/usage/calls`
- `GET /api/llm/usage/calls/{call_id}`
- `GET /api/llm/usage/summary`
- `GET /api/llm/usage/trend`
- `POST /api/writing-ai/runs/stream`

公开模型 API 不返回 Token、鉴权头、SecretStr、完整 Prompt、模型原始输出或未经脱敏的上游错误。

### 前端模型选择

- 新增可复用 `ModelSelector` 和模型选择 Hook。
- 模型数据只从 `GET /api/llm/models` 获取，前端不硬编码十模型数组。
- 支持默认模型、最近选择、加载、空状态、错误状态和不可用模型禁用。
- 写作区和知识沉淀任务显式提交当前 `model_id`。
- 浏览器实测默认选中 DeepSeek V4 Pro。

### 模型监控页面

模型监控复用现有应用壳和午夜极光控制台风格，没有新增图表依赖。页面重构为同一路由内的四个紧凑功能入口，每次只展示一个主面板，避免所有区块纵向无限铺开：

- Token 趋势。
- 模型汇总。
- 调用明细。
- 模型可用性。

页面不包含下拉框或日期下拉，时间、模型、任务和状态均使用紧凑按钮筛选。Token 趋势使用原生 SVG 折线图，支持：

- 24 小时、7 天、30 天、全部时间范围。
- 全部模型或指定模型。
- 总 Token、输入 Token、缓存 Token、输出 Token、推理 Token。
- 小时或天级后端聚合。

模型汇总固定展示十个模型，可直接进入对应调用明细。调用明细将 Token 信息合并成紧凑列，并保留详情抽屉。模型可用性支持逐个和批量检测，页面加载时不会自动消耗额度。

## 关键变更入口

- `src/taichu/application/contracts/llm.py`：统一 LLM 应用契约。
- `src/taichu/application/contracts/llm_usage.py`：调用遥测仓储契约。
- `src/taichu/infrastructure/llm/catalog.py`：十模型目录。
- `src/taichu/infrastructure/llm/rightcode.py`：统一 Responses 与 Messages 网关。
- `src/taichu/infrastructure/llm/costs.py`：费用规范化。
- `src/taichu/infrastructure/llm_usage/jsonl_repository.py`：JSONL 记录、汇总和趋势聚合。
- `src/taichu/api/routes/llm.py`：模型、用量、汇总和趋势 API。
- `scripts/probe_rightcode_models.py`：安全真实协议探测。
- `web/src/components/llm/model-selector.tsx`：统一模型选择组件。
- `web/src/components/llm-monitor/model-monitor-shell.tsx`：模型监控页面。
- `web/src/lib/llm/token-trend.ts`：趋势时间范围与刻度逻辑。

## 自动化验证结果

- `uv sync`：通过。
- `uv run ruff check src tests scripts`：通过。
- `uv run mypy`：通过，213 个源文件无问题。
- `uv run python -m unittest discover -s tests`：通过，180 个测试全部成功。
- `npm run test:editor`：通过。
- `npm run test:agent-evaluation`：通过。
- `npm run test:llm`：通过。
- `npm run lint`：通过。
- `npm run build`：通过，包含 `/model-monitor` 静态路由。
- `git diff --check`：通过。

## 启动与网页验证

- `start.bat` 已在固定端口执行成功。
- 后端：`http://127.0.0.1:8000`。
- 前端：`http://localhost:3000`。
- 模型监控：`http://localhost:3000/model-monitor`。
- 浏览器确认：
  - 四个功能入口可切换。
  - 页面 `<select>` 数量为零。
  - Token 趋势 SVG 正常渲染。
  - 模型汇总展示十行模型。
  - 调用明细使用按钮筛选并可打开详情。
  - 模型可用性展示十个模型和检测操作。
  - 浏览器控制台错误、警告均为零。

## 网页手动验收路径

1. 在仓库根目录运行 `start.bat`。
2. 打开 `http://localhost:3000/model-monitor`。
3. 检查顶部统计条是否展示调用次数、成功率、Token、费用和平均耗时。
4. 点击“Token 趋势”，切换时间、模型和 Token 指标，确认折线图刷新且页面没有下拉框。
5. 点击“模型汇总”，确认固定展示十个模型，并通过“看明细”进入对应调用记录。
6. 点击“调用明细”，使用时间、状态、模型和任务按钮筛选，打开任意调用详情。
7. 点击“模型可用性”，确认页面不会自动检测；手动检测前出现费用确认提示。
8. 打开写作区或知识沉淀工作台，确认模型选择器默认显示 DeepSeek V4 Pro，并可切换其他可用模型。

## Token 安全检查

- `.env` 已被 `.gitignore` 忽略。
- `.env.example` 只包含占位值和公开端点。
- Git diff 和已跟踪文件未发现真实 `sk-` 密钥。
- 调用遥测中未发现真实密钥。
- 日志和前端响应不包含 Authorization、x-api-key 或 Token 内容。
- 对话中曾公开的旧 Token 不进入仓库，仍应在上游后台保持撤销状态。

## GPT Pro 验收重点

- DeepSeek 必须使用 Right Code `/deepseek/anthropic/v1/messages`，不得误判为 DeepSeek 官网渠道或恢复旧 DeepSeek Token。
- DeepSeek V4 Pro 必须是可用且真正生效的默认模型。
- GPT 使用 Responses；Claude 和 DeepSeek 使用各自的 Messages 端点，但应用层只能看到一个 RightCodeLLMGateway。
- 所有真实业务调用都必须生成脱敏 LLMCallRecord。
- 写作流式显示和最终持久化正文必须一致。
- 知识沉淀必须在完整 JSON 到达后再解析和校验。
- 模型监控页面必须无下拉框，并包含 Token 趋势、模型汇总、调用明细和模型可用性四个入口。
- 模型检测不得在页面加载时自动执行。

## 已知风险与注意事项

- Right Code 当前响应未提供可确认的实际费用字段，本地又未配置稳定价格，因此现有真实调用费用显示为“不可计算”，不是零费用。
- DeepSeek 推理模型的极小输出额度可能只返回 thinking 而没有 text；显式检测已经为 DeepSeek 预留 1024 Token。
- 模型可用性状态当前属于进程内检测状态，应用重启后会恢复为“未检测”，历史调用记录仍保留。
- `project_assets/derived/llm_usage/calls.jsonl` 是本机真实运行遥测，不属于代码或小说事实；发布代码时不得将本机实际调用记录一并提交。
- 当前工作区未创建 commit、未 push，验收报告记录的是未提交工作区状态。

## Git 发布状态

- 未创建 commit。
- 未 push。
- 未创建 Pull Request。

