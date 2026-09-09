# Pico Runtime 主循环：一条请求如何推进成可回放执行

> 外部写作风格样本，仅用于学习资料的表达方式，不是太初项目的事实来源。

## 背景
如果只看 `tools.py`、`memory.py`、`context_manager.py` 这些模块，很容易把项目理解成几个零散能力的组合。真实运行时不是这么工作的。用户给一句话之后，系统要先建状态、再组 prompt、再调模型、再接工具、再落工件、最后决定什么时候停。把这些动作串起来的，就是主循环。

对面试来说，这一层很关键。因为很多人能讲清工具、能讲清 memory、也能讲清 prompt，但一问到一句请求进来以后系统到底怎么往前推，就开始散。

这篇的任务，就是把这条执行线讲清楚。

## 这篇要解决什么问题
问题可以压成一句话：

**一条用户请求，怎么被 runtime 推进成一次有边界、有状态、可回放的 agent 执行。**

这里面有五个子问题：

+ 这轮请求什么时候算真正开始
+ 模型一轮一轮是怎么被调起来的
+ 模型说要调工具以后，runtime 怎么接住
+ 执行过程中哪些状态会持续更新
+ 什么情况下继续跑，什么情况下停

## 从一句话到一次 run
`Pico` 对一次请求的处理单位，是一轮独立运行。

CLI 在 `pico/cli.py` 里先把现场装好，包括：

+ 用哪个 provider
+ 用哪个 model
+ 当前工作区是什么
+ session 是新开还是恢复
+ 高风险工具怎么审批
+ 最多允许跑几步

这些东西定完以后，运行权就交给 `agent.ask(user_message)`。

从 runtime 的视角看，一次请求会走下面这条线：

