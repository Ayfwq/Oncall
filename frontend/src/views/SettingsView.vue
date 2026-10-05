<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api'
import type { FeishuReceiveType, FeishuSettings, Readiness } from '../types'
import ModelsView from './ModelsView.vue'

interface FeishuForm {
  enabled: boolean
  app_id: string
  app_secret: string
  app_secret_configured: boolean
  default_receive_id: string
  default_receive_id_type: FeishuReceiveType
}

interface MemoryFact {
  id: string
  content: string
  project_id: string | null
}

const readiness = ref<Readiness | null>(null)
const modelsView = ref<{ reload: () => Promise<void> } | null>(null)
const feishu = ref<FeishuForm>({ enabled: false, app_id: '', app_secret: '', app_secret_configured: false, default_receive_id: '', default_receive_id_type: 'chat_id' })
const error = ref('')
const message = ref('')
const savingFeishu = ref(false)
const deletingFeishu = ref(false)
const memories = ref<MemoryFact[]>([])
const newMemory = ref('')
const savingMemory = ref(false)

function errorText(e: unknown): string { return e instanceof Error ? e.message : String(e) }

async function load() {
  error.value = ''
  try {
    const [r, f, m] = await Promise.all([
      api<Readiness>('/settings/readiness'), api<FeishuSettings>('/settings/feishu'), api<MemoryFact[]>('/memories'),
    ])
    readiness.value = r
    const enteredSecret = f.app_secret_configured && feishu.value.app_id === f.app_id
      ? feishu.value.app_secret
      : ''
    feishu.value = { ...f, app_secret: enteredSecret }
    memories.value = m
  } catch (e) { error.value = errorText(e) }
}

async function refresh() {
  await Promise.all([load(), modelsView.value?.reload()])
}

async function saveFeishu() {
  error.value = ''; message.value = ''
  savingFeishu.value = true
  try {
    const payload = {
      enabled: feishu.value.enabled,
      app_id: feishu.value.app_id,
      app_secret: feishu.value.app_secret,
      default_receive_id: feishu.value.default_receive_id,
      default_receive_id_type: feishu.value.default_receive_id_type,
    }
    const result = await api<{ ok: boolean; message: string; restart_required: boolean }>('/settings/feishu', { method: 'PUT', body: JSON.stringify(payload) })
    message.value = result.message || '飞书配置已保存'
    feishu.value.app_secret_configured = Boolean(payload.app_secret || feishu.value.app_secret_configured)
    readiness.value = await api<Readiness>('/settings/readiness')
    setTimeout(() => { load() }, 2500)
  } catch (e) { error.value = errorText(e) }
  finally { savingFeishu.value = false }
}

async function deleteFeishu() {
  if (!confirm('解绑当前飞书机器人？旧凭证、接收位置和待发送飞书消息会清除；Web 会话历史会保留。')) return
  error.value = ''; message.value = ''; deletingFeishu.value = true
  try {
    const result = await api<{ message: string }>('/settings/feishu', { method: 'DELETE' })
    await load()
    message.value = result.message
  } catch (e) { error.value = errorText(e) }
  finally { deletingFeishu.value = false }
}

async function addMemory() {
  if (!newMemory.value.trim()) return
  error.value = ''; message.value = ''; savingMemory.value = true
  try {
    await api('/memories', { method: 'POST', body: JSON.stringify({ content: newMemory.value.trim() }) })
    newMemory.value = ''
    memories.value = await api<MemoryFact[]>('/memories')
    message.value = '记忆已保存'
  } catch (e) { error.value = errorText(e) }
  finally { savingMemory.value = false }
}

async function removeMemory(id: string) {
  error.value = ''; message.value = ''
  try {
    await api(`/memories/${id}`, { method: 'DELETE' })
    memories.value = memories.value.filter(item => item.id !== id)
    message.value = '记忆已删除'
  } catch (e) { error.value = errorText(e) }
}

function badge(ok: boolean) { return ok ? 'ok' : 'err' }
let statusTimer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  load()
  statusTimer = setInterval(async () => {
    try { readiness.value = await api<Readiness>('/settings/readiness') } catch { /* Keep last known state. */ }
  }, 10000)
})
onUnmounted(() => { if (statusTimer) clearInterval(statusTimer) })
</script>

