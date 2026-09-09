# Opik 评测页面查看与使用指南

## 先给结论

你截图中的 `Configuration` 是配置页，主要用于 Feedback definitions（反馈分数定义）、环境、模型供应商和 Workspace 设置，**不是查看本次评测结果的页面**。太初的结果主要看三个位置：Dataset 数据集、Experiment 实验、Trace 链路。

最快的方式不是在 Opik 左侧菜单里逐层寻找，而是先打开太初评测页，点击“打开实验”“查看数据集”或“查看 Trace”。链接由后端根据当前通过身份校验的 Experiment 生成，因此不容易误进旧项目或 Demo 项目。

## 从太初页面进入

本地服务启动后打开：

- 多步骤组合任务：`http://localhost:3000/task-monitor/general-agent/evaluation/multi-step`
- 异常中断恢复：`http://localhost:3000/task-monitor/general-agent/evaluation/recovery`

“Opik 云端评测结果”区域应显示“已校验”，并看到四个摘要：Dataset 条数和版本、Experiment 通过数、根 Trace 数、案例耗时中位数。下面是八维固定合同评分。

三个按钮的用途如下：

| 按钮 | 打开的页面 | 主要回答的问题 |
| --- | --- | --- |
| 打开实验 | Experiment compare | 这次批量评测整体表现怎样，哪些案例失败 |
| 查看数据集 | Dataset items | 实际跑了哪些输入、期望和合同元数据 |
| 查看 Trace | 项目 Trace 列表 | 每条任务内部调用了什么，慢或错在哪里 |

## 当前云端项目与直达链接

当前 Workspace 是 `qingluo201816`，项目名是 `taichu-general-agent-benchmark`。如果太初页面暂时没有启动，也可以使用以下直达链接：

### 多步骤组合任务

