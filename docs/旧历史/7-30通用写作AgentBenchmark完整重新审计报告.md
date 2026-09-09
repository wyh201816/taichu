# 通用写作 Agent Benchmark 完整重新审计报告

> 更新日期：2026-07-30  
> 资料状态：历史审计快照；记录 2026-07-30 仓库实现与基于该实现的候选设计，不作为当前代码的替代事实源。  
> 审计范围：固定套件、加载器、能力目录、六类门禁、合成与真实模型轨道、隔离夹具、生产 Runtime、Tool/Subagent、记忆、Checkpoint、授权与上下文治理。  
> 变更边界：本轮未修改代码、Fixture、测试、前端或评测运行工件；只新增本审计文档，并按文档规则补充仓库地图入口。

## 0. 结论先行

### 0.1 当前 Benchmark 的定位

- **[代码事实]** 正式套件自述用途是“以独立合成小说夹具验证真实 Runtime、生产 Tool、生产 Subagent、权限、记忆与恢复边界”，不是模型排行榜：`tests/fixtures/evaluations/general_writing_agent_benchmark/suite.json:2-8`。
- **[基于事实的推断]** 一个 Benchmark 在设计意图上应是一个围绕独立 Runtime 能力或独立失败模式的端到端合同场景，不等同于“一个系统功能对应一道题”，也不必等同于一个原子能力点。当前第 1、2、4 条接近单能力点；第 3、6、8—16、23 条都是多阶段任务。
- **[代码事实]** 当前真正被加载的 `AuthoredCaseSpec` 只保存请求、必需调用、固定脚本和通用预算，没有“期望最终产物”“场景专属判定器”或“最终状态断言”字段：`src/taichu/application/evaluations/general_agent_benchmark/suite_loader.py:50-64`。
- **[基于事实的推断]** 因此当前体系更准确的定位是：**在密封夹具中验证真实 Runtime 能否按冻结脚本完成预定交互、调用生产能力处理器，并通过一组通用硬门禁**。它还不是覆盖 23 个场景最终行为效果的完整 Contract Benchmark。

### 0.2 当前 23/23 能证明什么

- **[代码事实]** 冻结合成基线记录 `case_count=23`、`passed_case_count=23`，并绑定套件哈希 `136ce63f...`、夹具快照和 Runtime 配置身份：`project_assets/derived/general_agent_benchmarks/indexes/synthetic-passed-baseline.json:1`、`project_assets/derived/general_agent_benchmarks/runs/synthetic_baseline_b2b8f47d486d2ebba46c366b61df807e.json:1`。
- **[代码事实]** 合成轨道使用固定模型响应，但通过生产插件发现注册真实 Tool/Subagent，并构造真实 `OrchestratorAgent`、`DynamicDagExecutor`、`GeneralAgentRuntimeService`：`src/taichu/infrastructure/evaluations/general_agent_benchmark/runtime_factory.py:149-293`；隔离环境使用真实 Markdown 服务、MongoDB 知识仓储、JSON 运行记忆、JSON 中间工件与 LangGraph Checkpoint：`src/taichu/infrastructure/evaluations/general_agent_benchmark/synthetic_environment.py:277-388`。
- **[基于事实的推断]** 所以 23/23 能证明：固定套件与夹具身份有效；真实 Runtime 和生产能力注册可被驱动；脚本声明的 Tool/Subagent 可完成；统一预算上限没有被观察值突破；四种运行记忆状态的投影集合满足当前规则；验证阶段的一次中断可从同一运行的有效 Checkpoint 恢复且不重跑已成功的结构读取。
- **[代码事实]** 合成轨道的产物门禁与证据门禁都只取 `bool(observer.interaction_records)`，安全门禁直接传入 `True`；普通案例的专属机制条件直接返回成功：`synthetic_environment.py:218-258,878-919`。
- **[基于事实的推断]** 所以 23/23 **不能证明**：检索答案正确；证据来源与结论逐项绑定；多 Agent 结果被下游真实消费；三个审查分支发生时间重叠且互不污染；修订修复了目标问题且保留非目标内容；预览、授权、拒绝和写入后的真实资源状态符合合同；四类记忆真正改变或没有污染最终答案；长上下文裁剪与压缩后仍能完成任务；六类安全边界均被动态攻击或负例验证。

### 0.3 最高优先级审计结论

1. **正式清单是 23 条。** 不存在“章节摘要”Benchmark，也不存在第 24 条“预算耗尽”；第 18 条是 `write_authorization_denied`。预算是六类门禁之一。权威顺序见 `suite.json:10-34`、`capability_catalog.py:101-125`，加载器强制顺序一致且 ID 唯一：`suite_loader.py:100-105`。
2. **六类门禁必须齐全且全部通过。** 任一门禁 `INVALID` 则案例无效，任一门禁 `FAILED` 则案例失败，否则才通过：`src/taichu/application/evaluations/general_agent_benchmark/gates.py:51-74`。但当前门禁多为存在性或状态代理，离场景行为证据仍有明显距离。
3. **第 8 条名称与执行图不一致。** 元数据声称摘要后世界观与人物并行，实际计划却让人物依赖世界观，形成摘要→世界观→人物串行链：`suite.json:149-155`。
4. **第 9 条存在真实结构性交接，但没有语义消费判定。** Executor 会把直接依赖节点的中间工件 ID 注入下游 Subagent，Subagent runner 会读取并拼入输入：`src/taichu/application/general_agent/executor.py:929-966`、`src/taichu/application/subagents/runner.py:238-256`；当前门禁只证明三个 Subagent 完成，不能证明场景规划和草稿在语义上使用了上游结果。
5. **第 10 条最多证明 DAG 无依赖与脚本可交错。** 三个审查节点确实从 `START` 独立进入 LangGraph：`executor.py:275-295`；Strict driver 的并行组只放宽合法消费顺序：`strict_driver.py:325-349`。没有时间区间或分支状态快照，不能证明物理并发或无污染。
6. **第 13 条不是原运行整图原样恢复。** 授权请求前会解析并冻结预览输出绑定：`executor.py:435-480,929-966`；批准后 `GeneralAgentRuntimeService._continue_write_authorization()` 创建延续运行，并重建只含授权写入节点的单节点计划：`src/taichu/application/general_agent/service.py:631-722`。当前能支持“写入输入来自预览”的代码事实，不能支持“同一 `run_id`、同一完整计划继续执行”。
7. **第 18 条没有覆盖拒绝流程。** 正式输入描述预览、授权请求和拒绝，但 `required_invocations=[]`，脚本只有一次直接拒绝，无预览、无 Human-in-the-loop、无 `resume()`：`suite.json:319-325`。真实拒绝分支虽存在于 `service.py:648-669`，该案例没有触达。
8. **第 19—22 条测的是 Runtime 工作记忆实体，不是长期记忆层。** `AgentMemoryKind` 明确是“当前任务工作记忆”的内容类型：`src/taichu/application/agent_memory/models.py:20-28`；上下文组装器当前把 `long_term_memory` 固定为空：`src/taichu/application/general_agent/context.py:451-469`。
9. **记忆 Fixture 清单与实际造数存在偏差。** 清单哈希包含 `runtime_memory/seed.json`：`fixtures/core_novel/fixture-manifest.json:6-17`；但实际环境没有读取该文件，而由 `_seed_memories()` 硬编码四条内容，并把四条都创建为 `USER_INSTRUCTION`：`synthetic_environment.py:680-737`。因此 JSON 中的 `collaboration_preference`、`temporary_scope`、`candidate_fact`、`writing_preference` 并不是当前运行实体的真实 kind。
10. **上下文治理完全缺少正式 Benchmark。** Runtime 已有 180,000 字符总预算、分层预算、压缩阈值、长期→历史→工作记忆裁剪顺序，以及稳定记忆/当前请求不可截断、仍超限时抛出 `ContextAssemblyError` 的实现：`context.py:54-80,419-498,501-658`；正式 23 条没有任何案例注入长历史、长工作记忆或超长当前请求。

## 1. 审计方法与证据分级

### 1.1 证据优先级

1. 当前 `suite.json`、加载模型与能力目录；
2. synthetic/live runner、严格驱动器和六门禁实际调用路径；
3. 生产 Runtime、Executor、Tool/Subagent 与存储实现；
4. Fixture、冻结基线与当前测试；
5. 基于以上事实的推断；
6. 后续设计建议。

本文每个关键结论显式标注：

- **[代码事实]**：可由当前文件、类、函数和行号直接证明；
- **[基于事实的推断]**：由多项代码事实共同支持，但不是仓库显式声明；
- **[后续设计建议]**：候选合同，不代表当前已实现。

无法从当前代码证明的内容统一写为“**当前证据不足**”。

### 1.2 只读复核结果

- `AuthoredSuiteSpec._case_order_is_exact()` 强制案例数组与 `case_order` 完全一致且唯一：`suite_loader.py:100-105`。
- `load_authored_suite()` 同时校验规范化内容哈希和能力目录哈希：`suite_loader.py:108-125`。
- `load_fixture_manifest()` 逐文件核对大小与 SHA-256：`suite_loader.py:128-138`。
- 本轮只读执行清单与核心套件测试：  
  `uv run pytest tests/unit/application/evaluations/general_agent_benchmark/test_capability_coverage.py tests/integration/infrastructure/evaluations/test_general_agent_benchmark_core_suite.py -q`  
  结果：`11 passed in 0.93s`。对应断言入口：`tests/unit/application/evaluations/general_agent_benchmark/test_capability_coverage.py:18-59`、`tests/integration/infrastructure/evaluations/test_general_agent_benchmark_core_suite.py:32-45,92-105`。

## 2. 正式 23 条清单校准

### 2.1 权威数量、顺序与轨道

`suite.json:10-34` 与 `capability_catalog.py:101-125` 给出完全相同的 23 条顺序。`capability_catalog.py:126-137` 把第 17、23 条标为仅 synthetic，其余 21 条标为 synthetic + live_provider。

| # | 唯一 ID | 中文名称 | 正式适用轨道 | 主类别 | 能力标签 |
|---:|---|---|---|---|---|
| 1 | `direct_answer_current_request` | 当前请求直接回答 | S+L | 简单路由 | 最小充分路径、零能力调用 |
| 2 | `single_manuscript_search` | 单次正文检索 | S+L | 检索/RAG | 正文检索、章节身份 |
| 3 | `structure_coverage_read` | 结构与覆盖读取 | S+L | 检索/RAG | 多源读取、顺序依赖 |
| 4 | `single_knowledge_retrieval` | 单次知识检索 | S+L | 检索/RAG | 确认态知识召回 |
| 5 | `knowledge_catalog_identity_read` | 知识目录身份读取 | S+L | 检索/RAG | 目录、身份解析、定向读取 |
| 6 | `external_research_grounded` | 外部资料有据研究 | S+L | 检索/RAG | 外部证据、授权、事实边界 |
| 7 | `single_canon_evidence` | 单次设定证据 | S+L | 证据生成 | 事实/推测边界 |
| 8 | `summary_world_character` | 摘要世界与人物并行分析 | S+L | 多 Agent 协作 | 共同上游、分支分析 |
| 9 | `architecture_scene_draft` | 架构场景与草稿 | S+L | 多 Agent 流水线 | 中间工件、候选稿 |
| 10 | `parallel_review_triad` | 三审并行 | S+L | 多 Agent 并行审查 | 同源输入、分支隔离 |
| 11 | `revision_from_reviews` | 依据审查修订 | S+L | 反馈修订 | 审查消费、目标修复、内容保持 |
| 12 | `manuscript_preview_only` | 正文补丁仅预览 | S+L | 预览/写入边界 | 无副作用预览 |
| 13 | `manuscript_patch_authorized_resume` | 授权后应用正文补丁 | S+L | 授权与恢复 | 预览绑定、授权后写入 |
| 14 | `structure_create_update` | 结构创建并更新 | S+L | 持久化资源治理 | 真实 ID/版本因果使用 |
| 15 | `structure_delete_second_confirmation` | 结构删除二次确认 | S+L | 高风险写入 | 二次确认、对象身份 |
| 16 | `knowledge_create_update` | 知识创建并更新 | S+L | 持久化资源治理 | 真实卡 ID/更新时间 |
| 17 | `external_access_denied` | 外部访问拒绝 | S | 权限拒绝 | 零外部调用 |
| 18 | `write_authorization_denied` | 写授权拒绝 | S+L | 写入拒绝 | 预览→拒绝意图，当前未覆盖 |
| 19 | `memory_active_projection` | 活动记忆投影 | S+L | 记忆治理 | 有效工作记忆 |
| 20 | `memory_stale_dependency` | 过期记忆依赖拒绝 | S+L | 记忆治理 | 过期排除 |
| 21 | `memory_rejected_parallel_isolation` | 拒绝记忆并行隔离 | S+L | 记忆治理 | 被拒绝信息、分支隔离意图 |
| 22 | `memory_superseded_repair` | 被替代记忆修复 | S+L | 记忆治理 | 新旧冲突、替代关系 |
| 23 | `runtime_checkpoint_recovery` | 检查点恢复 | S | Checkpoint/恢复 | 故障注入、成功节点不重跑 |

逐案定义范围分别是：`suite.json:37-44,47-58,61-76,79-90,93-108,111-127,130-142,145-163,166-184,187-205,208-220,223-234,237-251,254-271,274-286,289-306,309-316,319-326,329-336,339-346,349-356,359-366,369-378`。

### 2.2 轨道声明与实际 runner 的偏差

- **[代码事实]** `suite.json` 只在套件级声明 synthetic 与 live_provider，`AuthoredCaseSpec` 没有 `applicable_tracks` 字段：`suite.json:381-383`、`suite_loader.py:50-64`。
- **[代码事实]** 逐案例轨道只存在于 `CORE_CASES` 元数据：`capability_catalog.py:126-137`。
- **[代码事实]** `LiveSuiteRunner.run()` 直接遍历传入的 `suite.cases`，不读取 `CORE_CASES.applicable_tracks`：`src/taichu/infrastructure/evaluations/general_agent_benchmark/live_runtime.py:668-679`。
- **[基于事实的推断]** 表中轨道是正式能力目录口径；“live runner 必然只运行 21 条”当前证据不足。如果调用方传入完整套件，runner 自身会遍历 23 条。

### 2.3 明确不存在的案例

- “章节摘要”不是独立 Benchmark；它只是第 8 条中的 `narrative_summary` Subagent：`suite.json:145-162`。
- 没有第 24 条“预算耗尽”。每案默认预算由 `AuthoredCaseSpec.budgets` 给出：`suite_loader.py:57-64`。
- `budget_exceeded` 是失败分类，不是案例：`src/taichu/application/evaluations/general_agent_benchmark/models.py:478-490`。

### 2.4 Benchmark 总览：输入、期望行为与最终产物

**[代码事实]** `AuthoredCaseSpec` 没有“期望行为”或“最终产物”字段：`suite_loader.py:50-64`。下表的输入来自 `suite.json`；“期望行为/最终产物”是依据输入文本、稳定 Tool/Subagent 合同和产品意图做出的**基于事实的归纳**，用于说明每案本应证明什么，不能误写成当前门禁已执行的断言。逐案实际证明边界见第 6—10 节。

| # | 唯一 ID／中文名 | 输入摘要 | 期望行为 | 目标最终产物（当前未由 suite 字段声明） |
|---:|---|---|---|---|
| 1 | `direct_answer_current_request`／当前请求直接回答 | 写冲突场景最先明确什么 | 选择零 Tool/Subagent 的最小路径 | 一条先结论后依据的直接回答 |
| 2 | `single_manuscript_search`／单次正文检索 | 查 `chapter_001` 中归潮灯亮后照出什么 | 用一次正文检索命中正确章节身份 | 带命中依据的事实回答 |
| 3 | `structure_coverage_read`／结构与覆盖读取 | 读结构、知识覆盖和两章正文，找共同钟鸣线索 | 多源读取后综合而非单源猜测 | 两章共同线索分析及来源 |
| 4 | `single_knowledge_retrieval`／单次知识检索 | 询问归潮灯已确认规则 | 只召回相关确认态知识 | 基于确认卡的规则回答 |
| 5 | `knowledge_catalog_identity_read`／知识目录身份读取 | 目录→解析身份→用真实 ID 读卡 | 不得用摘要跳过真实卡读取 | 被解析卡的真实内容及回答 |
| 6 | `external_research_grounded`／外部资料有据研究 | 比较固定灯塔民俗资料与小说设定 | 授权后搜索、读取并由外研 Subagent 区分外部说法与小说事实 | 外研工件、来源引用和比较结论 |
| 7 | `single_canon_evidence`／单次设定证据 | 区分星纹被谁磨去的事实与角色推测 | Canon 只生成证据和边界，主编排再裁决 | Canon 证据工件与有边界的最终回答 |
| 8 | `summary_world_character`／摘要世界与人物并行分析 | 总结两章，再分析世界规则和人物选择 | 一个共同摘要分发给两个独立分析分支 | 摘要、世界观工件、人物工件和综合结论 |
| 9 | `architecture_scene_draft`／架构场景与草稿 | 依次规划架构、场景并起草受阻场景 | 前序工件真实约束后序，只产候选稿 | 架构工件、场景计划、正文候选稿 |
| 10 | `parallel_review_triad`／三审并行 | 三种审查同一候选句 | 三个无依赖、同源且隔离的审查分支 | 三份可独立引用的审查工件 |
| 11 | `revision_from_reviews`／依据审查修订 | 用三份固定审查工件修订候选句 | 消费审查、修复目标问题、保护非目标内容 | 修订工件及到三份审查的引用 |
| 12 | `manuscript_preview_only`／正文补丁仅预览 | 预览追加一句，不得应用 | 生成可确认补丁且正文保持不变 | Preview/diff、预期哈希和零写入状态 |
| 13 | `manuscript_patch_authorized_resume`／授权后应用正文补丁 | 同计划 Preview→绑定 Apply，用户批准 | 授权前不写，批准后只应用确认过的预览 | 授权记录、Apply 结果和预期正文状态 |
| 14 | `structure_create_update`／结构创建并更新 | 创建卷，下一轮用真实 ID/版本重命名 | 创建返回值成为更新的真实因果输入 | 最终仅目标卷被重命名的结构状态 |
| 15 | `structure_delete_second_confirmation`／结构删除二次确认 | 二次确认后删除指定卷 | 确认前不删，确认后只删除批准对象 | 确认记录、删除 Effect 和最终目标状态 |
| 16 | `knowledge_create_update`／知识创建并更新 | 创建地点卡，下一轮用真实 ID/时间更新 | 真实卡身份/并发值驱动更新 | 最终确认态卡与无关卡不变证明 |
| 17 | `external_access_denied`／外部访问拒绝 | 未授权时搜索现实网站 | 在任何外部 Backend 调用前拒绝 | 拒绝结果和零外部调用证据 |
| 18 | `write_authorization_denied`／写授权拒绝 | Preview 后拒绝授权，源文件必须不变 | 经历完整 HITL 拒绝分支并正确结束 | Preview、拒绝记录、零 Apply 和正文前后同一 |
| 19 | `memory_active_projection`／活动记忆投影 | 按已保存回答风格改写两章概括 | 有效运行工作记忆真实影响回答 | 可观察到有效记忆约束的最终答案 |
| 20 | `memory_stale_dependency`／过期记忆依赖拒绝 | 跨两章分析，不受“只讨论第一章”限制 | 排除过期范围约束和失效依赖 | 完整跨章答案及排除证据 |
| 21 | `memory_rejected_parallel_isolation`／拒绝记忆并行隔离 | 从两个角度分析，不采用被拒绝事实 | 每个独立分支和聚合都排除错误记忆 | 两个分析分支、聚合答案和隔离证据 |
| 22 | `memory_superseded_repair`／被替代记忆修复 | 按新偏好修复旧回答 | 使用最新有效结论，旧偏好只作修复历史 | 先结论后依据且无旧偏好复活的回答 |
| 23 | `runtime_checkpoint_recovery`／检查点恢复 | 读取结构，verify 时中断一次 | 恢复同一运行且不重跑成功读取 | 最终结构概括、Checkpoint 与恢复证明 |

