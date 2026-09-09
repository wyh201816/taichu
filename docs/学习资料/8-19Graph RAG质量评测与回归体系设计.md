# Graph RAG 质量评测与回归体系设计

> 首次形成日期：2026-08-19  
> 更新日期：2026-08-24
> 状态：核心体系已实现；30 条 Golden 首轮全量确定性与语义门禁已通过。本文件记录当前实现和后续调优边界，代码契约仍以源码为准。

## 一、目标与边界

这套体系用于长期回答两个工程问题：

1. 生产 Graph RAG 的检索、图关系和上下文装配是否仍能找回回答所需证据。
2. 检索代码、语料、索引、Embedding、重排器或生成 Prompt 变化后，CI 是否能自动发现回归并留下逐案证据。

技术主线是生产级 RAG Evaluation，CI 是自动执行载体。第一版只保留必要指标、30 条人工维护 Golden 和三种运行规模；不建设 Claims Engine、模型排行榜、审批流、时间线专项门禁、Nightly，也不保留 Graph ON/OFF 双实现或消融入口。

```text
                 RAG Evaluation
                       │
         ┌─────────────┼─────────────┐
         ↓             ↓             ↓
     Retriever      Generator      Graph
         │             │             │
    Recall@10      Faithfulness   Relation Recall@10
    MRR@10         Relevancy      Complete Path Recall
         │             │             │
         └─────────────┼─────────────┘
                       ↓
                30 Golden Cases
                       ↓
                  Regression
                       ↓
                      CI
                       ↓
                失败/灰区人工诊断
```

## 二、被测生产链

评测只能调用用户实际使用的 `retrieve_story_context → VectorGraphRAGService`，不得复制一条简化检索器冒充生产效果。当前单一生产链为：

```text
Query
  ├─ 中文 BM25 Passage Top 30
  └─ HNSW Dense Passage Top 30
             ↓
      Milvus RRF(k=60) Passage Top 30
             ↓
从命中 Passage 的 entity_ids / relation_ids 取得图种子
             ↓
查询感知的受控 Graph Expansion
             ↓
通过 relation.passage_ids 回取 Graph Passage（最多 20）
             ↓
与 RRF Passage 合并、按 passage_id 去重
             ↓
一次 BGE 全候选重排（Top 10 为评测与追踪边界）
             ↓
查询感知 Context Assembly（最多 3 份互补证据）
             ↓
Markdown / MongoDB 权威回源与投影校验
```

图扩展固定为 Passage-first，而不是 Entity-first：查询阶段不调用 LLM 抽取实体，不从“秦浩轩”等 Hub 节点直接读取全部邻接关系，也不调用 LLM 重排关系。图种子必须先被 BM25/Dense/RRF 的相关 Passage 支持。当前硬预算是：种子实体 5、种子关系 32、单跳、每跳实体 20、普通实体关系 10、Hub 实体关系 5、Beam 24、全局关系 56、Graph Passage 20。

BGE 对 RRF 与 Graph 合并后的候选只调用一次。最终上下文选择器最多选 3 份互补证据：单事实问题优先使用最小连续原文句窗；因果、过程、方式或共同经历问题保留更宽的同章父级邻域；知识卡按相关字段和关系压缩。应用服务仅保留能够从 Markdown 或当前 MongoDB `lifecycle=confirmed` 卡连续重建的投影，否则回退到完整权威内容。

## 三、Golden 数据集

当前固定 30 条：

| 类别 | 数量 | 目标 |
|---|---:|---|
| 单事实检索 | 6 | 基础命中、排序和直接回答 |
| 正文与知识卡交叉问题 | 6 | 跨来源证据与复杂语义 |
| Graph 关系问题 | 14 | 必需关系和完整关系链 |
| 困难负例 | 4 | 正确识别当前资料无法确认 |

第一版字段保持最小：

```text
case_id
query
category
smoke
graph_required
expected_source_ids
expected_relations
expected_path
expected_claims
reference_answer
```

