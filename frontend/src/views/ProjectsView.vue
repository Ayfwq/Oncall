<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessageBox } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import type { MonitoredServer, ProjectDraftTestResult, ProjectSummary, ServerTestResult } from '../types'

const route = useRoute()
const router = useRouter()
const rows = ref<ProjectSummary[]>([])
const servers = ref<MonitoredServer[]>([])
const serverMode = ref<'existing' | 'new'>('existing')
const serverId = ref('')
const showServerManager = ref(route.query.manage === 'servers')
const serverForm = ref({ name: '', node_metrics_url: '', gpu_metrics_url: '', enabled: true })
const serverTest = ref<ServerTestResult | null>(null)
const testedServerSignature = ref('')
const testingServer = ref(false)
const savingServer = ref(false)
const testingServerId = ref('')

const name = ref('')
const description = ref('')
const healthUrl = ref('')
const metricsUrl = ref('')
const creating = ref(false)
const testing = ref(false)
const loading = ref(false)
const message = ref('')
const messageError = ref(false)
const search = ref('')
const testedSignature = ref('')
const testResult = ref<ProjectDraftTestResult | null>(null)

const nodeCommand = `docker run -d --name oncall-node-exporter --restart unless-stopped --network host --pid host -v "/:/host:ro,rslave" quay.io/prometheus/node-exporter:v1.12.1 --path.rootfs=/host`
const gpuCommand = `docker run -d --name oncall-dcgm-exporter --restart unless-stopped --gpus all --cap-add SYS_ADMIN -p 9400:9400 nvcr.io/nvidia/k8s/dcgm-exporter:4.6.0-4.8.3-distroless`
const serverSignature = computed(() => `${serverForm.value.node_metrics_url.trim()}|${serverForm.value.gpu_metrics_url.trim()}`)
const canSaveServer = computed(() => Boolean(serverForm.value.name.trim() && serverForm.value.node_metrics_url.trim() && serverTest.value?.ok && testedServerSignature.value === serverSignature.value))
const signature = computed(() => [serverId.value, healthUrl.value.trim(), metricsUrl.value.trim()].join('|'))
const canTest = computed(() => Boolean(serverId.value && healthUrl.value.trim() && metricsUrl.value.trim()))
const canCreate = computed(() => Boolean(name.value.trim() && testResult.value?.ok && testedSignature.value === signature.value))
const filteredRows = computed(() => {
  const q = search.value.trim().toLowerCase()
  return q ? rows.value.filter(x => `${x.name} ${x.description || ''} ${x.server_name || ''}`.toLowerCase().includes(q)) : rows.value
})
const enabledCount = computed(() => rows.value.filter(x => x.enabled).length)
const selectedServer = computed(() => servers.value.find(x => x.id === serverId.value))
const checkLabels: Record<string, string> = { server: '服务器采集器', service: '健康检查', prometheus: '应用指标' }

function errorMessage(error: unknown): string {
  const raw = String(error instanceof Error ? error.message : error || '未知错误')
  try {
    const body = JSON.parse(raw)
    if (Array.isArray(body?.detail)) return body.detail.map((x: { msg?: string }) => x.msg || '参数错误').join('；')
    if (typeof body?.detail === 'string') return body.detail
    if (typeof body?.message === 'string') return body.message
  } catch { /* API may return plain text */ }
  return raw.replace(/^\s*"|"\s*$/g, '')
}

function show(text: string, error = false) { message.value = text; messageError.value = error }

function setServerMode(mode: 'existing' | 'new') {
  serverMode.value = mode
  if (mode === 'new') serverId.value = ''
}

function projectPayload() {
  return {
    name: name.value.trim() || '待创建的 Python 项目', server_id: serverId.value,
    description: description.value.trim(), environment: 'production',
    health_url: healthUrl.value.trim(), metrics_url: metricsUrl.value.trim(),
    poll_interval: 30, enabled: false,
  }
}

async function copy(text: string) {
  try { await navigator.clipboard.writeText(text); show('安装命令已复制') }
  catch { show('浏览器无法自动复制，请手动选择命令', true) }
}