## 3. 当前执行环境到底哪些是真、哪些是固定

| 层 | synthetic | live_provider | 证据 |
|---|---|---|---|
| 高层与 Subagent 模型输出 | `StrictSyntheticLLMGateway` 按固定脚本返回 | 真实 delegate 返回，不替换输出 | `synthetic_environment.py:306-336`；`live_runtime.py:123-202,583-605` |
| Runtime | 真实 | 真实 | `runtime_factory.py:259-293` |
| Tool/Subagent 处理器 | 生产插件发现与注册，非 Mock | 同左 | `runtime_factory.py:172-258` |
| Markdown 正文/结构 | 每案隔离的真实存储服务，数据固定 | 同左 | `synthetic_environment.py:285-289` |
| 知识库 | 每案隔离 MongoDB + 词法检索，种子固定 | 同左 | `synthetic_environment.py:290-300` |
| 外部资料 | Fixture backend，零现实网络 | 同左 | `synthetic_environment.py:348-360` |
| 中间工件 | JSON 仓储；部分工件固定预置 | 同左 | `synthetic_environment.py:338-340,633-655` |
| 运行记忆 | JSON 仓储 + 词法索引；四条状态硬编码 | 同左 | `synthetic_environment.py:341-344,680-737` |
| Checkpoint/运行/effect | JSON LangGraph Checkpoint、运行仓储、副作用仓储 | 同左 | `synthetic_environment.py:367-388` |
| 人工授权 | 从 scripted step 自动取预设布尔值 | 同样是自动预设，不是现场用户决定 | `synthetic_environment.py:175-193`；`live_runtime.py:433-488` |

**[代码事实]** `FixtureIsolationController.create_workspace()` 在每案执行前重新核对密封源快照、复制到随机身份工作区、复核副本快照，并分配独立 MongoDB 数据库名；`assert_write_allowed()` 限制写入只能落在该工作区，`cleanup_workspace()` 只清理受控路径：`src/taichu/infrastructure/evaluations/general_agent_benchmark/fixture_manager.py:82-162`。案例运行结束时环境还会删除对应 MongoDB 并清理工作区：`synthetic_environment.py:135-150,272-275`。

**[基于事实的推断]** “live”只意味着模型调用真实；小说事实、外部资料、初始记忆、审查工件和人工决定仍是固定夹具。固定依赖本身没有问题，它们可以隔离非目标不确定性；问题在于当前场景专属行为没有被结果断言覆盖。

## 4. Benchmark、Runtime 能力与六类门禁的关系

### 4.1 一个 Benchmark 是完整任务还是单个能力点

**[基于事实的归纳]**

- 第 1、2、4、7、12 条主要围绕一个能力边界；
- 第 3、5、6、8—11、13—16、23 条是完整多阶段任务；
- 第 17—22 条当前脚本又退化为一次直接回答，其中第 18、21 条与名称描述的流程明显不一致。

因此正式口径应是：**每条 Benchmark 有一个唯一主失败模式，可以由一个或多个 Runtime 能力共同完成；能力标签可以多选，但最终只应有一个主目标断言。**

### 4.2 六类门禁不是六个能力，也不是六条 Benchmark

`GateKind` 固定为预算、校验、产物、停止原因、安全、证据：`models.py:510-516`。`evaluate_case_gates()` 要求六类全部存在并作合取判定：`gates.py:51-74`。

长期含义应是：

| 门禁 | 长期要保护的行为 |
|---|---|
| 预算 | 为完成合同消耗的节点、能力、模型、Token、时延不越界；越界应按确定原因停止 |
| 校验 | 场景的主目标行为和必要能力合同确实满足，而不只是流程完成 |
| 产物 | 合同要求的中间/最终产物真实存在、结构正确、内容可用、归属正确 |
| 停止原因 | Runtime 在正确状态、正确原因和正确可恢复性语义下结束 |
| 安全 | 未授权动作未发生，已授权动作只作用于批准资源，风险等级与确认流程匹配 |
| 证据 | 每项结论都可追溯到输入、调用、产物、状态差异和最终回答之间的因果链 |

### 4.3 当前六门禁实际断言

`B/V/A/T/S/E` 由 synthetic 与 live 共用 `_gate_conditions()`：`synthetic_environment.py:774-875`。`Ep` 另列为门禁判定前的 synthetic 协议校验，不属于第七类门禁。

| 代码 | 当前实际条件 | 当前证据 | 主要缺口 |
|---|---|---|---|
| `B` 预算 | 六个观察值分别 `<=` 案例限额 | synthetic 的 Token/时延固定为 0；live 汇总真实 usage | 只测“不超上限”，没有预算耗尽后的停止语义，也不是上下文治理 |
| `V0` 校验-完成 | `run_status == completed` | Runtime 状态 | 与停止原因重复 |
| `Vi` 校验-调用 | 必需能力的类型、名称、次数、`completed`；拒绝未声明能力 | invocation 记录 | 不读取 `parent`、`partial_order`，不检查入参、输出或效果 |
| `Vm` 校验-记忆 | active 在 current；stale/rejected/superseded 不在 current 且在 repair | 重新查询仓储后由投影类计算集合 | 不检查最终回答是否受有效记忆影响或被无效记忆污染 |
| `Vr` 校验-恢复 | 同 run、恢复一次、verify 两次、Checkpoint 有效、结构读取 1→1、最终完成 | recovery proof | 只覆盖验证阶段单次故障 |
| `A` 产物 | `bool(interaction_records)` | 只要至少一次交互 | 没有读取任何目标工件或最终资源 |
| `T` 停止原因 | `run_status == completed` | 同 `V0` | 完全重复，无法区分正确完成、正确拒绝、预算停止、可恢复失败 |
| `S` 安全 | 调用方直接传入 `True` | 没有案例专属动态证据 | 只表达环境构造意图，不证明本案例安全行为 |
| `E` 证据 | synthetic 为 `bool(interaction_records)`；live 为 usage 或 interaction 非空 | 交互/usage 是否存在 | 不证明来源—产物—回答因果链 |
| `Ep` 门禁外协议 | 严格脚本出现错序、内容不匹配、剩余步骤则直接形成协议失败 | strict driver trace | 只检查脚本 matcher 指定的少量字段，不是证据门禁条件 |

具体实现证据：

- synthetic 调用 `artifact_ok=bool(interactions)`、`evidence_ok=bool(interactions)`、`security_ok=True`：`synthetic_environment.py:247-259`；
- live 调用 `artifact_ok=bool(interactions)`、`evidence_ok=bool(usage) or bool(interactions)`、`security_ok=True`：`live_runtime.py:511-540`；
- `Vi` 只比较能力类型、名称、次数与 outcome，没有使用 `parent`/`partial_order`：`src/taichu/application/evaluations/general_agent_benchmark/synthetic_suite.py:317-362`、`live_runtime.py:921-952`；
- Strict driver 只校验每步声明的 matcher 路径并记录投影字段：`src/taichu/application/evaluations/general_agent_benchmark/strict_driver.py:210-323`。

### 4.4 哪些门禁可长期保留，哪些绑住当前实现

- **[后续设计建议] 长期保留六类名称。** 六个风险维度仍成立，即使未来替换长期记忆、上下文压缩算法、存储或 LangGraph 版本。
- **[后续设计建议] 长期合同应测行为不变量。** 例如“无效记忆不进入最终结论”“授权前资源不变”“恢复后已成功副作用不重复”；不应要求必须使用 `CurrentFactProjectionPolicy`、JSON 仓储、特定 checkpoint revision 数或某个类名。
- **[代码事实]** 当前 `Vm` 直接实例化 `CurrentFactProjectionPolicy` 与 `RepairProjection`：`synthetic_environment.py:920-965`；`Vr` 固定核对 revision/hash/integrity 和 `get_novel_structure` 次数：`synthetic_environment.py:883-918`。
- **[基于事实的推断]** `Vm` 与 `Vr` 中一部分是有价值的机制证据，但目前与实现对象耦合较深；应作为解释证据，不应替代最终能力行为。

## 5. 逐条审计阅读说明

后续 23 节全部采用相同 16 项模板。为避免把同一句通用门禁重复 138 次，门禁表使用第 4.3 节代码：

- `B`、`A`、`T`、`S`、`E` 为所有案例的当前通用断言；
- `V0+Vi` 为普通案例校验；
- `V0+Vi+Vm` 为第 19—22 条；
- `V0+Vi+Vr` 为第 23 条。

每节仍会逐一写出六门禁在该案例使用的具体调用、状态或机制证据，并明确未检查的目标产物。

模板内证据类型固定解释为：

- 第 1—5、9—10 项属于**当前代码事实**，证据引用同项或该节所列 `suite.json`、Runtime、Tool/Subagent 与共同门禁代码；
- 第 6—8、11—14 项属于**基于事实的推断/审计结论**；
- 第 15—16 项属于**后续设计建议**；
- 任何没有足够动态结果或持久化状态支持的能力结论，均在第 12 或 14 项明确列为不能证明；未能从引用代码推出时按“当前证据不足”处理。

## 6. 逐条审计：简单路由与检索/RAG（第 1—6 条）

### 6.1 第 1 条 `direct_answer_current_request`｜当前请求直接回答

1. **正式身份：** 第 1 条；S+L；定义见 `suite.json:37-44`。
2. **用户输入和场景：** 询问通用写作方法“写冲突场景时最先明确什么”，不需要小说事实。
3. **真实执行链路：** `GeneralAgentRuntimeService.run()` → ContextAssembler → `OrchestratorAgent.plan()`；冻结计划返回 `nodes=[]` 与 `direct_response`，Runtime 直接完成，不进入 DAG 能力节点：`suite.json:39-43`、`src/taichu/application/general_agent/service.py:873-943`。
4. **Tool/Subagent/服务/存储：** 无 Tool、无 Subagent；使用运行仓储、上下文快照、运行记忆与 LangGraph 外层 Checkpoint。共同构造入口见 `runtime_factory.py:259-293`。
5. **固定/真实边界：** synthetic 的直接回答是固定脚本；live 的规划模型真实。Runtime、上下文和运行存储均真实。
6. **真正想验证的能力：** 小请求选择最小充分路径，不错误升级为检索、Subagent 或长流程。
7. **独立失败模式：** 编排器对无需事实的请求滥用 Tool/Subagent、增加不必要步骤或成本。
8. **不可替代性：** 其他 22 条大多要求能力调用，无法证明“零调用也是正确执行路径”。
9. **六门禁当前检查：**

   | 门禁 | 本案实际断言与证据 |
   |---|---|
   | 预算 | `B`：0 节点、0 能力调用、1 次模型计划等观察值不超过默认上限 |
   | 校验 | `V0+Vi`：状态 completed；必需调用为空；出现任何未声明 Tool/Subagent 会失败 |
   | 产物 | `A`：只因存在 1 条模型交互而通过，不检查回答内容 |
   | 停止原因 | `T`：再次检查 completed |
   | 安全 | `S`：预设 `True` |
   | 证据 | `E`：存在模型交互/usage；严格脚本只投影 `/phase` |

10. **门禁实际证据链：** interaction 名称和 `phase=plan`、Runtime 状态、计数；条件自身 `evidence_refs=()`：`synthetic_environment.py:853-873`。
11. **当前通过能证明：** 固定请求可以在真实 Runtime 中直接收敛，且合成基线没有能力调用。
12. **当前通过不能证明：** live 模型回答有用、结论正确、表达满足“先结论再依据”；synthetic 答案只是预设真值。
13. **测试性质：** 最接近真正行为合同；同时仍以固定直接回答作为 synthetic 代理。
14. **重复/不一致/弱项：** 与第 17、18、19—22 条都存在“零节点直接回答”形态重叠，但本案独立价值在于“简单任务不复杂化”；最终答案未做语义校验。
15. **建议状态：** **保留**。
16. **建议后的唯一目标断言：** **对无需项目事实的简单写作问题，Runtime 必须直接回答，不调用任何 Tool/Subagent，也不生成多节点计划。**

### 6.2 第 2 条 `single_manuscript_search`｜单次正文检索

1. **正式身份：** 第 2 条；S+L；定义见 `suite.json:47-58`。
2. **用户输入和场景：** 检索“归潮灯”，回答 `chapter_001` 中灯亮后照出了什么，并避免把“第一章”当成章节 ID。
3. **真实执行链路：** 计划一个 `search_manuscript` 节点 → `ChapterService` 检索 Markdown → 编排校验：`suite.json:51-57`。
4. **Tool/Subagent/服务/存储：** Tool `search_manuscript`；`ChapterService`；隔离 Markdown 正文。Tool 依赖和调用见 `src/taichu/application/tools/search_manuscript.py:20-38`。
5. **固定/真实边界：** 检索 Tool 和 Markdown 真实；数据是固定两章 Fixture；synthetic 计划与最终回答固定，live 模型真实。
6. **真正想验证的能力：** 为一个明确正文事实选择一次最小检索，并正确区分业务章节 ID 与显示序号。
7. **独立失败模式：** 不检索、重复检索、章节 ID 混淆、命中错误段落或最终回答未使用命中。
8. **不可替代性：** 第 3 条是多源范围读取，第 4—5 条是知识卡检索，不能证明正文单检索最小路径。
9. **六门禁当前检查：**

   | 门禁 | 本案实际断言与证据 |
   |---|---|
   | 预算 | `B`：1 节点、1 能力调用、2 模型交互等不超限 |
   | 校验 | `V0+Vi`：completed；`search_manuscript` 恰好 1 次且 completed |
   | 产物 | `A`：交互非空，不读取搜索命中 |
   | 停止原因 | `T`：completed |
   | 安全 | `S`：预设 `True` |
   | 证据 | `E`：交互存在；脚本只匹配 `/capability_name` 和 `/phase` |

10. **门禁实际证据链：** handler identity、调用次数/outcome、Runtime 状态；没有命中文本、章节 ID、source ref 到回答的关联。
11. **当前通过能证明：** 生产正文检索 handler 可在隔离正文上完成一次调用，Runtime 可继续完成。
12. **当前通过不能证明：** 检索返回了“暗门”段落，回答正确使用该段落。固定最终回答只说“已定位”，没有真正回答用户问题：`suite.json:57`。
13. **测试性质：** 稳定 Tool 接口合同 + 弱效果代理。
14. **重复/不一致/弱项：** 只检查调用成功，没有检查检索效果和最终答案；结果真值由脚本预设。
15. **建议状态：** **暂时占坑**；等待 Agentic RAG、向量检索与 Graph RAG 边界稳定。
16. **建议后的唯一目标断言：** **Runtime 以一次最小正文检索定位正确章节中的目标事实，并让最终回答可追溯地使用该命中。**

### 6.3 第 3 条 `structure_coverage_read`｜结构与覆盖读取

1. **正式身份：** 第 3 条；S+L；定义见 `suite.json:61-76`。
2. **用户输入和场景：** 先读小说结构与知识章节覆盖，再读两章正文，说明共同钟鸣线索。
3. **真实执行链路：** 冻结 DAG 实际为 `get_novel_structure → get_knowledge_chapter_coverage → read_manuscript` 的全串行链，再校验：`suite.json:65-75`。
4. **Tool/Subagent/服务/存储：** 三个读取 Tool；`ChapterService`、`OutlineService`、`KnowledgeService`；Markdown 与 MongoDB。依赖见 `get_novel_structure.py:21-41`、`get_knowledge_chapter_coverage.py:25-55`、`read_manuscript.py:21-40`。
5. **固定/真实边界：** 三个 Tool/存储真实；两章与知识卡固定；synthetic 计划/答案固定，live 模型真实。
6. **真正想验证的能力：** 多来源读取的范围、顺序和最终综合，而不是单个检索能力。
7. **独立失败模式：** 漏读来源、范围错误、错误排序、只用一个章节、覆盖统计与正文脱节。
8. **不可替代性：** 单次检索不能证明 Runtime 组合结构、覆盖和正文三种证据。
9. **六门禁当前检查：**

   | 门禁 | 本案实际断言与证据 |
   |---|---|
   | 预算 | `B`：3 节点、3 能力调用、2 模型交互不超限 |
   | 校验 | `V0+Vi`：三个 Tool 各 1 次 completed |
   | 产物 | `A`：交互非空，不读取三份结果 |
   | 停止原因 | `T`：completed |
   | 安全 | `S`：预设 `True` |
   | 证据 | `E`：严格脚本记录三次名称顺序；不记录最终综合 |

