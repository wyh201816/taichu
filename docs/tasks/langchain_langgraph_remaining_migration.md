# 太初 LangChain / LangGraph 剩余迁移实施任务包

> 生成日期：2026-08-30  
> 依据：`docs/reviews/langchain_langgraph_migration_audit.md`  
> 状态：已执行（Agent Runtime 迁移完成；全仓 MyPy 既有类型债务不纳入本任务）  
> 范围：只处理审计已有代码证据的问题；不以减少代码量或“更像 LangChain”为目标。

## 实施结果（2026-08-30）

| Phase | 状态 | 结果 |
|---|---|---|
| Phase 0 | 完成 | 先补 HIL 故障窗、provider Tool stream、真实 Tool identity、全局预算、Context/HIL 边界等失败契约。 |
| Phase 1 | 完成 | 官方 checkpoint 成为恢复唯一事实源；JSON run 降为业务投影；conversation thread 稳定。 |
| Phase 2 | 完成 | Anthropic/OpenAI Responses 结构化流、Writing AI chunk 消费和有界背压闭合。 |
| Phase 3 | 完成 | `ToolRuntime` 真实 call ID、持久化全局预算、并行原子扣减与幂等重放闭合。 |
| Phase 4 | 完成 | HIL 显式消息/轮次/请求身份、澄清 issue 解析与运行记忆生命周期修正。 |
| Phase 5 | 完成 | 公共/transport profile 分离，structured judge、trace sequence/hash/error 与资源隔离收敛。 |
| Phase 6 | 完成 | 删除有证据的 adapter/mock/fallback/兼容残留，补 `AsyncExitStack`、弱引用锁、LRU 与架构门禁。 |
| Phase 7 | 完成（类型债务例外） | 全量 pytest、Ruff、lock、diff、恢复基准与固定端口通过；全仓 MyPy 仍保留审计时已确认的独立失败基线。 |

### 最终复验

```text
pytest: 1062 passed, 23 subtests passed
Synthetic Runtime / Freeze: 12 passed
Mongo Runtime / Tool Budget: 3 passed
Recovery benchmark: 36/36 组合通过
Ruff: passed
uv lock --check: passed
git diff --check: passed
scoped MyPy（本轮核心生产文件与架构测试）: passed
full MyPy: 621 errors in 86 files（既有独立类型债务，未伪装为通过）
固定端口: backend / models / General Agent / vector / usage / frontend 均为 200
```

恢复压力测试曾在全量回归中暴露旧 benchmark instrumentation 的时序竞态：stream 消费者无法稳定观察 `StateSnapshot.next`。修复把 `PLAN_CREATED` 与 `VERIFICATION_STARTED` 放到对应 LangGraph 节点入口，此时上一 super-step 已提交；随后 37-case 双隔离冻结门禁通过，未修改 Synthetic fixture、Suite、Oracle 或 baseline 掩盖失败。

本任务包至此结束。后续不再继续 Agent Runtime 框架迁移；非 LLM API 分层、惰性 Manifest 协议和全仓 MyPy 应分别建立独立任务，不能夹带进入本任务。

## 0. 执行总则

本任务包证明当前仍有值得处理的 Runtime、streaming、Tool identity 和边界问题，因此需要继续一批迁移。执行时必须遵守以下顺序和门禁：

1. 每个 Phase 开始前重新读取“涉及文件”及其直接调用者/测试，不能只依赖本任务包的行号。
2. 每个 Phase 先补能失败的契约测试或复现，再修改生产代码。
3. 每个 Phase 只改本阶段职责，禁止一次性大爆改。
4. 每个 Phase 完成后执行本阶段测试、Ruff、触及范围 MyPy 和 `git diff --check`。
5. 每个 Phase 自行 review：检查真实调用链、旧实现清理、异常路径、资源生命周期、类型与用户可见中文文案。
6. review 和测试通过后自动进入下一 Phase，不等待人工逐阶段确认。
7. 若遇到外部模型/数据库不可用，先完成所有可离线验证项；只有无法证明契约时才记录阻塞，禁止用 mock 绿灯冒充真实兼容。
8. 禁止改变无关业务行为；禁止重构小说事实源、Markdown/MongoDB/Milvus 的既定边界。
9. 禁止把 `run_id`、副作用账本、审计、trace/replay、计费或小说 Context 错迁进 LangGraph checkpoint。
10. 禁止通过更新固定 baseline、降低断言、增加 `Any`/`cast` 或吞异常让门禁变绿。

### 全程保护的不变量

