export type GeneralAgentRunStatus =
  | "init"
  | "clarifying"
  | "planning"
  | "executing"
  | "waiting_human"
  | "verifying"
  | "replanning"
  | "completed"
  | "failed"
  | "cancelled"
  | "timeout";

export type GeneralAgentNodeStatus =
  | "pending"
  | "running"
  | "success"
  | "failed"
  | "skipped"
  | "waiting_human";

export type GeneralAgentNodeKind = "tool" | "subagent";

export type GeneralAgentEffectStatus =
  | "prepared"
  | "started"
  | "succeeded"
  | "failed"
  | "unknown"
  | "reconciled"
  | "requires_human";

export type GeneralAgentScopeType =
  | "none"
  | "selection"
  | "chapter"
  | "range"
  | "novel";

export type GeneralAgentScope = {
  scope_type: GeneralAgentScopeType;
  current_chapter_id?: string | null;
  chapter_ids: string[];
  selection_text: string;
  direct_context: string;
};

export type GeneralAgentInputBinding = {
  source_node_id: string;
  source_path: string;
  target_path: string;
};

export type GeneralAgentPlanNode = {
  node_id: string;
  kind: GeneralAgentNodeKind;
  capability_name: string;
  objective: string;
  input_data: Record<string, unknown>;
  dependencies: string[];
  attempt_id?: string | null;
  input_bindings: GeneralAgentInputBinding[];
  continue_on_failure: boolean;
};

export type GeneralAgentExecutionPlan = {
  rationale: string;
  requires_clarification: boolean;
  clarification_question: string;
  direct_response: string;
  nodes: GeneralAgentPlanNode[];
  final_response_guidance: string;
};

export type GeneralAgentNodeRun = {
  node_id: string;
  plan_revision: number;
  kind: GeneralAgentNodeKind;
  capability_name: string;
  objective: string;
  dependencies: string[];
  status: GeneralAgentNodeStatus;
  resolved_input: Record<string, unknown>;
  output: Record<string, unknown>;
  source_refs: string[];
  artifact_refs: string[];
  trace_id?: string | null;
  authorization_grant_id?: string | null;
  authorization_approved: boolean;
  authorization_second_confirmation: boolean;
  authorization_resource_scopes: string[];
  effect_id?: string | null;
  effect_status?: GeneralAgentEffectStatus | null;
  reconciliation_reason?: string;
  duplicate_execution_protected?: boolean;
  started_at?: string | null;
  finished_at?: string | null;
  duration_ms: number;
  error_type?: string | null;
  error_message?: string | null;
};

export type GeneralAgentHumanRequest = {
  request_id: string;
  kind: "clarification" | "write_authorization" | "effect_reconciliation";
  prompt: string;
  node_id?: string | null;
  tool_name?: string | null;
  effect_id?: string | null;
  input_sha256?: string | null;
  input_summary: Record<string, unknown>;
  resource_scopes: string[];
  second_confirmation_required: boolean;
  created_at: string;
};

export type AgentMemoryKind =
  | "user_instruction"
  | "task_summary"
  | "resource_summary"
  | "work_note"
  | "unresolved_issue"
  | "fact_reference";

export type AgentMemoryValidity =
  | "active"
  | "stale"
  | "rejected"
  | "superseded";

export type AgentMemoryDependencyRelation =
  | "basis"
  | "review_target"
  | "repair_source";

export type AgentMemoryEntry = {
  memory_id: string;
  kind: AgentMemoryKind;
  content: string;
  source_refs: string[];
  artifact_refs: string[];
  run_ids: string[];
  conversation_id: string;
  created_request_index: number;
  expires_after_request_index?: number | null;
  request_index: number;
  retention_priority: number;
  created_at: string;
  updated_at: string;
  expires_at?: string | null;
  supersedes_memory_id?: string | null;
  content_sha256: string;
  basis_sha256: string;
  producer_ref?: string | null;
  result_type?: string | null;
  evidence_anchors: Array<{
    reference: string;
    content_sha256: string;
  }>;
  dependencies: Array<{
    memory_id: string;
    relation: AgentMemoryDependencyRelation;
  }>;
  validity: AgentMemoryValidity;
  invalidated_at?: string | null;
  invalidation_reason: string;
  invalidated_by_memory_id?: string | null;
  sensitivity: "normal" | "private" | "restricted";
  deleted_at?: string | null;
};

export type GeneralAgentCompressionStats = {
  compressed: boolean;
  fallback_used: boolean;
  input_char_count: number;
  output_char_count: number;
  estimated_token_count: number;
  omitted_message_count: number;
  omitted_node_count: number;
  selected_memory_count: number;
};

export type GeneralAgentContextMemory = {
  memory_id: string;
  kind: string;
  content: string;
  source_refs: string[];
  artifact_refs: string[];
  content_sha256: string;
  basis_sha256: string;
  validity: AgentMemoryValidity;
  invalidation_reason: string;
  invalidated_by_memory_id?: string | null;
  supersedes_memory_id?: string | null;
  result_type?: string | null;
  producer_ref?: string | null;
};

