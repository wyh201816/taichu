import type {
  GeneralAgentNodeRun,
  GeneralAgentNodeStatus,
  GeneralAgentRun,
  GeneralAgentRunStatus,
} from "@/lib/types/general-agent";

const activeStatuses = new Set<GeneralAgentRunStatus>([
  "init",
  "clarifying",
  "planning",
  "executing",
  "verifying",
  "replanning",
]);

export const generalRunStatusLabels: Record<GeneralAgentRunStatus, string> = {
  init: "正在初始化",
  clarifying: "正在判断信息缺口",
  planning: "正在规划",
  executing: "正在执行",
  waiting_human: "等待作者",
  verifying: "正在校验",
  replanning: "正在重新规划",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  timeout: "已超时",
};

export const generalNodeStatusLabels: Record<GeneralAgentNodeStatus, string> = {
  pending: "等待中",
  running: "执行中",
  success: "已完成",
  failed: "失败",
  skipped: "已跳过",
  waiting_human: "等待作者",
};

const capabilityLabels: Record<string, string> = {
  get_novel_structure: "读取小说结构",
  get_knowledge_chapter_coverage: "统计知识库章节覆盖",
  read_manuscript: "读取正文",
  retrieve_story_context: "统一检索小说证据",
  resolve_knowledge_identity: "知识身份匹配",
  list_knowledge_catalog: "浏览知识目录",
  read_knowledge_cards: "读取知识卡",
  search_external_sources: "搜索外部资料",
  read_external_source: "读取外部来源",
  preview_manuscript_patch: "预览正文修改",
  apply_manuscript_patch: "写入正文修改",
  create_novel_structure_items: "创建卷章",
  update_novel_structure: "调整卷章结构",
  delete_novel_structure_items: "归档卷章",
  create_confirmed_knowledge: "创建确认知识",
  update_confirmed_knowledge: "更新确认知识",
  canon_evidence: "小说事实取证",
  external_research: "外部资料研究",
  narrative_summary: "叙事归纳",
  worldbuilding: "世界设定",
  character: "人物设计",
  story_architecture: "剧情架构",
  scene_planning: "场景规划",
  drafting: "正文创作",
  revision: "正文修改",
  consistency_reviewer: "一致性审查",
  narrative_reviewer: "叙事审查",
  style_reviewer: "文风审查",
  "general_writing_orchestrator.plan": "高层任务规划",
  "general_writing_orchestrator.replan": "重新规划任务",
  "context.extract": "提取运行上下文",
  "general_writing_orchestrator.verify": "执行结果校验",
};

export function isGeneralAgentRunActive(status: GeneralAgentRunStatus): boolean {
  return activeStatuses.has(status);
}

export function generalNodeStatusLabel(
  nodeStatus: GeneralAgentNodeStatus,
  runStatus: GeneralAgentRunStatus,
): string {
  if (nodeStatus === "failed" && isGeneralAgentRunActive(runStatus)) {
    return "等待自动修复";
  }
  return generalNodeStatusLabels[nodeStatus];
}

export function generalNodeVisualStatus(
  nodeStatus: GeneralAgentNodeStatus,
  runStatus: GeneralAgentRunStatus,
): GeneralAgentNodeStatus {
  if (nodeStatus === "failed" && isGeneralAgentRunActive(runStatus)) {
    return "running";
  }
  return nodeStatus;
}

export function generalNodeErrorMessage(
  errorMessage: string | null | undefined,
  runStatus: GeneralAgentRunStatus,
): string | null {
  if (!errorMessage || isGeneralAgentRunActive(runStatus)) {
    return null;
  }
  return errorMessage;
}

export function generalRunProgressSummary(run: GeneralAgentRun): string {
  const nodes = currentGeneralAgentNodes(run);
  if (
    run.status === "replanning" ||
    (isGeneralAgentRunActive(run.status) &&
      nodes.some(node => node.status === "failed"))
  ) {
    return "正在修复执行问题";
  }
  if (!nodes.length) {
    return "本次任务无需调用额外能力";
  }
  const completedNodes = nodes.filter(node => node.status === "success").length;
  return `已完成 ${completedNodes}/${nodes.length} 个能力步骤`;
}

export function generalAgentContinuationRequestIndex(
  run: GeneralAgentRun,
  conversationRuns: GeneralAgentRun[],
): number | undefined {
  if (run.status !== "waiting_human") {
    return undefined;
  }
  return conversationRuns.find(item => item.parent_run_id === run.run_id)
    ?.request_index;
}

export function generalCapabilityLabel(name: string): string {
  if (name === "general_writing_orchestrator.plan.materialize" || name === "general_writing_orchestrator.replan.materialize") return "补齐能力调用参数";
  return knownGeneralCapabilityLabel(name) ?? "未识别能力";
}

export function knownGeneralCapabilityLabel(name: string): string | undefined {
  return capabilityLabels[name];
}

export function currentGeneralAgentNodes(run: GeneralAgentRun): GeneralAgentNodeRun[] {
  return run.node_runs.filter(node => node.plan_revision === run.plan_revision);
}
