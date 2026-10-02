import assert from "node:assert/strict";
import { generalSubagentResultViewKind, generalSubagentResultViewKinds, readableEntries, splitReadableContent } from "../../src/lib/general-agent-readable-content";

assert.deepEqual(Object.keys(generalSubagentResultViewKinds).sort(), [
  "canon_evidence",
  "character",
  "consistency_reviewer",
  "drafting",
  "external_research",
  "narrative_reviewer",
  "narrative_summary",
  "revision",
  "scene_planning",
  "story_architecture",
  "style_reviewer",
  "worldbuilding",
]);
assert.equal(
  generalSubagentResultViewKind("consistency_reviewer"),
  "consistency_review",
);
assert.equal(generalSubagentResultViewKind("unknown_agent"), undefined);
assert.deepEqual(
  readableEntries({
    query: "秦浩轩",
    run_id: "隐藏",
    answer: "人物说明",
    knowledge_type: "character",
    resolution: "not_found",
  }),
  [
    { key: "query", label: "检索内容", value: "秦浩轩" },
    { key: "answer", label: "回答", value: "人物说明" },
    { key: "knowledge_type", label: "知识类型", value: "人物" },
    { key: "resolution", label: "解析结果", value: "未找到" },
  ],
);

const embeddedMemory = splitReadableContent(
  '资源摘要：已读取人物资料。结果摘要：{"lifecycle":"draft","artifact_type":"narrative_summary","summary":"秦浩轩完成引气。","source_refs":["internal-ref"]}',
);
assert.equal(embeddedMemory[0]?.kind, "text");
assert.equal(embeddedMemory[1]?.kind, "structured");
if (embeddedMemory[1]?.kind === "structured") {
  assert.deepEqual(readableEntries(embeddedMemory[1].value), [
    { key: "lifecycle", label: "记录状态", value: "待审核" },
    { key: "artifact_type", label: "产物类型", value: "叙事摘要" },
    { key: "summary", label: "内容摘要", value: "秦浩轩完成引气。" },
    { key: "source_refs", label: "来源证据", value: "已携带 1 处可追溯来源" },
  ]);
}
const truncatedMemory = splitReadableContent(
  '资料摘要：已完成检索。结果摘要：{"kind":"resource_summary","summary":"内容被截断…',
);
const truncatedTail = truncatedMemory.at(-1);
assert.equal(truncatedTail?.kind, "text");
assert.match(
  truncatedTail?.kind === "text" ? truncatedTail.text : "",
  /技术残片不在作者页面展示/,
);
assert.deepEqual(readableEntries({ kind: "resource_summary" }), [
  { key: "kind", label: "执行类型", value: "资料摘要" },
]);
assert.deepEqual(
  readableEntries({
    summary: "保留的业务内容",
    policy_name: "general_agent_runtime",
    unknown_internal_key: "不应展示",
  }),
  [{ key: "summary", label: "内容摘要", value: "保留的业务内容" }],
);
const localizedPrompt = splitReadableContent("结论应能回到 source_ref，并使用 [retrieve_story_context] 取证。");
assert.deepEqual(localizedPrompt, [
  { kind: "text", text: "结论应能回到 来源证据，并使用 【统一检索小说证据】 取证。" },
]);
assert.deepEqual(splitReadableContent("不要使用 Markdown 代码块。输出 Schema："), [
  { kind: "text", text: "不要使用 代码块。模型返回要求：" },
]);
console.log("通用写作助手可读内容测试通过。");