- `expected_source_ids` 使用章节或知识卡的稳定 ID，不使用会随切片变化的 `chunk_index`。
- `expected_relations` 以主体、谓词、客体表达必需关系；`expected_path` 使用规范化三元组生成的稳定关系 ID。
- `expected_claims` 和 `reference_answer` 用于表达期望事实、生成 Prompt 设计和人工诊断，第一版不对自然语言回答做 Claim 抽取、别名归一和确定性匹配。
- Golden 内不设置 `forbidden_source_ids`、`forbidden_claims` 或 `review_status`。放入固定集合即表示维护者已经确认；检索到某来源不等同于错误使用该来源，最终事实正确性由语义评测和人工诊断判断。
- `as_of_chapter_id` 只在生产检索真正具备并需要章节时间边界时使用；本轮不另造时间线硬门禁。

Ground Truth 必须来自 Markdown 或 MongoDB confirmed 卡，不能从 Milvus 当前召回结果反推。Milvus、评测 JSON 和模型回答都只是派生数据。

## 四、三层评测

### 4.1 DeepEval 无参考自动语义评测

对实际问题、最终组装上下文和实际生成回答运行三个指标：

- `Contextual Relevancy`：上下文是否围绕问题，是否混入过多无关背景。
- `Faithfulness`：回答事实是否得到实际上下文支持。
- `Answer Relevancy`：回答是否直接回应问题。

评测把最终组装上下文作为一个整体交给 Judge，避免默认模板把多段桥接证据拆开、逐段误判。Graph 用例使用图感知的上下文相关性模板：完整路径中的桥接关系可共同构成相关证据，无关背景仍应扣分。困难负例没有可回答上下文时不强制产生上下文相关性分数。

Judge 与答案生成都通过太初统一 LLM 网关，报告记录供应商和模型身份。任一语义调用异常会以执行失败进入门禁，不能被当成低分或跳过后伪装通过。

### 4.2 Golden 确定性回归

确定性层按职责分开评分：

| 层级 | 指标 | 取值边界 |
|---|---|---|
| Retriever | `Recall@10`、`MRR@10` | 最终 BGE Top 10 的权威来源 |
| Data Integrity | 权威回源通过率 | 最终进入上下文的所有证据 |
| Graph | `Relation Recall@10` | 最终 BGE Top 10 所携带关系 |
| Graph | `Complete Path Recall` | 受控扩展阶段已发现的完整路径 |
| Graph 诊断 | `Graph Expansion Noise Rate` | 只用于定位，不是核心硬门禁 |

`Relation Recall@10` 和 `Complete Path Recall` 故意取不同阶段：前者回答必需关系是否进入最终排序边界，后者回答图扩展本身是否找全路径。这样可以区分“没有扩出来”和“扩出来但在 Passage/BGE 阶段掉队”。第一版不增加 `nDCG`、`Hop Coverage` 或确定性 Claims 匹配。

### 4.3 人工兜底

人工只负责：

- 新建或修改 Golden 时核对问题、来源、关系路径、断言和参考答案。
- 诊断 CI 失败或语义灰区，判断应修改生产链、索引、Golden、Prompt 还是经过校准的阈值。

人工不是每次发布的最终确认步骤，第一版不建设审批页面或多人 Review 状态机。

## 五、自动门禁

### 5.1 当前硬阈值

| 指标 | 门槛 |
|---|---:|
| 平均 `Recall@10` | `>= 0.80` |
| 平均 `MRR@10` | `>= 0.50` |
| 权威回源通过率 | `= 1.00` |
| 平均 `Relation Recall@10` | `>= 0.70` |
| 完整路径通过率 | `>= 0.60` |
| 各 DeepEval 指标均分 | `>= 0.70` |

平均分不能掩盖关键断链：任一应有来源的用例 `Recall@10=0`、任一 Graph 用例 `Complete Path Recall=0`、任一证据未通过权威回源，都会直接失败。检索、生成或 Judge 的基础设施异常同样直接失败并保存失败报告。

### 5.2 运行时点

