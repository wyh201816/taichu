# Pico 全局架构导览：从一条请求到整个系统

> 外部写作风格样本，仅用于学习资料的表达方式，不是太初项目的事实来源。

这篇文档把大家把 `pico` 整个项目讲成一个能一口气读下来的系统。

如果你是第一次接触 `pico`，读完以后应该能回答这些问题：

+ `pico` 到底是什么，不是什么
+ 一条请求从终端进来以后，系统怎么往前推
+ 为什么它不只是“模型 + 工具”这么简单
+ `memory`、`checkpoint`、`resume`、`.pico/` 这些状态到底各干什么
+ 每个核心模块在代码里是什么位置

如果你已经做过一轮开发，现在是回来复习，这篇文档可以帮你梳理一遍整体的架构

## 前言
`pico` 是接近一个面向代码仓库任务的本地 coding harness。模型当然重要，但模型只是其中一层。真正让它能连续工作的，是模型外面的几件事一起成立：

+ 启动时先把工作区、模型后端和会话状态装配好
+ 运行时有一条清楚的主控制循环
+ 工具不直接执行，先经过统一边界（类似网关）
+ 上下文不是乱塞进 prompt，是按 section 和预算组织
+ 记忆、checkpoint、恢复和运行工件会一起把状态留住
+ 评测和指标是验证这套系统有没有跑稳的证据链

> `pico` 做的事，是把“模型在真实代码仓库里持续工作”这件事，从一次一答的聊天，变成一条有状态、有边界、能恢复、能复盘、能评测的执行链。
>



## 一、先看全貌
<img src="../assets/diagrams/pico-overall-architecture-zh.png" title="null" crop="0,0,1,1" id="k1kh0" class="ne-image"><img src="https://cdn.nlark.com/yuque/0/2026/png/65082237/1776826420418-47f28b51-7789-453f-8ea1-d9f40ba9835a.png" width="836" title="" crop="0,0,1,1" id="u69d7110e" class="ne-image">

一开始不用急着整个架构都塞进脑子里去想，可以先从这三条主线去开始看：

第一条是主控制流。它从 `CLI / 启动装配` 开始，进入 `Pico.ask()` 的主控制循环，再往两边接上 `上下文管理`、`模型适配层` 和 `工具边界 / 护栏`。这个部分回答的是，一条请求进来以后，系统怎么一步步往前推。

第二条是状态面。它把 `记忆 / 恢复` 和 `持久化` 放在一起。这里最关键是这些状态各自解决不同问题。session 让系统还能继续干，run artifacts 让你知道刚才到底发生了什么，durable memory 则负责把长期还成立的事实留在会话外面。

第三条是证据面。`评测 / 指标` 这一块不是主请求路径的一部分，但它很重要。没有这层，很多“这个版本更好了”的说法都只能靠体感。有了固定 benchmark、FakeModelClient、metrics 和工件汇总，这套运行时才不只是能跑，还能被比较、被回看。

把这张总图压成更短的抽象，可以先记成下面三层：

| 层 | 作用 | 对应模块 |
| --- | --- | --- |
| 控制面 | 决定系统怎么跑 | `cli.py`、`runtime.py`、`context_manager.py`、`tools.py`、`models.py` |
| 状态面 | 决定系统记住什么、恢复什么 | `memory.py`、`run_store.py`、`task_state.py`、`.pico/` |
| 证据面 | 决定系统怎么被验证、比较、复盘 | `evaluator.py`、`metrics.py`、`trace.jsonl`、`report.json` |


---

## 二、一条请求怎么跑完整个 Pico
顺着一条请求带大家梳理一遍

假设你在终端里输入：

```bash
uv run pico "inspect the test failures and propose a fix"
```

系统大概会经历下面这条线：

