<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
const route = useRoute()
const navOpen = ref(false)
const sectionTitle = computed(() => route.path.startsWith('/incidents') ? '告警中心' : route.path.startsWith('/projects') ? '项目管理' : route.path === '/knowledge' ? '知识库' : route.path === '/settings' ? '工作区设置' : '智能对话')
watch(() => route.path, () => { navOpen.value = false })
</script>

<template>
  <div class="app">
    <div v-if="navOpen" class="nav-scrim" @click="navOpen = false"></div>
    <aside id="workspace-nav" class="sidebar" :class="{ 'is-open': navOpen }">
      <div class="brand">
        <span class="brand-symbol" aria-hidden="true"><svg viewBox="0 0 32 32" fill="none"><path d="M3 17h7l4-10 5 19 4-12 3 3h3" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
        <div><span class="brand-name">PulseOps<span class="brand-period">.</span></span><span class="brand-caption">巡脉 · 智能运维</span></div>
      </div>
      <div class="workspace-label"><span class="workspace-avatar">P</span><div>默认工作区<small>Personal workspace</small></div><span class="workspace-chevron">⌄</span></div>
      <div class="nav-caption">工作空间 <span>WORKSPACE</span></div>
      <nav class="nav" aria-label="主导航">
        <router-link to="/" class="nav-item">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
          <span>智能对话</span><span class="nav-ai">AI</span>
        </router-link>
        <router-link to="/incidents" class="nav-item">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
          <span>告警中心</span>
        </router-link>
        <router-link to="/projects" class="nav-item">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/></svg>
          <span>项目管理</span>
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
    </aside>
    <div class="workspace-main">
      <header class="workspace-header">
        <button class="mobile-menu" :aria-label="navOpen ? '关闭菜单' : '打开菜单'" :aria-expanded="navOpen" aria-controls="workspace-nav" @click="navOpen = !navOpen">☰</button>
        <div class="breadcrumb"><span>工作空间</span><span class="breadcrumb-slash">/</span><b>{{ sectionTitle }}</b></div>
        <div class="header-tools"><span class="workspace-mode"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M10 2 3 5v5c0 4 7 8 7 8s7-4 7-8V5z"/><path d="m7 10 2 2 4-4"/></svg>只读运维助手</span><router-link to="/settings" class="header-avatar" aria-label="工作区设置">P</router-link></div>
      </header>
      <main class="main" :class="{ 'chat-main': route.path === '/' }"><router-view /></main>
    </div>
  </div>
</template>
