import type { ReactNode } from "react";

import {
  generalSubagentResultViewKind,
  type SubagentResultViewKind,
} from "@/lib/general-agent-readable-content";

export function GeneralAgentSubagentResult({
  capabilityName,
  value,
}: {
  capabilityName: string;
  value: unknown;
}) {
  const kind = generalSubagentResultViewKind(capabilityName);
  if (!kind || !hasGeneralAgentResult(value)) return null;
  return <SubagentResultValue kind={kind} value={value} />;
}

export function hasGeneralAgentResult(value: unknown): boolean {
  if (value === null || value === undefined || value === "") return false;
  if (Array.isArray(value)) return value.length > 0;
  if (isRecord(value)) return Object.keys(value).length > 0;
  return true;
}

function SubagentResultValue({
  kind,
  value,
}: {
  kind: SubagentResultViewKind;
  value: unknown;
}) {
  if (kind === "canon_evidence") return <CanonEvidenceResult value={value} />;
  if (kind === "external_research") return <ExternalResearchResult value={value} />;
  if (kind === "narrative_summary") return <NarrativeSummaryResult value={value} />;
  if (kind === "worldbuilding") return <WorldbuildingResult value={value} />;
  if (kind === "character") return <CharacterResult value={value} />;
  if (kind === "story_architecture") return <StoryArchitectureResult value={value} />;
  if (kind === "scene_planning") return <ScenePlanningResult value={value} />;
  if (kind === "drafting") return <WritingResult value={value} mode="drafting" />;
  if (kind === "revision") return <WritingResult value={value} mode="revision" />;
  return <ReviewResult value={value} kind={kind} />;
}

function CanonEvidenceResult({ value }: { value: unknown }) {
  const output = record(value);
  const evidence = records(output.evidence);
  const conflicts = records(output.conflicting_evidence);
  return (
    <ResultShell
      summary={string(output.answer) || "没有形成事实结论。"}
      meta={`可信度：${confidenceLabel(string(output.confidence))}`}
    >
      <EvidenceList title="支持证据" items={evidence} />
      <EvidenceList title="冲突证据" items={conflicts} />
      <TextList title="尚未确认" items={strings(output.unknowns)} />
      <Warnings value={output.warnings} />
    </ResultShell>
  );
}

function ExternalResearchResult({ value }: { value: unknown }) {
  const output = record(value);
  const sources = records(output.sources);
  return (
    <ResultShell
      summary={string(output.conclusion) || "没有形成研究结论。"}
      meta={`可信度：${confidenceLabel(string(output.confidence))}`}
    >
      {sources.length ? (
        <ResultSection title={`资料来源（${sources.length}）`}>
          <div className="grid gap-1">
            {sources.map((source, index) => {
              const url = string(source.url);
              return (
                <article
                  key={`${url}-${index}`}
                  className="rounded-[var(--tc-radius-control)] px-2.5 py-2 odd:bg-[var(--tc-surface-muted)]"
                >
                  <p className="text-xs font-medium text-[var(--tc-text-primary)]">
                    {string(source.title) || `来源 ${index + 1}`}
                  </p>
                  {string(source.claim) ? (
                    <p className="mt-1 whitespace-pre-wrap text-xs leading-5 text-[var(--tc-text-secondary)]">
                      {string(source.claim)}
                    </p>
                  ) : null}
                  {string(source.reliability_note) ? (
                    <p className="mt-1 text-[11px] leading-5 text-[var(--tc-text-muted)]">
                      {string(source.reliability_note)}
                    </p>
                  ) : null}
                  {isHttpUrl(url) ? (
                    <a
                      href={url}
                      target="_blank"
                      rel="noreferrer"
                      className="mt-1 block break-all text-[11px] text-cyan-300 hover:underline"
                    >
                      {url}
                    </a>
                  ) : null}
                </article>
              );
            })}
          </div>
        </ResultSection>
      ) : null}
      <TextList title="资料分歧" items={strings(output.disagreements)} />
      <TextList title="时效提醒" items={strings(output.timeliness_notes)} />
      <Warnings value={output.warnings} />
    </ResultShell>
  );
}