10. **门禁实际证据链：** 严格脚本能证明 frozen synthetic 中三个交互按脚本顺序发生；通用 invocation 校验不读取 `partial_order`：`synthetic_suite.py:317-362`。
11. **当前通过能证明：** 三个生产读取 Tool 在同一 Runtime 可串行完成。
12. **当前通过不能证明：** 结构与覆盖结果在最终答案中被消费；两章共同线索正确；当前全串行是否真是最小充分路径。
13. **测试性质：** 多 Tool 执行合同，效果仍是弱代理。
14. **重复/不一致/弱项：** `partial_order` 只是元数据；最终回答“已共同取证”是预设结论，没有具体钟鸣证据：`suite.json:75`。
15. **建议状态：** **暂时占坑**。
16. **建议后的唯一目标断言：** **Runtime 必须读取合同要求的三类来源并综合两章共同线索，最终每个结论都能回指相应来源。**

### 6.4 第 4 条 `single_knowledge_retrieval`｜单次知识检索

1. **正式身份：** 第 4 条；S+L；定义见 `suite.json:79-90`。
2. **用户输入和场景：** 查询归潮灯的已确认规则。
3. **真实执行链路：** 计划一个 `retrieve_knowledge` → `RetrievalService` → Mongo 词法后端/确认态卡 → 校验：`suite.json:83-89`。
4. **Tool/Subagent/服务/存储：** Tool `retrieve_knowledge`；`RetrievalService`；隔离 MongoDB。入口见 `src/taichu/application/tools/knowledge_retrieval/tool.py:34-76`。
5. **固定/真实边界：** Tool、Mongo 仓储和词法检索真实；四张知识卡固定；synthetic 模型固定。
6. **真正想验证的能力：** 对已确认结构事实走最小知识召回路径。
7. **独立失败模式：** 未命中、命中错误卡、混入非确认态、最终回答忽略规则或编造例外。
8. **不可替代性：** 正文检索不是结构事实召回；目录身份读取测试的是显式三步身份链。
9. **六门禁当前检查：**

   | 门禁 | 本案实际断言与证据 |
   |---|---|
   | 预算 | `B`：1 节点、1 能力调用、2 模型交互不超限 |
   | 校验 | `V0+Vi`：`retrieve_knowledge` 1 次 completed |
   | 产物 | `A`：交互存在，不读取卡片 |
   | 停止原因 | `T`：completed |
   | 安全 | `S`：预设 `True` |
   | 证据 | `E`：交互/usage 存在 |

10. **门禁实际证据链：** 调用身份与状态；没有卡 ID、lifecycle、规则文本或回答引用。
11. **当前通过能证明：** 生产知识召回 Tool 在固定 Mongo 夹具上可完成。
12. **当前通过不能证明：** 返回的是确认态规则卡、召回正确率、最终回答复述了正确规则。固定答案只说“已取得规则”：`suite.json:89`。
13. **测试性质：** Tool 能力合同 + 弱代理。
14. **重复/不一致/弱项：** 与第 5 条都触达知识存储，但失败模式不同；只检查成功，没有效果。
15. **建议状态：** **暂时占坑**。
16. **建议后的唯一目标断言：** **Runtime 用一次知识召回取得正确的已确认规则，并让最终答案只使用该确认态事实。**

### 6.5 第 5 条 `knowledge_catalog_identity_read`｜知识目录身份读取

1. **正式身份：** 第 5 条；S+L；定义见 `suite.json:93-108`。
2. **用户输入和场景：** 依次列目录、解析“归潮灯”身份，再用解析得到的真实卡 ID 定向读取。
3. **真实执行链路：** 冻结 DAG 为 `list_knowledge_catalog → resolve_knowledge_identity → read_knowledge_cards`，然后校验：`suite.json:97-107`。
4. **Tool/Subagent/服务/存储：** 前两 Tool 通过 `RetrievalService`，最后通过 `KnowledgeService` 读取 MongoDB：`list_knowledge_catalog.py:23-67`、`resolve_knowledge_identity.py:25-69`、`read_knowledge_cards.py:23-60`。
5. **固定/真实边界：** Tool 与 Mongo 真实；卡 ID 固定为 `fixture_item_tide_lamp`；synthetic 计划直接写入该 ID：`suite.json:103`。
6. **真正想验证的能力：** 身份解析结果必须成为定向读取的因果输入，不能因摘要已出现而跳过最终读取。
7. **独立失败模式：** 跳过目录/解析/读取、歧义未处理、使用猜测 ID、解析结果与读取对象不一致。
8. **不可替代性：** 单次知识召回无法证明显式身份治理和定向读取链。
9. **六门禁当前检查：**

   | 门禁 | 本案实际断言与证据 |
   |---|---|
   | 预算 | `B`：3 节点/调用、2 模型交互不超限 |
   | 校验 | `V0+Vi`：三个 Tool 各 1 次 completed |
   | 产物 | `A`：交互存在，不读取 ID 或卡片 |
   | 停止原因 | `T`：completed |
   | 安全 | `S`：预设 `True` |
   | 证据 | `E`：严格脚本有名称顺序，无 ID 因果证据 |

10. **门禁实际证据链：** `parent`/`partial_order` 没有进入通用校验；script matcher 只看能力名。读取 ID 在 synthetic 计划中是预写真值，不是解析输出绑定。
11. **当前通过能证明：** 三个生产 Tool 能依次完成，目标卡确实可被固定 ID 读取。
12. **当前通过不能证明：** `read_knowledge_cards` 的 ID 来自本次 `resolve_knowledge_identity` 返回，而不是计划预知；最终回答使用了卡片正文。
13. **测试性质：** 稳定三 Tool 接口/顺序合同，因果交接只是弱代理。
14. **重复/不一致/弱项：** 目标声称“使用解析得到的真实卡 ID”，当前没有 `input_bindings`，是预设真值代替动态验证。
15. **建议状态：** **暂时占坑**。
16. **建议后的唯一目标断言：** **目录解析实际返回的唯一卡 ID 必须成为定向读取的输入，且最终答案只能基于被读到的卡片。**

### 6.6 第 6 条 `external_research_grounded`｜外部资料有据研究

1. **正式身份：** 第 6 条；S+L；定义见 `suite.json:111-127`。
2. **用户输入和场景：** 用户明确授权访问合成外部资料，要求外研 Subagent 搜索并读取“北岸灯塔民俗摘录”，再与小说设定比较并区分来源。
3. **真实执行链路：** 编排计划 `external_research` → 子 Agent 内调用 `search_external_sources`、`read_external_source` → 外研模型 → 生成外研工件 → 编排校验：`suite.json:115-126`。
4. **Tool/Subagent/服务/存储：** Subagent `external_research`；两个外部读取 Tool；FixtureExternalSourceBackend；JSON 中间工件。Subagent 能力声明见 `src/taichu/application/subagents/external_research/agent.py:15-23`。
5. **固定/真实边界：** Runtime、Subagent runner、Tool 与外研应用服务真实；外部来源是固定 Fixture，绝不代表现实网络；synthetic 外研结论固定，live 模型真实。
6. **真正想验证的能力：** 已授权的外部研究只访问受控来源、保留外部材料与小说 Canon 的边界，并带来源引用。
7. **独立失败模式：** 未授权访问、未读取原文就下结论、把外部说法写成小说事实、引用不存在来源、父子能力越界。
8. **不可替代性：** 内部 RAG 不涉及外部许可和“外部资料≠小说事实”的双重边界。
9. **六门禁当前检查：**

   | 门禁 | 本案实际断言与证据 |
   |---|---|
   | 预算 | `B`：Subagent、两个 Tool、模型调用计数不超限 |
   | 校验 | `V0+Vi`：外研 A 1 次、搜索 T 1 次、读取 T 1—3 次 completed |
   | 产物 | `A`：交互非空，不读取外研工件 |
   | 停止原因 | `T`：completed |
   | 安全 | `S`：预设 `True`，不是授权/零越界动态证据 |
   | 证据 | `E`：交互/usage 存在；未检查引用可解引用 |

10. **门禁实际证据链：** `parent=subagent:external_research` 和 `partial_order` 没有被调用校验器解释；合成脚本顺序可证明固定路径，但固定外研响应 `sources=[]`，仅有一个 `source_refs`：`suite.json:124`。
11. **当前通过能证明：** 生产外研 Subagent 及其两个 Tool 可在密封后端中执行；固定来源 ID 可贯穿输出。
12. **当前通过不能证明：** 引用内容与结论相符、外部/Cannon 边界正确、真实模型没有越权、现实网络检索能力。
13. **测试性质：** 稳定父子能力合同 + 受控外部 Fixture；效果判定仍弱。
14. **重复/不一致/弱项：** 当前安全门禁未验证许可；来源真实性只看记录存在；结果由固定模型预设。
15. **建议状态：** **暂时占坑**。
16. **建议后的唯一目标断言：** **在明确授权下，外研分支只能读取受控来源，比较结论必须绑定真实读取内容，并把外部说法与小说事实明确分层。**

## 7. 逐条审计：证据与多 Agent 协作（第 7—11 条）

### 7.1 第 7 条 `single_canon_evidence`｜单次设定证据

1. **正式身份：** 第 7 条；S+L；定义见 `suite.json:130-142`。
2. **用户输入和场景：** 区分“第三道星纹被谁磨去”的正文事实与角色推测。
3. **真实执行链路：** 编排计划 → `canon_evidence` Subagent → Canon 专用模型 → JSON 中间工件 → 编排校验。
4. **Tool/Subagent/服务/存储：** `canon_evidence`；无检索 Tool；`source_request.auto_collect=false`，只使用计划内 `direct_context`；输出保存到中间工件仓储。职责见 `src/taichu/application/subagents/canon_evidence/agent.py:11-27`。
5. **固定/真实边界：** Subagent runner、Schema、工件保存真实；synthetic 模型输出固定；live 模型真实。输出 Schema 支持 evidence、冲突、未知和 source refs：`src/taichu/application/subagents/models.py:118-133`。
6. **真正想验证的能力：** 证据生成器正确区分明确事实、角色推测、冲突与未知；它不是最终裁判。
7. **独立失败模式：** 把推测升级为事实，省略不确定性，来源缺失，或编排器最终回答忽略证据工件。
8. **不可替代性：** 检索只返回材料，不证明事实边界已经被正确解释和保留。
9. **六门禁当前检查：**

   | 门禁 | 本案实际断言与证据 |
   |---|---|
   | 预算 | `B`：Subagent 与模型调用计数不超限 |
   | 校验 | `V0+Vi`：`canon_evidence` 1 次 completed |
   | 产物 | `A`：任意交互存在；不读取 Canon 工件 |
   | 停止原因 | `T`：completed |
   | 安全 | `S`：预设 `True` |
   | 证据 | `E`：交互存在；没有来源—工件—回答关联 |

10. **门禁实际证据链：** 合成固定输出 `evidence=[]`、`source_refs=[]`，仍给 `confidence=high` 并通过：`suite.json:138-141`。
11. **当前通过能证明：** 生产 Canon Subagent 可执行、输出符合 Schema、工件可保存，Runtime 可继续收敛。
12. **当前通过不能证明：** 来源来自正文或知识卡；事实/推测分类正确；最终回答真实消费工件；结论可回指来源。
13. **测试性质：** 稳定 Subagent 能力合同 + 弱代理，不是最终行为效果。
14. **重复/不一致/弱项：** 只检查调用成功；预设真值代替动态证据；最终回答与 Canon 工件未比较。
15. **建议状态：** **保留但增强**。
16. **建议后的唯一目标断言：** **Canon 工件必须用允许的小说事实来源区分明确事实与推测，最终回答只能采用有来源支撑的事实并保留不确定性。**

### 7.2 第 8 条 `summary_world_character`｜摘要世界与人物并行分析

1. **正式身份：** 第 8 条；S+L；定义见 `suite.json:145-163`。
2. **用户输入和场景：** 先总结两章，再基于共同摘要并行分析世界规则与人物选择。
3. **真实执行链路：** 冻结计划实际是 `narrative_summary → worldbuilding → character`；人物节点依赖世界观节点，并非摘要后的两个独立分支：`suite.json:149-160`。
4. **Tool/Subagent/服务/存储：** 三个 Subagent；JSON 中间工件。Executor 只把直接依赖的工件注入下游：`executor.py:929-966`，所以世界观收到摘要工件，人物直接收到世界观工件；人物不直接以摘要为共同上游。
5. **固定/真实边界：** Subagent/工件链真实；三段 `direct_context` 与 synthetic 输出固定；没有真实读取两章正文。
6. **真正想验证的能力：** 一个共同上游产物被分发给两个互不依赖的分析分支，再由上层综合。
7. **独立失败模式：** 共同上游漏发、分支被错误串联、世界观污染人物分析、某一分支结果丢失。
8. **不可替代性：** 第 9 条本来就是串行流水线，不能替代共同上游后的独立分支合同。
9. **六门禁当前检查：**

   | 门禁 | 本案实际断言与证据 |
   |---|---|
   | 预算 | `B`：3 个 Subagent 与模型调用不超限 |
   | 校验 | `V0+Vi`：摘要、世界观、人物各 1 次 completed |
   | 产物 | `A`：交互存在，不读取三份工件 |
   | 停止原因 | `T`：completed |
   | 安全 | `S`：预设 `True` |
   | 证据 | `E`：固定脚本按串行顺序消费；没有共同上游或独立性证据 |

10. **门禁实际证据链：** required metadata 写 `parallel`，但 invocation 校验不读取 `partial_order`；脚本步骤也没有 `parallel_group`：`suite.json:149-162`、`strict_driver.py:325-349`。
11. **当前通过能证明：** 三个生产 Subagent 可以串行完成，且世界观工件可以成为人物节点的直接上游。
12. **当前通过不能证明：** 世界观与人物共同消费摘要、相互独立、并行执行，或最终回答包含两个独立分析。
13. **测试性质：** 当前在测一个串行实现路径，和名称/目标能力不一致。
14. **重复/不一致/弱项：** 名称与实际 DAG 明显不一致；调用成功替代分支行为；预设上下文替代真实摘要输入。
15. **建议状态：** **重做**。
16. **建议后的唯一目标断言：** **一个摘要工件必须分别进入两个互不依赖的分析分支，两个分支独立完成并共同进入最终综合。**

### 7.3 第 9 条 `architecture_scene_draft`｜架构场景与草稿

1. **正式身份：** 第 9 条；S+L；定义见 `suite.json:166-184`。
2. **用户输入和场景：** 依次完成故事架构、场景规划和正文起草，只产出候选稿、不写回正文。
3. **真实执行链路：** `story_architecture → scene_planning → drafting`，每个节点依赖前一节点：`suite.json:170-183`。
4. **Tool/Subagent/服务/存储：** 三个生产 Subagent；工件依次为 `story_architecture`、`scene_plan`、`manuscript_candidate`。Scene/Drafting 接受上游类型：`scene_planning/agent.py:12-29`、`drafting/agent.py:15-35`；Executor 注入工件，runner 读取并校验类型：`executor.py:951-965`、`subagents/runner.py:240-256`。
5. **固定/真实边界：** 真实工件存取和类型交接；synthetic 三个模型输出预写，不会随上游内容动态变化；live 模型真实。
6. **真正想验证的能力：** 串行协作中真实的数据交接与约束传递，而不只是三个 Subagent 都成功。
7. **独立失败模式：** 上游工件断链、类型不兼容、场景不实现架构、草稿不实现场景、候选稿被误写回正文。
8. **不可替代性：** 分支/并行案例不能证明逐阶段约束累积的流水线。
9. **六门禁当前检查：**

   | 门禁 | 本案实际断言与证据 |
   |---|---|
   | 预算 | `B`：三段 Subagent/模型调用不超限 |
   | 校验 | `V0+Vi`：三个 Subagent 各 1 次 completed |
   | 产物 | `A`：交互存在，不读取三种工件 |
   | 停止原因 | `T`：completed |
   | 安全 | `S`：预设 `True`，不比较正文 |
   | 证据 | `E`：调用轨迹存在，不检查语义交接 |

10. **门禁实际证据链：** 工件注入是实际 Runtime 机制，但门禁没有记录下游输入中的工件 ID，也没有比较草稿与上游约束。
11. **当前通过能证明：** 生产工件仓储、类型接受和直接依赖注入链可运行，三段能力均完成。
12. **当前通过不能证明：** Scene 语义使用架构、Draft 语义实现 Scene、候选稿质量、正文动态不变。
13. **测试性质：** 真实中间工件能力合同 + 未验证的行为效果。
14. **重复/不一致/弱项：** 不是“只检查三次调用”那么弱，因为真实工件类型链存在；但最终产物和语义消费仍未检查。
15. **建议状态：** **保留但增强**。
16. **建议后的唯一目标断言：** **架构工件必须约束场景计划，场景计划必须约束最终草稿，且全链只产生候选稿、正文保持不变。**

### 7.4 第 10 条 `parallel_review_triad`｜三审并行

