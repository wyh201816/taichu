"use client";

import Link from "next/link";
import { GitBranch, Scale } from "lucide-react";

import { cn } from "@/lib/utils";

export function GeneralAgentMonitorNav({
  active,
  accent = false,
}: {
  active: "monitor" | "evaluation";
  accent?: boolean;
}) {
  const items = [
    {
      key: "monitor" as const,
      href: "/task-monitor/general-agent",
      label: "节点监控",
      icon: GitBranch,
    },
    {
      key: "evaluation" as const,
      href: "/task-monitor/general-agent/evaluation/multi-step",
      label: "效果评测",
      icon: Scale,
    },
  ];
  return (
    <nav
      aria-label="通用写作助手任务监控"
      className={cn(
        "flex items-center rounded-[var(--tc-radius-control)] bg-[var(--tc-surface-card)]",
        accent
          ? "h-12 gap-4 px-5"
          : "gap-1 p-1",
      )}
    >
      {items.map(item => {
        const Icon = item.icon;
        const isActive = active === item.key;
        return (
          <Link
            key={item.key}
            href={item.href}
            aria-current={isActive ? "page" : undefined}
            className={cn(
              "relative inline-flex items-center gap-1.5 rounded-[var(--tc-radius-control)] text-sm transition-colors duration-150 motion-reduce:transition-none",
              accent ? "h-full px-2" : "px-3 py-2",
              isActive
                ? "bg-[var(--tc-surface-muted)] text-[var(--tc-text-primary)]"
                : accent
                  ? "text-[var(--tc-text-muted)] hover:text-[var(--tc-text-primary)]"
                  : "text-[var(--tc-text-muted)] hover:bg-[var(--tc-surface-muted)] hover:text-[var(--tc-text-primary)]",
            )}
          >
            <Icon className="size-4" />
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
