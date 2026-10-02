"use client";

import Link from "next/link";
import {
  Activity,
  Bot,
  ChevronRight,
  GitBranch,
  Network,
  RefreshCw,
  Scale,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { listAgentTasks } from "@/lib/api/agent-workbench";
import { listGeneralAgentRuns } from "@/lib/api/general-agent";
import type { AgentRunSummary } from "@/lib/types/agent-workbench";
import type { GeneralAgentRunSummary } from "@/lib/types/general-agent";
import { isGeneralAgentRunActive } from "@/lib/general-agent-display";

export function TaskMonitorOverview() {
  const [tasks, setTasks] = useState<AgentRunSummary[]>([]);
  const [generalTasks, setGeneralTasks] = useState<GeneralAgentRunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const runningCount = useMemo(
    () => tasks.filter(task => task.status === "running").length,
    [tasks],
  );
  const generalRunningCount = useMemo(
    () => generalTasks.filter(task => isGeneralAgentRunActive(task.status)).length,
    [generalTasks],
  );

  async function load() {
    try {
      const [response, generalResponse] = await Promise.all([
        listAgentTasks(),
        listGeneralAgentRuns({ pageSize: 100 }),
      ]);
      setTasks(response.runs);
      setGeneralTasks(generalResponse.runs);
      setError("");
    } catch {
      setError("任务监控加载失败，请确认后端服务是否可用");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let ignore = false;
    async function loadFromEffect() {
      try {
        const [response, generalResponse] = await Promise.all([
          listAgentTasks(),
          listGeneralAgentRuns({ pageSize: 100 }),
        ]);
        if (ignore) {
          return;
        }
        setTasks(response.runs);
        setGeneralTasks(generalResponse.runs);
        setError("");
      } catch {
        if (!ignore) {
          setError("任务监控加载失败，请确认后端服务是否可用");
        }
      } finally {
        if (!ignore) {
          setLoading(false);
        }
      }
    }

    void loadFromEffect();
    const timer = window.setInterval(() => {
      void loadFromEffect();
    }, 3000);
    return () => {
      ignore = true;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <AppShell activePath="/task-monitor">
      <section className="mx-auto max-w-[1200px] px-5 py-6">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div>
            <p className="text-xs text-[var(--tc-text-muted)]">任务监控</p>
            <h1 className="text-lg font-semibold text-[var(--tc-text-primary)]">
              智能体任务入口
            </h1>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label="刷新任务概览"
            onClick={() => void load()}
          >
            <RefreshCw className="size-4" />
          </Button>
        </div>

        {error ? (
          <div className="mb-3 rounded-[var(--tc-radius-control)] border border-red-700/70 bg-red-950/20 px-3 py-2 text-sm text-[var(--tc-text-primary)]">
            {error}
          </div>
        ) : null}

        <div className="grid grid-cols-2 gap-4">
          <div className="grid content-start gap-2">
            <h2 className="px-1 text-sm font-medium text-[var(--tc-text-secondary)]">监控</h2>

            <Link
              href="/task-monitor/knowledge-extraction"
              className="flex items-center justify-between gap-3 rounded-[var(--tc-radius-control)] border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-card)] px-3 py-3 text-sm text-[var(--tc-text-primary)] hover:bg-[var(--tc-surface-muted)]"
            >
              <span className="flex min-w-0 items-center gap-3">
                <span className="flex size-8 shrink-0 items-center justify-center rounded-[var(--tc-radius-control)] border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-muted)] text-[var(--tc-monitor-knowledge)]">
                  <Bot className="size-4" />
                </span>
                <span className="min-w-0">
                  <span className="block font-medium">知识沉淀工作流监控</span>
                  <span className="mt-0.5 flex items-center gap-2 text-xs text-[var(--tc-text-muted)]">
                    <Activity className="size-3" />
                    {loading
                      ? "正在读取任务"
                      : `共 ${tasks.length} 个任务，${runningCount > 0 ? `${runningCount} 个运行中` : "当前无运行中任务"}`}
                  </span>
                </span>
              </span>
              <ChevronRight className="size-4 shrink-0 text-[var(--tc-text-muted)]" />
            </Link>

            <Link
              href="/task-monitor/general-agent"
              className="flex items-center justify-between gap-3 rounded-[var(--tc-radius-control)] border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-card)] px-3 py-3 text-sm text-[var(--tc-text-primary)] hover:bg-[var(--tc-surface-muted)]"
            >
              <span className="flex min-w-0 items-center gap-3">
                <span className="flex size-8 shrink-0 items-center justify-center rounded-[var(--tc-radius-control)] border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-muted)] text-[var(--tc-monitor-general-agent)]">
                  <GitBranch className="size-4" />
                </span>
                <span className="min-w-0">
                  <span className="block font-medium">通用写作智能体监控</span>
                  <span className="mt-0.5 flex items-center gap-2 text-xs text-[var(--tc-text-muted)]">
                    <Activity className="size-3" />
                    {loading
                      ? "正在读取任务"
                      : `共 ${generalTasks.length} 个任务，${generalRunningCount > 0 ? `${generalRunningCount} 个运行中` : "当前无运行中任务"}`}
                  </span>
                </span>
              </span>
              <ChevronRight className="size-4 shrink-0 text-[var(--tc-text-muted)]" />
            </Link>

            <Link
              href="/task-monitor/rag"
              className="flex items-center justify-between gap-3 rounded-[var(--tc-radius-control)] border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-card)] px-3 py-3 text-sm text-[var(--tc-text-primary)] hover:bg-[var(--tc-surface-muted)]"
            >
              <span className="flex min-w-0 items-center gap-3">
                <span className="flex size-8 shrink-0 items-center justify-center rounded-[var(--tc-radius-control)] border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-muted)] text-[var(--tc-monitor-rag)]">
                  <Network className="size-4" />
                </span>
                <span className="min-w-0">
                  <span className="block font-medium">RAG 建模监控</span>
                  <span className="mt-0.5 block text-xs text-[var(--tc-text-muted)]">
                    查看正文片段、知识卡、实体、关系与 Milvus 集合状态
                  </span>
                </span>
              </span>
              <ChevronRight className="size-4 shrink-0 text-[var(--tc-text-muted)]" />
            </Link>
          </div>

          <div className="grid content-start gap-2">
            <h2 className="px-1 text-sm font-medium text-[var(--tc-text-secondary)]">评测</h2>

            <Link
              href="/task-monitor/knowledge-extraction/evaluation"
              className="flex items-center justify-between gap-3 rounded-[var(--tc-radius-control)] border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-card)] px-3 py-3 text-sm text-[var(--tc-text-primary)] hover:bg-[var(--tc-surface-muted)]"
            >
              <span className="flex min-w-0 items-center gap-3">
                <span className="flex size-8 shrink-0 items-center justify-center rounded-[var(--tc-radius-control)] border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-muted)] text-[var(--tc-monitor-knowledge)]">
                  <Scale className="size-4" />
                </span>
                <span className="min-w-0">
                  <span className="block font-medium">知识沉淀工作流评测</span>
                  <span className="mt-0.5 block text-xs text-[var(--tc-text-muted)]">
                    对比历史任务输出、期望知识卡与差异明细
                  </span>
                </span>
              </span>
              <ChevronRight className="size-4 shrink-0 text-[var(--tc-text-muted)]" />
            </Link>

            <Link
              href="/task-monitor/general-agent/evaluation/multi-step"
              className="flex items-center justify-between gap-3 rounded-[var(--tc-radius-control)] border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-card)] px-3 py-3 text-sm text-[var(--tc-text-primary)] hover:bg-[var(--tc-surface-muted)]"
            >
              <span className="flex min-w-0 items-center gap-3">
                <span className="flex size-8 shrink-0 items-center justify-center rounded-[var(--tc-radius-control)] border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-muted)] text-[var(--tc-monitor-general-agent)]">
                  <Scale className="size-4" />
                </span>
                <span className="min-w-0">
                  <span className="block font-medium">通用写作智能体评测</span>
                  <span className="mt-0.5 block text-xs text-[var(--tc-text-muted)]">
                    评估完成度、能力路径、权限边界、执行健康和答案覆盖
                  </span>
                </span>
              </span>
              <ChevronRight className="size-4 shrink-0 text-[var(--tc-text-muted)]" />
            </Link>

            <Link
              href="/task-monitor/rag/evaluation"
              className="flex items-center justify-between gap-3 rounded-[var(--tc-radius-control)] border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-card)] px-3 py-3 text-sm text-[var(--tc-text-primary)] hover:bg-[var(--tc-surface-muted)]"
            >
              <span className="flex min-w-0 items-center gap-3">
                <span className="flex size-8 shrink-0 items-center justify-center rounded-[var(--tc-radius-control)] border border-[var(--tc-border-subtle)] bg-[var(--tc-surface-muted)] text-[var(--tc-monitor-rag)]">
                  <Scale className="size-4" />
                </span>
                <span className="min-w-0">
                  <span className="block font-medium">RAG 质量评测</span>
                  <span className="mt-0.5 block text-xs text-[var(--tc-text-muted)]">
                    查看 Golden 用例、Graph 关系链与最近回归结果
                  </span>
                </span>
              </span>
              <ChevronRight className="size-4 shrink-0 text-[var(--tc-text-muted)]" />
            </Link>

          </div>
        </div>
      </section>
    </AppShell>
  );
}
