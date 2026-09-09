# Pico 上下文瘦身：长链路任务如何守住有效信息

> 外部写作风格样本，仅用于学习资料的表达方式，不是太初项目的事实来源。

## 背景
问题从 prompt 能不能拼出来，变成了长链路任务里系统怎么把真正有用的上下文留下来。

代码任务一旦进入多轮循环，最先膨胀起来的通常不是用户输入，往往是别的东西：

+ 工具输出
+ 文件内容
+ shell 日志
+ 历史记录
+ 相关记忆

这些内容有个共同点，都可能很长，而且价值分布很不均匀。有些内容对下一步很关键，有些只是调查过程里的副产品。如果系统不做控制，提示词很快就会被旧历史、长日志和重复信息占满。

这里主要给大家讲`Pico` 怎么把上下文瘦身做成一条分层机制。

## 这篇要解决什么问题
问题可以压成一句话：

**当 agent 在一轮任务里不断读文件、跑命令、积累历史时，runtime 怎么把最有用的上下文继续送给模型，又不让 prompt 被撑爆。**

这里真正要回答的是四件事：

+ 长输出最早在哪一层就开始收
+ history 和 relevant memory 怎么被压缩
+ 最终 prompt 超预算时，谁先让位
+ 这套规则为什么比粗暴截断更稳

## 为什么不能靠粗暴截断
从工程角度看，粗暴截断的问题不在于它简单，而在于它不知道自己切掉的是什么。

代码仓库 agent 的上下文里，有些东西硬度很高：

+ 当前用户请求
+ 系统规则
+ 仓库基线

有些东西可以压缩：

+ 相关记忆
+ 旧历史
+ 重复读取
+ 长日志

如果不分层，直接对整段 prompt 一刀切，结果经常会变成最该保留的东西被一起切坏。尤其是当前请求，一旦失真，后面所有优化都没意义。

`Pico` 这里的判断很明确，不走一把大剪刀，而是把瘦身拆成几层。

## Pico 现在是怎么做上下文瘦身的
当前这套机制可以拆成四层。

1. 源头先裁长输出
2. 记忆层先做限额
3. 历史按新旧分层压缩
4. 最后按 section 预算再做一轮收缩