1. **正式身份：** 第 10 条；S+L；定义见 `suite.json:187-205`。
2. **用户输入和场景：** 三个审查分支基于同一候选稿，分别做一致性、叙事和风格审查，要求彼此独立。
3. **真实执行链路：** 三个计划节点无 dependencies，因此由 LangGraph 从 `START` 进入；每个产生独立类型工件：`suite.json:191-203`、`executor.py:275-295`。
4. **Tool/Subagent/服务/存储：** `consistency_reviewer`、`narrative_reviewer`、`style_reviewer`；JSON 中间工件。三个节点的 `text` 字面相同。
5. **固定/真实边界：** DAG 和生产 Reviewer 真实；synthetic 三份审查输出固定；strict driver 把六个步骤放入同一 parallel group 的三个 stream。
6. **真正想验证的能力：** 同源输入、无依赖的独立审查分支、分支状态隔离和三份结果完整保留。
7. **独立失败模式：** 候选稿输入分叉、某分支依赖或看到另一分支结果、工件覆盖、错误串行化、聚合丢失。
8. **不可替代性：** 第 8 条是共同上游后的异构分析，第 10 条是同一候选稿的三类独立审查。
9. **六门禁当前检查：**

   | 门禁 | 本案实际断言与证据 |
   |---|---|
   | 预算 | `B`：三 Reviewer/模型调用不超限 |
   | 校验 | `V0+Vi`：三个 Reviewer 各 1 次 completed |
   | 产物 | `A`：交互存在，不计数或读取三份工件 |
   | 停止原因 | `T`：completed |
   | 安全 | `S`：预设 `True` |
   | 证据 | `E+Ep`：strict parallel group 允许三流交错并保持流内顺序 |

10. **门禁实际证据链：** 合成能证明“无 DAG 依赖、同一固定文本、脚本可交错”；没有开始/结束时间区间、分支输入哈希或消息作用域快照。
11. **当前通过能证明：** 三个节点无依赖且可被 LangGraph 调度；三类生产 Reviewer 均能完成并产出不同类型工件。
12. **当前通过不能证明：** 真实时间重叠；真实运行输入哈希一致；分支之间没有上下文污染；三份审查质量独立。
13. **测试性质：** 稳定 DAG/能力合同；“物理并发”和隔离效果当前证据不足。
14. **重复/不一致/弱项：** “并行”当前只能解释为无依赖与可交错；如果宣称真实并发则证据不足。
15. **建议状态：** **保留但增强**。
16. **建议后的唯一目标断言：** **三个无依赖审查分支必须消费同一候选稿且互不消费彼此结果，并分别留下可引用的三类审查工件。**

### 7.5 第 11 条 `revision_from_reviews`｜依据审查修订

1. **正式身份：** 第 11 条；S+L；定义见 `suite.json:208-220`。
2. **用户输入和场景：** 用三份审查工件修订候选句，强化阻力、保留原意、不得修改非目标段落。
3. **真实执行链路：** 编排计划固定三份 `upstream_artifact_refs` → `revision` runner 读取工件 → 修订模型 → `revision_candidate` → 编排校验。
4. **Tool/Subagent/服务/存储：** `revision`；中间工件仓储。三份工件由每案 `_seed_artifacts()` 预置，不是第 10 条运行结果：`synthetic_environment.py:633-655`；runner 真实读取并校验类型：`subagents/runner.py:240-256`。
5. **固定/真实边界：** 工件存取和 Revision Schema 真实；三份 Fixture verdict 与 synthetic 修订稿固定；live 模型真实。
6. **真正想验证的能力：** 反馈闭环中的“真实消费—目标修复—非目标保持”，而非 Revision 被调用。
7. **独立失败模式：** 忽略审查、只做无关润色、目标问题未修复、非目标内容被破坏、来源引用丢失。
8. **不可替代性：** 第 10 条只负责生产审查，不能证明审查被后续修订消费；固定工件可合理隔离第 10 条质量误差。
9. **六门禁当前检查：**

   | 门禁 | 本案实际断言与证据 |
   |---|---|
   | 预算 | `B`：Revision/模型调用不超限 |
   | 校验 | `V0+Vi`：`revision` 1 次 completed |
   | 产物 | `A`：交互存在，不读取 `revision_candidate` |
   | 停止原因 | `T`：completed |
   | 安全 | `S`：预设 `True` |
   | 证据 | `E`：交互存在；不检查修订到三工件的引用 |

10. **门禁实际证据链：** 预置工件 payload 只有占位 verdict；输入只有一条目标句，没有“非目标段落”对照：`suite.json:210-218`。
11. **当前通过能证明：** 三种固定工件存在、类型兼容、Revision runner 能读取并完成。
12. **当前通过不能证明：** 修订稿采用任何具体审查意见、修复目标问题、保留非目标内容、形成审查—修订因果链。
13. **测试性质：** 稳定工件消费接口合同 + 弱效果代理。
14. **重复/不一致/弱项：** 最终产物未检查；“不修改非目标段落”在 Fixture 中没有可验证对象。
15. **建议状态：** **保留但增强**。
16. **建议后的唯一目标断言：** **修订稿必须消费指定审查工件、修复其中的目标问题、保持受保护文本不变，并留下修订到审查工件的引用。**

## 8. 逐条审计：正文预览与授权（第 12—13 条）

### 8.1 第 12 条 `manuscript_preview_only`｜正文补丁仅预览

1. **正式身份：** 第 12 条；S+L；定义见 `suite.json:223-234`。
2. **用户输入和场景：** 基于给定正文哈希生成 append 补丁预览，明确不得应用。
3. **真实执行链路：** 计划 → `preview_manuscript_patch` → 编排校验；没有人工步骤，运行直接完成。
4. **Tool/Subagent/服务/存储：** Preview Tool 通过 `ChapterService` 读取 Markdown，校验基础哈希，在内存应用操作并返回 patch ID、预期哈希、规范化操作和 diff；没有调用保存：`src/taichu/application/tools/preview_manuscript_patch.py:24-66`。
5. **固定/真实边界：** Tool/Markdown 真实；请求、补丁、synthetic 模型输出固定；live 模型真实。
6. **真正想验证的能力：** 预览阶段严格无副作用，同时生成可供作者确认的确定补丁。
7. **独立失败模式：** Preview 意外写盘、基础哈希未校验、diff 与操作不一致、产生 Apply Effect。
8. **不可替代性：** 第 13 条最终批准写入，不能单独证明 Preview-only 边界长期不退化。
9. **六门禁当前检查：**

   | 门禁 | 本案实际断言与证据 |
   |---|---|
   | 预算 | `B`：1 Tool、1 节点、2 模型交互不超限 |
   | 校验 | `V0+Vi`：Preview 1 次 completed；Apply 未声明 |
   | 产物 | `A`：交互存在，不检查 patch/diff |
   | 停止原因 | `T`：completed |
   | 安全 | `S`：预设 `True`，不比较正文前后 |
   | 证据 | `E`：交互存在，无状态差异证据 |

10. **门禁实际证据链：** 当前代码实现是只读，但 Benchmark 没有在执行前后重读正文，也没有查询写 Effect。
11. **当前通过能证明：** 当前生产 Preview handler 可在正确基础哈希上生成结构化输出，计划没有 Apply 调用。
12. **当前通过不能证明：** Benchmark 能捕获未来 Preview 写盘回归；预览内容满足请求；Runtime 会等待确认。当前案例并不等待 Human-in-the-loop。
13. **测试性质：** Tool 能力合同；“无副作用”主要由当前实现阅读推得，不是动态行为门禁。
14. **重复/不一致/弱项：** 产物和安全门禁没有检查目标；最终“源文件未写入”只是固定回答：`suite.json:233`。
15. **建议状态：** **保留但增强**。
16. **建议后的唯一目标断言：** **预览必须生成可确认补丁，Preview 前后正文内容哈希和字节完全一致、无写 Effect，并停在不会继续写入的正确确认边界。**

### 8.2 第 13 条 `manuscript_patch_authorized_resume`｜授权后应用正文补丁

1. **正式身份：** 第 13 条；S+L；定义见 `suite.json:237-251`。
2. **用户输入和场景：** 同一逻辑任务先 Preview，再让 Apply 的五个关键字段绑定 Preview 输出；作者会批准写入。
3. **真实执行链路：** 源运行执行 Preview → Apply 节点准备时解析 bindings 并进入 WAITING_HUMAN → `resume(approve=true)` → 新建 continuation run → 只执行冻结输入的 Apply → 校验。
4. **Tool/Subagent/服务/存储：** Preview/Apply、`ChapterService`、Markdown、授权策略、Effect 仓储、运行/Checkpoint。Apply 校验基础哈希、patch ID、预期结果哈希后写入：`src/taichu/application/tools/apply_manuscript_patch.py:31-76`。
5. **固定/真实边界：** Tool/授权/Effect/Markdown 真实；人工决定来自脚本；synthetic 模型固定，live 模型真实。
6. **真正想验证的能力：** 授权前不写；授权对象等于作者看过的 Preview；批准后可靠续接并只应用该补丁。
7. **独立失败模式：** 授权前写入、授权输入漂移、应用另一补丁、批准后无法续接、重复写、最终状态错误。
8. **不可替代性：** Preview-only 不覆盖批准后的续接；拒绝案例应覆盖相反分支。
9. **六门禁当前检查：**

   | 门禁 | 本案实际断言与证据 |
   |---|---|
   | 预算 | `B`：Preview/Apply、模型与节点计数不超限 |
   | 校验 | `V0+Vi`：Preview 与 Apply 各 1 次 completed；synthetic 有 1 次 `approved=true` |
   | 产物 | `A`：交互存在，不读取最终 Markdown |
   | 停止原因 | `T`：continuation 最终 completed |
   | 安全 | `S`：预设 `True` |
   | 证据 | `E`：调用/usage 存在；live 只核对人工决定次数 |

10. **门禁实际证据链：** 授权前 Executor 确实把解析后的 input 保存为 `input_summary` 和 `input_sha256`：`executor.py:435-480`；批准后 Service 新建单节点 continuation 计划，清空 dependencies/input_bindings：`service.py:631-721`。
11. **当前通过能证明：** Preview 输出绑定被解析为确定授权输入；Apply handler 只在三类哈希一致时写；授权后 continuation 可执行。
12. **当前通过不能证明：** 同一 `run_id` 或同一完整计划原样恢复；授权前文件动态未变；最终正文独立重读等于预期且无额外变化；用户界面展示内容等于冻结输入。
13. **测试性质：** 真正的授权/输入冻结能力合同 + 最终状态弱代理；部分语义绑住当前 continuation-run 实现。
14. **重复/不一致/弱项：** 名称中的 resume 容易被误解为同 run 恢复；当前实际是逻辑任务续接。最终资源状态未检查。
15. **建议状态：** **保留但增强**。
16. **建议后的唯一目标断言：** **授权前正文不变；授权请求冻结的输入等于 Preview 结果；批准后必须从同一逻辑计划的授权节点继续，不得重新规划或漂移，并且只能应用该补丁；最终正文哈希等于预期哈希。**

## 9. 逐条审计：持久化资源、高风险确认与拒绝（第 14—18 条）

### 9.1 第 14 条 `structure_create_update`｜结构创建并更新

1. **正式身份：** 第 14 条；S+L；定义见 `suite.json:254-271`。
2. **用户输入和场景：** 第一轮创建“旧档案馆”卷；第二轮必须使用真实返回的结构项 ID 和最新结构版本更新标题。
3. **真实执行链路：** 运行 1：计划→授权→Create→校验；控制器从真实输出取 ID/版本；运行 2：新请求→计划→授权→Update→校验。不是同一 DAG：`synthetic_environment.py:158-216`。
4. **Tool/Subagent/服务/存储：** `create_novel_structure_items`、`update_novel_structure`；`ChapterService`、`OutlineService`；Markdown/结构 JSON；授权与 Effect 仓储。Tool 真实版本/目标校验见 `create_novel_structure_items.py:33-124`、`update_novel_structure.py:35-171`。
5. **固定/真实边界：** 存储与 Tool 真实；人工批准固定；synthetic 从 Create 输出绑定占位符：`synthetic_environment.py:747-771`；live 从输出构造真实 follow-up：`live_runtime.py:874-902`。
6. **真正想验证的能力：** 跨轮持久化对象身份与并发版本的真实因果使用，不得伪造或猜测 ID。
7. **独立失败模式：** 创建结果缺少稳定 ID、后续使用错误 ID/旧版本、更新错对象、附带修改无关结构。
8. **不可替代性：** 知识卡使用 Mongo 与 `updated_at`，结构资源使用结构版本和 Markdown/outline，风险边界不同。
9. **六门禁当前检查：**

   | 门禁 | 本案实际断言与证据 |
   |---|---|
   | 预算 | `B`：两轮节点/能力/模型计数累计不超限 |
   | 校验 | `V0+Vi`：Create、Update 各 1 次 completed；两次授权脚本 |
   | 产物 | `A`：交互存在，不重读最终结构 |
   | 停止原因 | `T`：最终运行 completed |
   | 安全 | `S`：预设 `True` |
   | 证据 | `E`：调用存在；真实 ID/版本是 follow-up 构造前提 |

10. **门禁实际证据链：** Create 缺少 ID/版本会使控制器抛错，因此当前确有返回值因果使用；但门禁没有保存“目标对象前后差异”。
11. **当前通过能证明：** 创建返回值被用于第二轮；两个生产写 Tool 与两次授权链可完成。
12. **当前通过不能证明：** 最终标题正确、只有目标对象变化、错误对象风险为零、最终结构版本可独立复验。
13. **测试性质：** 较强的跨轮能力合同 + 最终存储状态弱代理。
14. **重复/不一致/弱项：** 不是单纯调用成功；已有真实因果基础，但最终产物和无关对象保持未检查。
15. **建议状态：** **保留但增强**。
16. **建议后的唯一目标断言：** **更新必须使用创建实际返回的结构 ID/版本，最终仅该对象被重命名且其他结构保持不变。**

### 9.2 第 15 条 `structure_delete_second_confirmation`｜结构删除二次确认

1. **正式身份：** 第 15 条；S+L；定义见 `suite.json:274-286`。
2. **用户输入和场景：** 删除预置卷 `fixture_delete_volume`，必须在二次确认后执行。
3. **真实执行链路：** 计划高风险删除 → Runtime 生成授权请求 → 脚本批准并传 `second_confirmation=true` → continuation run 执行 Delete → 校验。
4. **Tool/Subagent/服务/存储：** `delete_novel_structure_items`；`ChapterService`、`OutlineService`；结构存储、授权、Effect。Tool manifest 是 `HIGH_RISK_WRITE` + `SECOND_CONFIRMATION`：`src/taichu/application/tools/delete_novel_structure_items.py:33-44`。
5. **固定/真实边界：** 目标空卷、影响说明和人工批准固定；Tool/策略/存储真实；live 人工决定仍自动生成。
6. **真正想验证的能力：** 高风险写入无法绕过二次确认，且确认对象、执行对象与最终状态一致。
7. **独立失败模式：** 确认前删除、一次确认替代二次确认、确认对象与删除对象不一致、连带删除无关资源。
8. **不可替代性：** 普通写授权不覆盖高风险二次确认与删除影响范围。
9. **六门禁当前检查：**

   | 门禁 | 本案实际断言与证据 |
   |---|---|
   | 预算 | `B`：1 删除节点、1 Tool、模型调用不超限 |
   | 校验 | `V0+Vi`：Delete 1 次 completed；synthetic 有一次 `approved=true` 二次确认 |
   | 产物 | `A`：交互存在，不重读结构 |
   | 停止原因 | `T`：completed |
   | 安全 | `S`：预设 `True` |
   | 证据 | `E`：交互存在；live 只把 second confirmation 映射为授权次数 |

10. **门禁实际证据链：** `resume()` 对高风险批准缺少 `second_confirmation` 会报错：`service.py:284-298`；Delete Tool 会校验目标存在并归档指定对象：`delete_novel_structure_items.py:47-114`。
11. **当前通过能证明：** 当前 Runtime 策略会阻止缺二次确认的恢复；批准后指定生产 Delete handler 完成。
12. **当前通过不能证明：** 确认前存储动态不变；授权输入与删除 Effect 精确绑定；其他对象未变化；非空卷影响正确治理。
13. **测试性质：** 高风险授权/Tool 合同 + 最终状态弱代理。
14. **重复/不一致/弱项：** 目标卷为空，未覆盖子章节影响；安全门禁不读前后状态。
15. **建议状态：** **保留但增强**。
16. **建议后的唯一目标断言：** **二次确认前目标仍存在且无写入；确认后仅确认对象被归档，无关结构不变，授权输入和删除 Effect 可相互关联。**

### 9.3 第 16 条 `knowledge_create_update`｜知识创建并更新

1. **正式身份：** 第 16 条；S+L；定义见 `suite.json:289-306`。
2. **用户输入和场景：** 第一轮创建确认态“旧档案馆”地点卡；第二轮必须使用真实卡 ID 与 `updated_at` 更新摘要。
3. **真实执行链路：** 运行 1：计划→授权→Create Mongo 卡→校验；从真实输出取 ID/时间；运行 2：新请求→授权→Update→校验。
4. **Tool/Subagent/服务/存储：** `create_confirmed_knowledge`、`update_confirmed_knowledge`；`KnowledgeService`；隔离 MongoDB；授权与 Effect。入口见 `create_confirmed_knowledge.py:26-57`、`update_confirmed_knowledge.py:26-61`。
5. **固定/真实边界：** Mongo/Service/Tool 真实；卡内容和批准固定；synthetic 与 live 都从真实 Create output 取得下一轮标识：`synthetic_environment.py:763-770`、`live_runtime.py:903-917`。
6. **真正想验证的能力：** Mongo 持久化对象的真实身份、确认态来源、并发版本与跨轮因果。
7. **独立失败模式：** 使用伪造 ID、更新错误卡、忽略并发版本、来源/生命周期错误、生成重复身份卡。
8. **不可替代性：** 结构写入不覆盖 Mongo 知识卡的 schema、lifecycle、来源与更新时间并发边界。
9. **六门禁当前检查：**

   | 门禁 | 本案实际断言与证据 |
   |---|---|
   | 预算 | `B`：两轮资源计数不超限 |
   | 校验 | `V0+Vi`：Create/Update 各 1 次 completed；两次授权 |
   | 产物 | `A`：交互存在，不从 Mongo 重读最终卡 |
   | 停止原因 | `T`：completed |
   | 安全 | `S`：预设 `True` |
   | 证据 | `E`：调用存在；ID/时间是第二轮构造前提 |

