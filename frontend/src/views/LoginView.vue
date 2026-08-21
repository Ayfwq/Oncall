<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, ApiError } from '../api'
import type { AuthUser } from '../types'
const username = ref('admin'), password = ref(''), error = ref(''), busy = ref(false)
const router = useRouter()
async function login() {
  if (!username.value || !password.value) { error.value = '请输入用户名和密码'; return }
  busy.value = true; error.value = ''
  try {
    await api<AuthUser>('/auth/login', { method: 'POST', body: JSON.stringify({ username: username.value, password: password.value }) })
    password.value = ''
    router.push('/')
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) error.value = '用户名或密码错误'
    else error.value = '无法连接服务，请确认已启动'
  } finally { busy.value = false }
}
function onLoginKey(e: KeyboardEvent) {
  if (e.isComposing || e.keyCode === 229) return
  login()
}
</script>

<template>
  <div class="login-wrap">
    <div class="login-card">
      <div class="login-logo">
        <span class="brand-mark">◈</span>
        <div>
          <h1>Oncall</h1>
          <p class="sub">AI SRE · 本地智能运维助手</p>
        </div>
      </div>
      <el-input v-model="username" size="large" placeholder="用户名" style="margin-bottom: 12px" @keydown.enter="onLoginKey" />
      <el-input v-model="password" size="large" type="password" placeholder="密码" show-password @keydown.enter="onLoginKey" />
      <p v-if="error" style="color: var(--danger); font-size: 13px; margin: 10px 0 0">{{ error }}</p>
      <el-button type="primary" size="large" style="width: 100%; margin-top: 20px" :loading="busy" @click="login">登 录</el-button>
    </div>
  </div>
</template>