- `conversation_id` 始终是 LangGraph `thread_id`。
- 同一 conversation 的正常续轮与 interrupt resume 始终使用同一 thread。
- `run_id`/`parent_run_id` 只承担业务谱系、审计、幂等和回放。
- 官方 checkpoint 是 Agent 执行/恢复状态唯一事实源。
- JSON run 可以是可重建的业务投影，但不能驱动 `Command(resume=...)`、节点恢复或计划回灌。
- RightCode SSE 只在 infrastructure 解析，application 只看 LangChain 消息/chunk。
- Tool 权限、副作用、幂等、授权和结果预算继续由太初 application 负责。
- 小说事实仍以 Markdown 正文与 MongoDB confirmed 知识卡为准；Store 只保存运行记忆/能力结果。

## Phase 0：锁定当前基线与失败契约

### 目标

在修改生产代码前，把审计发现转成可重复失败的测试，并记录当前工作树的真实门禁，避免现有绿灯继续掩盖问题。

### 开始前重新读取

- `docs/reviews/langchain_langgraph_migration_audit.md`
- `AGENTS.md`
- `pyproject.toml`
- `src/taichu/application/general_agent/service.py`
- `src/taichu/application/general_agent/executor.py`
- `src/taichu/application/tools/registry.py`
- `src/taichu/application/subagents/runner.py`
- `src/taichu/infrastructure/llm/rightcode.py`
- 下面列出的现有测试文件

### 涉及文件

- `tests/unit/application/general_agent/test_recovery_cases_24_25.py`
- `tests/unit/application/general_agent/test_runtime.py`
- `tests/unit/application/tools/test_registry.py`
- `tests/unit/application/subagents/`
- `tests/unit/infrastructure/llm/test_rightcode_gateway.py`
- `tests/integration/api/test_llm_api.py`
- `tests/unit/application/general_agent/` 下新增的预算、Context、记忆契约测试

### 修改内容

新增或重写以下失败测试：

1. 授权故障窗：JSON 已投影 waiting、官方图尚未 interrupt 时，一次批准必须被消费并完成；断言 `StateSnapshot.interrupts`。
2. Anthropic `tool_use + input_json_delta + message_stop`：必须产生规范化 tool call，不能返回空响应。
3. Tool Call identity：模型 `ToolCall.id` 与绑定时业务 ID 不同、同一工具多次调用时，ToolMessage、artifact、trace 映射必须准确。
4. 任务级 `max_total_tool_calls`：直接 Tool、子 Agent Agent loop 和来源预取共同消费同一上限。
5. HIL turn boundary：用户请求与澄清回答文本相同也不能错分当前轮。
6. 澄清回答后，原 `UNRESOLVED_ISSUE` 必须失效；一次性回答不能自动升级为永久偏好。
7. Writing AI 流接口：根据产品现有契约断言真实结构化增量，而不是 `deltas == ""`。

同时记录：

- 当前 `git rev-parse HEAD`、分支、工作树状态。
- 实际依赖版本。
- 全量 `pytest`、Ruff、MyPy、uv lock 和固定端口 API 基线。
- MyPy 现有错误清单；不得写“passed”除非命令实际返回 0。

### 不修改内容

- 不修改任何生产实现。
- 不更新/冻结 synthetic baseline。
- 不改 README 中的架构结论。
- 不修改小说数据、知识卡、正文或向量索引。

### 验收标准

- 新增测试能在当前实现上稳定暴露对应缺陷。
- 每个失败都能定位到单一职责边界，不依赖真实付费模型。
- 现有通过测试的历史语义已明确标记为待替换，而不是直接删除证据。
- 基线记录包含当前全量 MyPy 的真实结果。

### 测试命令

```powershell
uv run pytest tests/unit/application/general_agent/test_recovery_cases_24_25.py -q
uv run pytest tests/unit/application/tools/test_registry.py tests/unit/application/subagents -q
uv run pytest tests/unit/infrastructure/llm/test_rightcode_gateway.py tests/integration/api/test_llm_api.py -q
uv run pytest tests/unit/application/general_agent -q
uv run ruff check .
uv run mypy
uv lock --check
git diff --check
```

### 自检项

- 测试是否检查官方 graph state，而不只检查 JSON projection。
- Tool ID 测试是否明确使用两个不同 ID。
- Streaming 测试是否包含分段 JSON、tool-only 响应与 usage。
- 预算测试是否覆盖并行/预取，而不只覆盖单个 agent。
- 是否没有把当前已知失败通过 `xfail` 永久隐藏。

### 回滚风险

低。此 Phase 只新增测试和证据；若测试夹具耦合过重，应先抽取 test helper，不能改生产协议来迁就测试。

## Phase 1：让 LangGraph checkpoint 成为唯一恢复事实源

### 目标

消除 `JsonGeneralAgentRunRepository` 对 Agent 执行恢复的控制权，使 `MongoDBSaver`、`StateSnapshot.interrupts` 与 `Command(resume=...)` 成为唯一恢复协议，同时保留 `run_id` 业务审计投影。

### 开始前重新读取