function NarrativeSummaryResult({ value }: { value: unknown }) {
  const output = record(value);
  return (
    <ResultShell summary={string(output.summary) || "没有形成叙事摘要。"}>
      <TextList title="关键事件" items={strings(output.key_events)} numbered />
      <TextList title="人物变化" items={strings(output.character_changes)} />
      <TextList title="未解决事项" items={strings(output.unresolved_items)} />
      <Warnings value={output.warnings} />
    </ResultShell>
  );
}

function WorldbuildingResult({ value }: { value: unknown }) {
  const output = record(value);
  return (
    <ResultShell summary={string(output.proposal) || "没有形成世界设定方案。"}>
      <TextList title="规则" items={strings(output.rules)} numbered />
      <TextList title="代价" items={strings(output.costs)} />
      <TextList title="约束" items={strings(output.constraints)} />
      <TextList title="冲突风险" items={strings(output.conflict_risks)} />
      <KnowledgeProposals value={output.knowledge_proposals} />
      <Warnings value={output.warnings} />
    </ResultShell>
  );
}

function CharacterResult({ value }: { value: unknown }) {
  const output = record(value);
  return (
    <ResultShell summary={string(output.analysis) || "没有形成人物分析。"}>
      <TextList title="建议" items={strings(output.proposals)} numbered />
      <TextList title="关系变化" items={strings(output.relationship_changes)} />
      <TextList title="行为边界" items={strings(output.behavior_constraints)} />
      <TextList title="风险" items={strings(output.risks)} />
      <KnowledgeProposals value={output.knowledge_proposals} />
      <Warnings value={output.warnings} />
    </ResultShell>
  );
}

function StoryArchitectureResult({ value }: { value: unknown }) {
  const output = record(value);
  return (
    <ResultShell summary={string(output.overview) || "没有形成剧情架构。"}>
      <TextList title="阶段目标" items={strings(output.stage_goals)} numbered />
      <TextList title="剧情线" items={strings(output.plotlines)} />
      <TextList title="冲突升级" items={strings(output.escalation)} numbered />
      <TextList title="伏笔" items={strings(output.foreshadowing)} />
      <TextList title="前置依赖" items={strings(output.dependencies)} />
      <Warnings value={output.warnings} />
    </ResultShell>
  );
}

function ScenePlanningResult({ value }: { value: unknown }) {
  const output = record(value);
  const beats = records(output.beats);
  return (
    <ResultShell
      summary={string(output.overview) || "没有形成场景方案。"}
      meta={string(output.viewpoint) ? `视角：${string(output.viewpoint)}` : undefined}
    >
      {beats.length ? (
        <ResultSection title={`场景节拍（${beats.length}）`}>
          <ol className="grid gap-1">
            {beats.map((beat, index) => (
              <li
                key={`${number(beat.order)}-${index}`}
                className="grid grid-cols-[28px_minmax(0,1fr)] gap-2 rounded-[var(--tc-radius-control)] px-2 py-2 odd:bg-[var(--tc-surface-muted)]"
              >
                <span className="font-mono text-[11px] text-[var(--tc-text-muted)]">
                  {number(beat.order) || index + 1}
                </span>
                <div className="min-w-0">
                  <p className="text-xs font-medium text-[var(--tc-text-primary)]">
                    {string(beat.goal) || "未命名节拍"}
                  </p>
                  <p className="mt-1 whitespace-pre-wrap text-xs leading-5 text-[var(--tc-text-secondary)]">
                    {string(beat.action) || "没有动作内容。"}
                  </p>
                  {[string(beat.information_release), string(beat.transition)]
                    .filter(Boolean)
                    .map(item => (
                      <p key={item} className="mt-1 text-[11px] leading-5 text-[var(--tc-text-muted)]">
                        {item}
                      </p>
                    ))}
                </div>
              </li>
            ))}
          </ol>
        </ResultSection>
      ) : null}
      <TextList title="连续性要求" items={strings(output.continuity_requirements)} />
      <Warnings value={output.warnings} />
    </ResultShell>
  );
}