1. CLI 解析参数，决定工作目录、provider、model、approval、resume 模式。
2. `build_agent()` 把 `WorkspaceContext`、`SessionStore` 和具体 `ModelClient` 装好。
3. 如果是交互模式，就进入 REPL；如果是 one-shot，就直接把请求交给 `Pico.ask()`。
4. `Pico.ask()` 为这次请求创建 run、写第一版 `task_state.json`，再开始主循环。
5. 每一轮都会重新构建 prompt，把 prefix、memory、history 和 current request 拼起来。
6. 模型返回结果以后，runtime 先 `parse()`，判断这是工具调用、最终答案，还是一次需要重试的无效输出。
7. 如果是工具调用，就走 `run_tool()`，在统一边界里校验、审批、执行、比对工作区变化，再把结果写回状态。
8. 这轮发生过的事情会进入 session history、working memory、trace、checkpoint，必要时还会写 durable memory。
9. 直到模型给出 `<final>`，或者命中 step limit / retry limit，这次 run 才结束。

## 三、启动装配：先把运行现场搭起来
<img src="https://cdn.nlark.com/yuque/0/2026/png/65082237/1776826525579-7041df1c-dac3-4804-8a44-927113da1c49.png" width="768" title="" crop="0,0,1,1" id="u82964102" class="ne-image">

启动装配主要负责，用户怎么启动 `pico`，最后为什么会落成一个已经能工作的 `Pico` 实例。

这里最关键的文件是 `pico/cli.py`。它把几个真正影响运行时的对象装配出来：

+ 当前工作区的快照，也就是 `WorkspaceContext`
+ 当前用哪个模型后端，也就是 `_build_model_client()`
+ 当前 session 是新开还是恢复，也就是 `SessionStore`
+ 当前 approval policy、max steps、resume 模式这些运行时约束

`pico` 的启动不是简单的先起个 REPL 再说，它会先把运行现场装好，再决定是 one-shot 还是 REPL。这让后面的行为更统一。REPL 和 one-shot 的区别只是交互方式不同，真正的运行时骨架是一套。

下面这段代码能直接看出这个装配点长什么样：

```python
def build_agent(args):
    configured_secret_names = _configured_secret_names(args)
    workspace = WorkspaceContext.build(args.cwd)
    store = SessionStore(workspace.repo_root + "/.pico/sessions")
    model = _build_model_client(args)
    session_id = args.resume
    if session_id == "latest":
        session_id = store.latest()
```

CLI 负责把用户怎么启动翻译成runtime 拿到什么对象图。

从这里往后，主循环不再关心命令行细节。

## 四、主控制循环：Pico.ask() 才是系统心脏
<img src="../assets/diagrams/modules/pico-module-runtime-loop-zh.png" title="null" crop="0,0,1,1" id="A7Wbh" class="ne-image"><img src="https://cdn.nlark.com/yuque/0/2026/png/65082237/1776826765519-39a019b7-0f8a-4acd-8c2c-cf7b26af4a62.png" width="768" title="" crop="0,0,1,1" id="u17e030c8" class="ne-image">

`pico` 真正的中心在 `pico/runtime.py` 的 `Pico.ask()`。

如果只看这一个函数，可以把它理解成四个动作不断循环：

+ 感知：重新整理当前状态，构建这一轮 prompt
+ 决策：请求模型，拿回一轮输出
+ 行动：如果模型要调工具，就执行工具
+ 记录：把这轮发生的事写回状态和工件

这哥agent loop值得大家反复去可能啊：

```python
while tool_steps < self.max_steps and attempts < max_attempts:
    attempts += 1
    task_state.record_attempt()
    prompt, prompt_metadata = self._build_prompt_and_metadata(user_message)
    raw = self.model_client.complete(prompt, self.max_new_tokens, ...)
    kind, payload = self.parse(raw)
```

主循环最容易被忽略的点有三个。

第一，`prompt` 不是在请求开始时构建一次就算了，而是每轮都重建。因为工作区、memory、history、checkpoint 都可能变，上一轮的工具结果也会直接影响下一轮 prompt。

第二，模型输出不会直接进入执行阶段。中间有一个很关键的 `parse()`。这层负责把模型返回的文本接到控制流里，判断它到底是 `<tool>`、`<final>`，还是一次 malformed output，需要进入 retry。

