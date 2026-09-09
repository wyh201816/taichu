# 通用写作智能体 Opik 评测完整链路

## 面向谁，以及读完能得到什么

本文面向需要维护、复跑或在面试中解释太初通用写作智能体评测的人。读完后应能回答四个问题：评测案例从哪里来、如何真实执行、为什么能判定通过、Opik 与太初前端各自展示哪一部分证据。

核心判断是：**Opik 是评测数据集、实验、评分与 Trace 的可观测投影，不是评测事实的唯一来源，也不替代太初已有的确定性合同门禁。** 太初负责定义和执行合同，Opik 负责保存可复用 Dataset、组织批量 Experiment、聚合评分并呈现嵌套执行链路。

## 当前已经落地的规模与结果

权威 Suite 仍保存通用写作智能体的完整固定基准；面试展示只从中投影两组容易解释、与项目真实需求匹配的入口：

| 入口 | 场景分类 | 合同数 | Opik Dataset | 当前云端实验 |
| --- | ---: | ---: | --- | ---: |
| 多步骤组合任务 | 9 类 | 18 条 | `taichu-general-agent-multi-step` | 18/18 通过 |
| 异常中断恢复 | 4 类 | 8 条 | `taichu-general-agent-recovery` | 8/8 通过 |

18 条多步骤任务不是 18 个只换措辞的问答，而是 9 类任务各保留一个基础场景和一个约束变化场景：拆解小说大纲、生成人物与世界设定、分章节续写、修改前文设定、检索前文伏笔、改写章节、输出与证据合同校验、角色一致性校验、剧情逻辑闭环。

8 条恢复任务对应 Runtime 当前确实存在的 8 个持久化窗口：规划后执行前、工具结果消费前、子图中断、等待人工授权、写入后对账前、校验阶段、多次连续中断、检查点损坏或不可用。这个规模比“120 条任务、60 组中断”更符合单本玄幻小说助手的真实维护成本，也更容易逐条解释测试意图和失败原因。

## 一条结果如何产生

```mermaid
flowchart LR
    A[权威 Suite 合同] --> B[两个评测入口投影]
    B --> C[Opik Dataset 与稳定 item]
    C --> D[逐案例确定性执行]
    D --> E[固定门禁与八维评分]
    D --> F[嵌套 Trace 与 Span]
    E --> G[Opik Experiment]
    F --> G
    G --> H[后端只读校验与缓存]
    H --> I[太初两个评测页面]
```

对应源码的阅读顺序如下：

1. `tests/fixtures/evaluations/general_writing_agent_benchmark/suite.json`：权威案例合同。
2. `src/taichu/application/evaluations/general_agent_benchmark/portfolio.py`：把合同按 18 条和 8 条两个入口分组。
3. `src/taichu/infrastructure/evaluations/general_agent_benchmark/opik_integration.py`：同步 Dataset、设置追踪和提供命令行入口。
4. `src/taichu/infrastructure/evaluations/general_agent_benchmark/opik_evaluation.py`：逐案例执行 `opik.evaluate()` 并生成八维评分。
5. `src/taichu/infrastructure/evaluations/general_agent_benchmark/synthetic_environment.py`、`synthetic_runtime.py`、`recovery_harness.py`：执行任务、工具、专业智能体和恢复子图，并生成嵌套 Trace。
6. `src/taichu/infrastructure/evaluations/general_agent_benchmark/opik_query.py`：校验并读取云端结果。
7. `src/taichu/api/routes/general_agent_benchmarks.py`：通过 `/api/general-agent-benchmarks/opik/summary` 输出无密钥快照。
8. `web/src/components/agent-task-monitor/general-agent-evaluation-shell.tsx`：在两个评测入口显示 Dataset、Experiment、评分和 Trace 链接。

## 第一段：权威合同与两个展示入口

Suite 的每条案例不是只有一个问题和一个参考答案，而是同时声明：

- 用户请求原文与任务目标；
- 预期最终产物和允许的终止状态；
- 必须调用的 Tool 或专业智能体、调用次数和父子关系；
- 资源预算、授权边界与可恢复状态；
- 行为、产物、结束状态、安全和证据等固定断言。

