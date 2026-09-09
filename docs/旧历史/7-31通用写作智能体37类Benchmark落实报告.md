# 通用写作智能体 37 类 Benchmark 落实报告

> 更新日期：2026-07-31  
> 资料状态：历史实现与验收快照；当前代码事实以权威 Suite、源码、测试和活动冻结工件为准。  
> 权威套件：`tests/fixtures/evaluations/general_writing_agent_benchmark/suite.json`

## 一、结果

通用写作智能体固定基准已经由历史 23 条升级为 Suite@2 的 37 条行为合同：

- Synthetic 轨道精确适用 37 条，活动冻结结果为 37/37；
- Live Provider 轨道精确适用前 21 条，不允许把恢复与上下文压力案例错误送入真实模型轨道；
- 每条案例都有唯一 ID、中文名称、中文摘要、固定输入、场景与夹具引用、期望终态、行为断言、六类证据要求和资源预算；
- 每条结果必须同时通过预算、校验、产物、停止原因、安全、证据六类门禁；
- 活动 37、上一版 37 和历史 23 可独立读取，历史内容不被当前套件回填或改写；
- 页面默认显示当前 37/37，历史运行仍显示自身的 23/23 或 37/37，用户界面不暴露内部运行 ID。

本次结果证明固定 Synthetic Harness、真实 Runtime 组合、生产 Tool/Subagent 合同、密封夹具、typed Oracle、六类门禁与证据链在当前身份下闭合。它不证明真实模型长期质量、线上真实流量效果、创作文风优劣或模型排名。

## 二、37 条覆盖边界

权威顺序与逐条字段只维护在 `suite.json`，本报告不复制第二份完整定义。当前覆盖由以下能力组构成：

1. 最小充分路由与直接回答；
2. 正文、结构、知识与外部资料检索占坑合同；
3. 设定证据生成及事实/推测边界；
4. 共同上游后的独立分析、流水线交接、并行审查与反馈修订；
5. 正文预览、授权恢复、拒绝、结构和知识持久化；
6. 有效、过期、被拒绝和被替代运行工作记忆；
7. 规划后、Tool 结果提交后、Subagent 中断、授权等待、写后 Effect 对账、校验中断、多次中断、Checkpoint 完整性/版本八类恢复场景；
8. 长历史、长工作记忆、大节点输出、多来源超限、压缩结果等价、无效记忆隔离、长当前请求、安全拒绝八类上下文压力。

第 2—6 条继续明确标记为检索/RAG 占坑合同，等待 Agentic RAG、向量检索和 Graph RAG 的正式能力合同稳定后再决定内部策略；其他案例使用密封结果隔离检索误差。

## 三、关键实现边界

### 3.1 权威套件与选择

- `suite_loader.AuthoredSuiteSpec` 强制 37 条 ID、顺序和中文名称；
- `SuiteSelectionValidator` 强制套件身份、轨道、顺序、重复项和适用范围；
- API 在创建运行前完成选择校验，非法选择返回 422 且不产生运行或工作区副作用；
- API 套件详情只返回顺序、中文名称、摘要和轨道，不返回固定脚本、Oracle 配置或敏感夹具。

### 3.2 真实 Runtime 观察与六门禁

- Synthetic 环境通过生产组合根执行 Runtime、Tool、Subagent、记忆、Checkpoint 和持久化服务；
- 结果由 typed observation 和 typed Oracle 形成行为断言；
- 协议或身份错误会使六类门禁全部进入 `invalid`，不再用统一完成态、交互存在、空证据、预设安全真值或普通案例默认成功冒充通过；
- CapabilityResult、Effect、恢复决定、上下文 AssemblyTrace、资源前后态和 Fixture 隔离均形成可寻址证据。

### 3.3 恢复与父生命周期

- Tool/Subagent 能力结果由 owner-aware CapabilityResult 保存和复用；
- 进程中断通过通用 Fault Hook 注入，生产 Runtime 不认识 Benchmark case ID；
- 写副作用使用 Effect 状态链对账，避免恢复时重复执行；
- 多次中断、损坏 Checkpoint 和版本不兼容均有独立失败模式；
- 案例结束后由父级生命周期统一密封、审计和清理工作区，异常路径同样销毁。

### 3.4 上下文压力

第 36 条曾在回归中暴露真实缺口：名称声称“长当前请求”，但权威原文只是短句。最终实现增加套件显式、确定性的长请求展开合同，加载后 `user_request_raw` 为 20,038 字符，包含换行、双空格和受保护关键事实；Runtime intake、上下文快照、模型可见投影和 Oracle 使用同一份原文身份，不再用压力层的另一份文本替代用户输入。

## 四、活动冻结身份

