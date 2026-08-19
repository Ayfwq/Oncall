<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
const router = useRouter()
const route = useRoute()
const navOpen = ref(false)
const pageTitle = computed(() => {
  if (route.path.startsWith('/projects')) return '监控项目'
  if (route.path.startsWith('/incidents')) return '告警中心'
  if (route.path.startsWith('/knowledge')) return '知识库'
  if (route.path.startsWith('/settings')) return '系统设置'
  return 'AI 运维工作台'
})
watch(() => route.path, () => { navOpen.value = false })
async function logout() {
  await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' })
  router.push('/login')
}
</script>

<template>
  <div class="app">
    <div v-if="$route.path !== '/login' && navOpen" class="nav-scrim" @click="navOpen = false"></div>
    <aside v-if="$route.path !== '/login'" class="sidebar" :class="{ 'is-open': navOpen }">
      <div class="brand">
        <span class="brand-mark">◈</span>
        <span class="brand-name">Oncall</span>
        <span class="brand-sub">AI SRE</span>
      </div>
      <nav class="nav">
        <router-link to="/" class="nav-item">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
          <span>对话</span>
        </router-link>
        <router-link to="/incidents" class="nav-item">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
          <span>告警</span>
        </router-link>
        <router-link to="/projects" class="nav-item">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/></svg>
          <span>项目</span>
        </router-link>
        <router-link to="/knowledge" class="nav-item">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
          <span>知识库</span>
        </router-link>
        <router-link to="/settings" class="nav-item">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
          <span>设置</span>
        </router-link>
      </nav>
      <div class="sidebar-footer">
        <div class="avatar">A</div>
        <div class="who"><b>admin</b><span>本地管理员</span></div>
        <button class="logout" @click="logout">退出</button>
      </div>
    </aside>
    <main class="main">
      <header v-if="$route.path !== '/login'" class="topbar">
        <button class="mobile-menu" aria-label="打开菜单" @click="navOpen = !navOpen">☰</button>
        <div class="topbar-title"><span>{{ pageTitle }}</span><small>本地智能运维控制台</small></div>
        <div class="topbar-actions">
          <span class="runtime-pill"><i></i> 本地服务正常</span>
          <button class="topbar-icon" title="刷新当前页面" @click="router.go(0)">↻</button>
        </div>
      </header>
      <router-view />
    </main>
  </div>
</template>