| 时点 | 自动执行 | 目的 |
|---|---|---|
| 普通 PR | 评测契约单测 + 5 条真实生产链 Smoke | 快速发现装配、数据或基础设施断裂 |
| RAG 相关 PR | 相关单测 + 30 条确定性回归 + 固定 10 条 DeepEval | 对检索、图、Prompt、语料、索引和模型配置变更执行质量门禁 |
| 手动/发布前 | 30 条确定性回归 + 30 条 DeepEval | 冻结完整版本基线 |

普通 PR 由 `.github/workflows/rag-smoke.yml` 触发；RAG 路径 PR 和手动全量由 `.github/workflows/rag-regression.yml` 触发，运行在具备 Milvus、Embedding、CUDA BGE 和模型网关的自托管 Windows Runner。当前不设置 Nightly；只有出现每日会漂移的线上模型或索引后，再依据真实需求增加。

## 六、首轮全量基线

可信基线报告：`project_assets/derived/rag_evaluations/20260823T191756Z-full.json`。报告创建时间是北京时间 2026-08-24，30 条确定性与 30 条语义用例全部执行完成，门禁结果为通过。

| 确定性指标 | 结果 |
|---|---:|
| `Recall@10` | `0.8365` |
| `MRR@10` | `0.7042` |
| 权威回源通过率 | `1.0000` |
| `Relation Recall@10` | `0.8929` |
| 完整路径通过率 | `1.0000` |

| 语义指标 | 样本数 | 均分 | 最低分 |
|---|---:|---:|---:|
| 上下文相关性 | 26 | `0.8530` | `0.3333` |
| 忠实度 | 30 | `0.9933` | `0.8000` |
| 回答相关性 | 30 | `0.9526` | `0.5000` |

运行身份：DeepSeek 官方网关；Embedding 为 `Qwen3-Embedding-4B-Q4_K_M`；Reranker 为 `BAAI/bge-reranker-v2-m3`；索引构建 Graph 模型为 `deepseek-v4-pro`；生成与 Judge 为 `deepseek-v4-flash`。

当前仍有 8 条上下文相关性和 2 条回答相关性低于单案 `0.70`。门禁按语义指标均分判断，因此本轮通过；这些案例已经保留在报告中，属于下一轮上下文选择与拒答 Prompt 的可见调优尾项，不能删除样例或隐藏分数。确定性 Graph 指标已经从早期 `Relation Recall@10≈0.292`、完整路径通过率 `≈0.083` 提升到 `0.8929/1.0000`。

## 七、实现入口

```text
tests/fixtures/evaluations/rag_graph_core/suite.json
src/taichu/application/evaluations/rag/
src/taichu/infrastructure/evaluations/rag/
src/taichu/infrastructure/vector_graph/controlled_retriever.py
src/taichu/infrastructure/vector_graph/backend.py
src/taichu/infrastructure/vector_graph/hybrid_backend.py
src/taichu/application/vector_graph/service.py
scripts/evaluate_rag.py
.github/workflows/rag-smoke.yml
.github/workflows/rag-regression.yml
project_assets/derived/rag_evaluations/
```

评测报告是可重建的审计与回放产物，不是正文或结构事实源。结果仓储可以兼容读取旧报告中的消融字段，但当前 Runner、API 和页面不再生成或展示 Graph ON/OFF 数据。

## 八、自我 Review

当前设计已经守住以下边界：

- Retriever、Graph、Generator 分层，能够定位断点而不是只看最终答案。
- 评测复用真实生产链，权威事实始终回到 Markdown/MongoDB。
- Passage-first 和全局预算从结构上阻断 Hub Entity 与 Context Explosion。
- Graph 的完整路径与最终关系排序分别测量，不用重复指标堆叠复杂度。
- DeepEval 补足自然语言语义判断，但不能替代稳定来源、关系路径和权威回源硬门禁。
- CI 按变更风险分级，当前不为了形式完整而运行 Nightly。

下一轮只应围绕真实低分案例继续优化上下文装配和回答 Prompt，并比较冻结基线差值；不要重新加入 Claims Engine、`nDCG`、Graph ON/OFF、审批状态或时间线专项基础设施。