export type GeneralAgentContextSnapshot = {
  snapshot_id: string;
  phase: "plan" | "replan" | "verify";
  conversation_id: string;
  run_id: string;
  created_at: string;
  policy_snapshot: Record<string, unknown>;
  memory_refs: Array<{
    memory_id: string;
    content_sha256: string;
    state_sha256: string;
  }>;
  envelope: {
    phase: "plan" | "replan" | "verify";
    stable_memory: string[];
    working_memory: {
      memories: GeneralAgentContextMemory[];
      invalidated_memories: GeneralAgentContextMemory[];
      plan_summary?: Record<string, unknown> | null;
      node_summaries: Array<Record<string, unknown>>;
      unresolved_issues: string[];
      replan_guidance: string;
      session_memory?: SessionWorkingMemory;
    };
    long_term_memory: GeneralAgentContextMemory[];
    history_memory: {
      summary: string;
      messages: Array<{
        role: "user" | "assistant";
        content: string;
        created_at: string;
      }>;
      total_message_count: number;
      omitted_message_count: number;
    };
    current_request: {
      content: string;
      human_responses: string[];
      user_constraints: string[];
      scope: Record<string, unknown>;
    };
    category_stats: Array<{
      category: string;
      selected_count: number;
      selected_char_count: number;
      omitted_count: number;
      compressed: boolean;
      reason: string;
    }>;
    total_char_count: number;
    estimated_token_count: number;
    context_window_tokens?: number;
    token_count_method?: string;
    pipeline_events?: ContextPipelineEvent[];
    compressed: boolean;
    fallback_used: boolean;
  };
  content_sha256: string;
};

export type GeneralAgentRun = {
  context_pipeline?: { working_memory: SessionWorkingMemory; events: ContextPipelineEvent[] };
  run_id: string;
  task_id: string;
  conversation_id: string;
  request_index: number;
  parent_run_id?: string | null;
  agent_name: "general_writing_assistant";
  user_goal: string;
  model_id?: string | null;
  scope: GeneralAgentScope;
  author_constraints: string[];
  external_access_allowed: boolean;
  limits: {
    max_plan_nodes: number;
    max_replans: number;
    max_concurrency: number;
    max_total_tool_calls: number;
    max_runtime_seconds: number;
  };
  status: GeneralAgentRunStatus;
  current_request_message_id?: string | null;
  messages: Array<{
    role: "user" | "assistant";
    content: string;
    created_at: string;
    message_id?: string | null;
    turn_id?: string | null;
    request_index?: number | null;
    message_type?: "user_request" | "assistant_final" | "human_prompt" | "human_response" | null;
    human_request_id?: string | null;
  }>;
  plan?: GeneralAgentExecutionPlan | null;
  plan_revision: number;
  replan_count: number;
  node_runs: GeneralAgentNodeRun[];
  pending_human_request?: GeneralAgentHumanRequest | null;
  final_answer: string;
  verification_issues: string[];
  memory_refs: string[];
  context_snapshot_id?: string | null;
  context_snapshot?: GeneralAgentContextSnapshot | null;
  compression_stats: GeneralAgentCompressionStats;
  context_resume_differences: string[];
  lifecycle_events: Array<{
    status: GeneralAgentRunStatus;
    reason: string;
    created_at: string;
  }>;
  checkpoint_revision: number;
  resumable: boolean;
  created_at: string;
  updated_at: string;
  started_at: string;
  finished_at?: string | null;
  errors: string[];
};

export type GeneralAgentRunSummary = {
  run_id: string;
  conversation_id: string;
  request_index: number;
  agent_name: string;
  user_goal: string;
  status: GeneralAgentRunStatus;
  scope_type: string;
  plan_revision: number;
  replan_count: number;
  completed_node_count: number;
  failed_node_count: number;
  total_node_count: number;
  waiting_human_kind?: string | null;
  final_answer_preview: string;
  memory_count: number;
  context_snapshot_id?: string | null;
  context_compressed: boolean;
  estimated_context_tokens: number;
  created_at: string;
  updated_at: string;
  finished_at?: string | null;
};

export type GeneralAgentRunRequest = {
  user_goal: string;
  model_id: string;
  conversation_id?: string | null;
  start_new_conversation: boolean;
  scope: GeneralAgentScope;
  author_constraints: string[];
  external_access_allowed: boolean;
};

export type GeneralAgentResumeRequest = {
  answer?: string;
  approve?: boolean;
  second_confirmation?: boolean;
  effect_resolution?: "recheck" | "confirm_not_applied" | "cancel";
};

export type GeneralAgentRunResponse = { run: GeneralAgentRun };

export type GeneralAgentStreamEvent = {
  type: string;
  event_type: string;
  run_id: string;
  status: GeneralAgentRunStatus;
  checkpoint_revision: number;
  detail: string;
  run: GeneralAgentRun;
};

export type GeneralAgentContextSnapshotListResponse = {
  run_id: string;
  snapshots: GeneralAgentContextSnapshot[];
  total: number;
};

