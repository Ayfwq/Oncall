<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import { formatBeijingTime } from '../formatTime'
import { collectorLabel, metricLabel } from '../metricLabels'
import type { MetricsSource, ProjectConfig, SnapshotDTO } from '../types'

const route = useRoute()
const id = String(route.params.id)
const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const loadingSnapshot = ref(false)
const message = ref('')
const error = ref(false)
const snapshot = ref<SnapshotDTO | null>(null)
const snapshotDialogVisible = ref(false)
const PROJECT_TEST_TIMEOUT_MS = 20000

type FormMetrics = MetricsSource
interface FormConfig {
  name: string
  server_id: string | null
  description: string
  enabled: boolean
  poll_interval: number
  metrics_sources: FormMetrics[]
  log_sources: NonNullable<ProjectConfig['log_sources']>
  database_profiles: NonNullable<ProjectConfig['database_profiles']>
  server?: ProjectConfig['server']
}

const cfg = ref<FormConfig>({ name: '', server_id: null, description: '', enabled: true, poll_interval: 30, metrics_sources: [], log_sources: [], database_profiles: [] })
const quick = ref({ metricsUrl: '', pollInterval: 30 })

const HOST_METRICS = [['host.exporter.up', '系统采集器'], ['host.cpu.percent', 'CPU 使用率'], ['host.memory.percent', '内存使用率'], ['host.disk.usage_percent', '磁盘使用率'], ['host.net.rx_bytes_per_sec', '网络接收'], ['host.net.tx_bytes_per_sec', '网络发送']]
const APP_METRICS = [['app.up', '应用指标可用'], ['app.http.rps', '请求速率'], ['app.http.error_rate', '错误率'], ['app.http.p95_ms', 'P95 延迟'], ['process.target.cpu_percent_sum', '进程 CPU'], ['process.target.rss_bytes_sum', '进程内存']]
const CONTAINER_METRICS = [['container.up', '容器存活'], ['container.cpu.percent', '容器 CPU'], ['container.memory.percent', '容器内存'], ['container.restarts', '重启次数']]
const DATABASE_METRICS = [['db.up', '数据库可用'], ['db.connections.active', '活跃连接'], ['db.connections.max', '最大连接'], ['db.connections.utilization_percent', '连接使用率'], ['db.long_transactions', '长事务'], ['db.lock_waits', '锁等待'], ['db.deadlocks_total', '死锁累计'], ['db.cache_hit_percent', '缓存命中率'], ['db.replication_lag_seconds', '复制延迟']]
const hasDatabase = computed(() => cfg.value.database_profiles.some(x => x.enabled))
const metricCount = computed(() => HOST_METRICS.length + APP_METRICS.length + (cfg.value.server?.container_metrics_url ? CONTAINER_METRICS.length : 0) + (hasDatabase.value ? DATABASE_METRICS.length : 0))
const snapshotSignals = computed(() => Object.entries(snapshot.value?.signals || {}).map(([key, value]) => ({ key, label: metricLabel(key), value: formatSignalValue(key, value) })))
const activeAlerts = computed(() => ((snapshot.value?.resources?.active_alerts || []) as Array<{ alertname?: string; severity?: string; annotations?: { summary?: string }; value?: number | null }>))
const DOWN_ALERTS: Record<string, string> = { node: 'OncallHostExporterDown', application: 'OncallApplicationMetricsDown', database: 'OncallDatabaseDown', container: 'OncallContainerMetricsDown' }
const collectorRows = computed(() => Object.entries(snapshot.value?.collector_status || {}).map(([key, value]) => ({ key, ok: Boolean((value as { ok?: unknown })?.ok) })))
const targetRows = computed(() => {
  const raw = snapshot.value?.resources?.prometheus
  const rows = Array.isArray(raw)
    ? raw
    : Array.isArray((raw as { sources?: unknown[] } | undefined)?.sources)
      ? ((raw as { sources: Array<{ name?: string; url?: string; ok?: boolean }> }).sources).map(source => ({
          metric: { component: 'application', instance: source.url || source.name || '' },
          value: [0, source.ok ? '1' : '0'],
        }))
      : []
  return (rows as Array<{ metric?: { component?: string; instance?: string }; value?: [number, string] }>).map(row => {
    const component = row.metric?.component || 'unknown'
    const up = Number(row.value?.[1]) === 1
    const alerting = !up && activeAlerts.value.some(alert => alert.alertname === DOWN_ALERTS[component])
    return { component, instance: row.metric?.instance || '', status: up ? 'up' : alerting ? 'down' : 'pending' }
  })
})
const componentLabel = (value: string) => ({ node: '服务器指标', application: 'Python 应用指标', database: 'PostgreSQL 指标', container: 'Docker 容器指标', gpu: 'GPU 指标' } as Record<string, string>)[value] || value