第三，`Pico.ask()` 从第一轮开始就同时在做两件事，一边推进任务，一边生产证据。`task_state.json`、`trace.jsonl`、`report.json` 不是跑完以后补写的装饰，它们本来就是这条主循环的一部分。

## 五、上下文管理：不是把信息都塞进 prompt
<img src="https://cdn.nlark.com/yuque/0/2026/png/65082237/1776826784574-f0810e6c-2fbe-45bf-81a1-e932f3b75616.png" width="836" title="" crop="0,0,1,1" id="u951c441e" class="ne-image"><img src="../assets/diagrams/modules/pico-module-context-manager-zh.png" title="null" crop="0,0,1,1" id="NyKrX" class="ne-image">很多 agent 一开始都栽在同一个地方，前几轮还好，跑到后面 prompt 越来越胖，历史越来越乱，真正该保留的东西和该丢掉的东西混在一起。

`pico` 的处理方式在 `pico/context_manager.py` 里。它不是粗暴截断，而是先把 prompt 拆成几段，再分别管：

+ `prefix`
+ `memory`
+ `relevant memory`
+ `history`
+ `current request`

这几个 section 里，`current request` 的地位最高，默认不裁。因为最新请求一旦失真，后面所有优化都没有意义。

pico对prompt做了预算控制。`ContextManager` 不只知道“总预算是 12000 chars”，还知道各 section 的初始预算、最低 floor，以及超预算以后先裁谁。当前默认顺序是：

```latex
relevant_memory -> history -> memory -> prefix
```

这套设计的价值，不在于 prompt 一定会更短，而在于系统知道自己在删什么。删的是旧历史，还是相关记忆，还是稳定前缀，这三件事的后果完全不一样。把这些 section 拆开以后，prompt 组装这件事就不再是黑盒。

从代码上看，`ContextManager.build()` 的输入是状态，输出不是单纯一个字符串，而是：

+ 最终 prompt
+ prompt metadata

这份 metadata 后面会进入 trace 和 report，所以你不只知道这一轮 prompt 长什么样，还知道它为什么长成这样。

## 六、工具边界：模型不能直接碰外部世界
<img src="../assets/diagrams/modules/pico-module-tools-guardrails-zh.png" title="null" crop="0,0,1,1" id="j0zvv" class="ne-image"><img src="https://cdn.nlark.com/yuque/0/2026/png/65082237/1776826804608-8ece2794-bda6-4fab-af11-9f238eea199c.png" width="836" title="" crop="0,0,1,1" id="u7aaacc80" class="ne-image">

对 code agent 来说，危险的地方在于调用工具之前有没有统一边界。

`pico` 这一层在 `pico/tools.py` 和 `pico/runtime.py` 里一起完成。`tools.py` 定义工具白名单和 schema，`runtime.py` 的 `run_tool()` 负责把所有工具执行都收口到一个边界里。

当前工具列表不算多：

+ `list_files`
+ `read_file`
+ `search`
+ `run_shell`
+ `write_file`
+ `patch_file`
+ `delegate`

`run_tool()` 现在至少会做这些检查：

+ 工具是不是已注册
+ 参数是不是合法
+ 路径有没有逃出工作区
+ 是不是刚刚连续发起过完全相同的调用
+ 高风险工具在当前 approval policy 下能不能执行

执行前后，runtime 还会对工作区做快照，比较这次动作到底改了什么。这样工具结果不只是一段返回文本，还会带上：

+ `affected_paths`
+ `workspace_changed`
+ `tool_status`
+ `tool_error_code`

这一层的设计思想是模型不能直接碰外部世界，所有动作都必须经过统一边界。没有这个边界，后面的 memory、trace、benchmark 都会变得不可信，因为你已经不知道系统到底做过什么。