export type GeneralAgentLLMReplayMessage = {
  role: "system" | "developer" | "user" | "assistant" | "tool";
  content: string;
  tool_calls: Array<{
    call_id: string;
    name: string;
    arguments_json: string;
  }>;
  tool_call_id?: string | null;
  tool_name?: string | null;
  is_error: boolean;
};

export type GeneralAgentLLMReplay = {
  call_id: string;
  run_id: string;
  context_snapshot_id?: string | null;
  task_type: string;
  task_name: string;
  feature: string;
  model_id: string;
  upstream_model: string;
  wire_protocol: string;
  status: "completed" | "failed";
  response_mode: "text" | "json";
  temperature?: number | null;
  max_output_tokens?: number | null;
  wire_request_body?: Record<string, unknown> | null;
  messages: GeneralAgentLLMReplayMessage[];
  tools: Array<{
    name: string;
    description: string;
    parameters: Record<string, unknown>;
    strict: boolean;
  }>;
  tool_choice: "auto" | "none" | "required";
  response_tool_calls: Array<{
    call_id: string;
    name: string;
    arguments_json: string;
  }>;
  response_text: string;
  request_sha256: string;
  response_sha256: string;
  redaction_count: number;
  input_tokens?: number | null;
  cached_input_tokens?: number | null;
  output_tokens?: number | null;
  reasoning_tokens?: number | null;
  total_tokens?: number | null;
  finish_reason?: string | null;
  provider_request_id?: string | null;
  started_at: string;
  finished_at: string;
  duration_ms: number;
  error_code?: string | null;
  error_message?: string | null;
};

export type GeneralAgentLLMReplayListResponse = {
  run_id: string;
  calls: GeneralAgentLLMReplay[];
  total: number;
};

export type GeneralAgentRunListResponse = {
  runs: GeneralAgentRunSummary[];
  page: number;
  page_size: number;
  total: number;
};

export type GeneralAgentConversationSummary = {
  conversation_id: string;
  title: string;
  status: GeneralAgentRunStatus;
  request_count: number;
  latest_run_id: string;
  created_at: string;
  updated_at: string;
};

export type GeneralAgentConversationListResponse = {
  conversations: GeneralAgentConversationSummary[];
  page: number;
  page_size: number;
  total: number;
};

export type GeneralAgentConversationResponse = {
  conversation_id: string;
  runs: GeneralAgentRun[];
};

export type GeneralAgentConversationDeleteResponse = {
  conversation_id: string;
  deleted_count: number;
};

export type AgentMemoryListResponse = {
  conversation_id: string;
  memories: AgentMemoryEntry[];
  total: number;
};

export type AgentMemoryResponse = { memory: AgentMemoryEntry };

export type GeneralAgentInvocationType = "tool" | "subagent" | "llm";
export type GeneralAgentInvocationStatus = "completed" | "failed" | "timed_out";

export type GeneralAgentInvocationTrace = {
  lifecycle: "confirmed";
  trace_id: string;
  capability_type: GeneralAgentInvocationType;
  capability_name: string;
  task_id: string;
  run_id: string;
  call_id: string;
  parent_call_id?: string | null;
  caller_type: string;
  caller_name: string;
  status: GeneralAgentInvocationStatus;
  input_sha256: string;
  input_char_count: number;
  output_char_count: number;
  source_count: number;
  side_effect: string;
  authorization_reference?: string | null;
  model_role?: string | null;
  model_id?: string | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  retry_count: number;
  started_at: string;
  finished_at: string;
  duration_ms: number;
  error_type?: string | null;
  error_message?: string | null;
};

export type GeneralAgentTraceListResponse = {
  traces: GeneralAgentInvocationTrace[];
  total: number;
};

export type GeneralAgentRecoverySnapshot = {
  run_id: string;
  checkpoint: {
    status: "available" | "missing";
    checkpoint_count: number;
    latest_checkpoint_id?: string | null;
    latest_step?: number | null;
  };
  checkpoints: Array<{
    checkpoint_id: string;
    source: string;
    step: number;
    created_at?: string | null;
  }>;
  effects: Array<{
    effect_id: string;
    node_id: string;
    tool_name: string;
    status: GeneralAgentEffectStatus;
    resource_scopes: string[];
    authorization_bound: boolean;
    duplicate_execution_protected: boolean;
    reason: string;
    updated_at: string;
  }>;
};

export type GeneralAgentRecoveryResponse = {
  recovery: GeneralAgentRecoverySnapshot;
};

export interface SessionMemoryItem { content: string; source_ids: string[] }
export type SessionWorkingMemory = Record<"task_goal" | "file_list" | "workflow_state" | "error_knowledge" | "constraints", Record<string, SessionMemoryItem>>;
export interface ContextPipelineEvent {
  event_id: string; stage: "truncate" | "clear" | "extract" | "fold" | "full";
  reason: string; status: "completed" | "failed" | "skipped"; created_at: string;
  before_tokens: number; after_tokens: number; source_ids: string[]; detail: string;
}
export interface ContextCompactionOperation {
  request_id: string; conversation_id: string;
  status: "queued" | "running" | "completed" | "failed"; message: string;
}