function formatSignalValue(key: string, value: unknown) {
  const n = Number(value)
  if (value === null || value === undefined || value === '') return '暂无数据'
  if (key.endsWith('_percent') || key.endsWith('.percent')) return Number.isFinite(n) ? `${n.toFixed(1)}%` : String(value)
  if (key.endsWith('_ms')) return Number.isFinite(n) ? `${Math.round(n)} ms` : String(value)
  if (typeof value === 'boolean') return value ? '正常' : '异常'
  return Number.isFinite(n) ? (Number.isInteger(n) ? String(n) : n.toFixed(2)) : String(value)
}

function setMessage(text: string, isError = false) { message.value = text; error.value = isError }
function failText(e: unknown) {
  const raw = e instanceof Error ? e.message : String(e || '未知错误')
  try { const body = JSON.parse(raw); return body.detail || body.message || raw } catch { return raw.replace(/^\s*"|"\s*$/g, '') }
}
function blankMetrics(url = ''): FormMetrics { return { id: null, service_id: null, name: 'app', url, auth_type: 'none', token: '', scrape_timeout_ms: 5000, route_label: 'handler', enabled: true } }

function normalize(data: ProjectConfig) {
  cfg.value = {
    name: data.name || '', server_id: data.server_id, description: data.description || '', enabled: data.enabled !== false, poll_interval: data.poll_interval || 30,
    server: data.server,
    metrics_sources: (data.metrics_sources || []).map(x => ({ ...x, name: x.name || 'app', token: '' })),
    log_sources: data.log_sources || [], database_profiles: data.database_profiles || [],
  }
  quick.value = { metricsUrl: cfg.value.metrics_sources.find(x => x.enabled)?.url || '', pollInterval: cfg.value.poll_interval }
}