function WritingResult({
  value,
  mode,
}: {
  value: unknown;
  mode: "drafting" | "revision";
}) {
  const output = record(value);
  const isRevision = mode === "revision";
  return (
    <ResultShell summary={isRevision ? "修改后的正文" : "生成的正文"}>
      <p className="whitespace-pre-wrap text-[13px] leading-6 text-[var(--tc-text-secondary)]">
        {string(output.text) || "没有返回正文。"}
      </p>
      <TextList
        title={isRevision ? "修改摘要" : "已应用约束"}
        items={strings(isRevision ? output.change_summary : output.constraints_applied)}
      />
      {isRevision ? (
        <TextList title="保留约束" items={strings(output.preserved_constraints)} />
      ) : null}
      <TextList title="风险" items={strings(output.risks)} />
      <Warnings value={output.warnings} />
    </ResultShell>
  );
}

function ReviewResult({
  value,
  kind,
}: {
  value: unknown;
  kind: "consistency_review" | "narrative_review" | "style_review";
}) {
  const output = record(value);
  const issues = records(output.issues);
  return (
    <ResultShell summary={string(output.verdict) || "没有形成审校结论。"}>
      {issues.length ? (
        <ResultSection title={`问题（${issues.length}）`}>
          <div className="grid gap-1">
            {issues.map((issue, index) => (
              <article
                key={`${string(issue.problem)}-${index}`}
                className="rounded-[var(--tc-radius-control)] px-2.5 py-2 odd:bg-[var(--tc-surface-muted)]"
              >
                <div className="flex items-start justify-between gap-3">
                  <p className="text-xs font-medium text-[var(--tc-text-primary)]">
                    {string(issue.problem) || `问题 ${index + 1}`}
                  </p>
                  <span className="shrink-0 text-[11px] text-[var(--tc-text-muted)]">
                    {severityLabel(string(issue.severity))}
                    {string(issue.dimension) ? ` · ${string(issue.dimension)}` : ""}
                  </span>
                </div>
                {string(issue.evidence) ? (
                  <p className="mt-1 whitespace-pre-wrap text-xs leading-5 text-[var(--tc-text-secondary)]">
                    {string(issue.evidence)}
                  </p>
                ) : null}
                {string(issue.suggestion) ? (
                  <p className="mt-1 text-[11px] leading-5 text-[var(--tc-text-muted)]">
                    建议：{string(issue.suggestion)}
                  </p>
                ) : null}
              </article>
            ))}
          </div>
        </ResultSection>
      ) : (
        <p className="text-xs text-[var(--tc-text-muted)]">没有发现需要处理的问题。</p>
      )}
      {kind === "consistency_review" ? (
        <TextList
          title="已检查维度"
          items={strings(output.checked_dimensions).map(reviewDimensionLabel)}
        />
      ) : null}
      {kind === "narrative_review" ? (
        <TextList title="叙事优点" items={strings(output.strengths)} />
      ) : null}
      {kind === "style_review" ? (
        <TextList title="文风观察" items={strings(output.style_observations)} />
      ) : null}
      <Warnings value={output.warnings} />
    </ResultShell>
  );
}

function EvidenceList({
  title,
  items,
}: {
  title: string;
  items: Record<string, unknown>[];
}) {
  if (!items.length) return null;
  return (
    <ResultSection title={`${title}（${items.length}）`}>
      <ol className="grid gap-1">
        {items.map((item, index) => (
          <li
            key={`${string(item.claim)}-${index}`}
            className="grid grid-cols-[28px_minmax(0,1fr)] gap-2 rounded-[var(--tc-radius-control)] px-2 py-2 odd:bg-[var(--tc-surface-muted)]"
          >
            <span className="font-mono text-[11px] text-[var(--tc-text-muted)]">
              {index + 1}
            </span>
            <div className="min-w-0">
              <p className="text-xs leading-5 text-[var(--tc-text-primary)]">
                {string(item.claim) || "未命名证据"}
              </p>
              {string(item.excerpt) ? (
                <p className="mt-1 whitespace-pre-wrap text-[11px] leading-5 text-[var(--tc-text-muted)]">
                  {string(item.excerpt)}
                </p>
              ) : null}
            </div>
          </li>
        ))}
      </ol>
    </ResultSection>
  );
}