- `src/taichu/application/general_agent/service.py`
- `src/taichu/application/general_agent/executor.py`
- `src/taichu/application/general_agent/recovery.py`
- `src/taichu/application/general_agent/models.py`
- `src/taichu/application/contracts/general_agent_run.py`
- `src/taichu/infrastructure/general_agent_runs/json_repository.py`
- `src/taichu/main.py`
- Phase 0 的故障窗测试

### 涉及文件

- 上述 Runtime/仓储文件
- `src/taichu/application/general_agent/events.py`
- `src/taichu/api/routes/general_agent.py`
- 对应 unit/integration/recovery benchmark 测试

### 修改内容

按小步顺序执行：

1. 建立“官方 graph state → 业务 run projection”的单向映射函数，明确投影字段和非权威字段。
2. resume 先读取同一 `conversation_id` 的官方 `StateSnapshot` 与 interrupt payload，再构造 `Command(resume=...)`；不再用 JSON pending kind 选择恢复协议。
3. UI 只有在官方 interrupt checkpoint 已提交后才显示 `WAITING_HUMAN`。
4. 停止 `_plan_node()` 从 JSON durable run 回灌 graph state；计划恢复由官方 checkpoint 完成。
5. 启动恢复基于官方 graph state/next/interrupts 判断，JSON status 只作索引和展示。
6. 把 `_checkpoint()` 改名并降级为业务投影保存，或拆为明确的 `project_run_snapshot()`；移除恢复语义的 `checkpoint_revision`。
7. 保留 effect ledger、capability result ledger、trace/replay/context snapshot，但只保存关联 ID 与业务证据。
8. 设计历史 JSON 兼容读取：旧 run 可展示/审计，但不得被新 Runtime 当作 checkpoint 回灌。

### 不修改内容

- 不删除 `run_id`、`parent_run_id`。
- 不删除副作用账本、能力结果幂等记录、审计/replay。
- 不改变 DAG 依赖、授权、`continue_on_failure` 或 Tool 业务逻辑。
- 不改变 conversation/thread 生命周期。
- 不把产品历史改为直接读取 checkpoint 内部消息。

### 验收标准

- 任意正常 continuation 和 HIL resume 都只用 `conversation_id` thread。
- 授权 checkpoint 前后两个故障窗均只需一次批准。
- JSON run 丢失时，官方 checkpoint 仍能恢复执行；业务投影可重建或明确降级展示。
- JSON run 与官方 state 冲突时，执行以官方 state 为准并产生可审计告警。
- 搜索不到 JSON plan/pending/status 回灌 LangGraph state 的生产调用。
- `GeneralAgentRunRepository` 契约不再称为 Runtime checkpoint repository。

### 测试命令

```powershell
uv run pytest tests/unit/application/general_agent/test_runtime.py -q
uv run pytest tests/unit/application/general_agent/test_recovery_cases_24_25.py -q
uv run pytest tests/unit/application/general_agent/test_recovery_cases_26_27.py -q
uv run pytest tests/unit/application/general_agent/test_recovery_cases_28_29.py -q
uv run pytest tests/unit/application/general_agent/test_dynamic_dag_recovery.py -q
uv run pytest tests/integration/infrastructure/general_agent_runs/test_mongodb_checkpointer.py -q
uv run pytest tests/integration/infrastructure/general_agent_runs/test_mongodb_store.py -q
uv run mypy src/taichu/application/general_agent src/taichu/infrastructure/general_agent_runs
uv run ruff check src/taichu/application/general_agent src/taichu/infrastructure/general_agent_runs tests/unit/application/general_agent
git diff --check
```

### 自检项

- 是否仍有 `thread_id=run_id` 进入生产主链。
- 是否仍有 JSON pending 决定 `Command` 类型。
- 是否把“业务状态投影”误删成没有 API 可展示状态。
- node 重放时副作用是否仍由 ledger 幂等保护。
- 同一 conversation 多 run 是否仍能正确区分当前 `run_id` 审计所有者。
- Mongo client/checkpointer/store 生命周期是否未被破坏。

### 回滚风险

高。现有恢复测试固化了 JSON durable 行为。采用双读观测、单向投影、停止回灌、最后删字段的顺序；禁止一次提交同时删除所有历史兼容层。

## Phase 2：修复 BaseChatModel 结构化流式链

### 目标

让默认 `deepseek-v4-pro` 的 Anthropic Messages tool-use SSE 正确转换为 LangChain `AIMessageChunk.tool_call_chunks`，并统一 Writing AI endpoint、前端和测试的流式语义。

### 开始前重新读取

- `src/taichu/infrastructure/llm/rightcode.py`
- `src/taichu/infrastructure/llm/adapter.py`
- `src/taichu/infrastructure/llm/contracts.py`
- `src/taichu/infrastructure/llm/catalog.py`
- `src/taichu/application/services/writing_ai_service.py`
- `src/taichu/api/routes/writing_ai.py`
- `web/src/components/editor/editor-shell.tsx`
- RightCode/adapter/Writing API 测试