async function load() {
  loading.value = true
  try { normalize(await api<ProjectConfig>(`/projects/${id}`)) } catch (e) { setMessage('加载失败：' + failText(e), true) } finally { loading.value = false }
}
async function prepareQuick() {
  if (!quick.value.metricsUrl.trim()) { setMessage('Prometheus /metrics 地址必填', true); return false }
  const source = cfg.value.metrics_sources.find(x => x.enabled) || blankMetrics()
  Object.assign(source, { url: quick.value.metricsUrl.trim(), enabled: true })
  if (!cfg.value.metrics_sources.includes(source)) cfg.value.metrics_sources = [source]
  cfg.value.poll_interval = Number(quick.value.pollInterval) || 30
  return true
}
function validUrl(value: string) { try { const u = new URL(value); return ['http:', 'https:'].includes(u.protocol) && !!u.hostname } catch { return false } }
function validate() {
  const errs: string[] = []
  if (!cfg.value.name.trim()) errs.push('项目名称必填')
  if (!cfg.value.server_id) errs.push('未绑定服务器')
  if (!validUrl(quick.value.metricsUrl)) errs.push('Prometheus /metrics 地址必须是完整的 http(s) URL')
  if (!Number.isInteger(Number(cfg.value.poll_interval)) || Number(cfg.value.poll_interval) < 10) errs.push('采集间隔至少为 10 秒')
  return errs
}
function toPayload() {
  return {
    name: cfg.value.name.trim(), server_id: cfg.value.server_id, description: cfg.value.description || '', environment: 'production', enabled: cfg.value.enabled, timezone: 'Asia/Shanghai', poll_interval: Number(cfg.value.poll_interval),
    metrics_sources: cfg.value.metrics_sources.map(x => ({ ...x, token: x.token || null })), log_sources: cfg.value.log_sources, database_profiles: cfg.value.database_profiles,
  }
}
async function save() {
  if (saving.value || !await prepareQuick()) return
  const errs = validate(); if (errs.length) { setMessage(errs.join('；'), true); return }
  saving.value = true; setMessage('保存中…')
  try { await api(`/projects/${id}`, { method: 'PUT', body: JSON.stringify(toPayload()) }); setMessage('配置已保存，Prometheus 规则已同步'); await load() } catch (e) { setMessage('保存失败：' + failText(e), true) } finally { saving.value = false }
}
async function test() {
  if (testing.value || !await prepareQuick()) return
  const errs = validate(); if (errs.length) { setMessage(errs.join('；'), true); return }
  testing.value = true; setMessage('正在测试服务器、应用、日志和数据库采集…')
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), PROJECT_TEST_TIMEOUT_MS)
  try {
    snapshot.value = await api<SnapshotDTO>(`/projects/${id}/test`, { method: 'POST', body: JSON.stringify(toPayload()), signal: controller.signal })
    const statuses = Object.values(snapshot.value.collector_status || {})
    setMessage(statuses.some(x => !x.ok) ? '测试完成，但有采集入口异常' : '测试通过：采集入口正常，告警由 Prometheus 判断', statuses.some(x => !x.ok))
  } catch (e) {
    setMessage(e instanceof DOMException && e.name === 'AbortError' ? '采集测试超过 20 秒，已停止。请检查 Collector、应用指标地址和数据库连接。' : '测试失败：' + failText(e), true)
  } finally {
    window.clearTimeout(timeout)
    testing.value = false
  }
}
async function latest(showDialog = true) {
  loadingSnapshot.value = true
  try {
    const result = await api<{ snapshot: Partial<SnapshotDTO> | null; collector_status: SnapshotDTO['collector_status'] | null; observed_at: string | null }>(`/projects/${id}/snapshot`)
    snapshot.value = result.snapshot ? { ...result.snapshot, project_id: result.snapshot.project_id || id, observed_at: result.observed_at || result.snapshot.observed_at || '', signals: result.snapshot.signals || {}, resources: result.snapshot.resources || {}, resource_signals: result.snapshot.resource_signals || {}, collector_status: result.collector_status || result.snapshot.collector_status || {} } as SnapshotDTO : null
    snapshotDialogVisible.value = showDialog
  } catch (e) { setMessage('读取 Prometheus 快照失败：' + failText(e), true) } finally { loadingSnapshot.value = false }
}
onMounted(async () => { await load(); await latest(false) })
</script>

