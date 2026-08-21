export interface AuthUser {
  id: string
  username: string
}

export type ConversationType = 'chat' | 'incident'

export interface Conversation {
  id: string
  title: string
  type: ConversationType | string
  project_id: string | null
  incident_id: string | null
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

export interface ProjectSummary {
  id: string
  name: string
  description: string
  enabled: boolean
  poll_interval: number
  updated_at: string
}

export interface ProcessTarget {
  id: string | null
  name: string
  executable: string | null
  cmdline_filters: string[]
  cwd: string | null
  port: number | null
  enabled: boolean
}

export interface LogSource {
  id: string | null
  path: string
  encoding: string
  parser_config: Record<string, unknown>
  enabled: boolean
}

export interface DockerTarget {
  id: string | null
  container_ref: string
  enabled: boolean
}

export interface DatabaseProfile {
  id: string | null
  type: string
  host: string
  port: number
  database: string
  username: string
  password: string | null
  sslmode: string
  enabled: boolean
}

export interface ServiceEndpoint {
  id: string | null
  name: string
  url: string
  method: string
  expected_status: number
  timeout_ms: number
  enabled: boolean
}

export interface MonitoringRule {
  id: string | null
  metric_key: string
  resource_key: string
  operator: string
  trigger_threshold: number | null
  trigger_for: number
  recovery_threshold: number | null
  recovery_for: number
  severity: string
  enabled: boolean
}

export interface ProjectConfig {
  id: string
  name: string
  description: string
  enabled: boolean
  timezone: string
  poll_interval: number
  process_targets: ProcessTarget[]
  log_sources: LogSource[]
  docker_targets: DockerTarget[]
  database_profiles: DatabaseProfile[]
  service_endpoints: ServiceEndpoint[]
  rules: MonitoringRule[]
}

export interface CollectorStatusEntry {
  ok: boolean
  error: string | null
}

export interface SnapshotDTO {
  project_id: string
  observed_at: string
  signals: Record<string, number | boolean | string | null>
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
  project_scope: string | null
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
  development_fallback?: boolean
}

export interface Readiness {
  environment: string
  llm: ComponentReadiness
  embedding: { model: string; configured: boolean; development_fallback: boolean }
  rerank: { model: string | null; configured: boolean; development_fallback: boolean }
  feishu: {
    enabled: boolean
    configured: boolean
    default_receive_id_configured: boolean
    auto_bind_supported: boolean
  }
  security: { secret_master_key_configured: boolean; default_admin_password_in_use: boolean }
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