![](https://cdn.nlark.com/yuque/__mermaid_v3/2c4df6f8cb6a12a137a0b9960a827ba0.svg)

这张图需要注意两个地方。

第一，`ask()` 不只是调模型，它是在驱动一条完整执行链。

第二，工具执行并没有把控制流带出 runtime，结果还会重新回到状态里，再进入下一轮。

## ask() 一开始先做什么
`pico/runtime.py` 里的 `ask()`，开头先立运行现场，再进入推理和执行。

它会按顺序做这些事：

1. 把当前请求写进 `task_summary`
2. 把用户消息记进 session history
3. 创建 `TaskState`
4. 在 `.pico/runs/<run_id>/` 下创建本轮 run 目录
5. 立刻写出第一版 `task_state.json`
6. 追加一条 `run_started` trace

代码骨架大概就是这样：

```python
def ask(self, user_message):
    self.memory.set_task_summary(user_message)
    self.record({"role": "user", "content": user_message, "created_at": now()})

    task_state = TaskState.create(
        run_id=self.new_run_id(),
        task_id=self.new_task_id(),
        user_request=user_message,
    )
    self.current_task_state = task_state
    self.current_run_dir = self.run_store.start_run(task_state)
    self.emit_trace(task_state, "run_started", {...})
```

这个设计把一次请求和一次 run 绑定死了。

后面你查问题、做实验、回放 trace，都不需要再从一整段 session history 里猜边界。

## 主循环到底在循环什么（重要）
`ask()` 的 while 循环可以压成四个阶段：

+ **感知**：把当前状态重新整理成 prompt
+ **决策**：调模型，拿回一轮输出
+ **落地**：如果模型要调工具，就执行工具
+ **记录**：把这轮发生的事写回状态和工件

核心骨架可以看这一段：

```python
while tool_steps < self.max_steps and attempts < max_attempts:
    attempts += 1
    task_state.record_attempt()
    self.run_store.write_task_state(task_state)

    prompt, prompt_metadata = self._build_prompt_and_metadata(user_message)
    raw = self.model_client.complete(prompt, self.max_new_tokens, ...)
    kind, payload = self.parse(raw)

    if kind == "tool":
        task_state.record_tool(name)
        result = self.run_tool(name, args)
        self.record({...})
        self.emit_trace(task_state, "tool_executed", {...})
        continue

    if kind == "retry":
        self.record({...})
        continue

    task_state.finish_success(final)
    self.run_store.write_report(task_state, self.build_report(task_state))
    return final
```

这里有两个状态字段要注意：

+ `attempts` 统计模型被调了多少轮
+ `tool_steps` 统计真正进入执行阶段的工具调用次数

一个任务 attempts 很高但 tool_steps 很低，通常是模型输出格式不好或者一直在 retry。

tool_steps 很高，说明它真的在执行很多动作。

## 为什么 prompt 每轮都要重建
runtime 每轮都会重新走 `_build_prompt_and_metadata()`。

主要原因是，下面几个东西每一轮都可能变：

+ history
+ memory
+ workspace 状态
+ 工具数和 prefix 状态
+ 当前请求对应的相关上下文

所以 prompt 在 runtime 里不是一份固定文本，更像当前执行状态的投影。`_build_prompt_and_metadata()` 除了组 prompt，还会顺手带出一份 metadata，里面会记：

+ prefix 长度
+ workspace 长度
+ history 长度
+ tool 数量
+ prefix hash
+ workspace fingerprint
+ prompt cache key

这些元数据对主循环很重要，后面 trace、report、实验聚合都要靠它们解释这一轮 prompt 到底发生了什么。

## 模型输出怎么进入控制流
模型返回的是文本，runtime 需要的是动作。中间这一步由 `parse()` 来做。

`parse()` 会把一轮模型输出压成三类结果：

+ `tool`
+ `final`
+ `retry`

它支持两类工具调用格式：

+ `<tool>...</tool>` 里包 JSON
+ XML 风格 `<tool name="...">...</tool>`

核心逻辑可以直接看这一段：

```python
if "<tool>" in raw:
    payload = json.loads(body)
    return "tool", payload

if "<tool" in raw:
    payload = Pico.parse_xml_tool(raw)
    if payload is not None:
        return "tool", payload

if "<final>" in raw:
    return "final", final

if raw.strip():
    return "final", raw.strip()

return "retry", Pico.retry_notice(...)
```

模型说了什么不重要，runtime 关心的是接下来该走哪条分支。

## 工具调用怎么重新接回主循环
当 `parse()` 返回 `tool` 之后，主循环不会直接跳到具体工具函数，而是统一先走 `run_tool()`。

`run_tool()` 负责把执行前后的关键动作串起来：

1. 检查工具是不是已注册
2. 校验参数是否合法
3. 判断是不是连续重复调用
4. 高风险工具走审批
5. 真正执行工具
6. 把结果裁剪后返回
7. 更新 memory 里的局部事实

工具执行没有脱离主循环。工具结果回来以后，会继续写回这些状态：

+ session history
+ `TaskState`
+ trace
+ report 使用的最后一轮 metadata
+ memory 里的轻量事实

这样 runtime 才能在下一轮继续推进，而不是每次工具调用都像一条临时岔路。

## TaskState 为什么是主循环的中心状态
`pico/task_state.py` 里的 `TaskState` 很值得单独讲，因为它把这轮运行的中心状态收在了一个地方。

它主要记录这些字段：

+ `run_id`
+ `task_id`
+ `status`
+ `tool_steps`
+ `attempts`
+ `last_tool`
+ `stop_reason`
+ `final_answer`

这里最值钱的设计是 `status` 和 `stop_reason` 分开存。

`status` 回答的是这轮现在是什么状态，比如 `running`、`completed`、`stopped`、`failed`。  
`stop_reason` 回答的是它为什么停，比如 `final_answer_returned`、`step_limit_reached`、`retry_limit_reached`。

这两个维度分开以后，后面做 benchmark 和失败归因时，就不会把所有停机都混成一个模糊的失败。

## RunStore 为什么要跟主循环绑在一起
如果主循环只返回一个最终答案，很多工程问题都没法解释。

`Pico` 这里把工件落盘直接绑进主循环，靠的是 `RunStore`。

每次 run 至少会写三类工件：

+ `task_state.json`
+ `trace.jsonl`
+ `report.json`

这三类工件分工很清楚。

+ `task_state.json` 负责看当前这轮跑到哪了
+ `trace.jsonl` 负责看过程里发生了什么
+ `report.json` 负责看最后结果和关键指标

这也是为什么 `ask()` 看上去比普通 demo agent 重一点。它不是只求跑通，还要把每一步都留证据。

## 循环什么时候停
当前主循环的停止条件很清楚，主要有三类。

### 一，模型给出最终答案
这时 `task_state.finish_success(final)`，状态会进入 `completed`，然后写 report，函数直接返回。

### 二，达到工具步数上限
这时 runtime 会返回一条停止说明，并把 `stop_reason` 记成 `step_limit_reached`。

### 三，达到重试上限
这一般意味着模型连续多轮输出了坏格式、空响应或者没法进入有效动作。

runtime 会把它记成 `retry_limit_reached`。

在面试的时候你可以这么说：runtime 关心的不只是成功路径，也关心怎么有边界地停下来。

## 当前设计的trade-off
这一版 runtime 很克制，它减少了不少复杂度。

### 减少了 planner 和多级调度
当前主循环就是单循环，没有额外 planner，也没有复杂 phase machine。

这样做的好处是调试简单，控制流清楚，trace 也更容易看。

### 减少了花哨编排
工具调用、模型调用、状态回写都还在一个明确的总调度器里。

复杂度集中，坏处是 `ask()` 会显得比较重，好处是主链路很完整。

### 换来了可回放和可解释
`prompt_metadata`、`TaskState`、`trace`、`report` 这些东西都让代码更厚，但这部分厚度换来了 runtime 级别的解释能力。你可以说清楚这轮为什么停、调了几次模型、用了几步工具、最后留下了什么证据。

对一个本地 coding harness 来说，这个取舍是合理的。主循环先做稳，后面别的能力才有地方挂。

## 对于runtime，还有什么能改进的地方，如果继续开发的话
我会优先补三件事。

### 一，细化失败类别
当前已经区分 final、retry、tool，也有 step limit 和 retry limit。

再往下做，最值得补的是把模型空响应、解析失败、后端异常拆得更开，这样 report 和评测会更干净。

### 二，补更显式的阶段字段
现在 trace 是事件序列，可读性已经够用。

如果以后想做可视化或复杂评测，runtime 可以再加一层统一的 phase 标记。

### 三，补父子 run 关系
现在 `delegate` 已经能工作，但在主循环里还是被当成一种特殊工具。

后面如果子 agent 变多，父子 run 的关系值得被单独建模。

## 面试里怎么讲runtime相关的内容
> Pico 的 runtime 主循环负责把一条用户请求推进成一次独立 run。`ask()` 开头先建 `TaskState`、run 目录和第一条 trace，然后每轮都重新组 prompt、调模型、解析输出。如果模型要调工具，统一先过 `run_tool()`，工具结果回来以后再写回 history、task state、trace 和 memory，继续下一轮。停机条件也很清楚，要么拿到 final answer，要么命中 step limit 或 retry limit。这样一轮请求从开始到结束都会留下工件和状态，不会只剩一句最后答案。
>

## 总结
`Pico` 的运行时主循环，做的是把一句用户请求推进成一次有状态、有边界、可回放的执行。

这条主线把 prompt、model、tool、state、artifact 全部接到了同一个控制流里。前面的仓库基线、后面的工具治理、记忆系统、缓存和审计，最后都要挂在这条线上，整个 agent 才像一个真正能持续工作的 runtime。