async function load() {
  loading.value = true
  try {
    const [projects, serverRows] = await Promise.all([api<ProjectSummary[]>('/projects'), api<MonitoredServer[]>('/servers')])
    rows.value = projects
    servers.value = serverRows.filter(x => x.enabled)
    if (serverId.value && !servers.value.some(x => x.id === serverId.value)) serverId.value = ''
    serverMode.value = 'existing'
  } catch (error) { show('页面数据加载失败：' + errorMessage(error), true) }
  finally { loading.value = false }
}

async function testNewServer() {
  if (!serverForm.value.name.trim()) { show('请填写服务器名称', true); return }
  if (!serverForm.value.node_metrics_url.trim()) { show('请填写系统指标地址', true); return }
  testingServer.value = true
  serverTest.value = null
  try {
    const result = await api<ServerTestResult>('/servers/test', {
      method: 'POST', body: JSON.stringify({ ...serverForm.value, name: serverForm.value.name.trim(), gpu_metrics_url: serverForm.value.gpu_metrics_url.trim() || null }),
    })
    serverTest.value = result
    testedServerSignature.value = serverSignature.value
    show(result.ok ? '服务器连接正常，可以保存并继续' : `服务器连接失败：${result.error || '没有识别到有效指标'}`, !result.ok)
  } catch (error) { show('服务器测试失败：' + errorMessage(error), true) }
  finally { testingServer.value = false }
}

async function saveNewServer() {
  if (!serverForm.value.name.trim()) { show('请填写服务器名称', true); return }
  if (!canSaveServer.value) { show('请先测试服务器连接', true); return }
  savingServer.value = true
  try {
    const created = await api<MonitoredServer>('/servers', {
      method: 'POST', body: JSON.stringify({ ...serverForm.value, name: serverForm.value.name.trim(), gpu_metrics_url: serverForm.value.gpu_metrics_url.trim() || null }),
    })
    servers.value.unshift({ ...created, project_count: 0 })
    serverId.value = created.id
    serverMode.value = 'existing'
    serverForm.value = { name: '', node_metrics_url: '', gpu_metrics_url: '', enabled: true }
    serverTest.value = null
    testedServerSignature.value = ''
    show('服务器已连接，继续填写 Python 项目信息')
  } catch (error) { show('保存服务器失败：' + errorMessage(error), true) }
  finally { savingServer.value = false }
}

async function testSavedServer(row: MonitoredServer) {
  testingServerId.value = row.id
  try {
    const result = await api<ServerTestResult>(`/servers/${row.id}/test`, { method: 'POST' })
    show(result.ok ? `${row.name} 连接正常` : `${row.name} 连接失败：${result.error || '未知错误'}`, !result.ok)
  } catch (error) { show(`${row.name} 测试失败：` + errorMessage(error), true) }
  finally { testingServerId.value = '' }
}

async function removeServer(row: MonitoredServer) {
  try {
    await ElMessageBox.confirm(`确定删除服务器“${row.name}”吗？有关联项目时系统会拒绝删除。`, '删除服务器', { type: 'warning' })
    await api(`/servers/${row.id}`, { method: 'DELETE' })
    servers.value = servers.value.filter(x => x.id !== row.id)
    if (serverId.value === row.id) serverId.value = ''
    serverMode.value = 'existing'
    show('服务器已删除')
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    show('删除失败：' + errorMessage(error), true)
  }
}

async function testDraft() {
  if (!canTest.value) { show('请先选择服务器，并填写健康检查与 /metrics 地址', true); return }
  testing.value = true
  testResult.value = null
  try {
    const result = await api<ProjectDraftTestResult>('/projects/onboard/python/test', { method: 'POST', body: JSON.stringify(projectPayload()) })
    testResult.value = result
    testedSignature.value = signature.value
    show(result.ok ? '服务器、健康检查和项目指标均已通过' : '存在连接失败，请检查下方结果', !result.ok)
  } catch (error) { show('连接测试失败：' + errorMessage(error), true) }
  finally { testing.value = false }
}

async function add() {
  if (creating.value) return
  if (!name.value.trim()) { show('请填写项目名称', true); return }
  if (!canCreate.value) { show('地址有变化或尚未测试，请重新测试全部连接', true); return }
  creating.value = true
  try {
    const result = await api<{ id: string }>('/projects/onboard/python', { method: 'POST', body: JSON.stringify(projectPayload()) })
    await load()
    router.push(`/projects/${result.id}`)
  } catch (error) { show('创建失败：' + errorMessage(error), true) }
  finally { creating.value = false }
}

