<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { formatBeijingTime } from '../formatTime'
import { metricLabel } from '../metricLabels'
import type { IncidentSummary } from '../types'
const rows = ref<IncidentSummary[]>([]), router = useRouter(), deleting = ref<string | null>(null), selected = ref<string[]>([]), bulkDeleting = ref(false)
async function load() { rows.value = await api<IncidentSummary[]>('/incidents') }
onMounted(load)
const allSelected = computed(() => rows.value.length > 0 && selected.value.length === rows.value.length)
function toggleAll(value: string | number | boolean) { selected.value = value ? rows.value.map(x => x.id) : [] }
function toggleSelected(id: string, value: string | number | boolean) {
  if (value) selected.value = [...new Set([...selected.value, id])]
  else selected.value = selected.value.filter(x => x !== id)
}
const sev = (s: string) => s === 'critical' ? 'err' : 'warn'
const sevTxt = (s: string) => s === 'critical' ? '严重' : '警告'
const statusTxt = (s: string) => {
  const normalized = String(s || '').trim().toLowerCase()
  return ({ open: '待处理', investigating: '调查中', diagnosed: '已诊断', resolved: '已恢复' } as Record<string, string>)[normalized] || s
}
function incidentSummary(row: IncidentSummary) {
  return row.summary.split(row.anomaly_type).join(metricLabel(row.anomaly_type)).replace('still firing', '仍在触发').replace('triggered', '已触发')
}
async function removeIncident(row: IncidentSummary) {
  try {
    await ElMessageBox.confirm(
      `确定删除“${metricLabel(row.anomaly_type)}”告警吗？相关证据、诊断和调查会话会一并删除，但监控规则和历史指标会保留。`,
      '删除告警',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消', confirmButtonClass: 'el-button--danger' },
    )
    deleting.value = row.id
    await api(`/incidents/${row.id}`, { method: 'DELETE' })
    rows.value = rows.value.filter(item => item.id !== row.id)
    selected.value = selected.value.filter(id => id !== row.id)
    ElMessage.success('告警已删除')
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error(e instanceof Error ? e.message : '删除失败')
  } finally {
    deleting.value = null
  }
}
async function removeSelected() {
  if (!selected.value.length || bulkDeleting.value) return
  try {
    await ElMessageBox.confirm(
      `确定删除选中的 ${selected.value.length} 条告警吗？相关证据、诊断和调查会话会一并删除，但监控规则和历史指标会保留。`,
      '批量删除告警',
      { type: 'warning', confirmButtonText: '批量删除', cancelButtonText: '取消', confirmButtonClass: 'el-button--danger' },
    )
    bulkDeleting.value = true
    const result = await api<{ ok: boolean; deleted: number }>('/incidents', {
      method: 'DELETE',
      body: JSON.stringify({ ids: selected.value }),
    })
    rows.value = rows.value.filter(item => !selected.value.includes(item.id))
    selected.value = []
    ElMessage.success(`已删除 ${result.deleted} 条告警`)
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error(e instanceof Error ? e.message : '批量删除失败')
  } finally {
    bulkDeleting.value = false
  }
}
</script>

<template>
  <div class="page">
    <div class="page-head"><div><h1>告警</h1><p class="sub">由 Prometheus 判断、Alertmanager 合并去重后进入，可继续让 LLM 查询指标、日志和数据库</p></div><div class="incident-toolbar" v-if="rows.length"><el-checkbox :model-value="allSelected" @change="toggleAll">全选</el-checkbox><el-button type="danger" plain :loading="bulkDeleting" :disabled="!selected.length" @click="removeSelected">批量删除{{ selected.length ? `（${selected.length}）` : '' }}</el-button></div></div>
    <div class="grid" v-if="rows.length">
      <div class="card clickable incident-card" v-for="x in rows" :key="x.id" @click="router.push('/incidents/' + x.id)">
        <div class="incident-card-head">
          <div class="incident-badges"><el-checkbox :model-value="selected.includes(x.id)" @click.stop @change="toggleSelected(x.id, $event)" /><span class="badge" :class="sev(x.severity)">{{ sevTxt(x.severity) }}</span><span class="badge neutral">{{ statusTxt(x.status) }}</span></div>
          <el-button type="danger" plain size="small" :loading="deleting === x.id" @click.stop="removeIncident(x)">删除</el-button>
        </div>
        <b style="font-size: 15px">{{ metricLabel(x.anomaly_type) }}</b>
        <p class="muted" style="margin: 6px 0 2px">{{ incidentSummary(x) }}</p>
        <small class="muted">{{ formatBeijingTime(x.last_seen) }} · 本轮发生 {{ x.occurrence_count || 1 }} 次</small>
      </div>
    </div>
    <div v-else class="card" style="text-align: center; color: var(--text-3); padding: 60px 20px">
      <div style="font-size: 40px; margin-bottom: 10px">✓</div>
      暂无告警，一切正常
    </div>
  </div>
</template>

<style scoped>
.incident-card { position: relative; transition: border-color .18s ease, box-shadow .18s ease, transform .18s ease; }
.incident-card:hover { border-color: #cfe4dc; box-shadow: 0 10px 28px rgba(35, 89, 71, .08); transform: translateY(-1px); }
.incident-card-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
.incident-badges { display: flex; align-items: center; gap: 8px; }
.incident-toolbar { display: flex; align-items: center; gap: 12px; flex-shrink: 0; }
@media (max-width: 680px) { .incident-toolbar { margin-top: 10px; width: 100%; justify-content: flex-end; } }
</style>