### 涉及文件

- 上述 LLM provider、adapter、Writing AI、API 和前端文件
- `tests/unit/infrastructure/llm/test_rightcode_gateway.py`
- `tests/unit/infrastructure/llm/test_gateway_chat_model.py`
- `tests/unit/application/services/test_writing_ai_native_outputs.py`
- `tests/integration/api/test_llm_api.py`
- 相应前端测试（若存在）

### 修改内容

1. 在 RightCode Anthropic SSE 解析器内处理：
   - `content_block_start` 的 `tool_use.id/name/input`；
   - `content_block_delta` 的 `input_json_delta.partial_json`；
   - 多 tool call、分段 JSON、停止原因与 usage；
   - tool-only 响应不再触发空文本错误。
2. 对 OpenAI Responses 路径核对 `response.function_call_arguments.delta/done`，补齐同等契约。
3. `GatewayChatModel._astream()` 按 LangChain 规范逐步产生 `tool_call_chunks`，并保持 usage metadata 与最终消息合并一致。
4. Writing AI 选择一种明确产品契约：
   - 若需要正文增量，使用官方 tool-call parser diff 投影结构化字段；
   - 若供应商只支持最终结构化对象，则将接口定义为状态流并移除“正文实时增量”假象。
5. 前端消费与 API 测试必须和选定契约一致。
6. provider 原始 SSE、成本、replay 仍只留 infrastructure。

### 不修改内容

- 不在 application 解析 Anthropic/OpenAI 原始 SSE。
- 不改非流式 `with_structured_output()` 调用。
- 不把结构化输出退回 Prompt JSON。
- 不改 RightCode 鉴权、价格表或无关模型目录。

### 验收标准

- 默认模型标准 Anthropic tool-use 流产生 completed response 和正确 tool calls。
- 跨 chunk JSON 合并、多个 tool calls、usage、取消和错误均有测试。
- GatewayChatModel 只暴露 LangChain chunk，不泄漏 provider event。
- Writing AI 流接口不再以空 delta 作为成功标准。
- 非流式 Writing、Selection、Summary、Knowledge Extraction 无回归。

### 测试命令

```powershell
uv run pytest tests/unit/infrastructure/llm/test_rightcode_gateway.py -q
uv run pytest tests/unit/infrastructure/llm/test_gateway_chat_model.py -q
uv run pytest tests/unit/application/services/test_writing_ai_native_outputs.py -q
uv run pytest tests/integration/api/test_llm_api.py -q
uv run mypy src/taichu/infrastructure/llm src/taichu/application/services/writing_ai_service.py
uv run ruff check src/taichu/infrastructure/llm src/taichu/application/services/writing_ai_service.py tests/unit/infrastructure/llm tests/integration/api/test_llm_api.py
git diff --check
```

### 自检项

- 是否正确处理 tool-only、text+tool 和多工具三种响应。
- 是否误把 partial JSON 当成完整 JSON 每片解析。
- chunk index、call id、name、args 是否稳定。
- replay 是否记录规范化结果与原始 provider 证据。
- 用户取消连接后 HTTP stream 是否及时关闭。
- 前端中文错误是否仍清楚且不暴露内部 DTO。

### 回滚风险

中高。SSE 是状态协议；按 provider 协议分别加小测试和实现，不共享未经证明的事件分支。

## Phase 3：ToolRuntime、调用身份与全局预算

### 目标

让 LangChain 负责真实 Tool Call identity、运行参数隐藏和 ToolMessage 配对；让太初继续负责权限/幂等/副作用，并补齐跨子 Agent 的任务级预算。

### 开始前重新读取

- `src/taichu/application/tools/contract.py`
- `src/taichu/application/tools/registry.py`
- `src/taichu/application/general_agent/capability_resolution.py`
- `src/taichu/application/general_agent/executor.py`
- `src/taichu/application/subagents/runner.py`
- `src/taichu/application/invocations/models.py`
- `src/taichu/application/invocations/middleware.py`
- 当前安装版本的 `ToolRuntime`、`InjectedToolCallId`、`InjectedToolArg`、`BaseTool.tool_call_schema` 签名

### 涉及文件

- 上述 Tool/Runtime/Middleware 文件
- Tool 和 Subagent manifests
- invocation trace/result repositories
- Tool/Subagent/General Agent 对应测试

### 修改内容

1. `StructuredTool` handler 接收 `ToolRuntime` 或 injected call ID；用真实模型 call ID 构造/关联 `InvocationContext`。
2. 若太初仍需独立业务 `invocation_id`，明确拆成 `tool_call_id` 与 `invocation_id` 两字段并保存映射。
3. 用 injected args 隐藏授权 ID、幂等键等运行字段；从 `BaseTool.tool_call_schema` 派生规划器可见 schema。
4. 保留 ToolManifest 的权限、副作用、幂等、reconciler、结果预算与 caller policy。
5. 建立任务级共享工具预算：
   - LangGraph state/可归并 reducer 或原子业务账本记录已消费量；
   - 直接 Tool、来源预取、每个子 Agent Tool loop 都消费；
   - 官方 `ToolCallLimitMiddleware` 继续负责单 Agent 局部上限。