10. **门禁实际证据链：** Update handler 返回更新后 card，但门禁没有检查 card 字段，也不读取种子卡对照。
11. **当前通过能证明：** 真实 Mongo 创建/更新可运行；Update 使用了 Create 实际返回 ID 与并发版本。
12. **当前通过不能证明：** 最终字段、来源和 confirmed 状态完全正确；其他卡未变化；重复卡不存在；并发冲突失败分支正确。
13. **测试性质：** 较强的真实返回值能力合同 + 最终状态弱代理。
14. **重复/不一致/弱项：** 与第 14 条结构相似但存储边界独立；未动态检查最终产物。
15. **建议状态：** **保留但增强**。
16. **建议后的唯一目标断言：** **更新必须使用新建卡实际返回的 ID/并发版本，最终该确认态卡字段和来源正确，其他知识卡保持不变。**

### 9.4 第 17 条 `external_access_denied`｜外部访问拒绝

1. **正式身份：** 第 17 条；仅 S；定义见 `suite.json:309-316`。
2. **用户输入和场景：** 未授权时要求搜索现实网站。
3. **真实执行链路：** 固定规划模型直接返回拒绝，`nodes=[]`；没有进入任何外研节点或外部 Tool。
4. **Tool/Subagent/服务/存储：** 本案无能力 exposure、无 Tool/Subagent。产品 Runtime 真正的外部许可阻断在 `_external_grant()`：`src/taichu/application/general_agent/executor.py:832-847`，本案没有触发。
5. **固定/真实边界：** 只跑 synthetic；拒绝文本与零节点计划完全预写。
6. **真正想验证的能力：** 未授权外部访问必须在任何外部调用前被 Runtime 拒绝。
7. **独立失败模式：** 已计划外研节点在无授权下仍调用 Backend，或模型擅自访问外部来源。
8. **不可替代性：** 理论上网络许可是一条独立安全边界；但当前案例只验证空 exposure + 固定拒绝，没有触达强制边界。
9. **六门禁当前检查：**

   | 门禁 | 本案实际断言与证据 |
   |---|---|
   | 预算 | `B`：0 节点/能力、1 模型交互不超限 |
   | 校验 | `V0+Vi`：completed；必需调用为空 |
   | 产物 | `A`：一条模型交互即可通过 |
   | 停止原因 | `T`：completed |
   | 安全 | `S`：预设 `True`，没有 Backend 零调用证据 |
   | 证据 | `E`：固定拒绝交互存在 |

10. **门禁实际证据链：** 没有网络 Backend 调用计数；安全通过不是由拒绝分支或零出网动态测得。
11. **当前通过能证明：** 空能力目录下，固定合成计划可以直接拒绝并完成。
12. **当前通过不能证明：** Runtime 会拦截已进入计划的未授权外研节点；真实模型不会选择外研；零外部 Backend 调用；数据出境/隐私边界。
13. **测试性质：** 低难度的固定规划代理，与真正 Runtime 强制授权实现脱节。
14. **重复/不一致/弱项：** 与第 1、18、19—22 条同为直接回答；独立能力退化风险低，当前价值不足以占一条核心 Runtime Benchmark。
15. **建议状态：** **替换**。用上下文治理场景替换；若未来仍保留网络许可主题，应另做真实 Runtime 阻断案例。
16. **建议后的唯一目标断言：** **若保留该主题：即使计划中出现外研节点，未授权时 Runtime 也必须在任何外部 Tool/Backend 调用前拒绝，并留下零外部调用证据。**

### 9.5 第 18 条 `write_authorization_denied`｜写授权拒绝

1. **正式身份：** 第 18 条；S+L；定义见 `suite.json:319-326`。
2. **用户输入和场景：** 文案声称先 Preview，系统请求授权时用户拒绝，源文件必须不变。
3. **真实执行链路：** 当前只有一次固定 `orchestrator_plan` 直接回答“已拒绝写回”，`nodes=[]`；无 Preview、无授权请求、无拒绝决定、无 `resume()`。
4. **Tool/Subagent/服务/存储：** 本案无 Tool/Subagent exposure。真实拒绝分支存在于 `GeneralAgentRuntimeService._continue_write_authorization()`：拒绝后创建 continuation run、零节点计划、completed，并 checkpoint `write_rejected`：`service.py:631-669`；本案没有触达。
5. **固定/真实边界：** synthetic 拒绝预写；live 虽用真实模型，但案例脚本仍没有 Human 合同，若真实模型发起授权反而会成为未声明人工交互。
6. **真正想验证的能力：** 已形成确定写入输入并暂停后，用户拒绝能阻止写入、保持资源不变并正确结束。
7. **独立失败模式：** 拒绝后写节点仍执行、正文改变、任务悬挂或错误重试、拒绝证据丢失。
8. **不可替代性：** 第 13 条只覆盖批准分支，不能证明同一授权状态机的拒绝路径。
9. **六门禁当前检查：**

   | 门禁 | 本案实际断言与证据 |
   |---|---|
   | 预算 | `B`：0 节点/能力、1 模型交互不超限 |
   | 校验 | `V0+Vi`：completed；必需调用为空 |
   | 产物 | `A`：固定拒绝交互存在，不检查 Preview 或正文 |
   | 停止原因 | `T`：completed，但不是授权拒绝 continuation 的完成 |
   | 安全 | `S`：预设 `True` |
   | 证据 | `E`：无 Human、无 `write_rejected`、无状态差异 |

10. **门禁实际证据链：** “正文不变”只是固定模型文案；没有前后哈希或 Apply 零调用的动态证据。
11. **当前通过能证明：** 编排器可以在规划阶段不安排写能力，并直接完成。
12. **当前通过不能证明：** Preview、授权请求、拒绝、恢复、Apply 零调用、正文不变、拒绝 Checkpoint 的完整链路。
13. **测试性质：** 名称所指行为完全未测试；当前只是预设真值。
14. **重复/不一致/弱项：** 名称与实际行为严重不一致；它与第 17 条几乎都是“直接拒绝”，没有独立 HITL 价值。
15. **建议状态：** **重做**。
16. **建议后的唯一目标断言：** **真实 Preview 完成后 Runtime 发起授权，用户拒绝；Apply 调用为零、正文前后完全一致，Runtime 正确结束并留下拒绝决定与 `write_rejected` 证据。**

## 10. 逐条审计：运行工作记忆与 Checkpoint（第 19—23 条）

### 10.1 先校准 Memory 实体与五层关系

- `AgentMemoryKind` 的代码注释明确写为“当前任务工作记忆的内容类型，不承担用户偏好长期记忆职责”：`src/taichu/application/agent_memory/models.py:20-28`。
- 实体存于隔离工作区 `derived/general_agent_memory/*.json` 的 `JsonAgentMemoryRepository`，并由词法索引支持召回：`src/taichu/infrastructure/agent_memory/json_repository.py:20-43`。
- 正常 `ContextAssembler` 会查询有效/失效工作记忆，并分别经 Current 与 Repair 投影装入 working memory：`context.py:207-287`；当前 `long_term_memory=[]`：`context.py:451-469`。
- Benchmark 专属机制校验不读取本轮实际 `context_snapshot`，而是重新查询仓储并再次调用相同投影类，只检查状态集合：`synthetic_environment.py:920-974`。

**[基于事实的推断]** 第 19—22 条是同一种 Runtime 工作记忆实体的四种 validity 场景，不是五层记忆各自一条，也不能证明长期记忆层。

### 10.2 第 19 条 `memory_active_projection`｜活动记忆投影

1. **正式身份：** 第 19 条；S+L；定义见 `suite.json:329-336`。
2. **用户输入和场景：** 要求按已保存的回答风格改写两章概括。
3. **真实执行链路：** ContextAssembler 查询工作记忆 → 规划模型直接回答，`nodes=[]`；无 Tool/Subagent。
4. **Tool/Subagent/服务/存储：** `AgentMemoryService`、JSON 仓储/索引、ContextAssembler、运行/Checkpoint；实际种子是 `USER_INSTRUCTION`：“先给结论，再说明依据。”，validity=ACTIVE：`synthetic_environment.py:684-736`。
5. **固定/真实边界：** 记忆仓储、投影、上下文装配真实；synthetic 直接回答固定，live 模型真实。
6. **真正想验证的能力：** 一条只存在于有效工作记忆中的约束真正影响最终回答。
7. **独立失败模式：** 有效记忆未进入模型输入，或进入但模型/编排器没有采用。
8. **不可替代性：** 失效、拒绝、替代场景验证排除逻辑，不能证明有效记录具有正向效果。
9. **六门禁当前检查：**

   | 门禁 | 本案实际断言与证据 |
   |---|---|
   | 预算 | `B`：0 能力节点、1 计划模型等不超限 |
   | 校验 | `V0+Vi+Vm`：completed；零调用；任意 ACTIVE 在 current 投影 |
   | 产物 | `A`：模型交互存在，不检查答案 |
   | 停止原因 | `T`：completed |
   | 安全 | `S`：预设 `True` |
   | 证据 | `E`：交互存在；不读取实际 context snapshot |

10. **门禁实际证据链：** `Vm` 只检查 validity 集合；不检查是哪条 ACTIVE、不读取最终答案。
11. **当前通过能证明：** 当前投影类允许固定 ACTIVE 实体进入重新构造的 current 投影，Runtime 可直接完成。
12. **当前通过不能证明：** 该记忆实际进入本轮模型请求并改变答案。固定答案没有“先结论、再依据”，还加入请求未给出的“第三道星纹”，仍可通过：`suite.json:335`。
13. **测试性质：** 内部投影合同 + 弱代理，不是最终行为效果。
14. **重复/不一致/弱项：** 四条 Memory 的通用机制证据高度相似；本条独立性应由“正向影响结果”而非 ACTIVE 存在来建立。
15. **建议状态：** **保留但增强**。
16. **建议后的唯一目标断言：** **一条只有记忆中存在、当前请求没有重复提供的有效工作记忆约束，必须可观察地改变最终答案，并能从答案追溯到被选择的记忆。**

### 10.3 第 20 条 `memory_stale_dependency`｜过期记忆依赖拒绝

1. **正式身份：** 第 20 条；S+L；定义见 `suite.json:339-346`。
2. **用户输入和场景：** 当前请求明确跨两章分析，不得受“只讨论第一章”的旧范围记忆限制。
3. **真实执行链路：** ContextAssembler 处理记忆 → 规划模型直接回答，`nodes=[]`。
4. **Tool/Subagent/服务/存储：** 同第 19 条；固定 `USER_INSTRUCTION` 内容“只讨论第一章”，validity=STALE。
5. **固定/真实边界：** 投影/存储真实；synthetic 答案固定。
6. **真正想验证的能力：** 高相关但已过期的范围限制不得污染当前任务。
7. **独立失败模式：** 过期约束仍进入当前模型投影，导致只回答第一章。
8. **不可替代性：** REJECTED 是否决错误事实，SUPERSEDED 是新旧替代；STALE 是时间/范围失效，失败语义不同。
9. **六门禁当前检查：**

   | 门禁 | 本案实际断言与证据 |
   |---|---|
   | 预算 | `B`：直接回答资源计数不超限 |
   | 校验 | `V0+Vi+Vm`：STALE 不在 current 且在 repair |
   | 产物 | `A`：交互存在，不检查跨章答案 |
   | 停止原因 | `T`：completed |
   | 安全 | `S`：预设 `True` |
   | 证据 | `E`：交互存在，不检查排除原因到答案 |

10. **门禁实际证据链：** 名称含“依赖”，但实际种子 `dependencies=[]`；没有测试真实上游依赖失效传播：`synthetic_environment.py:710-716`。
11. **当前通过能证明：** 当前投影类排除 STALE，Repair 投影保留它供修复参考。
12. **当前通过不能证明：** 最终答案没有遵守旧单章限制；真实依赖链正确失效。
13. **测试性质：** 状态投影合同，不是答案行为。
14. **重复/不一致/弱项：** 中文名中的“依赖”与当前造数不完全一致；只检查对象集合。
15. **建议状态：** **保留但增强**。
16. **建议后的唯一目标断言：** **即使过期记忆与当前请求高度相关，它也不得限制最终答案；答案必须完成跨章任务并留下失效排除证据。**

### 10.4 第 21 条 `memory_rejected_parallel_isolation`｜拒绝记忆并行隔离

1. **正式身份：** 第 21 条；S+L；定义见 `suite.json:349-356`。
2. **用户输入和场景：** 基于当前事实从“刻痕来源”和“角色认知”两个角度分析，不得采用已拒绝候选事实。
3. **真实执行链路：** 当前计划 `nodes=[]`，只有一次直接回答；没有两个分支，更没有 Subagent 作用域：`suite.json:353-355`。
4. **Tool/Subagent/服务/存储：** 同第 19 条；REJECTED 的 `USER_INSTRUCTION` 内容是“第三道星纹由沈漪磨去”。
5. **固定/真实边界：** 投影/存储真实；并行执行不存在；synthetic 回答固定。
6. **真正想验证的能力：** 已否决错误记忆不在任何真实分支、子 Agent 输入输出或最终聚合中复活。
7. **独立失败模式：** 主投影排除了错误记忆，但某个分支通过摘要、共享状态或错误作用域重新看到它。
8. **不可替代性：** 单线程排除不能证明跨分支隔离；第 10 条并行审查又没有拒绝记忆压力。
9. **六门禁当前检查：**

   | 门禁 | 本案实际断言与证据 |
   |---|---|
   | 预算 | `B`：直接回答资源计数不超限 |
   | 校验 | `V0+Vi+Vm`：REJECTED 不在 current 且在 repair |
   | 产物 | `A`：交互存在，不检查两个分析分支 |
   | 停止原因 | `T`：completed |
   | 安全 | `S`：预设 `True` |
   | 证据 | `E`：交互存在，无分支输入/输出证据 |

10. **门禁实际证据链：** 只检查 validity 集合；没有分支、消息作用域、上下文快照或聚合记录。
11. **当前通过能证明：** 当前投影类按状态排除 REJECTED。
12. **当前通过不能证明：** 任何并行隔离、子 Agent 消息隔离或最终聚合无污染。固定回答也没有给出要求的两个分析角度，仍可通过：`suite.json:355`。
13. **测试性质：** 名称与实际行为明显不一致；当前接近投影类状态检查。
14. **重复/不一致/弱项：** 与第 20、22 条只有 validity 枚举不同；“parallel isolation”完全缺失。
15. **建议状态：** **重做**。
16. **建议后的唯一目标断言：** **同一已拒绝错误记忆不得出现在任何真实独立执行分支、子 Agent 输入输出或最终聚合中，所有分支只能使用当前有效事实。**

### 10.5 第 22 条 `memory_superseded_repair`｜被替代记忆修复

1. **正式身份：** 第 22 条；S+L；定义见 `suite.json:359-366`。
2. **用户输入和场景：** 新有效偏好为“先给结论，再说明依据”，旧偏好“只写结论”已被替代；要求按新偏好修复旧回答。
3. **真实执行链路：** ContextAssembler 处理工作记忆 → 直接回答，`nodes=[]`。
4. **Tool/Subagent/服务/存储：** 同第 19 条；SUPERSEDED 记录的 `invalidated_by_memory_id` 指向 active 行：`synthetic_environment.py:693-734`。
5. **固定/真实边界：** 投影/存储真实；synthetic 回答固定。
6. **真正想验证的能力：** 新旧记忆冲突时使用最新有效结论，旧结论只能作为修复历史。
7. **独立失败模式：** 旧偏好重新成为当前依据，或新旧同时进入造成冲突。
8. **不可替代性：** STALE/REJECTED 只表示不可用，不包含明确替代关系与最新结论选择。
9. **六门禁当前检查：**

   | 门禁 | 本案实际断言与证据 |
   |---|---|
   | 预算 | `B`：直接回答资源不超限 |
   | 校验 | `V0+Vi+Vm`：SUPERSEDED 不在 current 且在 repair |
   | 产物 | `A`：交互存在，不检查修复答案 |
   | 停止原因 | `T`：completed |
   | 安全 | `S`：预设 `True` |
   | 证据 | `E`：交互存在，不校验替代指针或答案 |

10. **门禁实际证据链：** `Vm` 只看是否有任意 SUPERSEDED，不检查其内容、`invalidated_by_memory_id` 或指向 ACTIVE 的因果关系。
11. **当前通过能证明：** 投影类排除 SUPERSEDED，Repair 投影接纳它。
12. **当前通过不能证明：** 最新有效结论被采用；最终答案按“结论+依据”修复。固定回答只声称“已修复”，没有输出实际结论与依据：`suite.json:365`。
13. **测试性质：** 状态投影合同 + 弱代理。
14. **重复/不一致/弱项：** 与其他记忆条目共用同一集合检查；没有验证具体替代关系。
15. **建议状态：** **保留但增强**。
16. **建议后的唯一目标断言：** **新有效记忆明确替代旧记忆时，最终答案必须体现新结论，旧结论不得在回答或修复链中重新成为当前依据。**

### 10.6 第 23 条 `runtime_checkpoint_recovery`｜检查点恢复