- Suite 内容哈希：`145c40a5b69ca64385dab0ffaa015b23669475d10d71f5590417687511b8e508`
- Fixture 快照哈希：`e7806a16e6a7431960f3d0cd597b4371dea1d2dbc3dc002094a6fad670a53182`
- 能力目录哈希：`90d404d3623069f9de9226a2abb49f3774b017c9ea300dafc78050bf4cea7a3a`
- Oracle 规则集哈希：`623c5dd6de9a225c9afd593b6f378573e4eb48aadb7942b78e1f1c8e9fd5984c`
- Runtime 代码快照哈希：`2507e854157f7a39a6046483dc902d71d96403490e3dd558c5184a4d60fd647f`
- 活动工件：`runs/synthetic_baseline_28a233df10c59e1e488637b3d9483805.json`
- 活动工件内容哈希：`812a83059d2c044866128a7bbac577fb4d329c9c32c25b7219259d2a968388f3`
- 工件清单哈希：`9447faa1db35510cc699a855010fde0945871286c15beacbb9e6bd5a3fe7cd2e`
- 基线目录哈希：`96750dc76955293c8789e0d471d0ab2dffc2e9a00f85f4fe9ce1b0e5d70c9a6b`
- 结果计数：总数 37、通过 37、失败 0、无效 0、未完成 0。

活动目录同时保留：

- 上一版 37：`runs/synthetic_baseline_5f74e6d9e5f793cd4be8b2414f018916.json`；
- 历史 23：`runs/synthetic_baseline_b2b8f47d486d2ebba46c366b61df807e.json`。

Hydration 状态为 `available`，当前 37、历史 37 与历史 23 均按自身身份读取，问题列表为空。

## 五、验证记录

### 5.1 后端与合同

执行：

```text
$benchmarkPaths = @(
  'tests/unit/application/evaluations/general_agent_benchmark',
  'tests/unit/infrastructure/evaluations/general_agent_benchmark',
  'tests/integration/api/test_general_agent_benchmarks_api.py'
)
$benchmarkPaths += Get-ChildItem tests/integration/infrastructure/evaluations `
  -Filter 'test_general_agent_benchmark_*.py'
uv run pytest -q @benchmarkPaths
```

最终聚焦回归结果：`471 passed`。

覆盖内容包括 37 条顺序和轨道、严格 Union、选择零副作用、ClaimCatalog/Oracle/Gate 正反例、CapabilityResult、父生命周期与异常清理、八类恢复、八类上下文压力、完整 Synthetic 双运行稳定性、历史 Hydration 和 API。

### 5.2 前端

执行并通过：

```text
npm run test:general-agent
npm run lint
npm run build
```

Next.js 生产构建成功，现有 `/task-monitor/general-agent/evaluation` 路由保持不变，没有新增依赖。

### 5.3 固定端口

以非交互方式两次执行根目录 `start.bat`，均成功：

- 后端：`http://127.0.0.1:8000`
- 前端：`http://localhost:3000`
- MongoDB、Qdrant、本地嵌入服务均复用；
- Suite API 返回 37 条、Synthetic 37、Live 21；
- 非适用 Live 案例提交返回 422，运行数前后不变；
- 桌面页面首屏显示当前 37/37，明细显示 37 条中文合同；
- 切换历史运行后显示其自身 23 条，不借用当前套件名称和摘要。

### 5.4 冻结

执行：

```text
uv run python scripts/run_general_agent_synthetic_baseline.py
```

正式冻结曾先后被案例观察缺失、合同哈希跨进程漂移和第 28 条具体 Checkpoint 修订号漂移阻断，活动指针均未错误切换。修复后连续两次完整运行均复用同一不可变工件 `synthetic_baseline_28a233df10c59e1e488637b3d9483805.json`，最终冻结 37/37。合同哈希现在直接基于规范化模型计算；恢复规范化只冻结“修订证据是否存在”的行为事实，具体修订号继续保留在底层审计证据中。

### 5.5 独立验证前补强

- 第 37 条现在真实进入生产 Runtime 的上下文组装入口；无法安全组装时在规划和能力调用前失败，保存 `unsafe_context` 结构化原因，并以不可恢复状态结束；
- 冻结证据证明该案例没有计划、节点、交互、CapabilityResult 或 Effect，页面只有在这些条件全部成立时才允许零脚本动作支撑总体结论；
- 活动运行排序不再按 `run_id` 哈希尾缀，而按创建/恢复新鲜度；当前活动 37 条基线始终位于最近运行首位；
- 固定端口 API 返回当前运行 `benchmark_run_19700101T000000Z_1022482c0955`，代码快照与活动工件中的快照哈希一致；
- 浏览器实测首屏为“整体能力门禁已通过、37/37 Benchmark 全部通过”；第 37 条六类门禁有据且技术明细默认折叠；历史第 1 次评测仍显示自身 23 条。

## 六、尚未由本基线证明的内容

- Live Provider 只获得前 21 条的运行资格，本报告没有把真实模型运行标记为 21/21；
- 第 2—6 条仍是检索/RAG 占坑合同，不代表未来向量、Graph RAG 或 Agentic RAG 的效果已完成；
- Synthetic 固定脚本不等于自然语言模型在长期、多样输入下稳定；
- 创作质量、文风、叙事一致性和引用真实性如需主观或语义裁判，仍需独立评测；
- 线上真实流量、机器资源波动、Provider 长期可用性和成本变化不在本次准入结论内。