![](https://cdn.nlark.com/yuque/__mermaid_v3/99868fffc8b9a0a013f3135565fe47ef.svg)

瘦身不只发生在 `ContextManager.build()` 里。很多长内容一进系统就已经开始被收了。

## 第一层，长输出先在入口处收
`Pico` 的第一道闸门，不在 prompt 组装阶段，而在内容刚进入系统的时候。

`pico/workspace.py` 里有一个通用的 `clip()`：

```python
def clip(text, limit=MAX_TOOL_OUTPUT):
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"
```

默认上限是 `MAX_TOOL_OUTPUT = 4000`。这意味着超长工具结果不会在系统内部无限流动。

当前这层裁剪至少出现在这些地方：

| 场景 | 上限 |
| --- | --- |
| 工具返回值统一裁剪 | 4000 |
| 工作区文档片段 | 1200 |
| `git status --short` | 1500 |
| trace 里的工具结果摘要 | 500 |
| `run_started` 里的用户请求摘要 | 300 |


除此之外，还有一层更细的压缩，`summarize_read_result()` 会把 `read_file` 的结果压成一句很短的摘要：

```python
summary = " | ".join(lines[:3])
return clip(summary, limit)
```

这一步的作用很直接，不让完整文件内容直接冲进长期上下文。

## 第二层，memory 自己先限额
`06` 这一章不展开记忆设计细节，但它和瘦身是绑在一起的，所以这里必须提。

`pico/memory.py` 里现在有几组硬上限：

+ `WORKING_FILE_LIMIT = 8`
+ `EPISODIC_NOTE_LIMIT = 12`
+ `FILE_SUMMARY_LIMIT = 6`

文本本身也会先裁长度：

+ `task_summary` 裁到 300
+ `episodic note` 裁到 500
+ `file summary` 裁到 500

这层设计解决的是一个很实际的问题，记忆自己不能先长成第二份历史。它得保持小，才能真的帮下一轮做事。

## 第三层，Relevant memory 不全带，只带少量
到了 `ContextManager.build(user_message)`，相关记忆会先被召回，再被预算控制。

现在默认只召回 top 3：

```python
selected_notes = self.agent.memory.retrieval_candidates(
    user_message,
    limit=RELEVANT_MEMORY_LIMIT,
)
```

`RELEVANT_MEMORY_LIMIT` 当前是 `3`。这里还有一个很实用的设计，每条 note 会平分这一段预算，不让一条超长笔记把其他笔记都挤掉。

代码在 `_render_relevant_memory()`：

```python
per_note_budget = self._per_note_budget(budget, len(note_texts), header)
rendered_notes = [_tail_clip(text, per_note_budget) for text in note_texts]
```

这一步说明两件事。

+ 相关记忆不会全带
+ 带进去的几条也会继续被压短

## 第四层，history 按新旧分层
最容易膨胀的一层，其实是 transcript。

`ContextManager._render_history_section()` 当前的思路很清楚，它把历史拆成新旧两层来看：

+ 最近窗口保留更多细节
+ 更旧的历史压得更狠

最近窗口当前固定是 `6`：

```python
recent_window = 6
recent_start = max(0, len(history) - recent_window)
```

这相当于一个很朴素的近因优先策略。越近的内容，越可能影响下一步动作，所以保留得更完整。

### 旧历史怎么压缩
旧历史不会原样回填。系统会把它重新渲染成更轻的摘要形式：

+ 普通 `user` / `assistant` 消息只保留一行短文本
+ 旧的 `read_file` 会按路径去重
+ 旧的工具输出会被压成一行摘要

对应代码里，旧工具和最近工具的单条长度上限本身就不同：

```python
line_limit = 900 if recent else 60
```

这个差异很重要。`Pico` 没有试图让所有历史都同等重要。

### 重复读取怎么折叠
旧历史里的重复 `read_file` 会按路径去重。

也就是说，系统会尽量保留我读过这个文件这件事，不会在旧历史里一遍遍重复我又读了一次。

记忆负责留短摘要，历史负责折叠重复过程。

## 最后一层，section 预算控制
上下文真的装箱，发生在 `ContextManager.build()`。

当前 prompt 的 section 顺序是固定的：

1. `prefix`
2. `memory`
3. `relevant_memory`
4. `history`
5. `current_request`

默认预算也是固定的：

| 部分 | 默认预算 |
| --- | --- |
| `prefix` | 3600 |
| `memory` | 1600 |
| `relevant_memory` | 1200 |
| `history` | 5200 |
| `current_request` | 不裁剪 |


默认总预算是 `12000`。

如果超预算，系统不会对整段 prompt 一刀切，而是按固定顺序收：

```python
DEFAULT_REDUCTION_ORDER = ("relevant_memory", "history", "memory", "prefix")
```

这条顺序很有代表性。平台已经把谁先让位写死了。

+ 先让 `relevant_memory` 收
+ 再让 `history` 收
+ 再动 `memory`
+ 最后才动 `prefix`

`current_request` 根本不进裁剪流程，它一直原样保留。

这就是 `Pico` 在上下文治理里的核心判断，**<font style="color:#DF2A3F;">用户当前这句话最硬，不能失真</font>**。

## floor 现在怎么生效
`pico/context_manager.py` 里虽然定义了 `DEFAULT_SECTION_FLOORS`，但默认运行时用的 floor 计算来自 `_compute_section_floors()`：

```python
floors = {
    section: max(20, int(budget) // 4)
    for section, budget in self.section_budgets.items()
}
```

也就是说，默认情况下，每个 section 最低会被压到自身预算的四分之一，除非显式传 `section_floors` 去覆盖。

## 这一层的设计怎么被验证
### 第一类是行为测试
`tests/test_context_manager.py` 现在已经覆盖了几件关键事：

+ section 顺序固定
+ 超预算时先裁 `relevant_memory` 再裁 `history`
+ `Relevant memory` 最多只保留 3 条
+ 当前用户请求会被完整保留
+ 新的上下文比更旧的上下文更容易留下来

这些测试解决的是机制有没有按当前设计工作。

### 第二类是长上下文实验
`pico/metrics.py` 和现有指标文档里，已经有 `full` 和 `no_context_reduction` 的对照。

当前大口径结果可以直接压成这几句：

+ 在 12 组长上下文实验配置下，平均 prompt 长度从 `6964` 压到 `5418`
+ 平均压缩率 `18.01%`
+ 最高压缩率 `35.63%`

## 目前没做什么
这套设计已经够用，但边界也很明确。

+ 现在还是 char 级预算控制，还没做到 token-aware
+ 规则能决定先裁哪一层，还不能真正理解哪一句最重要
+ 重复检测主要集中在旧 `read_file` 这一类场景
+ 最近窗口固定是 `6`，还没有更细的动态策略

## 如果继续往下做，优先怎么补
我会优先补三件事。

### 一，把预算从 char 往 token 再推进一步
现在这版已经够稳，但不同 provider 之间要做更公平的比较，token-aware 会更合适。

### 二，把历史保留策略再做细一点
现在是固定 recent window，后面可以继续区分失败事件、关键工具结果和普通历史。

### 三，把诊断视图再做清楚一点
现在 metadata 已经有 `budget_reductions`、`rendered_chars` 这些信息。后面还可以继续往前走，直接回答哪一层最常爆预算、哪一类内容最常被裁掉。

## 面试里怎么讲这一层
> Pico 的上下文治理没有走粗暴截断这条路。我做的是分层瘦身。长输出先在入口处裁掉，记忆层自己先做数量和长度限制，历史记录按新旧分层，最后 `ContextManager` 再按 section 预算做一轮收缩。超预算时先裁 `relevant_memory`，再裁 `history`，再裁 `memory`，最后才动 `prefix`。`current_request` 永远不裁。这套设计的核心是该保留的东西尽量稳定留下来，不让旧日志和重复上下文把当前请求埋掉。
>

## 总结
