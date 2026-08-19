<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
const rows = ref<any[]>([]), name = ref('本机监控'), description = ref(''), pollInterval = ref(300), creating = ref(false), loading = ref(false), message = ref(''), messageError = ref(false), search = ref(''), router = useRouter()
const filteredRows = computed(() => {
  const q = search.value.trim().toLowerCase()
  return q ? rows.value.filter(x => `${x.name} ${x.description || ''}`.toLowerCase().includes(q)) : rows.value
})
const enabledCount = computed(() => rows.value.filter(x => x.enabled).length)
const defaultRules = [
  { metric_key: 'host.cpu.percent', resource_key: 'default', operator: '>', trigger_threshold: 85, trigger_for: 2, recovery_threshold: 70, recovery_for: 2, severity: 'warning', enabled: true },
  { metric_key: 'host.memory.percent', resource_key: 'default', operator: '>', trigger_threshold: 85, trigger_for: 2, recovery_threshold: 75, recovery_for: 2, severity: 'warning', enabled: true },
  { metric_key: 'host.disk.usage_percent', resource_key: 'default', operator: '>', trigger_threshold: 85, trigger_for: 1, recovery_threshold: 80, recovery_for: 1, severity: 'warning', enabled: true },
]
function errorMessage(error: any): string {
  const raw = String(error?.message || error || '未知错误')
  try {
    const body = JSON.parse(raw)
    if (Array.isArray(body?.detail)) return body.detail.map((x: any) => x.msg || '参数错误').join('；')
    if (typeof body?.detail === 'string') return body.detail
    if (typeof body?.message === 'string') return body.message
  } catch { /* api may return plain text */ }
  return raw.replace(/^\s*"|"\s*$/g, '')
}

async function load() {
  loading.value = true
  try { rows.value = await api('/projects'); message.value = '' }
  catch (error: any) { messageError.value = true; message.value = '项目列表加载失败：' + errorMessage(error) }
  finally { loading.value = false }
}