<template>
  <div class="page project-detail">
    <div class="page-head"><div><div class="eyebrow">PROMETHEUS MONITORING</div><h1>{{ cfg.name || '项目监控' }}</h1><p class="sub">绑定服务器和两个项目地址，Prometheus 负责判断数字指标，Alertmanager 负责合并去重。</p></div><div class="head-actions"><el-button :loading="testing" @click="test">测试采集</el-button><el-button :loading="loadingSnapshot" @click="latest()">Prometheus 状态</el-button><el-button type="primary" :loading="saving" @click="save">保存配置</el-button></div></div>
    <el-alert v-if="message" :title="message" :type="error ? 'error' : 'success'" show-icon :closable="false" class="notice" />
     <section v-if="snapshot" class="card live-status"><div><b>Prometheus 实时采集</b><span>{{ formatBeijingTime(snapshot.observed_at) }}</span></div><div class="target-pills"><span v-for="target in targetRows" :key="target.component + target.instance" :class="target.status"><i></i>{{ componentLabel(target.component) }} · {{ target.status === 'up' ? '正常' : target.status === 'down' ? '不可用' : 'Prometheus 同步中' }}</span></div><div v-if="collectorRows.length" class="collector-status live-collector-status"><span v-for="item in collectorRows" :key="item.key" :class="item.ok ? 'ok' : 'bad'">{{ collectorLabel(item.key) }} · {{ item.ok ? '正常' : '失败' }}</span></div><div v-if="activeAlerts.length" class="live-alert"><b>{{ activeAlerts.length }} 条当前告警</b><span v-for="alert in activeAlerts" :key="alert.alertname">{{ alert.annotations?.summary || alert.alertname }}</span></div><div v-else class="no-alert">当前无 Alertmanager 告警</div></section>
    <div class="layout"><main>
       <section class="card setup-card"><div class="section-title"><div><span class="step">1</span><div><h2>项目基本信息</h2></div></div><el-tag :type="cfg.enabled ? 'success' : 'info'">{{ cfg.enabled ? '监控中' : '已停用' }}</el-tag></div><div class="form-grid readonly-form"><el-form-item label="项目名称"><div class="readonly-value">{{ cfg.name || '未设置' }}</div></el-form-item><el-form-item label="检测间隔"><div class="readonly-value">{{ cfg.poll_interval }} 秒</div></el-form-item><el-form-item label="项目说明" class="full"><div class="readonly-value">{{ cfg.description || '未填写' }}</div></el-form-item></div><div class="server-banner"><div class="server-icon">⌁</div><div><b>{{ cfg.server?.name || '未绑定服务器' }}</b><p>{{ cfg.server?.node_metrics_url || '未配置服务器指标' }}<span v-if="cfg.server?.container_metrics_url"> · 已配置容器指标</span></p></div><el-switch v-model="cfg.enabled" active-text="启用监控" /></div></section>
       <section class="card setup-card"><div class="section-title"><div><span class="step">2</span><div><h2>服务采集入口</h2></div></div></div><el-form-item label="Prometheus /metrics 地址（必填）"><el-input v-model="quick.metricsUrl" placeholder="例如：https://stock.example.com/metrics" /></el-form-item></section>
      <section class="card setup-card"><div class="section-title"><div><span class="step">3</span><div><h2>Prometheus 告警策略</h2><p>这里不再编辑项目规则。所有项目使用平台统一、带持续时间的降噪策略。</p></div></div><el-tag type="success" effect="plain">Prometheus 托管</el-tag></div><div class="alert-policy-note"><b>统一策略</b><span>持续 2–10 分钟才触发</span><span>Alertmanager 按项目合并去重</span><span>恢复事件自动关闭</span><span>LLM 再查日志和数据库明细</span></div><div class="rule-summary"><div><b>5</b><span>类监控信号</span></div><div><b>{{ metricCount }}</b><span>类标准指标</span></div><div><b>{{ activeAlerts.length }}</b><span>当前告警</span></div></div><div class="policy-grid"><div><b>可用性</b><span>应用、服务器、cAdvisor、PostgreSQL 采集目标</span></div><div><b>应用性能</b><span>5xx 错误率、P95 延迟</span></div><div><b>主机资源</b><span>内存、磁盘</span></div><div><b>容器资源</b><span>cAdvisor 采集 CPU、内存，并监测采集器是否中断</span></div><div><b>数据库</b><span>连接率、长事务、锁、复制延迟由 Prometheus 判断</span></div></div></section>
    </main><aside><section class="card side-card"><div class="side-label">监控范围</div><div class="metric-total">{{ metricCount }}<small> 项</small></div><p>Prometheus 负责所有数字指标判断；Docker 文本日志和 PostgreSQL 查询明细由 LLM 按需读取。</p><div class="metric-group"><b>服务器 · {{ HOST_METRICS.length }} 项</b><span v-for="m in HOST_METRICS" :key="m[0]">{{ m[1] }}</span></div><div class="metric-group"><b>应用 · {{ APP_METRICS.length }} 项</b><span v-for="m in APP_METRICS" :key="m[0]">{{ m[1] }}</span></div><div v-if="cfg.server?.container_metrics_url" class="metric-group"><b>容器 · {{ CONTAINER_METRICS.length }} 项</b><span v-for="m in CONTAINER_METRICS" :key="m[0]">{{ m[1] }}</span></div><div v-if="hasDatabase" class="metric-group database"><b>PostgreSQL · {{ DATABASE_METRICS.length }} 项</b><span v-for="m in DATABASE_METRICS" :key="m[0]">{{ m[1] }}</span></div></section></aside></div>
    <el-dialog v-model="snapshotDialogVisible" title="Prometheus 状态" width="min(760px, 92vw)" destroy-on-close :teleported="false"><div v-if="snapshot" class="snapshot-dialog-content"><div class="snapshot-meta"><div><span>查询时间</span><b>{{ formatBeijingTime(snapshot.observed_at) }}</b></div><div><span>当前指标</span><b>{{ Object.keys(snapshot.signals || {}).length }} 项</b></div></div><div class="collector-status"><span v-for="(v,k) in snapshot.collector_status" :key="k" :class="v.ok ? 'ok' : 'bad'">{{ collectorLabel(k) }} · {{ v.ok ? '正常' : '失败' }}</span></div><div v-if="activeAlerts.length" class="active-alerts"><b>当前 Alertmanager 告警</b><div v-for="alert in activeAlerts" :key="alert.alertname + String(alert.value)">{{ alert.annotations?.summary || alert.alertname }} · {{ alert.severity }}</div></div><div class="snapshot-signals-title">采集指标</div><div class="snapshot-signal-list"><div v-for="signal in snapshotSignals" :key="signal.key" class="snapshot-signal-row"><span>{{ signal.label }}</span><b>{{ signal.value }}</b></div><p v-if="!snapshotSignals.length" class="muted">Prometheus 暂无查询结果</p></div></div><el-empty v-else description="暂无 Prometheus 状态" /><template #footer><el-button type="primary" @click="snapshotDialogVisible = false">关闭</el-button></template></el-dialog>
  </div>