6. 并行 `Send` 下明确竞争/归并策略，超限后产生可审计、可预测的业务错误。
7. 核对 manifest 中 `accepted_scopes/max_retries/exposures/supports_streaming`：有消费者则执行，无消费者则删除或标记为纯描述，不能假装安全门禁。

### 不修改内容

- 不删除 ToolRegistry 的业务安全策略。
- 不让模型看见授权 token、幂等键或内部 runtime scope。
- 不把副作用账本换成 LangGraph Store。
- 不改变各业务 Tool 的领域输入输出含义。

### 验收标准

- 每个 LangChain ToolCall 的 ID 可唯一关联 ToolMessage、artifact、trace、幂等和 replay。
- 同一工具连续两次调用不会共享预绑定 call ID。
- 模型可见 schema 不包含 injected runtime 字段，真实执行仍能获得它们。
- `max_total_tool_calls` 对直接、预取、子 Agent 与并行分支全部生效。
- ToolRegistry 只保留太初业务策略与必要 adapter，不再手工模拟 Tool runtime identity。

### 测试命令

```powershell
uv run pytest tests/unit/application/tools -q
uv run pytest tests/unit/application/subagents -q
uv run pytest tests/unit/application/general_agent/test_orchestrator_planning_schema.py -q
uv run pytest tests/unit/application/general_agent/test_runtime.py -q
uv run pytest tests/unit/application/general_agent/test_dynamic_dag_recovery.py -q
uv run mypy src/taichu/application/tools src/taichu/application/subagents src/taichu/application/invocations src/taichu/application/general_agent
uv run ruff check src/taichu/application/tools src/taichu/application/subagents src/taichu/application/invocations src/taichu/application/general_agent tests/unit/application
git diff --check
```

### 自检项

- 是否错误地把业务 invocation ID 当作模型 call ID 覆盖。
- injected 字段是否真的从模型 schema 隐藏。
- 并行预算是否原子且可恢复，不依赖进程内全局变量。
- retry 是否重复计费/重复消费预算，语义是否写入测试。
- Tool error 是否仍由 LangChain ToolMessage 正确返回，不破坏业务中文错误。

### 回滚风险

高。调用身份关联到审计、幂等和回放；先双字段兼容，再切换读者，最后删除旧固定 ID。

## Phase 4：修正 Context / Memory 的 HIL 边界

### 目标

用显式 turn/message identity 取代文本相等推断，并让澄清问题/回答按业务生命周期正确失效与分类；保持小说 Context 和产品历史边界不变。

### 开始前重新读取

- `src/taichu/application/general_agent/context.py`
- `src/taichu/application/general_agent/service.py`
- `src/taichu/application/general_agent/models.py`
- `src/taichu/application/general_agent/memory_policy.py`
- `src/taichu/application/services/agent_memory_service.py`
- `src/taichu/infrastructure/agent_memory/langgraph_repository.py`
- 产品历史、长期记忆与相关评测测试

### 涉及文件

- 上述 Context/Memory/Runtime 文件
- Agent memory models/repository tests
- General Agent memory behavior/recovery tests

### 修改内容

1. 为当前请求/HIL 消息保存稳定 turn sequence、request ID 或 message ID；ContextAssembler 按标识切分，而不是比较 content。
2. 澄清问题创建 `UNRESOLVED_ISSUE` 时保存 request/turn 关联。
3. 接收回答后显式 resolve/supersede 原问题。
4. 澄清回答默认作为当前任务工作记忆或历史对话，不自动永久写 `USER_INSTRUCTION`；只有明确的跨任务偏好才进入长期运行记忆。
5. 保持五层顺序和角色边界：内部 Tool/子 Agent 轨迹不进入产品历史。
6. 对旧记录提供只读兼容，不重新创建 JSON runtime memory。

### 不修改内容

- 不用 `SummarizationMiddleware` 直接替代五层 ContextAssembler。
- 不把小说知识卡/正文写入 Agent Store 当事实源。
- 不开放作者手工 CRUD 单条运行记忆。
- 不改变 Markdown 长期记忆的产品职责。

### 验收标准

- 重复文本、同文澄清回答、多轮 continuation 都能准确识别当前请求。
- 已回答问题不再以 active unresolved issue 被召回。
- 一次性事实回答不会成为无期限用户偏好。
- 删除 conversation 时运行记忆仍按现有规则级联失效。
- Store 仍是运行记忆持久层，业务有效性由 AgentMemoryService 管理。

### 测试命令