onMounted(load)
</script>

<template>
  <div class="page projects-page">
    <section class="fresh-hero compact-hero">
      <div class="hero-copy"><span class="eyebrow"><i></i> MONITORED PROJECTS</span><h1>当前监控项目 <em>{{ rows.length }}</em> 个</h1><p>添加远程 Python 项目，填写健康检查地址和 Prometheus `/metrics` 地址即可。</p></div>
    </section>

    <p v-if="message" class="page-message" :class="messageError ? 'err' : 'ok'">{{ message }}</p>

    <section class="onboarding-layout">
      <div class="card wizard-card">
        <div class="card-heading"><div><span class="step-tag">QUICK ONBOARDING</span><h2>添加 Python 监控项目</h2><p>整套流程都在这里完成，必填内容保持最少。</p></div><span class="remote-chip">远程采集</span></div>

        <div class="field-block">
          <div class="field-title"><span class="field-number">1</span><div><b>项目运行在哪台服务器？</b><small>已有服务器直接复用；第一次使用就在这里连接</small></div></div>
          <div class="mode-tabs">
            <button :class="{ active: serverMode === 'existing' }" @click="setServerMode('existing')"><b>选择已有服务器</b><small>{{ servers.length ? `${servers.length} 台服务器可选` : '当前还没有服务器' }}</small></button>
            <button :class="{ active: serverMode === 'new' }" @click="setServerMode('new')"><b>添加新服务器</b><small>安装并连接采集器</small></button>
          </div>

          <div v-if="serverMode === 'existing'" class="existing-server">
            <template v-if="servers.length">
              <el-select v-model="serverId" size="large" placeholder="请选择项目所在的服务器"><el-option v-for="server in servers" :key="server.id" :value="server.id" :label="server.name"><span>{{ server.name }}</span><span class="option-note">{{ server.gpu_metrics_url ? 'CPU · GPU' : 'CPU · 内存 · 磁盘' }}</span></el-option></el-select>
              <div v-if="selectedServer" class="server-selected"><span class="live-dot"></span><b>{{ selectedServer.name }}</b><span>{{ selectedServer.gpu_metrics_url ? '含 GPU 采集' : '系统指标已配置' }}</span><a @click.prevent="showServerManager = !showServerManager">{{ showServerManager ? '收起管理' : '管理服务器' }}</a></div>
            </template>
            <div v-else class="server-empty"><span>▣</span><div><b>暂无可用服务器</b><small>先添加一台服务器，保存后会自动回到这里并选中它。</small></div><el-button type="primary" plain @click="setServerMode('new')">＋ 添加服务器</el-button></div>
          </div>

          <div v-else-if="serverMode === 'new'" class="new-server-box">
            <div class="simple-note"><span>名称和系统地址必填</span><p>系统地址用于整机指标；有 NVIDIA GPU 才填写 GPU 地址。</p></div>
            <el-collapse class="install-guide"><el-collapse-item title="服务器还没安装采集器？展开查看命令" name="install">
              <div class="command-row"><div><b>系统指标采集器（必须）</b><code>{{ nodeCommand }}</code></div><el-button size="small" @click="copy(nodeCommand)">复制</el-button></div>
              <div class="command-row"><div><b>GPU 采集器（可选）</b><code>{{ gpuCommand }}</code></div><el-button size="small" @click="copy(gpuCommand)">复制</el-button></div>
              <p class="install-hint">在目标 Linux 服务器执行；9100 和 9400 是这两个采集器的默认端口，Oncall 需要通过内网访问对应端口。</p>
            </el-collapse-item></el-collapse>
            <div class="server-form-grid">
              <el-form-item label="系统指标地址（必填，默认 9100）" required><el-input v-model="serverForm.node_metrics_url" size="large" placeholder="http://10.0.0.22:9100/metrics" /></el-form-item>
              <el-form-item label="GPU 指标地址（可选，默认 9400）"><el-input v-model="serverForm.gpu_metrics_url" size="large" placeholder="无 NVIDIA GPU 留空，例如 http://10.0.0.22:9400/metrics" /></el-form-item>
              <el-form-item label="服务器名称（必填）" required><el-input v-model="serverForm.name" size="large" placeholder="例如：服务器 B · 股票服务" /></el-form-item>
            </div>
            <div v-if="serverTest && testedServerSignature === serverSignature" class="server-test-result" :class="serverTest.ok ? 'passed' : 'failed'"><span>{{ serverTest.ok ? '✓' : '!' }}</span><div><b>{{ serverTest.ok ? '服务器连接正常' : '服务器连接失败' }}</b><small>{{ serverTest.ok ? '已直连并识别 CPU、内存、磁盘和网络指标' : (serverTest.error || '请检查地址和网络') }}</small></div></div>
            <div class="inline-actions"><el-button size="large" :loading="testingServer" @click="testNewServer">⚡ 测试服务器</el-button><el-button type="primary" size="large" :loading="savingServer" :disabled="!canSaveServer" @click="saveNewServer">保存并继续</el-button></div>
          </div>

          <div v-if="showServerManager && servers.length" class="server-manager"><div v-for="server in servers" :key="server.id" class="manager-row"><span class="server-glyph">▣</span><div><b>{{ server.name }}</b><small>{{ server.node_metrics_url }} · {{ server.project_count }} 个项目</small></div><el-button size="small" :loading="testingServerId === server.id" @click="testSavedServer(server)">测试</el-button><el-button size="small" type="danger" text @click="removeServer(server)">删除</el-button></div></div>
        </div>

        <div class="field-block" :class="{ locked: !serverId }">
          <div class="field-title"><span class="field-number">2</span><div><b>填写 Python 项目信息</b><small>{{ serverId ? '只需名称、健康检查和 Prometheus 地址' : '连接服务器后即可填写' }}</small></div></div>
          <template v-if="serverId">
            <div class="two-fields"><el-form-item label="项目名称" required><el-input v-model="name" size="large" placeholder="例如：股票行情 API" /></el-form-item><el-form-item label="用途说明（可选）"><el-input v-model="description" size="large" placeholder="例如：生产环境行情服务" /></el-form-item></div>
            <div class="endpoint-list">
              <div class="endpoint-row"><span class="endpoint-icon health">♥</span><div class="endpoint-label"><b>健康检查地址</b><small>判断服务是否可达</small></div><el-input v-model="healthUrl" size="large" placeholder="https://stock.example.com/health" /></div>
              <div class="endpoint-row"><span class="endpoint-icon metrics">⌁</span><div class="endpoint-label"><b>Prometheus 地址</b><small>获取请求、错误、延迟和进程指标</small></div><el-input v-model="metricsUrl" size="large" placeholder="https://stock.example.com/metrics" /></div>
            </div>
          </template>
          <div v-else class="locked-note"><span>→</span>请先在上方选择已有服务器，或连接一台新服务器。</div>
        </div>

        <div class="field-block final-block" :class="{ locked: !serverId }">
          <div class="field-title"><span class="field-number">3</span><div><b>一次验证，完成创建</b><small>同时检查服务器、健康状态和项目指标</small></div></div>
          <div v-if="testResult && testedSignature === signature" class="test-result" :class="testResult.ok ? 'passed' : 'failed'">
            <div v-for="check in testResult.checks" :key="check.key" class="check-item"><span>{{ check.ok ? '✓' : '!' }}</span><div><b>{{ checkLabels[check.key] }}</b><small>{{ check.ok ? '连接正常' : (check.error || '连接失败') }}</small></div></div>
            <p v-for="warning in testResult.warnings" :key="warning" class="test-warning">提示：{{ warning }}</p>
          </div>
          <div class="wizard-actions"><span>创建后先保持停用，确认首个采集快照后再开启告警。</span><div><el-button size="large" :loading="testing" :disabled="!canTest" @click="testDraft">⚡ 测试全部连接</el-button><el-button type="primary" size="large" :loading="creating" :disabled="!canCreate" @click="add">创建监控项目</el-button></div></div>
        </div>
      </div>

      <aside class="card coverage-card">
        <span class="step-tag">创建后自动获得</span><h3>无需手写指标和规则</h3><p>系统根据三个数据入口，自动生成完整监控。</p>
        <div class="source-map"><div><span class="coverage-icon mint">主机</span><p><b>服务器采集器</b><small>CPU、内存、磁盘、网络、负载</small></p></div><div><span class="coverage-icon coral">状态</span><p><b>健康检查</b><small>服务可达性与响应时间</small></p></div><div><span class="coverage-icon blue">应用</span><p><b>Prometheus</b><small>流量、错误率、P95/P99、进程资源</small></p></div></div>
        <div class="arrow-down">↓</div><div class="rule-note"><i></i><span><b>规则自动生成</b><small>固定阈值 + 历史基线 + 连续次数，异常后进入飞书告警和 AI 分析链路。</small></span></div>
        <div class="why-card"><b>为什么服务器仍会被保存？</b><p>它只是项目向导里的可复用配置，不再是独立操作模块。同一服务器上的第二个项目直接选择即可。</p></div>
      </aside>
    </section>

    <section class="project-section">
      <div class="list-heading"><div><span class="eyebrow plain">MONITORED SERVICES</span><h2>已接入项目</h2><p>{{ rows.length }} 个项目，{{ enabledCount }} 个正在监控</p></div><el-input v-model="search" clearable placeholder="搜索项目或服务器" class="project-search" /></div>
      <div class="project-grid"><article v-for="item in filteredRows" :key="item.id" class="project-tile" @click="router.push('/projects/' + item.id)"><div class="tile-top"><span class="project-glyph">Py</span><span class="status-dot" :class="item.enabled ? 'online' : ''"></span><small>{{ item.enabled ? '监控中' : '未启用' }}</small></div><h3>{{ item.name }}</h3><p>{{ item.description || '远程 Python 服务' }}</p><div class="tile-bottom"><span>▣ {{ item.server_name || '未绑定服务器' }}</span><span>{{ item.poll_interval }}s ↗</span></div></article><div v-if="loading" class="project-empty">正在加载项目…</div><div v-else-if="rows.length && !filteredRows.length" class="project-empty">没有匹配的项目</div><div v-else-if="!rows.length" class="project-empty"><span>＋</span><b>还没有项目</b><p>完成上面的三步即可创建第一个监控项目。</p></div></div>
    </section>
  </div>
