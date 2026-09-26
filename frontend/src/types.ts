export type ConversationType = 'chat' | 'incident'

export interface Conversation {
  id: string
  title: string
  type: ConversationType | string
  project_id: string | null
  incident_id: string | null
  archived: boolean
  updated_at: string
}

export interface ChatMessage {
  id?: string
  role: 'user' | 'assistant' | 'system'
  content: string
  channel?: string
  created_at?: string
  metadata?: Record<string, unknown> | null
}

export interface KnowledgeCitation {
  citation_id?: string
  document_id: string
  version_id?: string | null
  chunk_id?: string | null
  title?: string | null
  page_range?: string | null
  score?: number | null
  excerpt?: string | null
  used_in_answer?: boolean
}

export interface KnowledgeCitationDetail {
  chunk_id: string
  chunk_index: number
  document_id: string
  version_id: string
  title: string
  original_filename: string
  parser_version: string
  page_range: string | null
  heading_path: string[]
  content: string
  truncated: boolean
  metadata: Record<string, unknown>
  neighbors: Array<{
    chunk_id: string
    chunk_index: number
    heading_path: string[]
    page_range: string | null
    content: string
    truncated: boolean
  }>
}

export interface ProjectSummary {
  id: string
  server_id: string | null
  server_name: string | null
  name: string
  description: string
  environment: string
  enabled: boolean
  poll_interval: number
  updated_at: string
}

export interface MonitoredServer {
  id: string
  name: string
  node_metrics_url: string
  gpu_metrics_url: string | null
  container_metrics_url: string
  collector_url: string | null
  collector_token?: string | null
  enabled: boolean
  project_count: number
  created_at: string | null
  updated_at: string | null
}

export interface ServerTestResult {
  ok: boolean
  signals: Record<string, number>
  resource_signals: Record<string, Record<string, number>>
  resources: Record<string, unknown>
  error: string | null
}

export interface ProjectDraftTestResult {
  ok: boolean
  checks: Array<{ key: 'server' | 'prometheus' | 'logs' | 'database'; ok: boolean; error: string | null }>
  capabilities: {
    host_metrics: boolean
    gpu_metrics: boolean
    http_metrics: boolean
    process_metrics: boolean
    docker_logs: boolean
    database_health: boolean
    slow_sql: boolean
  }
  warnings: string[]
  signals: Record<string, number>
  collector_status: Record<string, { ok: boolean; error: string | null }>
}

export interface MetricsSource {
  id: string | null
  name: string
  url: string
  auth_type: 'none' | 'bearer' | 'basic'
  token: string | null
  scrape_timeout_ms: number
  route_label: string
  service_id: string | null
  enabled: boolean
}

export interface ProjectConfig {
  id: string
  server_id: string | null
  server?: MonitoredServer | null
  name: string
  description: string
  environment: string
  enabled: boolean
  timezone: string
  poll_interval: number
  metrics_sources: MetricsSource[]
  log_sources?: Array<{ id: string | null; path: string; encoding: string; parser_config: Record<string, unknown>; enabled: boolean }>
  database_profiles?: Array<{ id: string | null; type: 'postgresql'; host: string; port: number; database: string; username: string; password: string | null; sslmode: string; enabled: boolean }>
}

export interface CollectorStatusEntry {
  ok: boolean
  error: string | null
}

export interface SnapshotDTO {
  project_id: string
  observed_at: string
  signals: Record<string, number | boolean | string | null>
  resource_signals: Record<string, Record<string, number | boolean | string | null>>
  resources: Record<string, unknown>
  collector_status: Record<string, CollectorStatusEntry>
}

export interface IncidentSummary {
  id: string
  project_id: string
  status: string
  severity: string
  summary: string
  anomaly_type: string
  resource_key: string
  first_seen: string
  last_seen: string
  resolved_at: string | null
  occurrence_count: number
}

export interface IncidentDiagnosis {
  summary: string
  root_cause: string
  confidence: number
  severity?: string
  remediation?: string[]
  verification?: string[]
  knowledge_refs?: { title?: string; document_id?: string; page_range?: string }[]
}

export interface IncidentEvidence {
  id: string
  type: string
  source: string
  observed_at: string
  summary: string
  data?: unknown
  raw_ref?: string | null
}

export interface IncidentDetail extends IncidentSummary {
  conversation_id: string | null
  diagnosis: IncidentDiagnosis | null
  evidence: IncidentEvidence[]
}

export interface ToolRunTrace {
  tool_name: string
  status: string
  summary: string
  latency_ms: number
  result_size: number
  truncated: boolean
  error_code: string | null
  created_at: string
}

export interface RetrievalTrace {
  query: string
  hit_count: number
  refs: unknown
  latency_ms: number
  status: string
  error_code: string | null
  created_at: string
}

export interface AgentRunSummary {
  id: string
  mode: string
  status: string
  started_at: string
  finished_at: string | null
  tools: ToolRunTrace[]
  retrievals: RetrievalTrace[]
}

export interface IncidentTrace {
  agent_runs: AgentRunSummary[]
  notifications: {
    id: string
    status: string
    attempts: number
    last_error: string | null
    payload: unknown
    created_at: string
    sent_at: string | null
  }[]
}

export interface KnowledgeDocument {
  id: string
  title: string
  status: string
  updated_at: string
}

export interface KnowledgeJob {
  id: string
  type: string
  status: string
  attempts: number
  last_error: string | null
  updated_at: string
}

export interface ComponentReadiness {
  provider: string
  model: string | null
  configured: boolean
}

export interface Readiness {
  environment: string
  llm: ComponentReadiness
  embedding: { model: string; configured: boolean }
  rerank: { model: string | null; configured: boolean }
  feishu: {
    enabled: boolean
    configured: boolean
    default_receive_id_configured: boolean
    auto_bind_supported: boolean
  }
  security: { secret_master_key_configured: boolean }
  storage: { database: string; milvus_uri: string; data_dir: string }
}

export type FeishuReceiveType = 'chat_id' | 'open_id' | 'user_id' | 'union_id'

export interface FeishuSettings {
  enabled: boolean
  app_id: string
  app_secret_configured: boolean
  default_receive_id: string
  default_receive_id_type: FeishuReceiveType
  restart_required: boolean
}

export interface ModelSettings {
  model_provider: 'openai-compatible' | 'mock'
  model_display_name: string
  model_base_url: string
  model_name: string
  model_api_key_configured: boolean
  model_api_key?: string
  embedding_base_url: string
  embedding_model: string
  embedding_api_key_configured: boolean
  embedding_api_key?: string
  rerank_base_url: string
  rerank_model: string
  rerank_api_key_configured: boolean
  rerank_api_key?: string
}

export interface SSEEventMap {
  status: { stage: string }
  intent_routed: { intent: string; confidence?: number; reason?: string }
  knowledge_started: { query: string }
  knowledge_finished: { ok: boolean; count: number; summary?: string; error?: string }
  tool_started: { tool_name: string; tool_args?: Record<string, unknown> }
  tool_finished: { tool_name: string; ok: boolean; summary?: string; error?: string }
  rag_retrieved: { count: number; top?: { title: string; score: number }[] }
  diagnosis_ready: { severity: string; confidence: number; root_cause: string }
  token: { content: string }
  final: { content: string }
  error: { message: string }
}

export type SSEEventType = keyof SSEEventMap

export type SSEEvent = { [K in SSEEventType]: { type: K; data: SSEEventMap[K] } }[SSEEventType]