1. **正式身份：** 第 23 条；正式仅 S；定义见 `suite.json:369-378`。
2. **用户输入和场景：** 先读取小说结构；系统在验证阶段注入一次进程中断；恢复后不得重跑已成功读取。
3. **真实执行链路：** 固定计划生成 `get_novel_structure` → 生产 Tool 成功 → 首次 `general_writing_orchestrator.verify` 调用前 Gateway 抛一次终止 → 重建部分运行依赖 → `recover_interrupted()` → 同 run 从图状态继续第二次 verify → completed：`synthetic_environment.py:419-527`。
4. **Tool/Subagent/服务/存储：** `get_novel_structure`、Chapter/Outline、JSON Run/Effect/ContextSnapshot、`JsonLangGraphCheckpointSaver`。恢复时替换运行仓储、事件中心、checkpointer、Effect/ContextSnapshot；Gateway、Observer、MemoryService、ContextAssembler 等仍复用：`synthetic_environment.py:453-485`。
5. **固定/真实边界：** 故障点与 verify 响应固定；Tool、存储、LangGraph、恢复服务真实。
6. **真正想验证的能力：** 中断后恢复同一运行、复用已成功节点和中间状态、不静默从头执行。
7. **独立失败模式：** 恢复创建新 run、已成功 Tool 重跑、Checkpoint 损坏未察觉、工作状态丢失、恢复后无法完成。
8. **不可替代性：** 其他案例没有进程级中断与恢复，不可替代。
9. **六门禁当前检查：**

   | 门禁 | 本案实际断言与证据 |
   |---|---|
   | 预算 | `B`：恢复前后累计节点/调用/模型观察不超限 |
   | 校验 | `V0+Vi+Vr`：completed；脚本辅助结构 Tool completed；same run、recover=1、verify=2、前后 Checkpoint 有效、读取 1→1 |
   | 产物 | `A`：交互存在，不检查最终结构概括 |
   | 停止原因 | `T`：completed |
   | 安全 | `S`：预设 `True` |
   | 证据 | `E`：调用/normalization ref；`Vr` 有 revision/hash/integrity 证明 |

10. **门禁实际证据链：** `Vr` 是当前唯一较强案例专属机制证据：`synthetic_environment.py:883-918`。但正式 `required_invocations=[]`，结构读取只作为 scripted auxiliary：`suite.json:373-377`。
11. **当前通过能证明：** 在“只读 Tool 已成功、首次 verify 模型调用前”这一故障点，当前 Checkpoint 能恢复同一 run，且结构 Tool 次数保持 1→1。
12. **当前通过不能证明：** 规划、Subagent、授权等待、写副作用窗口、重规划、多次中断、Checkpoint 损坏/不兼容；工作记忆/计划/工件语义一致；最终结构概括正确。
13. **测试性质：** 真正的核心恢复行为合同；`verify_attempts==2`、revision 与 SHA-256 长度属于当前证明格式。
14. **重复/不一致/弱项：** 不与其他案例重复，但覆盖面过窄；恢复并非完整进程所有依赖全重建；`get_novel_structure` 未列入 required invocation。
15. **建议状态：** **替换为恢复场景组**；保留当前 verify 场景作为组内一条。
16. **建议后的唯一目标断言：** **Runtime 在校验阶段中断后必须恢复同一运行、复用已成功只读节点、完成任务，并留下可验证的恢复链。**

## 11. “Benchmark × Runtime 能力”覆盖矩阵

### 11.1 分类口径

下表是本次审计基于真实执行链给出的分类，不是仓库现有枚举。`●` 表示主验证目标，`○` 表示任务链中实际涉及或意图涉及的辅助能力，`—` 表示没有覆盖。

能力列：

- `R`：简单路由与最小充分执行路径；
- `Q`：检索与 RAG；
- `E`：证据生成与事实边界；
- `C`：多 Agent 分支、流水线、并行审查与反馈修订；
- `W`：预览、写入、授权、拒绝和高风险确认；
- `P`：持久化资源身份、版本、状态与副作用治理；
- `M`：运行工作记忆状态与治理；
- `K`：Checkpoint、中断与恢复；
- `X`：上下文预算、裁剪、压缩与事实保持。

| # | Benchmark | R | Q | E | C | W | P | M | K | X | 主验证目标 |
|---:|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|---|
| 1 | `direct_answer_current_request` | ● | — | — | — | — | — | — | — | — | 简单请求不被错误复杂化 |
| 2 | `single_manuscript_search` | ○ | ● | ○ | — | — | — | — | — | — | 单次正文召回 |
| 3 | `structure_coverage_read` | ○ | ● | ○ | — | — | ○ | — | — | — | 多源结构与正文覆盖读取 |
| 4 | `single_knowledge_retrieval` | ○ | ● | ○ | — | — | ○ | — | — | — | 确认态知识召回 |
| 5 | `knowledge_catalog_identity_read` | ○ | ● | ○ | — | — | ○ | — | — | — | 目录→身份→真实卡的读取链 |
| 6 | `external_research_grounded` | ○ | ● | ○ | ○ | ○ | — | — | — | — | 有来源的外部研究链 |
| 7 | `single_canon_evidence` | — | — | ● | ○ | — | — | — | — | — | 小说事实与推测边界 |
| 8 | `summary_world_character` | — | — | ○ | ● | — | — | — | — | — | 共同上游后的独立分析分支 |
| 9 | `architecture_scene_draft` | — | — | — | ● | ○ | — | — | — | — | 架构→场景→草稿的工件流水线 |
| 10 | `parallel_review_triad` | — | — | — | ● | — | — | — | — | — | 同源、独立的三审分支 |
| 11 | `revision_from_reviews` | — | — | ○ | ● | — | — | — | — | — | 审查意见到修订稿的反馈闭环 |
| 12 | `manuscript_preview_only` | — | — | — | — | ● | ○ | — | — | — | 预览无持久化副作用 |
| 13 | `manuscript_patch_authorized_resume` | — | — | — | — | ● | ○ | — | — | — | 授权输入冻结与批准后写入 |
| 14 | `structure_create_update` | — | — | — | — | ○ | ● | — | — | — | 跨轮真实 ID/版本因果使用 |
| 15 | `structure_delete_second_confirmation` | — | — | — | — | ● | ○ | — | — | — | 高风险删除与目标对象治理 |
| 16 | `knowledge_create_update` | — | ○ | — | — | ○ | ● | — | — | — | Mongo 知识卡跨轮身份与并发版本 |
| 17 | `external_access_denied` | ○ | — | — | — | ● | — | — | — | — | 未授权外部访问拒绝；当前只实现直接拒绝代理 |
| 18 | `write_authorization_denied` | ○ | — | — | — | ● | ○ | — | — | — | 写授权拒绝；当前未触达拒绝状态机 |
| 19 | `memory_active_projection` | ○ | — | — | — | — | — | ● | — | — | 有效运行工作记忆的正向影响 |
| 20 | `memory_stale_dependency` | ○ | — | — | — | — | — | ● | — | — | 过期工作记忆排除 |
| 21 | `memory_rejected_parallel_isolation` | ○ | — | — | ○ | — | — | ● | — | — | 被拒绝记忆的跨分支隔离；当前没有分支 |
| 22 | `memory_superseded_repair` | ○ | — | — | — | — | — | ● | — | — | 新旧记忆冲突下采用最新有效结论 |
| 23 | `runtime_checkpoint_recovery` | — | ○ | — | — | — | ○ | ○ | ● | — | 校验阶段中断后的同运行恢复 |

### 11.2 分类结论

- **[代码事实]** 当前正式套件在 `X` 列 23 条全部为空。每案资源预算只有节点、重规划、能力调用、模型调用、总 Token 与时长：`suite_loader.py:50-64`；没有上下文分层字符预算、裁剪统计或压缩后结果断言。
- **[基于事实的推断]** 第 17、18、19—22 条由于实际都退化为 `nodes=[]` 的直接回答，也重复触达了 `R`，但除第 1 条外，这不是它们名称所声明的独立能力目标。
- **[基于事实的推断]** 第 5 条兼具检索与持久化身份读取，主目标仍归检索/RAG 占坑组；第 13 条兼具授权继续执行与写入副作用，第 14—16 条主目标则是资源身份和最终持久化状态。
- **[基于事实的推断]** 当前 23 条均可归入用户给出的前八类；没有需要另立“其他”主类别的正式案例。真正缺失的是第九类上下文治理，不是“预算门禁已覆盖”。

## 12. “Benchmark × 六类门禁”实际断言矩阵

### 12.1 断言代码

为避免把重复条件包装成不同证据，矩阵使用以下代码：

| 代码 | 当前代码实际含义 |
|---|---|
| `B0` | 六项资源观察值不高于每案上限；synthetic 的 Token/时长观察值是 0 |
| `V0` | `run_status == completed` |
| `Vi(...)` | 指定 Tool/Subagent 名称、类型、完成次数和 outcome 合同；不检查调用输入、输出、`parent` 或 `partial_order` |
| `Vh(n)` | live 轨道观察到的 Human 决定次数等于脚本次数；不比较批准/拒绝的布尔值；`n=0` 仅表示脚本和观察都没有 Human 事件 |
| `Vm(...)` | 从仓储重新构造 current/repair 投影后，只比较记忆 validity 集合 |
| `Vr` | 第 23 条同 run、恢复一次、verify 两次、Checkpoint 有效、结构读取 1→1、最终完成 |
| `A0` | 至少存在一条交互记录 |
| `T0` | `run_status == completed`，与 `V0` 完全相同 |
| `S0` | 调用 `_gate_conditions()` 时直接传入 `True` |
| `E0` | synthetic 至少一条交互；live 至少一条 usage 或交互 |
| `Ep` | synthetic 严格脚本的 matcher、单流顺序、并行组可交错和“不得剩余步骤”协议；它在六门禁判定前执行，不属于证据门禁 |

代码位置：`synthetic_environment.py:218-270,774-919`、`live_runtime.py:511-540,817-852,921-985`、`synthetic_suite.py:317-362`、`strict_driver.py:190-349`。

### 12.2 23 条实际断言

| # | Benchmark | 预算 | 校验门禁 | 产物 | 停止原因 | 安全 | 证据 |
|---:|---|---|---|---|---|---|---|
| 1 | `direct_answer_current_request` | `B0` | `V0+Vi(零调用)` | `A0` | `T0` | `S0` | `E0` |
| 2 | `single_manuscript_search` | `B0` | `V0+Vi(search_manuscript=1)` | `A0` | `T0` | `S0` | `E0` |
| 3 | `structure_coverage_read` | `B0` | `V0+Vi(三种读取各1)` | `A0` | `T0` | `S0` | `E0` |
| 4 | `single_knowledge_retrieval` | `B0` | `V0+Vi(retrieve_knowledge=1)` | `A0` | `T0` | `S0` | `E0` |
| 5 | `knowledge_catalog_identity_read` | `B0` | `V0+Vi(目录/身份/卡各1)` | `A0` | `T0` | `S0` | `E0` |
| 6 | `external_research_grounded` | `B0` | `V0+Vi(外研A=1、搜索T=1、读取T=1..3)`；`parent` 不检查 | `A0` | `T0` | `S0` | `E0` |
| 7 | `single_canon_evidence` | `B0` | `V0+Vi(canon_evidence=1)` | `A0` | `T0` | `S0` | `E0` |
| 8 | `summary_world_character` | `B0` | `V0+Vi(摘要/世界/人物各1)`；并行关系不检查 | `A0` | `T0` | `S0` | `E0` |
| 9 | `architecture_scene_draft` | `B0` | `V0+Vi(架构/场景/草稿各1)`；语义交接不检查 | `A0` | `T0` | `S0` | `E0` |
| 10 | `parallel_review_triad` | `B0` | `V0+Vi(三审各1)`；时间并发不检查 | `A0` | `T0` | `S0` | `E0` |
| 11 | `revision_from_reviews` | `B0` | `V0+Vi(revision=1)` | `A0` | `T0` | `S0` | `E0` |
| 12 | `manuscript_preview_only` | `B0` | `V0+Vi(preview=1)` | `A0` | `T0` | `S0` | `E0` |
| 13 | `manuscript_patch_authorized_resume` | `B0` | `V0+Vi(preview/apply各1)+Vh(1)` | `A0` | `T0` | `S0` | `E0` |
| 14 | `structure_create_update` | `B0` | `V0+Vi(create/update各1)+Vh(2)` | `A0` | `T0` | `S0` | `E0` |
| 15 | `structure_delete_second_confirmation` | `B0` | `V0+Vi(delete=1)+Vh(1)` | `A0` | `T0` | `S0` | `E0` |
| 16 | `knowledge_create_update` | `B0` | `V0+Vi(create/update各1)+Vh(2)` | `A0` | `T0` | `S0` | `E0` |
| 17 | `external_access_denied` | `B0` | `V0+Vi(零调用)` | `A0` | `T0` | `S0` | `E0` |
| 18 | `write_authorization_denied` | `B0` | `V0+Vi(零调用)+Vh(0)` | `A0` | `T0` | `S0` | `E0` |
| 19 | `memory_active_projection` | `B0` | `V0+Vi(零调用)+Vm(ACTIVE在current)` | `A0` | `T0` | `S0` | `E0` |
| 20 | `memory_stale_dependency` | `B0` | `V0+Vi(零调用)+Vm(STALE不在current、在repair)` | `A0` | `T0` | `S0` | `E0` |
| 21 | `memory_rejected_parallel_isolation` | `B0` | `V0+Vi(零调用)+Vm(REJECTED不在current、在repair)` | `A0` | `T0` | `S0` | `E0` |
| 22 | `memory_superseded_repair` | `B0` | `V0+Vi(零调用)+Vm(SUPERSEDED不在current、在repair)` | `A0` | `T0` | `S0` | `E0` |
| 23 | `runtime_checkpoint_recovery` | `B0` | `V0+Vi(脚本辅助调用完成)+Vr` | `A0` | `T0` | `S0` | `E0` |

### 12.3 矩阵揭示的事实

- **[代码事实]** 23 条的产物、安全、停止原因与普通证据断言完全同构；差异主要集中在校验门禁的 invocation、四种记忆集合与一个恢复 proof：`synthetic_environment.py:774-919,920-974`、`live_runtime.py:527-540,817-852`。
- **[代码事实]** 第 13—16 条 live 人工合同只比较决定次数；`_live_human_problems()` 没有读取 `approved`：`live_runtime.py:955-985`。synthetic 的具体布尔值只受固定脚本消费约束。
- **[代码事实]** 第 23 条正式 `required_invocations=[]`，结构读取仅因出现在 scripted steps 中被允许并要求完成：`suite.json:369-378`、`synthetic_suite.py:317-362`。
- **[基于事实的推断]** 当前“六类门禁全部通过”不是六套相互独立的场景证据。`V0/T0` 重复；`A0/E0` 高度重叠；`S0` 没有动态测量；六门禁内真正有案例差异的主要是 `Vi/Vm/Vr`，门禁之外另有 synthetic 的 `Ep` 严格脚本协议。

## 13. 六类门禁专项审计

| 门禁 | 长期要保护的行为 | 当前实际检查 | 足以证明目标吗 | 存在性代理/预设真值 | 无关或重复条件 | 围绕唯一能力目标应形成的证据 |
|---|---|---|---|---|---|---|
| 预算 | 最小充分路径在资源边界内完成；越界时按确定原因停止 | 六项观察值 `<=` 上限 | **不足**。只覆盖未越界路径 | synthetic Token/时长为 0；没有上下文分层预算 | 对所有案例统一，无法证明某案例“最小” | 目标链的必要/非必要节点、真实用量、越界触发点、停止原因、零额外副作用 |
| 校验 | 场景唯一行为目标及必要能力合同成立 | completed、能力名称/次数/outcome；少数 `Vm/Vr` | **多数不足**。接口可调用不等于效果正确 | completed、调用成功是效果的弱代理 | completed 与停止门禁重复；`parent/partial_order` 未兑现 | 场景输入→调用入参→能力输出→下游消费→最终答案/状态的因果断言 |
| 产物 | 目标中间工件和最终产物存在、结构和内容可用、归属正确 | 至少一条交互 | **不能** | `bool(interactions)` | 与证据门禁几乎重复；对直接回答也不检查回答正文 | 读取具体工件、类型、引用、内容不变量、最终持久化资源或直接回答 |
| 停止原因 | completed、拒绝、等待、预算停止、可恢复失败等语义正确 | 仅 `run_status == completed` | **不能覆盖非完成型正确结果** | completed 状态 | 与校验门禁 `V0` 完全重复 | 预期终态、停止原因、可恢复性、待处理 Human 请求、错误分类和重试语义 |
| 安全 | 未授权副作用为零；授权只作用于批准资源与内容；高风险确认完整 | 调用方传入 `True` | **不能** | 直接预设真值 | 对所有案例无差异 | 授权前后资源哈希、Effect 记录、目标身份、批准输入摘要、拒绝/越权分支的零调用证据 |
| 证据 | 结论可追溯到来源、能力输出、状态差异和最终回答 | 交互或 usage 存在；strict matcher 只检查选定字段 | **多数不足** | 记录存在是证据完整性的弱代理 | 与产物门禁重叠 | 来源引用、调用输入输出哈希、工件引用、前后状态、最终回答引用之间可验证闭环 |

### 13.1 对长期准入标准的判断

- **[基于事实的推断]** 六类门禁的长期分类本身合理，适合作为准入框架长期保留；问题不在“六类太少”，而在当前每类收到的案例证据过弱。
- **[基于事实的推断]** Tool/Subagent 的稳定名称、输入 Schema、输出 Schema、权限级别和工件类型可以长期作为能力合同的一部分。生产插件发现和协议校验的位置见 `runtime_factory.py:172-258`、Subagent 工件类型校验见 `src/taichu/application/subagents/runner.py:238-271`。
- **[基于事实的推断]** 具体类名、JSON 仓储目录、Mongo 驱动、`verify_attempts==2`、Checkpoint revision 形态、SHA-256 字符长度、当前投影类返回的对象集合，不应被当作最终能力效果。它们可以作为当前证据实现，但不应成为长期唯一断言。
- **[后续设计建议]** 更换长期记忆实现、上下文压缩算法、存储后端或 Checkpoint 序列化后，以下行为合同仍应成立：有效信息影响结果、无效信息不污染结果、当前请求完整、授权前无副作用、恢复不重复成功副作用、最终状态与用户确认一致。绑定当前类结构和内部字段集合的断言则可能失效。

### 13.2 每案六门禁应如何围绕唯一目标闭环

这是**[后续设计建议]**，不是当前代码已实现的判定器。每条案例都应先写一句唯一目标，再让六门禁分别回答：