## 七、记忆、Checkpoint 与恢复：系统怎么持续工作
<img src="https://cdn.nlark.com/yuque/0/2026/png/65082237/1776826853046-ecce860e-ae1f-4185-9607-e97f4f7666cd.png" width="836" title="" crop="0,0,1,1" id="ubaee3508" class="ne-image"><img src="../assets/diagrams/modules/pico-module-memory-resume-zh.png" title="null" crop="0,0,1,1" id="oqxoG" class="ne-image">

`pico` 现在的记忆不是单层结构，而是至少分成三部分：

+ working memory，也就是 `LayeredMemory`
+ durable memory，也就是 `DurableMemoryStore`
+ checkpoint / resume_state，也就是会话恢复判断

`LayeredMemory` 负责当前工作面，里面主要是这些东西：

+ `task_summary`
+ `recent_files`
+ `file_summaries`
+ `episodic_notes`

它解决的是，模型是不是还在重复做刚做过的事，下一轮 prompt 还能不能拿回刚刚确认过的小结论。

`DurableMemoryStore` 负责长期仍然成立的事实。它不再继续塞在 session JSON 里，而是单独落到 `.pico/memory/`。这部分不是所有输出都自动写进去，而是要经过 promotion gate。比如项目约定、关键设计决策、依赖事实、用户偏好，这些才适合长期沉淀。

checkpoint 和 `resume_state` 解决的是另一个问题，这些旧状态现在还靠不靠谱。恢复的时候，runtime 会比较：

+ 文件 freshness
+ workspace fingerprint
+ tool signature
+ model / approval policy / feature flags

所以 `resume` 不是简单把旧 JSON 读回来，而是读回来以后，再判断哪些状态还是有效的。这是 `pico` 这套恢复机制最关键的地方。

## 八、模型适配层：Runtime 只想面对一个小接口
<img src="../assets/diagrams/modules/pico-module-model-clients-zh.png" title="null" crop="0,0,1,1" id="kxfn6" class="ne-image"><img src="https://cdn.nlark.com/yuque/0/2026/png/65082237/1776826878322-d781b5a1-a46b-46c2-91b9-de52d38283c3.png" width="836" title="" crop="0,0,1,1" id="u391d4773" class="ne-image">

从 runtime 视角看，它对模型层的要求其实很克制。它不想知道 HTTP 细节，不想知道某个 provider 走 JSON 还是 SSE，也不想知道 usage 字段长什么样。它只想做这几件事：

+ 给一段 prompt
+ 指定 `max_new_tokens`
+ 可选带上 prompt cache hint
+ 拿回一段文本
+ 顺手拿到少量 completion metadata

`pico/models.py` 的作用这么关键把三条 provider 路径都收成了统一的 `complete()` 行为：

+ `OllamaModelClient`
+ `OpenAICompatibleModelClient`
+ `AnthropicCompatibleModelClient`

其中 OpenAI-compatible 这条线最复杂，因为它不只是普通 JSON，还要处理 `/responses`、SSE 事件流、usage 结构抽取和 prompt cache metadata。Anthropic-compatible 走 `/messages`，Ollama 走本地 `/api/generate`，两条线都简单一些。

如果大家要接新的模型，在这加就行

## 九、持久化与评测：能跑不够，还要能回看、能比较
<img src="../assets/diagrams/modules/pico-module-persistence-benchmark-zh.png" title="null" crop="0,0,1,1" id="X8mjZ" class="ne-image"><img src="https://cdn.nlark.com/yuque/0/2026/png/65082237/1776826920638-eec956d1-a792-4831-81c1-2d2495e3c913.png" width="836" title="" crop="0,0,1,1" id="u7e623311" class="ne-image">

`pico` 到这里已经不只是一个能调工具的 agent 了。它还得回答这些问题：

+ 跑到一半中断了，下一次怎么接着来
+ 跑完以后结果不对，怎么回看过程
+ 两个版本谁更好，怎么稳定比较

这部分现在主要由三个模块负责：

+ `SessionStore`
+ `RunStore`
+ `evaluator.py / metrics.py`

`SessionStore` 管的是以后还能不能继续干。它把会话级状态保存在 `.pico/sessions/*.json`。

