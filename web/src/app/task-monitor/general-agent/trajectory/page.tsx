import { GeneralAgentTrajectoryShell } from "@/components/agent-task-monitor/general-agent-trajectory-shell";

export default async function GeneralAgentTrajectoryPage({ searchParams }: {
  searchParams: Promise<{ conversation?: string | string[]; run?: string | string[] }>;
}) {
  const params = await searchParams;
  return <GeneralAgentTrajectoryShell initialConversationId={typeof params.conversation === "string" ? params.conversation : ""}
    initialRunId={typeof params.run === "string" ? params.run : ""} />;
}