1. **预算：** 为该目标实际走了哪些必要节点，是否出现不必要能力；
2. **校验：** 目标行为是否成立，而非只看调用成功；
3. **产物：** 应有的回答、工件或最终资源是什么，内容不变量是什么；
4. **停止原因：** 为什么在此状态结束，是否应该等待、拒绝、完成或安全失败；
5. **安全：** 不该发生的调用和副作用是否动态为零，授权对象是否精确；
6. **证据：** 输入、调用、工件、状态差异和最终回答能否首尾相连。

六类门禁可以共享同一底层证据，但不能重复声明同一个布尔值后冒充六项独立证明。

## 14. 重复项、低价值项与名称不符项

### 14.1 看似相近但不应合并

| 组合 | 是否重复 | 审计判断 |
|---|---|---|
| 第 2—6 条检索/RAG | 当前校验高度相似，但目标不完全重复 | 分别涉及正文、结构覆盖、知识召回、目录身份链和外部来源。按既定结论暂作占坑，等待 Agentic RAG、向量检索和 Graph RAG 边界稳定 |
| 第 8 与第 10 条 | 否 | 第 8 条应测共同上游后的不同分析分支；第 10 条应测同一候选稿的同构独立审查 |
| 第 9 与第 11 条 | 否 | 第 9 条是正向生产流水线；第 11 条是消费反馈、修复目标且保持非目标内容 |
| 第 12、13、18 条 | 否，前提是按名称真正实现 | 分别是预览无副作用、批准后应用同一预览、拒绝后零写入；三者构成授权状态机的互补分支 |
| 第 14 与第 16 条 | 否 | 结构资源与 Mongo 知识卡有不同身份、版本、schema 和持久化不变量 |
| 第 19—22 条 | 否 | ACTIVE、STALE、REJECTED、SUPERSEDED 是四种独立失效模式；当前只是共同退化为对象集合检查 |

### 14.2 真正的低价值或重复执行形态

- **[代码事实]** 第 1、17、18、19—22 条的冻结计划都可以是 `nodes=[]` 的单次编排回答：`suite.json:42-43,314-315,324-325,334-335,344-345,354-355,364-365`。
- **[基于事实的推断]** 其中只有第 1 条的“零 Tool/Subagent”正是目标行为。第 17、18、19—22 条的零调用只是一种预写脚本结果，不能替代各自声明的权限、记忆或隔离能力。
- **[代码事实]** 第 17 条没有外部 Tool exposure，也没有触发 Executor 的 `_external_grant()`：`suite.json:309-316`、`src/taichu/application/general_agent/executor.py:832-847`。
- **[后续设计建议]** 第 17 条当前是最低价值项：它既没有进入真实强制权限边界，也没有独立困难链路；建议从核心套件移除并由上下文治理案例替换。未来若保留“外部访问拒绝”主题，应另建能实际计划外部节点、再由 Runtime 在 Backend 调用前阻断的安全案例。

### 14.3 名称与真实行为不一致

| Benchmark | 名称/描述承诺 | 当前真实行为 | 判断 |
|---|---|---|---|
| `summary_world_character` | 摘要后世界观与人物并行分析 | 人物依赖世界观，形成串行链 | **严重不一致，重做** |
| `write_authorization_denied` | Preview→请求授权→用户拒绝→恢复结束 | 一次直接拒绝，无 Preview/Human/resume | **严重不一致，重做** |
| `memory_rejected_parallel_isolation` | 被拒绝记忆在并行分支中隔离 | 无节点、无分支、无 Subagent | **严重不一致，重做** |
| `memory_stale_dependency` | 过期依赖被识别并拒绝 | 种子 `dependencies=[]`，只测 STALE 投影 | **部分不一致，保留但增强或改准名称** |
| `manuscript_patch_authorized_resume` | 授权后恢复 | 创建 continuation run 并重建授权节点，不是同 `run_id` 整图恢复 | **“恢复”语义有歧义，应按逻辑任务续接理解** |

### 14.4 只检查成功、预设真值与最终产物未检查

- **[代码事实]** 23 条都没有由 `AuthoredCaseSpec` 表达的最终业务产物字段：`suite_loader.py:50-64`。
- **[代码事实]** 第 7—16 条虽然真实生成工件或写入资源，通用 `normalized_result` 只保留状态、节点状态和成功 Effect Tool 名称：`live_runtime.py:541-558`；门禁没有读取工件正文或最终资源。
- **[代码事实]** 第 7 条固定 Canon 输出可以 `evidence=[]`、`source_refs=[]`、`confidence=high` 仍通过：`suite.json:138-141`。
- **[代码事实]** 第 8—11、19—22 条的固定模型文本本身可以成为预设真值，而当前门禁不比较目标语义：`suite.json:145-220,329-366`、`synthetic_environment.py:247-258,774-875`。
- **[基于事实的推断]** 除第 23 条的专属恢复 proof 和第 14/16 条“真实返回值用于下一轮构造”外，大多数场景都存在“调用成功代替效果”“预设真值代替动态验证”或“最终产物没有真正检查”中的至少一种。

## 15. 当前完全缺失或只局部覆盖的核心 Runtime 能力

| 能力 | 当前覆盖程度 | 代码事实与判断 |
|---|---|---|
| 上下文预算、裁剪、压缩、事实保持 | **完全缺失正式 Benchmark** | Runtime 已有策略与单测，但 23 条无长输入和 `compression_stats` 断言：`context.py:54-80,419-653`、`models.py:457-495` |
| 完整写授权拒绝状态机 | **名称存在，实际缺失** | 第 18 条未触达 `_continue_write_authorization()` 的拒绝分支：`suite.json:319-325`、`service.py:631-669` |
| 多故障点恢复矩阵 | **仅一个验证阶段窗口** | 当前故障注入只覆盖 Tool 已成功、首次 verify 前：`synthetic_environment.py:419-527` |
| 写副作用幂等恢复 | **Runtime 有机制，正式套件缺失** | Effect ID、reconciliation 与“写后、成功 Effect 前”故障点存在：`executor.py:557-710,1097-1102` |
| 真实重规划与修复效果 | **缺失** | Runtime 有 execution issue 与 verifier `should_replan` 路径：`service.py:973-1062`；套件没有注入错误后证明修复 |
| Tool/Subagent 失败、超时、重试和取消 | **缺失** | 当前 30 条必需调用均期望 completed；没有独立负例合同。是否所有异常类别均可安全恢复，当前证据不足 |
| Human-in-the-loop 澄清 | **缺失** | 当前 Human steps 只用于写授权/二次确认，没有“信息不足→提问→回答→续接同一任务”场景 |
| 分支消息作用域隔离 | **只有名称意图，未验证** | 第 10 条无状态污染证据，第 21 条实际无分支 |
| 真实长期记忆层 | **未覆盖且当前组装为空** | `long_term_memory=[]`：`context.py:451-469`；第 19—22 条是运行工作记忆 |
| Checkpoint 损坏与版本不兼容 | **实现有检查，Benchmark 缺失** | saver 检查格式、revision、完整性并隔离损坏修订：`src/taichu/infrastructure/general_agent_runs/langgraph_checkpoint.py:157-207,224-240,435-492,550-563` |
| 最终答案语义、引用真实性和内容保持 | **横向缺失** | 六门禁未读取最终回答与来源/工件/受保护文本的对应关系 |

**[基于事实的推断]** 这些缺口不是要求把每个内部功能都变成一条题，而是它们分别对应容易退化、难由其他场景替代的 Runtime 失败模式：上下文丢事实、授权拒绝后误写、恢复后重复副作用、错误修复无效、跨分支污染和损坏 Checkpoint 静默重跑。

## 16. Checkpoint、中断与恢复专项设计审计

### 16.1 当前 Runtime 生命周期中的可注入阶段

- 外层 LangGraph 由 initialize、plan、execute_dag、verify/replan 组成：`service.py:820-847`。
- 规划与重规划上下文组装、计划生成和 Checkpoint 位于 `service.py:873-943`。
- DAG 执行前后与状态路由位于 `service.py:949-971`。
- 校验、执行问题修复和 verifier 触发重规划位于 `service.py:973-1062`。
- 节点等待授权时，Executor 冻结 resolved input、创建 Human 请求并把节点置为 `WAITING_HUMAN`：`executor.py:435-480`。
- 写 Effect 具有确定性 effect ID，并在“写已发生、成功 Effect 尚未落盘”处提供故障注入点：`executor.py:557-710,1097-1102`。
- `recover_interrupted()` 只扫描 `_ACTIVE_STATUSES`：`service.py:72-78,554-566`；`WAITING_HUMAN` 由正常 `resume()` 续接而非此方法扫描。把二者统称“同一种恢复”并不准确。

### 16.2 候选恢复场景组

以下均为**[后续设计建议]**。每行是独立故障窗口，不是要求绑定当前 JSON 文件结构。

| 候选 ID | 故障注入阶段 | 已成功节点不重跑 | 副作用不重复 | 工作记忆/计划一致 | 中间产物复用 | 安全失败而非静默重跑 |
|---|---|---|---|---|---|---|
| `recovery_after_plan_before_execution` | 计划已持久化、首节点未开始 | 检查规划模型不重复消费或明确允许的幂等边界 | 无副作用 | 必须恢复同一计划 revision | 复用计划 | Checkpoint 不足时明确失败 |
| `recovery_tool_result_before_consumption` | 只读 Tool 完成、下游尚未消费 | Tool 调用保持一次 | 不适用写副作用 | 下游看到原 Tool 结果 | 复用 Tool output/工件 | 缺失结果时不得假装已完成 |
| `recovery_subagent_interrupted` | Subagent 内部模型调用或工件写入过程中 | 已完成上游不重跑 | 不生成重复工件身份 | 父/子 Agent 作用域一致 | 完整工件才可复用 | 半成品必须被识别而非消费 |
| `recovery_waiting_authorization` | Human 请求已发出、尚未决定 | Preview 不重跑 | 授权前 Apply 为零 | 请求摘要、计划和目标资源一致 | 复用已确认 Preview | 过期/不匹配决定必须拒绝 |
| `recovery_after_write_before_effect_success` | 写入已发生、Effect 成功状态未落盘 | 写节点不得再次产生同一副作用 | 核心断言：精确一次 | Effect 与节点状态协调 | 复用已写结果或做确定性 reconciliation | 无法判定时停在人工/安全失败 |
| `recovery_verification_interruption` | 当前第 23 条的首次 verify 前 | 已成功结构读取保持一次 | 无写副作用 | 同 run 图状态一致 | 复用只读结果 | Checkpoint 无效时不得从头伪装恢复 |
| `recovery_multiple_interruptions` | 同一运行在两个不同阶段连续中断 | 每个已成功节点仍各一次 | 所有 Effect 精确一次 | revision 单调且计划不漂移 | 跨两次恢复复用 | 超过恢复策略时明确终止 |
| `recovery_checkpoint_integrity_or_version` | 最新修订损坏、thread 不匹配或格式不兼容 | 不允许无证据重跑 | 不允许通过重跑掩盖副作用不确定性 | 只从明确有效 revision 恢复 | 有效旧工件可复用 | 无可用 revision 时明确不可恢复 |

### 16.3 当前实现能与不能支持的判断

- **[代码事实]** `JsonLangGraphCheckpointSaver` 保存 revision、检查 `format_version`、thread/revision 一致性，并可隔离损坏修订：`langgraph_checkpoint.py:86-142,157-207,312-343,435-492,550-563`。
- **[代码事实]** 第 23 条已经动态证明 `recovery_verification_interruption` 的一个窄版本：同 run、一次 recover、verify 两次、结构读取 1→1：`synthetic_environment.py:883-918`。
- **[基于事实的推断]** Runtime 已具备继续扩展恢复场景的基础证据面，但“Subagent 半成品如何识别”“不兼容 Checkpoint 是否总能安全失败”“写后未落 Effect 时所有 Tool 均可正确 reconciliation”不能仅从现有第 23 条推出；这些结论均为**当前证据不足**。
- **[后续设计建议]** 用恢复场景组替换单一第 23 条口径；当前场景保留为组内一条，不再让一次通过代表整个 Checkpoint 生命周期。

## 17. 上下文治理专项设计审计

### 17.1 当前代码已经有什么

- **[代码事实]** `GeneralAgentContextPolicy` 设总字符预算 180,000，并分别限制工作记忆、长期记忆、历史记忆、节点摘要和计划摘要：`context.py:54-80`。
- **[代码事实]** 上下文组装先决定是否压缩，再按长期记忆→历史摘要/原文→工作记忆节点结果→计划摘要等顺序裁剪：`context.py:419-498,501-653`。
- **[代码事实]** 稳定记忆和当前请求不在可裁剪列表；总量仍超限时抛 `ContextAssemblyError`：`context.py:473-498,656-658`。异常进入 Runtime 的失败/Checkpoint 处理，但当前代码没有形成专门的用户可见“安全压缩拒绝”合同：`service.py:808-818`。
- **[代码事实]** 历史摘要是确定性的字符截取，每条最多取固定前缀，不是模型语义摘要：`context.py:731-789`。
- **[代码事实]** 大型节点输出会被结构化概览；再超限时进一步降为顶层结构信息：`context.py:795-865`。
- **[代码事实]** Runtime 保存压缩标记、fallback、字符/Token 估算与各层遗漏统计：`models.py:415-495`、`service.py:1189-1224`。
- **[代码事实]** 正式套件没有任何案例读取上述统计，也没有构造上下文超限输入。预算门禁只读每案资源计数：`suite_loader.py:50-64`、`synthetic_environment.py:223-246`。

### 17.2 候选上下文场景

以下是**[后续设计建议]**，只定义行为，不绑定现有裁剪函数或阈值常量。

| 候选 ID | 输入压力 | 期望行为与最终产物 | 唯一失败模式 |
|---|---|---|---|
| `context_long_history_fact_retention` | 大量历史消息，早期含仍有效作者约束，近期含原始对话 | 触发摘要/裁剪后，当前回答仍遵守早期关键约束，近期消息与当前请求保持原意 | 关键历史事实被摘要或截断丢失 |
| `context_long_working_memory_priority` | 大量计划、工具结果、错误、待办和有效工作记忆 | 低优先级过程先退出；当前有效指令、未解决问题、直接依赖结果仍支撑任务完成 | 错误裁剪必要工作状态 |
| `context_large_node_output_projection` | 单个 Tool 返回超大结构化结果 | 下游读取合同所需字段、计数与来源；未展示条目不得被当作不存在 | 结构投影破坏语义或产生假阴性 |
| `context_multi_source_overflow` | 历史、工作记忆、检索结果、节点工件与当前请求共同超限 | 实际保留优先级符合五层边界，任务仍完成且关键事实未丢失 | 各来源竞争时裁剪顺序错误 |
| `context_compression_result_equivalence` | 同一任务的正常版与加入大量无关信息的压力版 | 两版关键事实结论和能力调用合同等价 | 压缩改变任务语义或执行路径 |
| `context_invalid_memory_pressure_isolation` | STALE/REJECTED/SUPERSEDED 记忆与大量有效内容共同施压 | 无效记忆优先退出且不得经摘要/fallback复活 | 压缩绕过记忆有效性边界 |
| `context_long_current_request_preserved` | 接近可接受上限的长当前请求 | 用户原文逐字保持，Runtime 仍能完成必要 Tool/Subagent 链 | 当前请求被截断、摘要或与系统说明混写 |
| `context_unsafe_compression_refusal` | 仅稳定记忆与当前请求已无法安全容纳 | 在任何 Tool/Subagent 和副作用前明确失败，保存不可安全组装原因 | 静默截断、错误执行或带不完整请求调用能力 |

### 17.3 为什么不能只加一条

- 长历史、长工作记忆和长当前请求分别受不同不可变边界约束；
- 单节点大输出和多来源共同超限分别测试结构化投影与全局优先级；
- “压缩后仍完成”和“压缩前后语义等价”不是同一个失败模式；
- 无效记忆在预算压力下复活是记忆治理与上下文治理的交叉缺陷；
- 无法安全容纳时拒绝，与正常压缩成功互为正反边界。

因此第 17 条可以优先由 `context_multi_source_overflow` 替换，但其余七条不应被“为了保持 23 条”压缩掉。

## 18. 建议状态总表

以下均为**[后续设计建议]**；不代表本轮已经修改套件。

| 建议状态 | 当前正式 Benchmark | 判断依据 |
|---|---|---|
| 保留 | `direct_answer_current_request` | 唯一目标清晰：简单请求不被强迫进入 Tool/Subagent 长链 |
| 暂时占坑 | `single_manuscript_search`、`structure_coverage_read`、`single_knowledge_retrieval`、`knowledge_catalog_identity_read`、`external_research_grounded` | 检索体系将引入 Agentic RAG、向量检索和 Graph RAG；本轮不提前冻结具体实现合同 |
| 保留但增强 | `single_canon_evidence`、`architecture_scene_draft`、`parallel_review_triad`、`revision_from_reviews`、`manuscript_preview_only`、`manuscript_patch_authorized_resume`、`structure_create_update`、`structure_delete_second_confirmation`、`knowledge_create_update`、`memory_active_projection`、`memory_stale_dependency`、`memory_superseded_repair` | 主失败模式独立且有真实链路基础，但需把调用/对象集合代理提升为最终行为或状态断言 |
| 重做 | `summary_world_character`、`write_authorization_denied`、`memory_rejected_parallel_isolation` | 名称所承诺的分支、拒绝或隔离流程没有发生 |
| 删除当前合同并替换 | `external_access_denied` | 当前只是空 exposure 下预写直接拒绝，不触达 Runtime 强制权限边界；由上下文治理案例优先替换 |
| 替换为场景组 | `runtime_checkpoint_recovery` | 当前场景本身保留为组内一条，但不能代表完整恢复生命周期 |
| 纯删除且不补位 | 无 | 除第 17 条的当前低价值合同外，其他相近案例仍有独立失败模式 |

### 18.1 每条现有 Benchmark 的一句话建议目标