<template>
  <div class="page settings-page">
    <div class="page-head">
      <div><h1>设置</h1><p class="sub">配置巡脉工作区、模型服务和飞书接入</p></div>
      <el-button @click="refresh">刷新</el-button>
    </div>
    <p v-if="error" style="color: var(--danger)">{{ error }}</p>
    <p v-if="message" style="color: var(--success)">{{ message }}</p>

    <div class="settings-stack settings-forms">
      <div class="card model-settings-card">
        <h3>模型接入</h3>
        <p class="muted">选择当前使用的三类模型，或添加新的模型服务配置。</p>
        <ModelsView ref="modelsView" embedded />
      </div>

      <div class="card">
        <h3>长期记忆</h3>
        <p class="muted">只保存你明确要求记住的事实；新建对话后仍可使用。</p>
        <el-input v-model="newMemory" type="textarea" :rows="2" placeholder="例如：我偏好先给出排查步骤，再给出原因分析" />
        <el-button type="primary" :loading="savingMemory" style="margin-top: 10px" @click="addMemory">保存记忆</el-button>
        <div v-if="memories.length" class="memory-list">
          <div v-for="item in memories" :key="item.id" class="memory-row">
            <span>{{ item.content }}<small v-if="item.project_id" class="muted">（项目记忆）</small></span>
            <el-button type="danger" link @click="removeMemory(item.id)">删除</el-button>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="feishu-header">
          <h3>飞书接入</h3>
          <el-switch v-model="feishu.enabled" aria-label="启用飞书" />
        </div>
        <el-form label-position="top">
          <el-form-item label="App ID"><el-input v-model="feishu.app_id" placeholder="cli_..." /></el-form-item>
          <el-form-item label="App Secret">
            <el-input v-model="feishu.app_secret" type="password" show-password placeholder="App ID 不变时可留空" />
          </el-form-item>
          <el-button type="primary" :loading="savingFeishu" @click="saveFeishu">验证凭证并保存</el-button>
          <el-button v-if="feishu.app_id || feishu.app_secret_configured" type="danger" plain :loading="deletingFeishu" @click="deleteFeishu">解绑并删除当前机器人</el-button>
        </el-form>
      </div>
    </div>

    <div class="settings-stack status-stack" v-if="readiness">
      <div class="card"><h3>飞书状态</h3><p class="muted" style="margin: 0">{{ !readiness.feishu.enabled ? '未启用' : readiness.feishu.connected ? '已连接' : '连接中 / 待检查' }}</p><div style="margin-top: 12px"><span class="badge" :class="readiness.feishu.connected ? 'ok' : 'neutral'">{{ readiness.feishu.connected ? '长连接正常' : readiness.feishu.configured ? '凭证已验证' : '未接入' }}</span><span v-if="readiness.feishu.bound" class="badge ok" style="margin-left: 8px">接收会话已绑定</span></div><p v-if="readiness.feishu.error" class="muted" style="margin: 10px 0 0">连接错误：{{ readiness.feishu.error }}</p></div>
      <div class="card"><h3>数据存储</h3><div class="kv"><span>PostgreSQL</span><span class="badge ok">已连接</span></div><div class="kv"><span>Milvus</span><span class="badge ok">已连接</span></div></div>
    </div>
  </div>
</template>

<style scoped>
.settings-page{max-width:1160px}.settings-stack{display:grid;grid-template-columns:minmax(0,1fr);gap:16px}.settings-forms{margin-bottom:18px}.settings-stack>.card{width:100%;padding:24px 26px}.settings-stack>.card h3{margin-top:0}.model-settings-card>p{margin:0 0 18px}.settings-forms :deep(.el-form){max-width:680px}.status-stack>.card{padding-top:20px;padding-bottom:20px}@media(max-width:700px){.settings-stack>.card{padding:20px 18px}}
.memory-list{margin-top:18px;border-top:1px solid #e5e7eb}.memory-row{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding:10px 0;border-bottom:1px solid #e5e7eb}.memory-row span{overflow-wrap:anywhere}
.feishu-header{display:flex;align-items:center;justify-content:space-between;gap:16px;max-width:680px;margin-bottom:18px}.feishu-header h3{margin:0}
</style>
