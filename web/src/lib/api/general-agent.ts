import { API_BASE_URL, apiRequest } from "@/lib/api-client";
import type {
  AgentMemoryListResponse,
  ContextCompactionOperation,
  GeneralAgentConversationDeleteResponse,
  GeneralAgentConversationListResponse,
  GeneralAgentConversationResponse,
  GeneralAgentContextSnapshotListResponse,
  GeneralAgentLLMReplayListResponse,
  GeneralAgentResumeRequest,
  GeneralAgentRecoveryResponse,
  GeneralAgentRunListResponse,
  GeneralAgentRunRequest,
  GeneralAgentRunResponse,
  GeneralAgentStreamEvent,
  GeneralAgentTraceListResponse,
} from "@/lib/types/general-agent";

const PREFIX = "/api/agent-workbench/general-assistant";

export async function streamGeneralAgentEvents(
  onEvent: (event: GeneralAgentStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}${PREFIX}/runs/stream/events`, {
    signal,
  });
  if (!response.ok) {
    throw new Error(`运行状态流连接失败：${response.status}`);
  }
  if (!response.body) {
    throw new Error("浏览器未返回通用写作助手运行状态流。");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      dispatchGeneralAgentEvent(line, onEvent);
    }
  }
  buffer += decoder.decode();
  dispatchGeneralAgentEvent(buffer, onEvent);
}

function dispatchGeneralAgentEvent(
  line: string,
  onEvent: (event: GeneralAgentStreamEvent) => void,
) {
  const trimmed = line.trim();
  if (!trimmed) {
    return;
  }
  onEvent(JSON.parse(trimmed) as GeneralAgentStreamEvent);
}

export async function startGeneralAgentRun(
  request: GeneralAgentRunRequest,
): Promise<GeneralAgentRunResponse> {
  return apiRequest<GeneralAgentRunResponse>(`${PREFIX}/runs/start`, {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export async function listGeneralAgentRuns(options?: {
  page?: number;
  pageSize?: number;
  status?: string;
}): Promise<GeneralAgentRunListResponse> {
  const params = new URLSearchParams({
    page: String(options?.page ?? 1),
    page_size: String(options?.pageSize ?? 30),
    status: options?.status ?? "all",
  });
  return apiRequest<GeneralAgentRunListResponse>(
    `${PREFIX}/runs?${params.toString()}`,
  );
}

export async function listGeneralAgentConversations(options?: {
  page?: number;
  pageSize?: number;
}): Promise<GeneralAgentConversationListResponse> {
  const params = new URLSearchParams({
    page: String(options?.page ?? 1),
    page_size: String(options?.pageSize ?? 30),
  });
  return apiRequest<GeneralAgentConversationListResponse>(
    `${PREFIX}/conversations?${params.toString()}`,
  );
}

export async function getGeneralAgentConversation(
  conversationId: string,
): Promise<GeneralAgentConversationResponse> {
  return apiRequest<GeneralAgentConversationResponse>(
    `${PREFIX}/conversations/${encodeURIComponent(conversationId)}`,
  );
}

export async function listGeneralAgentMemories(
  conversationId: string,
  options?: { includeDeleted?: boolean },
): Promise<AgentMemoryListResponse> {
  const params = new URLSearchParams({
    include_deleted: String(options?.includeDeleted ?? false),
  });
  return apiRequest<AgentMemoryListResponse>(
    `${PREFIX}/conversations/${encodeURIComponent(conversationId)}/memories?${params.toString()}`,
  );
}

export async function deleteGeneralAgentConversation(
  conversationId: string,
): Promise<GeneralAgentConversationDeleteResponse> {
  return apiRequest<GeneralAgentConversationDeleteResponse>(
    `${PREFIX}/conversations/${encodeURIComponent(conversationId)}`,
    { method: "DELETE" },
  );
}

export async function getGeneralAgentRun(
  runId: string,
): Promise<GeneralAgentRunResponse> {
  return apiRequest<GeneralAgentRunResponse>(
    `${PREFIX}/runs/${encodeURIComponent(runId)}`,
  );
}

export async function resumeGeneralAgentRun(
  runId: string,
  request: GeneralAgentResumeRequest,
): Promise<GeneralAgentRunResponse> {
  return apiRequest<GeneralAgentRunResponse>(
    `${PREFIX}/runs/${encodeURIComponent(runId)}/resume`,
    { method: "POST", body: JSON.stringify(request) },
  );
}

export async function cancelGeneralAgentRun(
  runId: string,
): Promise<GeneralAgentRunResponse> {
  return apiRequest<GeneralAgentRunResponse>(
    `${PREFIX}/runs/${encodeURIComponent(runId)}/cancel`,
    { method: "POST" },
  );
}

export async function deleteGeneralAgentRun(
  runId: string,
): Promise<{ run_id: string; deleted: boolean }> {
  return apiRequest<{ run_id: string; deleted: boolean }>(
    `${PREFIX}/runs/${encodeURIComponent(runId)}`,
    { method: "DELETE" },
  );
}

export async function listGeneralAgentTraces(
  runId: string,
  limit = 500,
): Promise<GeneralAgentTraceListResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  return apiRequest<GeneralAgentTraceListResponse>(
    `${PREFIX}/runs/${encodeURIComponent(runId)}/traces?${params.toString()}`,
  );
}

export async function getGeneralAgentRecovery(
  runId: string,
): Promise<GeneralAgentRecoveryResponse> {
  return apiRequest<GeneralAgentRecoveryResponse>(
    `${PREFIX}/runs/${encodeURIComponent(runId)}/recovery`,
  );
}

export async function listGeneralAgentContextSnapshots(
  runId: string,
): Promise<GeneralAgentContextSnapshotListResponse> {
  return apiRequest<GeneralAgentContextSnapshotListResponse>(
    `${PREFIX}/runs/${encodeURIComponent(runId)}/context-snapshots`,
  );
}

export async function listGeneralAgentLLMReplays(
  runId: string,
): Promise<GeneralAgentLLMReplayListResponse> {
  return apiRequest<GeneralAgentLLMReplayListResponse>(
    `${PREFIX}/runs/${encodeURIComponent(runId)}/llm-replays`,
  );
}

export async function requestContextCompaction(conversationId: string) {
  return apiRequest<{ operation: ContextCompactionOperation }>(`${PREFIX}/conversations/${encodeURIComponent(conversationId)}/compact`, { method: "POST" });
}
export async function getContextCompactionStatus(conversationId: string) {
  return apiRequest<{ operation: ContextCompactionOperation | null }>(`${PREFIX}/conversations/${encodeURIComponent(conversationId)}/compact`);
}
