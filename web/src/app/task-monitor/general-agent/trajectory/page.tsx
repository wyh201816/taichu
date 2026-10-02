import { redirect } from "next/navigation";

export default async function GeneralAgentTrajectoryPage({ searchParams }: {
  searchParams: Promise<{ conversation?: string | string[]; run?: string | string[] }>;
}) {
  const params = await searchParams;
  const query = new URLSearchParams({ assistant: "general", view: "trajectory" });
  if (typeof params.conversation === "string") query.set("conversation", params.conversation);
  if (typeof params.run === "string") query.set("run", params.run);
  redirect(`/agent-workbench?${query}`);
}