`portfolio.py` 不复制或重新定义案例，只保存两个入口的案例 ID、分类和无效调用规则。因此类别名称可以面向使用者解释，但结论仍由同一份 Suite 合同产生。

这里所说的“无效工具调用”有四类可复验含义：调用合同未允许的能力、超过调用次数上限、违反顺序或父子依赖、工具结果没有被下游步骤或最终答案消费。它不是从“调用总数看起来很多”主观推断出来的。

## 第二段：Dataset 同步与版本身份

命令行同步时，每条合同被转换为一个 Opik Dataset item：

- `input` 保存案例 ID 和用户请求原文；
- `expected_output` 保存目标、产物、终态和行为断言；
- `trajectory_contract` 保存所需能力调用及无效调用规则；
- `metadata` 保存 Suite 哈希、入口、分类、案例名称和故障计划引用。

item ID 由 Dataset 名称与案例 ID 确定性生成 UUIDv7。同一逻辑案例重复同步会覆盖同一 item 身份，避免旧 Suite 残留混入评测；item 内容发生变化时，Opik 通过内容哈希形成新的 Dataset 版本。Dataset 当前版本、案例数和 Suite 内容哈希共同构成后续实验的身份边界。

## 第三段：逐案例执行，而不是伪造一张结果表

带 `--run-evaluations` 运行时，程序先同步 Dataset，再将本次入口的精确 item ID 列表交给 `opik.evaluate()`。每个 item 都通过 `SyntheticOpikBenchmarkTask` 进入太初既有的 `SyntheticSuiteRunner`，并使用隔离工作区执行一条真实合同。

当前这两组是确定性合成评测：它们使用可复现的模型响应绑定和真实能力执行器验证编排合同，不向外部大模型购买一次裁判调用。这使失败能稳定复现，也解释了为什么 Opik 的模型费用显示为“未计量”。它证明的是 Runtime、能力调用、恢复与证据合同，不等价于对某个真实模型文笔质量的排名。

## 第四段：Trace 如何形成树状链路

执行器使用 `@opik.track()` 记录不同层级：

- 根 Trace：单条“通用写作智能体评测案例”；
- 模型 Span：规划、重规划、校验等模型轮次；
- Tool Span：真实工具名称、输入、结果、耗时和状态；
- 专业智能体 Span：子图调用及其内部步骤；
- 恢复 Span：故障注入位置、检查点选择、恢复决策、幂等对账结果。

装饰器先建立父子关系，`update_current_span()` 再把本次真实名称、元数据和输出补进当前 Span。关闭 `OPIK_ENABLED` 时追踪是安全空操作，不会改变 Runtime 业务逻辑。

Trace 用于定位“哪一步慢、哪一次调用失败、工具结果有没有被消费、恢复后是否重跑了成功节点”。它不能单独证明业务正确，最终仍需和固定门禁评分及本地证据包一起看。

## 第五段：八维固定评分

评测不额外调用 LLM Judge，而是把太初已经计算完成的确定性结果投影为八个 0 到 1 的 Opik 分数：

1. 案例总合同；
2. 能力调用合同；
3. 门禁·资源预算；
4. 门禁·行为校验；
5. 门禁·结果产物；
6. 门禁·结束状态；
7. 门禁·安全边界；
8. 门禁·证据完整性。

每条案例都必须具有这八项分数；“案例总合同”为 1 才计入通过数。Experiment 页面显示的是所有 item 的平均值，点击具体 item 后才能看到该案例的分数和原因。

## 第六段：Experiment 身份与防旧状态机制

`opik.evaluate()` 每次运行创建新的 Experiment，并把 Dataset item、任务 Trace 和评分关联起来。Experiment 元数据写入 Suite 哈希、入口 ID 和完整案例 ID 列表。

太初前端不会轻信“最新一条看起来成功”的云端记录。服务端读取时必须同时满足：