```powershell
uv run pytest tests/unit/application/agent_memory -q
uv run pytest tests/unit/application/services/test_agent_memory_run_deletion.py -q
uv run pytest tests/unit/infrastructure/agent_memory -q
uv run pytest tests/unit/application/evaluations/general_agent_benchmark/test_memory_behavior_cases.py -q
uv run pytest tests/unit/application/general_agent/test_runtime.py -q
uv run mypy src/taichu/application/general_agent src/taichu/application/services/agent_memory_service.py src/taichu/infrastructure/agent_memory
uv run ruff check src/taichu/application/general_agent src/taichu/application/services/agent_memory_service.py src/taichu/infrastructure/agent_memory tests/unit/application/agent_memory
git diff --check
```

### 自检项

- 当前请求原文是否保持一字不改。
- message/turn 标识是否可持久、可恢复且不依赖内存对象地址。
- resolve 是否误删仍需追踪的问题。
- 产品历史是否混入 system/developer/tool/subagent 轨迹。
- 记忆失效是否仍遵守来源指纹和生命周期策略。

### 回滚风险

中。历史记录缺少新标识；使用显式兼容投影，禁止用文本猜测作为新主路径继续保留。

## Phase 5：Provider、Structured Output 与 Middleware 边界收敛

### 目标

移除 API→RightCode 具体实现越层，删除 Eval Judge 二次 JSON 边界，修正 trace 语义，并只在证明等价时尝试 Orchestrator 官方 `response_format`。

### 开始前重新读取

- `src/taichu/api/deps.py`
- `src/taichu/api/routes/llm.py`
- `src/taichu/application/contracts/llm.py`
- `src/taichu/application/invocations/config.py`
- `src/taichu/application/invocations/middleware.py`
- `src/taichu/application/general_agent/orchestrator.py`
- `src/taichu/application/contracts/evaluation_judge.py`
- `src/taichu/application/evaluations/knowledge_extraction/judge.py`
- `src/taichu/infrastructure/evaluations/llm_judge_adapter.py`
- 当前安装版本 `create_agent(response_format=...)` 与 `ToolStrategy` 实现/测试

### 涉及文件

- 上述 API/application/infrastructure 文件
- composition root `src/taichu/main.py`
- provider、judge、orchestrator、trace 相关测试

### 修改内容

1. 在 application 定义 provider-neutral 的模型目录、切换和 probe service/port；API 不再 import 或 `isinstance(RightCodeLLMGateway)`。
2. 从应用可见 profile 移除 `base_url_key`；`wire_protocol` 只作为审计 identity snapshot，不参与业务分支。
3. 明确动态模型选择接口：任意注入的 `BaseChatModel` 要么支持统一 runtime model resolver，要么在 composition root 选择具体模型，不能依赖未声明的自定义 kwargs。
4. Eval Judge 端口直接返回 typed Pydantic result；raw response 独立作为审计快照，不再序列化后让应用二次 parse。
5. 修正 trace：model-call sequence 与 retry count 分开；输入哈希包含 tool_calls、ToolMessage call ID 和必要 artifact identity；明确 trace 写失败策略。
6. 对 Orchestrator 只做隔离 spike：验证当前 LangChain 1.3.11 能否同时满足“候选 schema 可见但不可执行、只允许输出 schema”。没有等价证明则保留现实现并记录理由，不强迁。

### 不修改内容

- 不把 RightCode HTTP/SSE、计费、fallback、replay 移出 infrastructure。
- 不丢失 provider/model/upstream/endpoint 审计身份。
- 不把 candidate capability tools 变成规划阶段可执行 tools。
- 不用 Prompt JSON 替代官方 structured output。

### 验收标准

- `src/taichu/api` 不再 import `taichu.infrastructure.llm.*`。
- application 的模型目录不包含 base URL/config key。
- Eval Judge 应用层不再 `model_validate_json` 解析刚由 structured output 产生的字符串。
- trace 能区分正常 Agent loop 次数和 retry，完整关联 tool call。
- Orchestrator 迁移与否都有可执行契约测试和当前版本证据。

### 测试命令

```powershell
uv run pytest tests/unit/infrastructure/llm -q
uv run pytest tests/unit/application/general_agent/test_capability_resolution.py -q
uv run pytest tests/unit/application/general_agent/test_orchestrator_planning_schema.py -q
uv run pytest tests/unit/application/services/test_knowledge_extraction_evaluation_service.py -q
uv run pytest tests/integration/api/test_llm_api.py -q
uv run pytest tests/integration/api/test_general_agent_api.py -q
uv run mypy src/taichu/api src/taichu/application/contracts src/taichu/application/invocations src/taichu/application/general_agent src/taichu/infrastructure/llm src/taichu/infrastructure/evaluations/llm_judge_adapter.py
uv run ruff check src/taichu/api src/taichu/application src/taichu/infrastructure/llm src/taichu/infrastructure/evaluations/llm_judge_adapter.py
git diff --check
```