</template>

<style scoped>
.projects-page{max-width:1280px;padding-top:26px}.fresh-hero{position:relative;overflow:hidden;display:grid;grid-template-columns:1.1fr .9fr;gap:34px;align-items:center;min-height:184px;margin-bottom:20px;padding:30px 34px;border:1px solid #d7eee7;border-radius:24px;background:linear-gradient(120deg,#f0fcf7 0%,#f7fbff 56%,#fff 100%);box-shadow:0 18px 50px -34px rgba(21,112,85,.45)}.fresh-hero:after{content:"";position:absolute;width:260px;height:260px;right:-90px;top:-150px;border-radius:50%;background:radial-gradient(circle,rgba(72,207,161,.18),transparent 68%)}.eyebrow{display:inline-flex;align-items:center;gap:8px;color:#27806a;font-size:11px;font-weight:750;letter-spacing:.13em}.eyebrow i{width:7px;height:7px;border-radius:50%;background:#35b98f;box-shadow:0 0 0 5px rgba(53,185,143,.12)}.eyebrow.plain{color:#6c847c}.hero-copy h1{margin:10px 0 9px;font-size:30px;line-height:1.2;letter-spacing:-.045em;color:#173b32}.hero-copy h1 em{color:#1b9c77;font-style:normal}.hero-copy p{max-width:590px;margin:0;color:#668078}.flow-line{position:relative;z-index:1;display:flex;align-items:center;justify-content:flex-end}.flow-line>i{width:30px;height:1px;background:#c9dfd8}.flow-step{display:flex;flex-direction:column;align-items:center;text-align:center;min-width:88px;color:#8a9d97}.flow-step>b{width:35px;height:35px;display:grid;place-items:center;margin-bottom:8px;border:1px solid #d5e4df;border-radius:50%;background:#fff;font-size:13px}.flow-step span{font-weight:650;font-size:12px}.flow-step small{display:block;color:#a2b1ac;font-weight:400;white-space:nowrap}.flow-step.done>b{color:#fff;border-color:#30ad85;background:#30ad85}.flow-step.done,.flow-step.active{color:#267761}.flow-step.active>b{color:#25856b;border:2px solid #51b99b;box-shadow:0 0 0 5px rgba(65,181,147,.1)}.page-message{margin:0 0 16px;padding:10px 14px;border-radius:11px;font-size:13px}.page-message.ok{color:#17694f;background:#edf9f4;border:1px solid #ccecdf}.page-message.err{color:#ae3f46;background:#fff4f4;border:1px solid #f4d6d8}.onboarding-layout{display:grid;grid-template-columns:minmax(0,1fr) 310px;gap:16px;align-items:start}.wizard-card{padding:26px 28px;border-color:#dfeae6;box-shadow:0 14px 42px -34px rgba(20,67,54,.5)}.card-heading{display:flex;justify-content:space-between;align-items:flex-start;padding-bottom:20px;border-bottom:1px solid #edf2f0}.card-heading h2{margin:5px 0 3px;font-size:21px;color:#223b35}.card-heading p,.coverage-card>p{margin:0;color:#7a8d87;font-size:13px}.step-tag{color:#2c9275;font-size:10px;font-weight:800;letter-spacing:.13em}.remote-chip{padding:5px 10px;border:1px solid #cbeade;border-radius:999px;color:#277b65;background:#f0fbf7;font-size:11px}.field-block{padding:22px 0;border-bottom:1px solid #edf2f0}.field-block.locked{opacity:.63}.final-block{border-bottom:0;padding-bottom:0}.field-title{display:flex;align-items:center;gap:11px;margin-bottom:13px}.field-title b,.field-title small{display:block}.field-title b{color:#2d433d;font-size:14px}.field-title small{color:#95a39f;font-size:11px}.field-number{width:27px;height:27px;display:grid;place-items:center;border-radius:9px;color:#278067;background:#eaf8f3;font-size:12px;font-weight:750}.mode-tabs{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px}.mode-tabs button{padding:13px 15px;text-align:left;border:1px solid #e0ebe7;border-radius:13px;color:#667c75;background:#fbfdfc;cursor:pointer}.mode-tabs button.active{color:#21735d;border-color:#77c9ae;background:#f0fbf7;box-shadow:0 0 0 3px rgba(53,185,143,.07)}.mode-tabs b,.mode-tabs small{display:block}.mode-tabs b{font-size:12px}.mode-tabs small{margin-top:2px;color:#95a49f;font-size:10px}.existing-server .el-select{width:100%}.server-selected{display:flex;align-items:center;gap:8px;margin-top:9px;color:#81918c;font-size:11px}.server-selected b{color:#496059}.server-selected a{margin-left:auto;color:#258269;cursor:pointer}.live-dot,.status-dot{width:7px;height:7px;border-radius:50%;background:#b7c2be}.live-dot,.status-dot.online{background:#34b98c;box-shadow:0 0 0 4px rgba(52,185,140,.1)}.option-note{float:right;color:#95a39f;font-size:11px}.new-server-box{padding:16px;border:1px solid #dcece6;border-radius:15px;background:#fbfefc}.simple-note{display:flex;align-items:center;gap:11px;margin-bottom:7px}.simple-note span{flex:0 0 auto;padding:5px 9px;border-radius:999px;color:#1e765e;background:#e8f8f2;font-size:10px;font-weight:750}.simple-note p{margin:0;color:#7d9089;font-size:11px}.install-guide{margin:4px 0 15px}.command-row{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;padding:8px 0}.command-row>div{min-width:0;flex:1}.command-row b{display:block;margin-bottom:4px;font-size:11px}.command-row code{display:block;overflow:auto;white-space:nowrap;padding:8px;font-size:10px}.install-hint{margin:5px 0;color:#82948e;font-size:10px}.server-form-grid{display:grid;grid-template-columns:1.2fr .8fr;gap:10px}.server-form-grid :deep(.el-form-item){margin-bottom:4px}.server-form-grid :deep(.el-form-item:nth-child(3)){grid-column:1/-1}.inline-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:8px}.server-test-result{display:flex;align-items:center;gap:9px;margin-top:9px;padding:9px 11px;border-radius:10px}.server-test-result.passed{color:#17694f;background:#edf9f4}.server-test-result.failed{color:#ae3f46;background:#fff1f2}.server-test-result>span{width:23px;height:23px;display:grid;place-items:center;border-radius:50%;color:#fff;background:#31ae84}.server-test-result.failed>span{background:#df6570}.server-test-result b,.server-test-result small{display:block;font-size:10px}.server-manager{display:grid;gap:7px;margin-top:12px;padding:10px;border-radius:12px;background:#f5f9f7}.manager-row{display:flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid #e3ebe8;border-radius:10px;background:#fff}.manager-row>div{min-width:0;flex:1}.manager-row b,.manager-row small{display:block}.manager-row b{color:#3c534c;font-size:11px}.manager-row small{overflow:hidden;color:#8c9b96;font-size:9px;text-overflow:ellipsis;white-space:nowrap}.server-glyph{color:#299071}.two-fields{display:grid;grid-template-columns:1fr 1fr;gap:14px}.two-fields :deep(.el-form-item){margin-bottom:12px}.endpoint-list{display:grid;gap:10px}.endpoint-row{display:grid;grid-template-columns:38px 155px minmax(0,1fr);align-items:center;gap:11px;padding:10px 12px;border:1px solid #e4ece9;border-radius:13px;background:#fbfdfc}.endpoint-icon{width:34px;height:34px;display:grid;place-items:center;border-radius:10px;font-weight:750}.endpoint-icon.health{color:#d65b65;background:#fff0f1}.endpoint-icon.metrics{color:#397bb7;background:#edf6ff;font-size:18px}.endpoint-label b,.endpoint-label small{display:block}.endpoint-label b{color:#354b45;font-size:12px}.endpoint-label small{color:#9aa8a4;font-size:10px}.locked-note{padding:18px;border:1px dashed #ccdcd7;border-radius:13px;color:#83958f;background:#fafcfb;font-size:12px}.locked-note span{margin-right:8px;color:#37a382}.test-result{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:15px;padding:12px;border-radius:13px}.test-result.passed{background:#f0faf6;border:1px solid #cfeee2}.test-result.failed{background:#fff6f6;border:1px solid #f2d8da}.check-item{display:flex;align-items:center;gap:8px}.check-item>span{width:25px;height:25px;display:grid;place-items:center;border-radius:50%;color:#fff;background:#31ae84;font-weight:800}.failed .check-item>span{background:#df6570}.check-item b,.check-item small{display:block}.check-item b{color:#355048;font-size:11px}.check-item small{max-width:180px;overflow:hidden;color:#7d928b;font-size:10px;text-overflow:ellipsis;white-space:nowrap}.test-warning{grid-column:1/-1;margin:5px 2px 0;color:#9a690d;font-size:11px}.wizard-actions{display:flex;justify-content:space-between;align-items:center;gap:16px}.wizard-actions>span{color:#93a09c;font-size:11px}.wizard-actions>div{display:flex;gap:8px}.coverage-card{padding:25px;border-color:#dce9e5;background:linear-gradient(160deg,#fff 0%,#f7fcfa 100%)}.coverage-card h3{margin:7px 0 5px;color:#2b443d;font-size:17px}.source-map{display:grid;gap:5px;margin:18px 0}.source-map>div{display:flex;align-items:center;gap:11px;padding:10px 5px;border-bottom:1px solid #e9f0ee}.source-map>div:last-child{border:0}.coverage-icon{width:38px;height:38px;display:grid;place-items:center;flex:0 0 auto;border-radius:11px;font-size:10px;font-weight:800}.coverage-icon.mint{color:#20836a;background:#e4f7f0}.coverage-icon.blue{color:#397bb7;background:#eaf4fd}.coverage-icon.coral{color:#c95c62;background:#fff0f0}.source-map p{margin:0}.source-map b,.source-map small{display:block}.source-map b{color:#3a514a;font-size:12px}.source-map small{color:#93a29d;font-size:10px}.arrow-down{text-align:center;color:#6cb89f}.rule-note{display:flex;gap:10px;padding:13px;border-radius:12px;background:#ecf8f4}.rule-note i{width:8px;height:8px;margin-top:5px;border-radius:50%;background:#38b58d}.rule-note span{flex:1}.rule-note b,.rule-note small{display:block}.rule-note b{color:#376257;font-size:11px}.rule-note small{color:#799087;font-size:10px;line-height:1.5}.why-card{margin-top:12px;padding:13px;border:1px solid #e1ebe7;border-radius:12px;background:#fff}.why-card b{color:#405750;font-size:11px}.why-card p{margin:4px 0 0;color:#82938d;font-size:10px;line-height:1.55}.project-section{margin-top:34px}.list-heading{display:flex;align-items:flex-end;justify-content:space-between;margin-bottom:14px}.list-heading h2{margin:3px 0 0;color:#2c413b;font-size:20px}.list-heading p{margin:0;color:#91a09b;font-size:11px}.project-search{max-width:265px}.project-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(245px,1fr));gap:13px}.project-tile{min-height:168px;padding:18px;border:1px solid #e1ebe7;border-radius:17px;background:rgba(255,255,255,.9);cursor:pointer;transition:.2s ease}.project-tile:hover{transform:translateY(-3px);border-color:#b8ded2;box-shadow:0 16px 34px -26px rgba(23,98,76,.55)}.tile-top{display:flex;align-items:center;gap:7px;color:#8b9b96;font-size:11px}.project-glyph{width:31px;height:31px;display:grid;place-items:center;margin-right:auto;border-radius:10px;color:#fff;background:linear-gradient(145deg,#44c299,#278c70);font-size:11px;font-weight:800}.project-tile h3{margin:16px 0 2px;color:#2f463f;font-size:15px}.project-tile>p{min-height:36px;margin:0;color:#859690;font-size:11px}.tile-bottom{display:flex;justify-content:space-between;margin-top:13px;padding-top:11px;border-top:1px solid #edf2f0;color:#80928c;font-size:10px}.project-empty{grid-column:1/-1;display:grid;place-items:center;min-height:150px;padding:26px;border:1px dashed #cfe0db;border-radius:17px;color:#8fa09b;background:rgba(255,255,255,.62);text-align:center}.project-empty>span{font-size:27px;color:#59b89b}.project-empty>b{color:#587068}.project-empty p{margin:0;font-size:11px}
.server-form-grid{grid-template-columns:1fr}.server-form-grid :deep(.el-form-item){grid-column:1/-1}
.compact-hero{grid-template-columns:1fr;min-height:132px;padding-top:25px;padding-bottom:25px}
.server-empty{display:flex;align-items:center;gap:12px;padding:15px 16px;border:1px dashed #c9ddd6;border-radius:13px;background:#fafdfc}.server-empty>span{width:35px;height:35px;display:grid;place-items:center;flex:0 0 auto;border-radius:10px;color:#2c9174;background:#eaf8f3}.server-empty>div{min-width:0;flex:1}.server-empty b,.server-empty small{display:block}.server-empty b{color:#405750;font-size:12px}.server-empty small{margin-top:2px;color:#899b95;font-size:10px}
@media(max-width:1020px){.fresh-hero{grid-template-columns:1fr}.flow-line{justify-content:flex-start}.onboarding-layout{grid-template-columns:1fr}.coverage-card{display:none}}@media(max-width:700px){.projects-page{padding-top:14px}.fresh-hero{padding:23px 20px;border-radius:18px}.hero-copy h1{font-size:25px}.flow-step small{display:none}.flow-line>i{width:14px}.wizard-card{padding:20px 17px}.mode-tabs,.two-fields,.server-form-grid{grid-template-columns:1fr}.server-form-grid :deep(.el-form-item:nth-child(3)){grid-column:auto}.endpoint-row{grid-template-columns:36px 1fr}.endpoint-row .el-input{grid-column:1/-1}.test-result{grid-template-columns:1fr}.wizard-actions,.list-heading{align-items:stretch;flex-direction:column}.wizard-actions>div{display:grid;grid-template-columns:1fr 1fr}.project-search{max-width:none}}
</style>