- [查看 18 条 Dataset items](https://www.comet.com/opik/qingluo201816/projects/01a05d0e-452e-70ed-a587-ae4224c9a42d/datasets/01a05d0f-cb66-75f7-8743-4d6edcfc8327/items)
- [查看 18/18 Experiment](https://www.comet.com/opik/qingluo201816/experiments/01a05d0f-cb66-75f7-8743-4d6edcfc8327/compare?experiments=%5B%2201a05d3f-505d-7244-ac93-245ec1829fcd%22%5D)

### 异常中断恢复

- [查看 8 条 Dataset items](https://www.comet.com/opik/qingluo201816/projects/01a05d0e-452e-70ed-a587-ae4224c9a42d/datasets/01a05d13-ff87-7358-9d68-887d98396cc0/items)
- [查看 8/8 Experiment](https://www.comet.com/opik/qingluo201816/experiments/01a05d13-ff87-7358-9d68-887d98396cc0/compare?experiments=%5B%2201a05d3f-db7f-74e1-900d-7f1c089bd173%22%5D)

### 全项目 Trace

- [查看太初评测 Trace](https://www.comet.com/opik/qingluo201816/projects/01a05d0e-452e-70ed-a587-ae4224c9a42d/traces)

如果页面顶部仍显示 “You are viewing a demo project”，先用左上 Workspace 下拉框确认已经进入 `qingluo201816`，并确认项目不是 Demo。直达链接已经包含 Workspace 和项目身份。

## Dataset 页面怎么看

进入 Dataset 后，先核对三件事：

1. 多步骤入口应为 18 条，恢复入口应为 8 条；
2. 当前版本应为 `v1`，以后合同变化会形成新版本；
3. item 的 `metadata.suite_content_hash`、`entry_id` 和案例分类应存在。

点击一条 item 后重点看：

- `input.user_request`：该案例真实用户请求；
- `input.case_id`：稳定案例身份；
- `expected_output`：目标、产物、终态和行为断言；
- `trajectory_contract`：必须调用的能力及无效调用规则；
- `metadata`：Suite、入口、分类和故障计划身份。

Dataset 是实验输入，不等于实验已经通过。判断结果必须继续看与该 Dataset 版本关联的 Experiment。

## Experiment 页面怎么看

Experiment compare 页面上方先确认名称、创建时间、Dataset 版本和完成状态。当前两次正式实验的名称以“太初·多步骤组合任务”或“太初·异常中断恢复”开头。

然后按以下顺序阅读：

1. 先看案例总合同平均分和通过案例数；
2. 再看能力调用合同，判断是否出现越权、超次、乱序或结果未消费；
3. 展开六个门禁评分，区分是预算、行为、产物、结束状态、安全还是证据失败；
4. 按分数小于 1 或失败状态筛选 item；
5. 点击失败 item，再进入它关联的 Trace。

八项都为 100% 表示这次固定合同全部通过，不表示“模型没有任何缺陷”。当前结果来自确定性合成 Runtime，因此费用为空是正常现象，不是链路漏采。

版本对比时，可以在同一 Dataset 的 Experiment compare 页面加入另一批 Experiment。只有 Suite 哈希、Dataset 版本、案例集合和 Runtime 配置具有可比性时，平均分变化才有解释价值；不要把不同案例集合直接排成模型排行榜。

## Trace 页面怎么看

Trace 列表中一条根 Trace 对应一个 Dataset item，也就是一条评测合同。进入 Trace 后，左侧或主区域会显示树状 Span。

建议从外到内阅读：

1. 根节点“评测案例”：确认案例名称、入口、结束状态和总耗时；
2. “模型”节点：确认规划、重规划、校验等轮次及输入输出；
3. “工具”节点：确认工具名称、参数、结果和是否报错；
4. “专业智能体”节点：确认子图是否在正确父节点下执行；
5. “恢复子图”节点：确认故障位置、检查点、恢复动作和重复副作用数量。

定位问题时常用的判断方式：

- 根 Trace 很慢：先按 Duration（耗时）排序，再看最长子 Span；
- 工具调用数异常：检查是否超过合同上限，或相同结果被重复请求；
- 评分失败但 Trace 成功：执行完成不代表产物、安全或证据门禁通过；
- Trace 中断：查看最后一个 Span 的错误、输入和父节点，再回到对应案例合同核对预期终态；
- 恢复案例失败：重点查看成功节点是否被重复执行、写入后是否先对账、是否产生重复副作用。

## 如何重新生成一批结果

配置保存在本地 `.env`，至少应确认以下名称存在；不要把真实密钥复制到文档、前端代码或提交记录：

```dotenv
OPIK_ENABLED=true
OPIK_PROJECT_NAME=taichu-general-agent-benchmark
OPIK_URL_OVERRIDE=https://www.comet.com/opik/api
OPIK_WORKSPACE=qingluo201816
OPIK_API_KEY=你的本地密钥
```

先做离线校验：

```powershell
uv run taichu-opik-benchmarks --dry-run
```

再同步并执行：

```powershell
uv run taichu-opik-benchmarks --run-evaluations
```

命令结束时会输出每个入口的通过数和 Experiment 地址。刷新太初评测页后，服务端最多缓存旧查询结果 60 秒；缓存到期后会自动读取最新且身份完全匹配的已完成 Experiment。

## 常见情况与处理

### 页面显示“未启用”

检查 `.env` 中 `OPIK_ENABLED=true`，然后按项目固定端口约定重启后端。前端不读取密钥，单独重启浏览器不能改变后端配置。

### 页面显示“暂不可用”

常见原因包括网络不可达、Workspace 或密钥不正确、Dataset 当前版本变化但尚未重新跑 Experiment、云端案例数或评分维度不完整。此时本地合同结果仍可看；先运行 `--dry-run`，再正式跑一批与当前 Suite 匹配的 Experiment。

### Opik 能看到实验，太初却拒绝展示

这通常不是前端故障，而是防旧状态校验生效。太初只接受当前 Suite 哈希、入口、完整案例列表、Dataset 当前版本、唯一 Trace 和八项评分全部匹配的已完成实验。重新同步并运行当前代码即可形成新实验。

### Configuration 页面只有 User feedback

这是正常的。Feedback definitions 用于配置可复用反馈类型；本项目八项固定评分由评测函数随 Experiment 写入，不需要在 Configuration 页面手工创建八个定义。请返回 Projects，或直接使用上方链接。

### 是否会产生费用

这两组固定评测使用确定性合成运行时，不调用真实外部模型，所以当前 Experiment 没有模型 Token 费用。Opik Cloud 账户套餐、自托管机器和未来真实模型评测的费用属于另外三条边界；套餐可能变化，应以 Comet 当前官方价格页为准。密钥只保存在后端环境，前端 `/opik/summary` 返回的都是只读、无凭据字段。

## 官方资料

- [Opik 评测概念](https://www.comet.com/docs/opik/evaluation/concepts)
- [Opik Trace 概览与筛选](https://www.comet.com/docs/opik/tracing/overview)
- [Opik Trace 入门](https://www.comet.com/docs/opik/tracing/getting-started)
- [Opik Experiment 参考](https://www.comet.com/docs/opik/v1/reference/typescript-sdk/evaluation/experiments)
