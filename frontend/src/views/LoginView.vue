<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
const username = ref('admin'), password = ref(''), error = ref(''), busy = ref(false)
const router = useRouter()
async function login() {
  if (!username.value || !password.value) { error.value = '请输入用户名和密码'; return }
  busy.value = true; error.value = ''
  try {
    const r = await fetch('/api/auth/login', {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: username.value, password: password.value }),
    })
    if (r.ok) router.push('/')
    else error.value = '用户名或密码错误'
  } catch {
    error.value = '无法连接服务，请确认已启动'
  } finally { busy.value = false }
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
      <el-input v-model="username" size="large" placeholder="用户名" style="margin-bottom: 12px" @keydown.enter="login" />
      <el-input v-model="password" size="large" type="password" placeholder="密码" show-password @keydown.enter="login" />
      <p v-if="error" style="color: var(--danger); font-size: 13px; margin: 10px 0 0">{{ error }}</p>
      <el-button type="primary" size="large" style="width: 100%; margin-top: 20px" :loading="busy" @click="login">登 录</el-button>
    </div>
  </div>
</template>