### 自检项

- API 是否仍通过类型逃逸或 app.state 取得具体网关。
- 审计 identity 是否保留而 transport config 是否移除。
- Eval raw snapshot 是否与 typed result 解耦。
- trace 写失败是否有明确告警/错误，而不是裸 `except Exception: pass`。
- Orchestrator 是否意外允许执行候选能力 Tool。

### 回滚风险

中高。模型选择、probe、计费与回放存在外部行为；先引入 application service facade，再迁 API，最后收窄具体 gateway 暴露。

## Phase 6：兼容层、资源生命周期与类型清理

### 目标

删除已失去运行用途的兼容入口和测试专用生产代码，修正 Saver/Store 组合与 Mongo 初始化竞态，并把本次涉及范围恢复到严格类型安全。

### 开始前重新读取

- `src/taichu/main.py`
- `src/taichu/application/general_agent/executor.py`
- `src/taichu/application/general_agent/events.py`
- `src/taichu/application/general_agent/recovery.py`
- `src/taichu/infrastructure/llm/adapter.py`
- `src/taichu/infrastructure/llm/mock.py`
- 全仓对待删类/函数/状态的引用与历史数据兼容测试

### 涉及文件

- composition root、Runtime、LLM adapter/mock
- `tests/fakes/`
- 对应 unit/integration tests
- 必要时 `.env.example`、`start.bat`（仅当配置/启动行为确实改变）

### 修改内容

1. 删除或私有化 `DynamicDagExecutor.execute()` 的 run-id thread 兼容入口。
2. 应用测试全面使用 `BaseChatModel` fake 后，删除 `LangChainLLMAdapter` 双重桥和生产 `infrastructure/llm/mock.py`。
3. Checkpointer/Store 由 composition root 成对注入；测试显式选择 InMemory 组合，不再生产静默降级。
4. 去掉 Mongo Store “先列集合再创建”的竞态写法，采用当前库支持的幂等初始化。
5. 核对并清理无调用的 `NodeAttemptStatus/NodeAttempt`、未使用状态/manifest 字段；历史数据仍依赖者先迁移再删。
6. 为事件 subscriber queue、run/conversation lock 增加明确背压/回收策略。
7. 处理本任务触及模块的全部 MyPy 错误，收紧 `Any`/cast；不扩展成无关领域重构。
8. 若改动 `main.py`、配置或依赖，严格按 `AGENTS.md` 验证 `start.bat` 固定端口启动。

### 不修改内容

- 不删除仍有真实调用的兼容层。
- 不顺手升级 LangChain/LangGraph 版本。
- 不删除 dev/eval/vector 传递依赖。
- 不改变小说事实源、检索索引或前端产品功能。

### 验收标准

- 生产源码不再包含只被测试使用的 LLM mock/双重 adapter。
- 没有公开入口以 `run_id` 默认建立 LangGraph thread。
- checkpointer/store 持久性组合明确且一致。
- Mongo 多进程启动不会因集合竞态失败。
- 本次触及路径 MyPy 为零；全量 MyPy 的任何剩余错误都有独立清单，不被虚报为通过。
- 无死代码、误导命名或无消费者的安全声明。

### 测试命令

```powershell
uv run pytest tests/unit/test_main_llm.py tests/unit/test_main_server.py -q
uv run pytest tests/unit/application/general_agent tests/unit/application/subagents tests/unit/application/tools -q
uv run pytest tests/unit/infrastructure/llm tests/integration/infrastructure/general_agent_runs -q
uv run ruff check .
uv run mypy src/taichu/application/general_agent src/taichu/application/subagents src/taichu/application/tools src/taichu/application/invocations src/taichu/infrastructure/llm src/taichu/main.py
uv lock --check
git diff --check
```

若触及启动敏感文件，再执行：

```powershell
start.bat
```

并按固定地址探测：

```text
http://127.0.0.1:8000/health
http://127.0.0.1:8000/api/llm/models
http://127.0.0.1:8000/api/agent-workbench/general-assistant/runs
http://127.0.0.1:8000/api/vector-graph/status
http://localhost:3000/home
```

### 自检项

- `rg` 是否确认待删对象无真实调用。
- InMemory 组件是否只在显式测试 factory 中出现。
- 资源关闭是否覆盖正常退出、启动失败和取消。
- lock/queue 回收是否不会中断活跃 subscriber。
- 是否因类型修复改变了业务数据形状。

### 回滚风险

中。兼容层删除对测试与历史 fixture 影响较大；每类兼容层单独提交、单独验证。

## Phase 7：完整回归、逆向复审与交付

### 目标

从 API 重新逆向追踪到 provider/checkpoint/store，证明旧 Runtime 已退出恢复路径，所有迁移目标真实成立，而不是只让新增测试通过。

### 开始前重新读取

