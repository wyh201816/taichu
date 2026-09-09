import { Suspense } from "react";

import { GeneralAgentEvaluationShell } from "@/components/agent-task-monitor/general-agent-evaluation-shell";

export default function GeneralAgentRecoveryEvaluationPage() {
  return (
    <Suspense
      fallback={
        <main className="min-h-screen bg-[#202020] px-8 py-10 text-[#f4f4f4]">
          <p className="text-sm text-[#a7a7a7]">正在加载异常恢复评测…</p>
        </main>
      }
    >
      <GeneralAgentEvaluationShell entryId="recovery" />
    </Suspense>
  );
}
