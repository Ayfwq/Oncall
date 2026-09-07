<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
import type { AuthUser, FeishuReceiveType, FeishuSettings, Readiness } from '../types'

interface FeishuForm {
  enabled: boolean
  app_id: string
  app_secret: string
  app_secret_configured: boolean
  default_receive_id: string
  default_receive_id_type: FeishuReceiveType
}

const readiness = ref<Readiness | null>(null)
const me = ref<AuthUser | null>(null)
const feishu = ref<FeishuForm>({ enabled: false, app_id: '', app_secret: '', app_secret_configured: false, default_receive_id: '', default_receive_id_type: 'chat_id' })
const error = ref('')
const message = ref('')
const password = ref({ current_password: '', new_password: '', confirm_password: '' })
const savingPassword = ref(false)
const savingFeishu = ref(false)

function errorText(e: unknown): string { return e instanceof Error ? e.message : String(e) }

async function load() {
  error.value = ''
  try {
    const [r, m, f] = await Promise.all([
      api<Readiness>('/settings/readiness'), api<AuthUser>('/auth/me'), api<FeishuSettings>('/settings/feishu'),
    ])
    readiness.value = r
    me.value = m
    feishu.value = { ...f, app_secret: '' }
  } catch (e) { error.value = errorText(e) }
}

async function changePassword() {
  error.value = ''; message.value = ''
  if (password.value.new_password.length < 6) { error.value = '新密码至少需要 6 位'; return }
  if (password.value.new_password !== password.value.confirm_password) { error.value = '两次输入的新密码不一致'; return }
  savingPassword.value = true
  try {
    const result = await api<{ ok: boolean; message: string }>('/auth/password', { method: 'POST', body: JSON.stringify(password.value) })
    message.value = result.message || '密码已修改'
    password.value = { current_password: '', new_password: '', confirm_password: '' }
  } catch (e) { error.value = errorText(e) }
  finally { savingPassword.value = false }
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
    feishu.value.app_secret = ''
    feishu.value.app_secret_configured = Boolean(payload.app_secret || feishu.value.app_secret_configured)
  } catch (e) { error.value = errorText(e) }
  finally { savingFeishu.value = false }
}

function badge(ok: boolean) { return ok ? 'ok' : 'err' }
onMounted(load)
</script>

<template>
  <div class="page settings-page">
    <div class="page-head">
      <div><h1>设置</h1><p class="sub">账号安全和飞书接入都可以在这里完成配置</p></div>
      <el-button @click="load">刷新</el-button>
    </div>
    <p v-if="error" style="color: var(--danger)">{{ error }}</p>
    <p v-if="message" style="color: var(--success)">{{ message }}</p>

    <div class="settings-stack settings-forms">
      <div class="card">
        <h3>登录管理</h3>
        <p class="muted">当前账号：{{ me?.username || '加载中…' }}（本地管理员）</p>
        <el-form label-position="top" @submit.prevent="changePassword">
          <el-form-item label="当前密码"><el-input v-model="password.current_password" type="password" show-password /></el-form-item>
          <el-form-item label="新密码"><el-input v-model="password.new_password" type="password" show-password placeholder="至少 6 位" /></el-form-item>
          <el-form-item label="确认新密码"><el-input v-model="password.confirm_password" type="password" show-password /></el-form-item>
          <el-button type="primary" :loading="savingPassword" @click="changePassword">修改密码</el-button>
        </el-form>
      </div>

      <div class="card">
        <h3>飞书接入</h3>
        <p class="muted">只需填写应用凭证。保存并重启服务后，在目标飞书会话中给机器人发送一条消息，系统会自动绑定接收位置。</p>
        <el-form label-position="top">
          <el-form-item label="启用飞书"><el-switch v-model="feishu.enabled" /></el-form-item>
          <el-form-item label="App ID"><el-input v-model="feishu.app_id" placeholder="cli_..." /></el-form-item>
          <el-form-item label="App Secret"><el-input v-model="feishu.app_secret" type="password" show-password placeholder="留空表示保持已有密钥" /><small v-if="feishu.app_secret_configured" class="muted">已有密钥已配置，页面不会回显。</small></el-form-item>
          <el-button type="primary" :loading="savingFeishu" @click="saveFeishu">验证凭证并保存</el-button>
        </el-form>
      </div>
    </div>

    <div class="settings-stack status-stack" v-if="readiness">
      <div class="card"><h3>模型</h3><p class="muted" style="margin: 0">{{ readiness.llm.provider }} · {{ readiness.llm.model }}</p><div style="margin-top: 12px"><span class="badge" :class="badge(readiness.llm.configured)">{{ readiness.llm.configured ? '已连接' : '未配置' }}</span></div></div>
      <div class="card"><h3>知识检索</h3><p class="muted" style="margin: 0">Embedding · {{ readiness.embedding.model }}</p><div style="margin-top: 12px"><span class="badge" :class="badge(readiness.embedding.configured)">{{ readiness.embedding.configured ? '已配置' : '未配置' }}</span><span class="badge" :class="badge(readiness.rerank.configured)" style="margin-left: 6px">Rerank · {{ readiness.rerank.configured ? '已配置' : '未配置' }}</span></div></div>
      <div class="card"><h3>飞书状态</h3><p class="muted" style="margin: 0">{{ readiness.feishu.enabled ? '已启用' : '未启用' }}</p><div style="margin-top: 12px"><span class="badge" :class="readiness.feishu.configured ? 'ok' : 'neutral'">{{ readiness.feishu.configured ? '凭证完整' : '未接入' }}</span></div></div>
      <div class="card"><h3>数据存储</h3><div class="kv"><span>PostgreSQL</span><span class="badge ok">已连接</span></div><div class="kv"><span>Milvus</span><span class="badge ok">已连接</span></div></div>
    </div>
  </div>
</template>

<style scoped>
.settings-page{max-width:900px}.settings-stack{display:grid;grid-template-columns:minmax(0,1fr);gap:16px}.settings-forms{margin-bottom:18px}.settings-stack>.card{width:100%;padding:24px 26px}.settings-stack>.card h3{margin-top:0}.settings-forms :deep(.el-form){max-width:680px}.status-stack>.card{padding-top:20px;padding-bottom:20px}@media(max-width:700px){.settings-stack>.card{padding:20px 18px}}
</style>