- Experiment 状态为 `completed`；
- Suite 哈希、入口 ID、案例 ID 列表与当前代码完全一致；
- Experiment 使用 Dataset 当前版本；
- Dataset、Experiment item 与当前入口案例数一致；
- 案例 ID 唯一且集合完整；
- 每个 item 都有唯一 Trace 和八项固定评分；
- Opik 声明的 Trace 数量与实际 item Trace 数量一致。

任何一项不满足，接口返回“暂不可用”而不是展示陈旧分数。查询结果在服务端缓存 60 秒，浏览器永远不会拿到 `OPIK_API_KEY`。

## 第七段：太初前端如何使用结果

两个入口分别位于：

- `http://localhost:3000/task-monitor/general-agent/evaluation/multi-step`
- `http://localhost:3000/task-monitor/general-agent/evaluation/recovery`

页面原有合同明细回答“这条任务为什么通过”；新增 Opik 区块回答“云端 Dataset 和 Experiment 是否对应当前版本、聚合分数是多少、从哪里打开 Trace”。Opik 暂时不可达时，本地合同结果仍可正常查看。

## 复跑命令

先只校验入口，不连接 Opik：

```powershell
uv run taichu-opik-benchmarks --dry-run
```

只同步两个 Dataset，不执行案例：

```powershell
uv run taichu-opik-benchmarks
```

同步并运行两组正式 Experiment：

```powershell
uv run taichu-opik-benchmarks --run-evaluations
```

只运行一个入口时增加 `--entry multi_step` 或 `--entry recovery`。每次正式执行都会生成新的 Experiment；不要覆盖旧实验，因为旧实验是版本对比和回归定位所需的证据。

## 当前一次真实云端验收记录

本次适配实际写入的项目为 `taichu-general-agent-benchmark`，Workspace 为 `qingluo201816`：

- 多步骤 Dataset：18 个 item，当前版本 `v1`；Experiment 为 18/18 通过，18 条根 Trace，八项平均分均为 1.0。
- 恢复 Dataset：8 个 item，当前版本 `v1`；Experiment 为 8/8 通过，8 条根 Trace，八项平均分均为 1.0。
- 抽样 Trace 能看到案例根节点及模型、工具、专业智能体或恢复子图等嵌套 Span。

这些数字只描述本次固定 Suite 和确定性合成运行时，不能外推为所有小说任务、所有模型或真实线上故障都达到 100%。

## 自动化验证入口

- `tests/unit/infrastructure/evaluations/general_agent_benchmark/test_opik_integration.py`：Dataset 投影与稳定 ID。
- `tests/unit/infrastructure/evaluations/general_agent_benchmark/test_opik_evaluation.py`：正式 Experiment 与八维评分。
- `tests/unit/infrastructure/evaluations/general_agent_benchmark/test_opik_query.py`：云端读取、缓存、旧版本拒绝和无密钥投影。
- `tests/integration/api/test_general_agent_benchmarks_api.py`：HTTP 契约。
- `web/tests/general-agent/evaluation-view.test.ts`：前端 API 和展示入口。

## 面试时可以怎样概括

可以表述为：我没有为了简历堆 120 条相似任务，而是从权威 Suite 中挑出 18 条多步骤合同和 8 条真实恢复窗口。每条合同都包含预期产物、能力调用关系、预算、安全与证据门禁；运行时由 Opik Dataset 批量驱动，`@opik.track()` 记录模型、工具、子图和恢复链路，已有确定性门禁再投影成八维评分。前端只展示与当前 Suite 哈希和 Dataset 版本严格匹配的已完成 Experiment，避免旧状态被误当成当前结果。

## 官方资料

- [Opik 评测概念：Dataset、Task、Metric 与 Experiment](https://www.comet.com/docs/opik/evaluation/concepts)
- [Opik Python `evaluate()` 参考](https://www.comet.com/docs/opik/python-sdk-reference/evaluation/evaluate.html)
- [Opik Trace 入门](https://www.comet.com/docs/opik/tracing/getting-started)
- [Opik Dataset 参考](https://www.comet.com/docs/opik/reference/typescript-sdk/evaluation/datasets)