`RunStore` 管的是刚才到底发生了什么。每次 `ask()` 都会新开一个 run 目录，里面至少有这三份工件：

+ `task_state.json`
+ `trace.jsonl`
+ `report.json`

这三份工件解决的问题也不同。`task_state` 是运行中的快照，`trace` 是逐事件时间线，`report` 是收口后的聚合摘要。

评测侧边系统在 `pico/evaluator.py` 和 `pico/metrics.py` 里。它们不是主请求路径的一部分，但它们让 `pico` 从能跑走到了能稳定比较。固定 benchmark、FakeModelClient、fixture repo、verifier、summary rows 这些东西，最后一起回答的是同一个问题，你怎么知道这次版本真的变好了。

## 十、`.pico/` 在磁盘上到底长什么样
系统跑起来以后，状态最终会落在本地 `.pico/` 目录里。把这个目录想明白，很多抽象会一下子落地。

```latex
.pico/
├── sessions/
│   └── <session_id>.json
├── runs/
│   └── <run_id>/
│       ├── task_state.json
│       ├── trace.jsonl
│       └── report.json
└── memory/
    ├── MEMORY.md
    └── topics/
        ├── project-conventions.md
        ├── key-decisions.md
        ├── dependency-facts.md
        └── user-preferences.md
```

这棵树里，最容易混淆的是三件事。

`sessions/` 存的是会话级状态，它面向的是继续工作。  
`runs/` 存的是单次请求的运行工件，它面向的是回放和审计。  
`memory/` 存的是 durable memory，它面向的是跨会话仍然成立的长期事实。

## 十一、关键代码入口：想回到源码，先从这里看
如果你已经有了pico的系统抽象，下一步建议你回到几个关键文件验证这个抽象是不是对的。

可以先按下面这个顺序读：

| 文件 | 角色 | 为什么先看它 |
| --- | --- | --- |
| `pico/cli.py` | 启动装配层 | 能看清 `pico` 怎么从命令行落成一个运行时对象图 |
| `pico/runtime.py` | 主控制循环 | 整个系统心脏，`Pico.ask()`、`run_tool()`、checkpoint、report 都在这里 |
| `pico/context_manager.py` | prompt 组装层 | 看清 section、预算和裁剪顺序 |
| `pico/tools.py` | 工具定义层 | 看白名单、schema 和基本执行逻辑 |
| `pico/memory.py` | 记忆内核 | working memory、durable memory、retrieval 都在这里 |
| `pico/models.py` | 模型适配层 | 看 provider 差异是怎么被吃掉的 |
| `pico/run_store.py` | 运行工件落盘 | 看 run artifact 为什么会分三类 |
| `pico/task_state.py` | 运行状态快照 | 看一轮请求到底被记录成什么 |
| `pico/evaluator.py` | benchmark harness | 看 benchmark 任务、fixture、verifier 怎么接进 Pico |
| `pico/metrics.py` | 指标聚合 | 看 report 最后怎么变成 summary rows 和 pass rate |


如果你只剩 15 分钟，优先看四个文件：

+ `pico/runtime.py`
+ `pico/context_manager.py`
+ `pico/memory.py`
+ `pico/models.py`

这四个文件基本已经把 `pico` 的系统骨架撑起来了。

## 复习版：10 分钟把 Pico 过一遍
如果你以后想快速回顾，按这个顺序复习就够了：

1. 先看中文总图，重新把主控制流、状态面、证据面放回脑子里。
2. 再看启动装配图和运行时主循环图，确认一条请求是怎么跑起来的。
3. 然后看上下文管理图和工具边界图，记住 prompt 和工具都不是黑盒。
4. 再看记忆 / 恢复图，确认 working memory、durable memory、checkpoint 的分工。
5. 最后看模型适配层和持久化 / 评测图，把 provider 差异和 run artifacts 接回整个系统。

如果你能顺着这条线把系统讲出来，说明 `pico` 你已经理解的很透彻了。