</template>

<style scoped>
.project-detail{--green:#18a77a;--ink:#18352d;--muted:#6e817b;max-width:1240px}.page-head{align-items:flex-end}.eyebrow{color:var(--green);font-size:11px;font-weight:800;letter-spacing:.14em;margin-bottom:8px}.head-actions{display:flex;gap:8px;flex-wrap:wrap}.notice{margin:0 0 16px}.layout{display:grid;grid-template-columns:minmax(0,1fr) 310px;gap:18px}.card{border:1px solid #e1ebe7;box-shadow:0 8px 24px rgba(35,89,71,.05)}.setup-card{margin-bottom:16px;padding:22px}.section-title{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:20px}.section-title>div:first-child{display:flex;gap:12px}.section-title h2{color:var(--ink);margin:0 0 4px;font-size:18px}.section-title p{color:var(--muted);margin:0;font-size:13px}.step{width:28px;height:28px;display:grid;place-items:center;border-radius:9px;color:#fff;background:var(--green);font-weight:800}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:0 16px}.form-grid .full{grid-column:1/-1}.hint{color:var(--muted);font-size:12px;margin:-8px 0 14px}.server-banner{background:#f1fbf7;border:1px solid #cdeee1;border-radius:12px;padding:13px 15px;display:flex;gap:12px;align-items:center;margin-top:8px}.server-icon{width:32px;height:32px;border-radius:10px;display:grid;place-items:center;color:#fff;background:var(--green);font-size:22px}.server-banner b{color:var(--ink)}.server-banner p{color:var(--muted);margin:3px 0 0;font-size:12px}.server-banner .el-switch{margin-left:auto}.side-card{padding:20px}.side-label{color:var(--muted);font-size:12px}.metric-total{color:var(--green);font-size:40px;font-weight:800;line-height:1.2}.metric-total small{font-size:14px}.side-card>p{color:var(--muted);font-size:12px;line-height:1.6}.metric-group{border-top:1px solid #edf2ef;padding:13px 0 2px;display:flex;flex-direction:column;gap:5px}.metric-group b{color:var(--ink);font-size:13px;margin-bottom:3px}.metric-group span{color:var(--muted);font-size:12px}.alert-policy-note{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;margin:-4px 0 16px;padding:11px 13px;border:1px solid #d8ece4;border-radius:10px;background:#f3faf7;color:var(--muted);font-size:12px}.alert-policy-note b{color:var(--green)}.alert-policy-note span::before{content:'✓';color:var(--green);margin-right:5px;font-weight:800}.rule-summary{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:16px}.rule-summary>div{background:#f6faf8;border-radius:11px;padding:12px}.rule-summary b{display:block;color:var(--green);font-size:22px}.rule-summary span{color:var(--muted);font-size:12px}.policy-grid{display:grid;grid-template-columns:1fr 1fr;gap:9px}.policy-grid>div{padding:11px;border:1px solid #e7efec;border-radius:10px;background:#fbfdfc}.policy-grid b,.policy-grid span{display:block}.policy-grid b{color:var(--ink);font-size:12px}.policy-grid span{color:var(--muted);font-size:11px;margin-top:4px}.collector-status{display:flex;gap:8px;flex-wrap:wrap}.collector-status span{border-radius:999px;padding:5px 10px;font-size:12px}.collector-status .ok{color:#137b58;background:#e5f7ef}.collector-status .bad{color:#b13e48;background:#ffeded}.snapshot-dialog-content{display:flex;flex-direction:column;gap:16px}.snapshot-meta{display:grid;grid-template-columns:1fr 1fr;gap:12px}.snapshot-meta>div{padding:12px 14px;border-radius:10px;background:#f4f8f6}.snapshot-meta span,.snapshot-meta b{display:block}.snapshot-meta span{color:var(--muted);font-size:11px;margin-bottom:4px}.snapshot-meta b{color:var(--ink);font-size:13px}.snapshot-signals-title{color:var(--ink);font-weight:700;font-size:13px}.snapshot-signal-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;max-height:48vh;overflow:auto}.snapshot-signal-row{display:flex;justify-content:space-between;gap:12px;align-items:center;padding:10px 12px;border:1px solid #e5efeb;border-radius:9px;background:#f8fbfa}.snapshot-signal-row span{color:var(--muted);font-size:12px}.snapshot-signal-row b{color:var(--ink);font-size:13px;white-space:nowrap}.active-alerts{padding:12px;border-radius:10px;background:#fff7eb;color:#7b5b20;font-size:12px}.active-alerts div{margin-top:7px}@media(max-width:900px){.layout{grid-template-columns:1fr}.layout aside{order:-1}.form-grid{grid-template-columns:1fr}.form-grid .full{grid-column:auto}}@media(max-width:560px){.policy-grid,.rule-summary{grid-template-columns:1fr}.snapshot-signal-list{grid-template-columns:1fr}}
.live-status{display:flex;align-items:center;gap:16px;flex-wrap:wrap;padding:14px 18px;margin-bottom:16px}.live-status>div:first-child{display:flex;flex-direction:column;min-width:150px}.live-status>div:first-child b{color:var(--ink)}.live-status>div:first-child span{color:var(--muted);font-size:11px;margin-top:3px}.target-pills{display:flex;gap:7px;flex-wrap:wrap;flex:1}.target-pills span{padding:6px 9px;border-radius:999px;font-size:11px}.target-pills i{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:5px}.target-pills .up{color:#137b58;background:#e5f7ef}.target-pills .up i{background:#20ae7b}.target-pills .down{color:#b13e48;background:#ffeded}.target-pills .down i{background:#e05a63}.live-alert,.no-alert{display:flex;flex-direction:column;font-size:11px;padding:7px 10px;border-radius:9px}.live-alert{color:#8c5a13;background:#fff4db}.no-alert{color:#137b58;background:#edf9f4}.database-path{padding:11px 13px;border-radius:10px;background:#f2f7ff;border:1px solid #dbe8fa;color:#52677e;font-size:12px;line-height:1.6}.metric-group.database b{color:#6b4bc3}
.readonly-value{min-height:32px;display:flex;align-items:center;padding:7px 11px;border:1px solid #e5ece9;border-radius:8px;color:var(--ink);background:#f7faf9;font-size:14px}.readonly-form :deep(.el-form-item){margin-bottom:16px}.readonly-form :deep(.el-form-item__label){color:var(--muted)}
.live-collector-status{flex-basis:100%}
.target-pills .pending{color:#8a6a1d;background:#fff6dc}.target-pills .pending i{background:#d9a52b}
</style>
