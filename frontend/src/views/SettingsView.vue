<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'

const readiness = ref<any>()
const me = ref<any>()
const feishu = ref({ enabled: false, app_id: '', app_secret: '', app_secret_configured: false, default_receive_id: '', default_receive_id_type: 'chat_id' })
const error = ref('')
const message = ref('')
const password = ref({ current_password: '', new_password: '', confirm_password: '' })
const savingPassword = ref(false)
const savingFeishu = ref(false)

async function load() {
  error.value = ''
  try {
    ;[readiness.value, me.value, feishu.value] = await Promise.all([
      api<any>('/settings/readiness'), api<any>('/auth/me'), api<any>('/settings/feishu'),
    ])
  } catch (e: any) { error.value = e.message }
}

async function changePassword() {
  error.value = ''; message.value = ''
  if (password.value.new_password.length < 8) { error.value = '新密码至少需要 8 位'; return }
  if (password.value.new_password !== password.value.confirm_password) { error.value = '两次输入的新密码不一致'; return }
  savingPassword.value = true
  try {
    const result = await api<any>('/auth/password', { method: 'POST', body: JSON.stringify(password.value) })
    message.value = result.message || '密码已修改'
    password.value = { current_password: '', new_password: '', confirm_password: '' }
  } catch (e: any) { error.value = e.message }
  finally { savingPassword.value = false }
}

async function saveFeishu() {
  error.value = ''; message.value = ''
  savingFeishu.value = true
  try {
    const result = await api<any>('/settings/feishu', { method: 'PUT', body: JSON.stringify(feishu.value) })
    message.value = result.message || '飞书配置已保存'
    feishu.value.app_secret = ''
    await load()
  } catch (e: any) { error.value = e.message }
  finally { savingFeishu.value = false }
}

function badge(ok: boolean, fallback = false) { return ok ? 'ok' : fallback ? 'neutral' : 'err' }
onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div><h1>设置</h1><p class="sub">账号安全和飞书接入都可以在这里完成配置</p></div>
      <el-button @click="load">刷新</el-button>
    </div>
    <p v-if="error" style="color: var(--danger)">{{ error }}</p>
    <p v-if="message" style="color: var(--success)">{{ message }}</p>

    <div class="grid settings-forms">
      <div class="card">
        <h3>登录管理</h3>
        <p class="muted">当前账号：{{ me?.username || '加载中…' }}（本地管理员）</p>
        <el-form label-position="top" @submit.prevent="changePassword">
          <el-form-item label="当前密码"><el-input v-model="password.current_password" type="password" show-password /></el-form-item>
          <el-form-item label="新密码"><el-input v-model="password.new_password" type="password" show-password placeholder="至少 8 位" /></el-form-item>
          <el-form-item label="确认新密码"><el-input v-model="password.confirm_password" type="password" show-password /></el-form-item>
          <el-button type="primary" :loading="savingPassword" @click="changePassword">修改密码</el-button>
        </el-form>
      </div>

      <div class="card">
        <h3>飞书接入</h3>
        <p class="muted">填写后点击保存，系统会先向飞书校验凭证。</p>
        <el-form label-position="top">
          <el-form-item label="启用飞书"><el-switch v-model="feishu.enabled" /></el-form-item>
          <el-form-item label="App ID"><el-input v-model="feishu.app_id" placeholder="cli_..." /></el-form-item>
          <el-form-item label="App Secret"><el-input v-model="feishu.app_secret" type="password" show-password placeholder="留空表示保持已有密钥" /><small v-if="feishu.app_secret_configured" class="muted">已有密钥已配置，页面不会回显。</small></el-form-item>
          <el-form-item label="默认接收目标（可选）"><el-input v-model="feishu.default_receive_id" placeholder="chat_id / open_id 等" /></el-form-item>
          <el-form-item label="接收目标类型"><el-select v-model="feishu.default_receive_id_type" style="width: 100%"><el-option label="群聊 chat_id" value="chat_id" /><el-option label="用户 open_id" value="open_id" /><el-option label="用户 user_id" value="user_id" /><el-option label="用户 union_id" value="union_id" /></el-select></el-form-item>
          <el-button type="primary" :loading="savingFeishu" @click="saveFeishu">验证并保存</el-button>
        </el-form>
      </div>
    </div>

    <div class="grid" v-if="readiness">
      <div class="card"><h3>模型</h3><p class="muted" style="margin: 0">{{ readiness.llm.provider }} · {{ readiness.llm.model }}</p><div style="margin-top: 12px"><span class="badge" :class="badge(readiness.llm.configured, true)">{{ readiness.llm.configured ? '已连接' : '未配置' }}</span></div></div>
      <div class="card"><h3>知识检索</h3><p class="muted" style="margin: 0">Embedding · {{ readiness.embedding.model }}</p><div style="margin-top: 12px"><span class="badge" :class="badge(readiness.embedding.configured, true)">{{ readiness.embedding.configured ? '语义向量' : 'BM25 混合' }}</span><span class="badge neutral" style="margin-left: 6px">Rerank · {{ readiness.rerank.configured ? '已配置' : '本地' }}</span></div></div>
      <div class="card"><h3>飞书状态</h3><p class="muted" style="margin: 0">{{ readiness.feishu.enabled ? '已启用' : '未启用' }}</p><div style="margin-top: 12px"><span class="badge" :class="readiness.feishu.configured ? 'ok' : 'neutral'">{{ readiness.feishu.configured ? '凭证完整' : '未接入' }}</span></div></div>
      <div class="card"><h3>数据存储</h3><div class="kv"><span>PostgreSQL</span><span class="badge ok">已连接</span></div><div class="kv"><span>Milvus</span><span class="badge ok">已连接</span></div></div>
    </div>
  </div>
</template>