function KnowledgeProposals({ value }: { value: unknown }) {
  const proposals = records(value);
  if (!proposals.length) return null;
  return (
    <ResultSection title={`待确认知识（${proposals.length}）`}>
      <div className="grid gap-1">
        {proposals.map((proposal, index) => (
          <article
            key={`${string(proposal.name)}-${index}`}
            className="rounded-[var(--tc-radius-control)] px-2.5 py-2 odd:bg-[var(--tc-surface-muted)]"
          >
            <div className="flex items-start justify-between gap-3">
              <p className="text-xs font-medium text-[var(--tc-text-primary)]">
                {string(proposal.name) || `知识候选 ${index + 1}`}
              </p>
              <span className="text-[11px] text-[var(--tc-text-muted)]">
                {knowledgeTypeLabel(string(proposal.knowledge_type))}
              </span>
            </div>
            <p className="mt-1 whitespace-pre-wrap text-xs leading-5 text-[var(--tc-text-secondary)]">
              {string(proposal.summary) || "没有内容摘要。"}
            </p>
            {string(proposal.rationale) ? (
              <p className="mt-1 text-[11px] leading-5 text-[var(--tc-text-muted)]">
                {string(proposal.rationale)}
              </p>
            ) : null}
          </article>
        ))}
      </div>
    </ResultSection>
  );
}

function TextList({
  title,
  items,
  numbered = false,
}: {
  title: string;
  items: string[];
  numbered?: boolean;
}) {
  if (!items.length) return null;
  return (
    <ResultSection title={`${title}（${items.length}）`}>
      <ol className="grid gap-1">
        {items.map((item, index) => (
          <li
            key={`${item}-${index}`}
            className="grid grid-cols-[28px_minmax(0,1fr)] gap-2 rounded-[var(--tc-radius-control)] px-2 py-1.5 text-xs leading-5 odd:bg-[var(--tc-surface-muted)]"
          >
            <span className="font-mono text-[11px] text-[var(--tc-text-muted)]">
              {numbered ? index + 1 : "·"}
            </span>
            <span className="whitespace-pre-wrap text-[var(--tc-text-secondary)]">{item}</span>
          </li>
        ))}
      </ol>
    </ResultSection>
  );
}

function Warnings({ value }: { value: unknown }) {
  return <TextList title="警告" items={strings(value)} />;
}

function ResultShell({
  summary,
  meta,
  children,
}: {
  summary: string;
  meta?: string;
  children: ReactNode;
}) {
  return (
    <div className="min-w-0">
      <div className="flex items-start justify-between gap-4">
        <p className="min-w-0 whitespace-pre-wrap text-[13px] leading-6 text-[var(--tc-text-secondary)]">
          {summary}
        </p>
        {meta ? (
          <span className="shrink-0 text-[11px] text-[var(--tc-text-muted)]">{meta}</span>
        ) : null}
      </div>
      <div className="tc-editor-scrollbar mt-4 grid max-h-[460px] gap-5 overflow-y-auto pr-2">
        {children}
      </div>
    </div>
  );
}

function ResultSection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section>
      <h4 className="mb-2 text-xs font-medium text-[var(--tc-text-primary)]">{title}</h4>
      {children}
    </section>
  );
}

function record(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}

function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && Boolean(item))
    : [];
}

function string(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function number(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isHttpUrl(value: string): boolean {
  return value.startsWith("https://") || value.startsWith("http://");
}

function confidenceLabel(value: string): string {
  return {
    high: "高",
    medium: "中",
    low: "低",
    unknown: "未知",
  }[value] ?? "未记录";
}

function severityLabel(value: string): string {
  return {
    critical: "严重",
    major: "主要",
    minor: "次要",
    suggestion: "建议",
  }[value] ?? "未分级";
}

function knowledgeTypeLabel(value: string): string {
  return {
    character: "人物",
    realm: "境界",
    technique: "功法",
    location: "地点",
    faction: "势力",
    item: "物品",
    rule: "规则",
    event: "事件",
  }[value] ?? value;
}

function reviewDimensionLabel(value: string): string {
  return {
    world_rules: "世界规则",
    character: "人物",
    timeline: "时间线",
    causality: "因果",
    state_continuity: "状态连续性",
    foreshadowing: "伏笔",
  }[value] ?? value;
}