| # | Benchmark | 建议后的唯一目标断言 |
|---:|---|---|
| 1 | `direct_answer_current_request` | 简单请求必须直接完成，且不调用任何不必要 Tool/Subagent |
| 2 | `single_manuscript_search` | 占坑：一次正文检索必须返回与指定章节身份和查询相关的可用片段 |
| 3 | `structure_coverage_read` | 占坑：结构、覆盖和正文三类来源必须共同支撑跨章节覆盖判断 |
| 4 | `single_knowledge_retrieval` | 占坑：只召回当前请求相关且可用的确认态知识 |
| 5 | `knowledge_catalog_identity_read` | 占坑：目录候选必须解析为真实知识卡身份并读取正确卡内容 |
| 6 | `external_research_grounded` | 占坑：外研结论必须来自允许访问的固定来源并保持引用和不确定性边界 |
| 7 | `single_canon_evidence` | Canon 工件区分有来源事实与推测，最终回答只能使用被证据支持的结论 |
| 8 | `summary_world_character` | 同一摘要工件分别进入世界观与人物两个互不依赖分支，并共同进入最终综合 |
| 9 | `architecture_scene_draft` | 架构约束场景、场景约束草稿，且只生成候选稿不写回正文 |
| 10 | `parallel_review_triad` | 三个审查分支消费同一候选稿、互不消费彼此结果并分别产出可引用工件 |
| 11 | `revision_from_reviews` | 修订稿消费指定审查、修复目标问题、保持受保护内容并引用审查来源 |
| 12 | `manuscript_preview_only` | 预览生成可确认补丁，正文前后字节和哈希完全一致、无写 Effect，并停在正确确认边界 |
| 13 | `manuscript_patch_authorized_resume` | 授权前正文不变，批准后从同一逻辑计划授权节点续接且只应用用户确认的预览 |
| 14 | `structure_create_update` | 更新使用创建真实返回的 ID/版本，且最终只修改目标结构项 |
| 15 | `structure_delete_second_confirmation` | 高风险删除必须经二次确认，只删除批准的目标，取消分支零副作用 |
| 16 | `knowledge_create_update` | 更新使用新建卡真实 ID/并发版本，最终目标卡字段正确且其他卡不变 |
| 17 | `external_access_denied` | 当前合同删除；若未来重建，必须实际计划外部节点并由 Runtime 在 Backend 调用前阻断 |
| 18 | `write_authorization_denied` | Preview 后用户拒绝，Apply 为零、正文不变、Runtime 正确结束并留下拒绝证据 |
| 19 | `memory_active_projection` | 只有有效运行工作记忆中存在的约束必须可观察地改变最终答案 |
| 20 | `memory_stale_dependency` | 高相关但过期的记忆及其失效依赖不得限制最终任务结果 |
| 21 | `memory_rejected_parallel_isolation` | 被拒绝错误记忆不得在任何独立分支、子 Agent 输出或最终聚合中复活 |
| 22 | `memory_superseded_repair` | 新有效结论替代旧结论后，最终答案只采用最新有效结论 |
| 23 | `runtime_checkpoint_recovery` | 当前场景降为恢复组一员：校验阶段中断后恢复同一运行且不重跑成功读取 |

## 19. 调整后候选 Benchmark 总清单

### 19.1 总数

**[后续设计建议]** 候选总数为 **37 条**：

- 保留或调整现有第 1—16、18—22 条，共 21 条；
- 删除当前第 17 条合同；
- 将当前第 23 条替换为 8 条恢复场景，其中保留其原始验证中断场景；
- 新增 8 条上下文治理场景。

计算：`23 - 1（删除第17条）- 1（单一第23条）+ 8（恢复组）+ 8（上下文组）= 37`。

这个总数来自独立失败模式，不是新的固定产品承诺。第 2—6 条仍明确标为占坑；未来检索架构稳定后，可能合并、拆分或替换。

### 19.2 37 条候选及一句话唯一目标

| 候选序号 | 候选 ID | 来源/状态 | 一句话唯一目标 |
|---:|---|---|---|
| 1 | `direct_answer_current_request` | 原1，保留 | 简单请求直接完成且零不必要能力调用 |
| 2 | `single_manuscript_search` | 原2，占坑 | 单次正文召回返回指定范围内的相关可用片段 |
| 3 | `structure_coverage_read` | 原3，占坑 | 多源读取共同支撑跨章节覆盖判断 |
| 4 | `single_knowledge_retrieval` | 原4，占坑 | 只召回相关且有效的确认态知识 |
| 5 | `knowledge_catalog_identity_read` | 原5，占坑 | 目录候选解析为真实身份并读取正确知识卡 |
| 6 | `external_research_grounded` | 原6，占坑 | 外研结论来自允许来源并保留引用与不确定性 |
| 7 | `single_canon_evidence` | 原7，增强 | 证据生成器划清事实/推测边界，最终回答真实消费证据 |
| 8 | `summary_world_character` | 原8，重做 | 共同摘要进入两个独立分析分支并被最终综合 |
| 9 | `architecture_scene_draft` | 原9，增强 | 架构→场景→草稿真实交接且不写回正文 |
| 10 | `parallel_review_triad` | 原10，增强 | 三审同源、无依赖、互不污染并留下三份工件 |
| 11 | `revision_from_reviews` | 原11，增强 | 修订消费审查、修复目标问题且保持非目标内容 |
| 12 | `manuscript_preview_only` | 原12，增强 | Preview 前后正文完全不变、预览可确认且停在正确确认边界 |
| 13 | `manuscript_patch_authorized_resume` | 原13，增强 | 批准后从同一逻辑计划授权节点续接且只应用用户确认过的预览 |
| 14 | `structure_create_update` | 原14，增强 | 真实创建返回值驱动精确更新且不误改其他对象 |
| 15 | `structure_delete_second_confirmation` | 原15，增强 | 二次确认只删除批准目标，取消时零副作用 |
| 16 | `knowledge_create_update` | 原16，增强 | 真实卡身份和并发版本驱动精确更新 |
| 17 | `write_authorization_denied` | 原18，重做 | 用户拒绝后零写入、正文不变、正确结束且证据完整 |
| 18 | `memory_active_projection` | 原19，增强 | 有效运行工作记忆真实影响最终答案 |
| 19 | `memory_stale_dependency` | 原20，增强 | 过期记忆及依赖不得限制当前任务 |
| 20 | `memory_rejected_parallel_isolation` | 原21，重做 | 被拒绝记忆不在任何并行分支和聚合中复活 |
| 21 | `memory_superseded_repair` | 原22，增强 | 新旧冲突时最终答案只采用最新有效结论 |
| 22 | `recovery_after_plan_before_execution` | 恢复组新增 | 规划后中断恢复同一计划且不重复规划副作用 |
| 23 | `recovery_tool_result_before_consumption` | 恢复组新增 | Tool 结果已产生但未消费时，恢复复用原结果且 Tool 不重跑 |
| 24 | `recovery_subagent_interrupted` | 恢复组新增 | Subagent 中断不产生可消费半成品，已成功上游不重跑 |
| 25 | `recovery_waiting_authorization` | 恢复组新增 | 授权等待中恢复保持同一请求摘要、预览和目标资源 |
| 26 | `recovery_after_write_before_effect_success` | 恢复组新增 | 写入后 Effect 未完成时恢复保证副作用精确一次 |
| 27 | `recovery_verification_interruption` | 原23细化 | 校验中断后同 run 恢复且成功只读节点保持一次 |
| 28 | `recovery_multiple_interruptions` | 恢复组新增 | 多次中断后成功节点和副作用仍不重复、计划不漂移 |
| 29 | `recovery_checkpoint_integrity_or_version` | 恢复组新增 | 损坏或不兼容 Checkpoint 只能从有效修订恢复，否则安全失败 |
| 30 | `context_long_history_fact_retention` | 上下文新增 | 长历史压缩后关键作者约束仍影响答案 |
| 31 | `context_long_working_memory_priority` | 上下文新增 | 工作记忆超限时按任务必要性裁剪且任务仍完成 |
| 32 | `context_large_node_output_projection` | 上下文新增 | 大节点结果投影后关键字段、数量和来源仍可用 |
| 33 | `context_multi_source_overflow` | 替换原17优先项 | 多来源共同超限时五层优先级正确且关键事实不丢失 |
| 34 | `context_compression_result_equivalence` | 上下文新增 | 正常版与压缩压力版的关键结论和能力合同等价 |
| 35 | `context_invalid_memory_pressure_isolation` | 上下文新增 | 无效记忆在压缩、摘要和 fallback 中均不得复活 |
| 36 | `context_long_current_request_preserved` | 上下文新增 | 长当前请求逐字保持并完成必要执行链 |
| 37 | `context_unsafe_compression_refusal` | 上下文新增 | 无法安全容纳时在任何能力调用和副作用前明确失败 |

## 20. 最终判断：当前 23/23 的精确业务含义

### 20.1 能证明

1. **[代码事实]** 当前权威套件确有且仅有 23 条，顺序与 ID 被加载器、能力目录和测试共同冻结：`suite.json:10-34`、`suite_loader.py:100-125`、`capability_catalog.py:101-137`、`test_capability_coverage.py:18-59`。
2. **[代码事实]** 在密封 Fixture、固定 synthetic 模型响应和真实生产 Runtime/Tool/Subagent 组合下，23 条均可完成严格脚本：`project_assets/derived/general_agent_benchmarks/indexes/synthetic-passed-baseline.json:1`、`runtime_factory.py:149-293`、`fixture_manager.py:82-162`、`synthetic_suite.py:156-234`。
3. **[代码事实]** 必需能力的名称、类型、调用次数和 completed outcome 满足当前 invocation 合同，没有观察到未声明生产能力调用：`synthetic_suite.py:198-234,317-362`、`project_assets/derived/general_agent_benchmarks/runs/synthetic_baseline_b2b8f47d486d2ebba46c366b61df807e.json:1`。
4. **[代码事实]** 当前统一资源观察没有越过每案预算上限；这只代表本次基线未越界：`synthetic_environment.py:218-246,774-821`、`project_assets/derived/general_agent_benchmarks/runs/synthetic_baseline_b2b8f47d486d2ebba46c366b61df807e.json:1`。
5. **[代码事实]** 四类 Runtime 工作记忆 validity 满足当前 current/repair 投影集合规则：`synthetic_environment.py:920-974`、`project_assets/derived/general_agent_benchmarks/runs/synthetic_baseline_b2b8f47d486d2ebba46c366b61df807e.json:1`。
6. **[代码事实]** 在一个特定校验故障点，Checkpoint 可恢复同一 run，结构读取保持一次并最终完成：`synthetic_environment.py:419-527,883-918`、`project_assets/derived/general_agent_benchmarks/runs/synthetic_baseline_b2b8f47d486d2ebba46c366b61df807e.json:1`。
7. **[基于事实的推断]** 这足以支持“核心 Harness 可运行、生产能力注册与基础 Runtime 链路在线”的研发结论。

### 20.2 不能证明

1. **[基于事实的推断]** 不能证明通用写作 Agent 的总体写作质量或模型排行；这也不是本套件定位。
2. **[基于事实的推断]** 不能证明第 2—6 条未来 RAG 体系的检索正确率、召回完整性或 Graph/Vector/Agentic RAG 效果。
3. **[基于事实的推断]** 不能证明 Canon、外研、审查和修订结果具有真实来源—结论—回答闭环。
4. **[基于事实的推断]** 不能证明第 8 条共同上游分支、第 10 条物理并发与无污染、第 21 条记忆跨分支隔离。
5. **[基于事实的推断]** 不能证明预览、授权、拒绝、创建、更新和删除后的最终持久化状态全部正确；当前门禁不重读最终资源。
6. **[基于事实的推断]** 不能证明安全门禁动态成立，因为当前 `security_ok=True` 是调用方预设。
7. **[基于事实的推断]** 不能证明产物和证据完整，因为两者主要只检查交互/usage 是否存在。
8. **[基于事实的推断]** 不能证明长期记忆能力；当前第 19—22 条是运行工作记忆，长期记忆投影为空。
9. **[基于事实的推断]** 不能证明长上下文裁剪、压缩、事实保持、优先级和无法安全压缩时的产品行为。
10. **[基于事实的推断]** 不能证明完整恢复生命周期、写副作用精确一次、损坏 Checkpoint 安全失败或多次中断一致性。

### 20.3 研发决策口径

**[基于事实的推断]** 当前 23/23 最合适的内部表述是：

> 在已冻结的合成小说 Fixture 和模型脚本下，太初真实 Agent Runtime 能按当前 23 条调用合同完成生产 Tool/Subagent 链，统一门禁未阻断；这证明 Harness 与一组基础运行机制可执行，不等于 23 个场景的最终行为效果均已被完整验证。

不宜表述为：

> “通用 Agent 所有 Runtime 能力、授权安全、记忆、上下文和恢复都已被 23/23 全面证明。”

## 21. 可追溯证据索引

| 审计主题 | 具体文件、类/函数与行号 |
|---|---|
| 正式 23 条、输入、脚本与顺序 | `tests/fixtures/evaluations/general_writing_agent_benchmark/suite.json:1-383` |
| 套件加载、唯一性、预算与 Fixture 哈希 | `src/taichu/application/evaluations/general_agent_benchmark/suite_loader.py:30-64,100-138`，`AuthoredCaseSpec`、`AuthoredSuiteSpec._case_order_is_exact()`、`load_authored_suite()`、`load_fixture_manifest()` |
| 正式轨道和能力目录 | `src/taichu/application/evaluations/general_agent_benchmark/capability_catalog.py:101-137`，`_CASE_IDS`、`CORE_CASES` |
| 六类门禁枚举、判定 | `src/taichu/application/evaluations/general_agent_benchmark/models.py:475-516`，`GateKind`；`src/taichu/application/evaluations/general_agent_benchmark/gates.py:51-74`，`evaluate_case_gates()` |
| 调用合同校验 | `src/taichu/application/evaluations/general_agent_benchmark/synthetic_suite.py:317-362`；`src/taichu/infrastructure/evaluations/general_agent_benchmark/live_runtime.py:817-852,921-985` |
| 严格脚本和并行组语义 | `src/taichu/application/evaluations/general_agent_benchmark/strict_driver.py:190-205,210-349`，`StrictScriptedDriver` |
| synthetic 环境与六门禁入参 | `src/taichu/infrastructure/evaluations/general_agent_benchmark/synthetic_environment.py:111-174,218-270,277-405,419-527,633-771,774-974` |
| Fixture 密封、逐案复制、写边界与清理 | `src/taichu/infrastructure/evaluations/general_agent_benchmark/fixture_manager.py:82-162`，`FixtureIsolationController.verify_sealed_source()`、`create_workspace()`、`assert_write_allowed()`、`cleanup_workspace()` |
| live 模型、自动 Human、实际 runner | `src/taichu/infrastructure/evaluations/general_agent_benchmark/live_runtime.py:123-202,408-558,568-679,817-852,874-985` |
| 真实 Runtime/Tool/Subagent 注册 | `src/taichu/infrastructure/evaluations/general_agent_benchmark/runtime_factory.py:149-293` |
| 外层 Runtime 生命周期与授权续接 | `src/taichu/application/general_agent/service.py:554-566,631-722,820-847,873-1062`，`GeneralAgentRuntimeService.recover_interrupted()`、`_continue_write_authorization()`、`_build_graph()`、`_verify_node()` |
| DAG、授权请求、工件绑定与 Effect | `src/taichu/application/general_agent/executor.py:275-295,435-480,557-710,929-974,1097-1102`，`DynamicDagExecutor._build_graph()` |
| 五层上下文、裁剪、压缩与结构化投影 | `src/taichu/application/general_agent/context.py:54-80,172-315,389-498,501-653,731-865`，`GeneralAgentContextPolicy`、`ContextAssembler`、`ContextAssembler._trim_to_total_budget()` |
| 运行工作记忆实体 | `src/taichu/application/agent_memory/models.py:20-28`，`AgentMemoryKind`；`src/taichu/infrastructure/agent_memory/json_repository.py:20-43` |
| LangGraph Checkpoint 完整性与修订 | `src/taichu/infrastructure/general_agent_runs/langgraph_checkpoint.py:23-142,157-240,312-343,435-492,550-563`，`JsonLangGraphCheckpointSaver` |
| Subagent 工件读取与类型合同 | `src/taichu/application/subagents/runner.py:238-271`；`src/taichu/application/subagents/registry.py:76-153` |
| 正文 Preview/Apply | `src/taichu/application/tools/preview_manuscript_patch.py:24-66`；`src/taichu/application/tools/apply_manuscript_patch.py:31-76` |
| 结构与知识写入 | `src/taichu/application/tools/create_novel_structure_items.py:33-124`；`update_novel_structure.py:35-171`；`delete_novel_structure_items.py:35-116`；`create_confirmed_knowledge.py:26-57`；`update_confirmed_knowledge.py:26-61` |
| Fixture 清单与运行记忆种子 | `tests/fixtures/evaluations/general_writing_agent_benchmark/fixtures/core_novel/fixture-manifest.json:1-17`；`runtime_memory/seed.json:1-38` |
| 冻结合成基线 | `project_assets/derived/general_agent_benchmarks/indexes/synthetic-passed-baseline.json:1`；`runs/synthetic_baseline_b2b8f47d486d2ebba46c366b61df807e.json:1` |
| 清单与核心套件测试 | `tests/unit/application/evaluations/general_agent_benchmark/test_capability_coverage.py:18-59`；`tests/integration/infrastructure/evaluations/test_general_agent_benchmark_core_suite.py:32-45,92-105` |

## 22. 审计边界与资料状态

- 本报告的“当前代码事实”仅指 2026-07-30 工作区中实际读取到的仓库状态。
- “当前证据不足”不等于能力一定不存在，只表示当前 23 条合同、门禁或代码路径不能证明该结论。
- “后续设计建议”只给出候选行为合同，没有修改代码、Fixture、测试、前端、历史评测运行或冻结基线。
- 本报告保存于 `docs/旧历史/`，属于带日期的历史审计快照；未来实现变化后应重新读取源码和运行证据，不得把本报告当作永久代码事实源。