async function add() {
  if (creating.value) return
  if (!name.value.trim()) { messageError.value = true; message.value = '项目名称必填'; return }
  const interval = Number(pollInterval.value)
  if (!Number.isInteger(interval) || interval < 10 || interval > 86400) {
    messageError.value = true; message.value = '采集间隔必须是 10～86400 秒的整数'; return
  }
  creating.value = true
  message.value = ''
  try {
    const r = await api<any>('/projects', { method: 'POST', body: JSON.stringify({
      name: name.value.trim(), description: description.value.trim(), timezone: 'Asia/Shanghai', poll_interval: interval,
      process_targets: [], log_sources: [], docker_targets: [], database_profiles: [], service_endpoints: [], rules: defaultRules,
    }) })
    await load(); router.push(`/projects/${r.id}`)
  } catch (error: any) {
    messageError.value = true; message.value = '创建失败：' + errorMessage(error)
  } finally { creating.value = false }
}
onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-head"><div><h1>监控项目</h1><p class="sub">绑定本机进程、日志、Docker、数据库与服务，供 Agent 诊断</p></div></div>
    <p v-if="message" class="msg" :class="messageError ? 'err' : ''">{{ message }}</p>
    <div class="overview-grid">
      <div class="overview-card"><span class="overview-label">项目总数</span><b>{{ rows.length }}</b><small>已创建监控边界</small></div>
      <div class="overview-card"><span class="overview-label">运行中</span><b class="success-number">{{ enabledCount }}</b><small>正在执行采集</small></div>
      <div class="overview-card"><span class="overview-label">配置建议</span><b>{{ rows.filter(x => x.enabled).length ? '下一步' : '开始' }}</b><small>{{ rows.length ? '进入项目完成目标绑定' : '创建第一个项目' }}</small></div>
    </div>
    <div class="card create-card" style="margin-bottom: 16px">
      <div>
        <h3 style="margin: 0 0 4px">创建一个监控项目</h3>
        <p class="muted" style="margin: 0 0 12px; font-size: 13px">先建立项目边界和采集频率，再进入详情绑定进程、日志、数据库、服务和告警规则。</p>
      </div>
      <div class="create-fields">
        <el-input v-model="name" placeholder="项目名称，例如：生产 API" />
        <el-input v-model="description" placeholder="用途说明（可选）" />
        <el-input-number v-model="pollInterval" :min="10" :max="86400" controls-position="right" />
        <el-button type="primary" :loading="creating" @click="add">＋ 创建项目</el-button>
      </div>
      <span class="muted" style="font-size: 12px">默认生成 CPU / 内存 / 磁盘基础规则；创建后必须在详情页完成目标配置并测试采集。</span>
    </div>
    <div class="list-toolbar">
      <div><h2>我的项目</h2><span class="muted">每个项目代表一台主机或一组需要持续观察的服务</span></div>
      <el-input v-model="search" clearable placeholder="搜索项目名称或说明" style="max-width: 270px" />
    </div>
    <div class="grid">
      <div class="card clickable project-card" v-for="x in filteredRows" :key="x.id" @click="router.push('/projects/' + x.id)">
        <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px">
          <span class="project-icon">⌁</span>
          <b style="font-size: 15px">{{ x.name }}</b>
          <span class="badge" :class="x.enabled ? 'ok' : 'neutral'">{{ x.enabled ? '监控中' : '已停用' }}</span>
        </div>
        <p class="muted" style="margin: 0">{{ x.description || '本机监控项目' }}</p>
        <div class="project-foot"><small class="muted">采集间隔 {{ x.poll_interval }}s</small><span class="open-link">查看配置 →</span></div>
      </div>
      <div v-if="rows.length && !filteredRows.length" class="card empty-filter">没有匹配的项目，试试其他关键词。</div>
      <div v-if="loading" class="card" style="text-align: center; color: var(--text-3); padding: 40px">项目加载中…</div>
      <div v-else-if="!rows.length" class="card" style="text-align: center; color: var(--text-3); padding: 40px">还没有项目，点击上方「创建项目」</div>
    </div>
  </div>
</template>

<style scoped>
.create-card { display: flex; flex-direction: column; gap: 8px; }
.create-fields { display: grid; grid-template-columns: minmax(180px, 1fr) minmax(220px, 1.4fr) 150px auto; gap: 10px; align-items: center; }
.overview-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 16px; }
.overview-card { background: linear-gradient(135deg, #fff, #fafaff); border: 1px solid var(--border); border-radius: var(--r-lg); padding: 16px 18px; box-shadow: var(--shadow-sm); }
.overview-card b { display: block; font-size: 24px; letter-spacing: -.03em; margin: 2px 0; }
.overview-card small { color: var(--text-3); font-size: 12px; }
.overview-label { color: var(--text-2); font-size: 12px; }
.success-number { color: var(--success); }
.list-toolbar { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; margin: 26px 0 12px; }
.list-toolbar h2 { margin: 0 0 2px; font-size: 17px; }
.list-toolbar span { font-size: 12px; }
.project-card { min-height: 150px; }
.project-icon { width: 28px; height: 28px; display: inline-grid; place-items: center; border-radius: 9px; background: var(--accent-soft); color: var(--accent-strong); font-size: 18px; }
.project-foot { display: flex; justify-content: space-between; align-items: center; margin-top: 18px; }
.open-link { color: var(--accent-strong); font-size: 12px; opacity: 0; transform: translateX(-4px); transition: .15s; }
.project-card:hover .open-link { opacity: 1; transform: translateX(0); }
.empty-filter { grid-column: 1 / -1; color: var(--text-3); text-align: center; }
.msg { font-size: 13px; color: var(--success); margin: -8px 0 14px; }
.msg.err { color: var(--danger); }
@media (max-width: 760px) { .create-fields, .overview-grid { grid-template-columns: 1fr; } .list-toolbar { align-items: stretch; flex-direction: column; } }
</style>