- 本任务包全部 Phase 的实际 diff
- `docs/reviews/langchain_langgraph_migration_audit.md`
- `AGENTS.md`
- 当前 `pyproject.toml`、`uv.lock`、`main.py`
- 所有新增/修改测试

### 涉及文件

- 原则上不再新增生产功能；只修正复审发现的本任务范围问题。
- 更新当前架构说明时遵守 `docs/rule.md`，不得把未来行为写成已实现事实。

### 修改内容

1. 重新搜索全部关键概念：thread/checkpoint/resume/pending/tool_call/schema/parser/DTO/stream/deepagents。
2. 重新画真实调用链并与审计表逐项对照。
3. 运行全量与专项门禁。
4. 使用真实 `MongoDBSaver + MongoDBStore` 做同会话多 run、动态 `Send`、interrupt/resume、进程重启集成验证。
5. 复验默认 Anthropic tool-use streaming 与 Writing AI endpoint。
6. 复验 ToolCall/trace/artifact identity 与跨子 Agent 总预算。
7. 复验 HIL 重复文本、unresolved issue 关闭和历史投影。
8. 检查旧实现、旧测试、旧依赖、旧配置和误导文档是否同步清理。

### 不修改内容

- 不通过更新 baseline 掩盖行为漂移。
- 不把审计中“不应迁移”的业务能力改为框架状态。
- 不在最终回归阶段追加无关重构。

### 验收标准

- P0/P1 的失败复现全部转为通过，且旧兼容路径不再被生产调用。
- 13 条迁移声明重新审计后没有 FAIL；PARTIAL 只允许有明确、合理保留的业务边界。
- 全量测试、Ruff、MyPy、uv lock、diff check 均执行并如实记录。
- 固定端口后端、Agent、模型、向量图和前端均返回预期状态。
- 当前 Runtime baseline 通过；没有更新固定工件来迁就实现。
- 最终 diff 不包含无关业务、小说数据或派生索引变更。

### 测试命令（完整回归）

```powershell
uv run pytest -q
uv run ruff check .
uv run mypy
uv lock --check
git diff --check
```

### Runtime / dependency / API baseline

```powershell
uv run pytest tests/unit/application/evaluations/general_agent_benchmark/test_replacement_boundary.py -q
uv run pytest tests/integration/infrastructure/evaluations/test_general_agent_benchmark_synthetic_runtime.py -q
uv run pytest tests/integration/infrastructure/general_agent_runs -q
uv run python -c "import importlib.metadata as m; [print(f'{n}={m.version(n)}') for n in ['langchain','langchain-core','langgraph','langgraph-checkpoint','langgraph-checkpoint-mongodb','langgraph-store-mongodb']]"
```

固定端口服务已经运行且属于本项目时直接复用；否则按 `start.bat` 约定清理并启动，随后验证：

```powershell
$targets = @(
  'http://127.0.0.1:8000/health',
  'http://127.0.0.1:8000/api/llm/models',
  'http://127.0.0.1:8000/api/agent-workbench/general-assistant/runs?page=1&page_size=1',
  'http://127.0.0.1:8000/api/vector-graph/status',
  'http://localhost:3000/home'
)
foreach ($target in $targets) {
  $response = Invoke-WebRequest -Uri $target -UseBasicParsing -TimeoutSec 10
  "${target}`t$($response.StatusCode)"
}
```

### 自检项

- 全量 MyPy 若非零，是否明确失败而不是只展示局部绿灯。
- 官方 checkpoint 是否是唯一 resume 权威。
- 默认 provider 是否真的通过 tool-use streaming 测试。
- ToolCall ID 和预算是否跨并行/重试/重启保持一致。
- JSON run 是否仅为投影，不再回灌图。
- API/domain/application 是否无 provider transport DTO 越层。
- 是否保留所有“Things That Should NOT Be Migrated”。

### 回滚风险

最终 Phase 不应产生大范围实现改动。若复审发现重大问题，退回对应 Phase 以小步修复并重新执行其门禁，不在最终阶段临时打补丁。

## 完成定义

只有同时满足以下条件，才能宣布本批迁移完成：

1. 官方 LangGraph checkpoint/interrupt 是唯一执行恢复事实源。
2. 默认 RightCode 模型的结构化流式 Tool Call 端到端可用。
3. LangChain ToolCall、太初审计/幂等/回放身份可准确关联。
4. 任务级工具预算、HIL turn boundary 与记忆 lifecycle 可验证。
5. API/application/domain 与 provider transport 边界清楚。
6. 旧兼容层和固化旧架构的测试在同一迁移中清理。
7. 所有验证结果按实际命令如实记录，没有用测试数量替代架构证据。

完成这些之后，**停止继续追求框架外观上的“官方化”**。下一阶段应转向产品能力、RAG、Eval 和 Context Engineering，而不是迁移本任务包明确要求保留的太初业务能力。
