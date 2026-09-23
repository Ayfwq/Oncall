<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ElMessageBox } from "element-plus";
import { useRouter } from "vue-router";
import { api } from "../api";
import {
  cadvisorInstallCommand,
  cadvisorRemoveCommand,
  cadvisorVerifyCommand,
  collectorInstallCommand,
  collectorRemoveCommand,
  collectorVerifyCommand,
  gpuExporterInstallCommand,
  gpuExporterRemoveCommand,
  gpuExporterVerifyCommand,
  nodeExporterInstallCommand,
  nodeExporterRemoveCommand,
  nodeExporterVerifyCommand,
} from "../collectorCommands";
import type {
  MonitoredServer,
  ProjectDraftTestResult,
  ProjectSummary,
  ServerTestResult,
} from "../types";

const router = useRouter();
const rows = ref<ProjectSummary[]>([]);
const servers = ref<MonitoredServer[]>([]);
const serverMode = ref<"existing" | "new">("existing");
const serverId = ref("");
const serverForm = ref({
  name: "",
  node_metrics_url: "",
  container_metrics_url: "",
  gpu_metrics_url: "",
  collector_url: "",
  collector_token: "",
  enabled: true,
});
const serverTest = ref<ServerTestResult | null>(null);
const testedServerSignature = ref("");
const testingServer = ref(false);
const savingServer = ref(false);
const testingServerId = ref("");
const deletingServerId = ref("");
const deletingProjectId = ref("");
const savedServerTests = ref<Record<string, ServerTestResult>>({});

const name = ref("");
const description = ref("");
const metricsUrl = ref("");
const databaseUrl = ref("");
const composeProject = ref("");
const creating = ref(false);
const testing = ref(false);
const loading = ref(false);
const message = ref("");
const messageError = ref(false);
const copiedCommand = ref("");
let copyResetTimer: ReturnType<typeof setTimeout> | undefined;
const search = ref("");
const testedSignature = ref("");
const testResult = ref<ProjectDraftTestResult | null>(null);
const testResultSummary = computed(() => {
  if (!testResult.value) return "";
  const passed = testResult.value.checks.filter((check) => check.ok).length;
  const failed = testResult.value.checks.length - passed;
  return testResult.value.ok
    ? "全部连接通过"
    : `已通过 ${passed} 项，${failed} 项失败`;
});
const testResultHint = computed(() =>
  testResult.value?.ok
    ? "所有监控数据入口均可用，可以创建项目。"
    : "请优先处理红色项目，修复后重新测试全部连接。",
);

const nodeCommand = nodeExporterInstallCommand();
const nodeCheckCommand = nodeExporterVerifyCommand();
const nodeRemoveCommand = nodeExporterRemoveCommand();
const gpuCommand = gpuExporterInstallCommand();
const gpuCheckCommand = gpuExporterVerifyCommand();
const gpuRemoveCommand = gpuExporterRemoveCommand();
const collectorCommand = computed(() =>
  collectorInstallCommand(serverForm.value.collector_token.trim()),
);
const collectorCheckCommand = computed(() =>
  collectorVerifyCommand(serverForm.value.collector_token.trim()),
);
const collectorRemoveCommandText = collectorRemoveCommand();
const cadvisorCommand = cadvisorInstallCommand();
const cadvisorCheckCommand = cadvisorVerifyCommand();
const cadvisorRemoveCommandText = cadvisorRemoveCommand();
const serverSignature = computed(
  () =>
    `${serverForm.value.node_metrics_url.trim()}|${serverForm.value.container_metrics_url.trim()}|${serverForm.value.gpu_metrics_url.trim()}|${serverForm.value.collector_url.trim()}|${serverForm.value.collector_token.trim()}`,
);
const canSaveServer = computed(() =>
  Boolean(
    serverForm.value.name.trim() &&
      serverForm.value.node_metrics_url.trim() &&
      serverForm.value.container_metrics_url.trim() &&
      serverForm.value.collector_url.trim() &&
      serverForm.value.collector_token.trim() &&
      serverTest.value?.ok &&
      testedServerSignature.value === serverSignature.value,
  ),
);
const signature = computed(() =>
  [
    serverId.value,
    metricsUrl.value.trim(),
    databaseUrl.value.trim(),
    composeProject.value.trim(),
  ].join("|"),
);
const canTest = computed(() =>
  Boolean(
    serverId.value &&
      metricsUrl.value.trim() &&
      databaseUrl.value.trim() &&
      composeProject.value.trim(),
  ),
);
const canCreate = computed(() =>
  Boolean(
    name.value.trim() &&
      testResult.value?.ok &&
      testedSignature.value === signature.value,
  ),
);
const filteredRows = computed(() => {
  const q = search.value.trim().toLowerCase();
  return q
    ? rows.value.filter((x) =>
        `${x.name} ${x.description || ""} ${x.server_name || ""}`
          .toLowerCase()
          .includes(q),
      )
    : rows.value;
});
const enabledCount = computed(() => rows.value.filter((x) => x.enabled).length);
const selectedServer = computed(() =>
  servers.value.find((x) => x.id === serverId.value),
);
const checkLabels: Record<string, string> = {
  server: "Node Exporter + cAdvisor",
  prometheus: "应用指标",
  logs: "Docker 日志",
  database: "PostgreSQL 数据库",
};
function serverTestItemsFor(result: ServerTestResult | null) {
  const resources = result?.resources || {};
  const read = (key: string) =>
    (resources[key] || {}) as Record<string, unknown>;
  const item = (key: string, label: string, optional = false) => {
    const resource = read(key);
    const configured = resource.configured !== false;
    const ok = configured && resource.ok === true;
    return {
      key,
      label,
      optional,
      ok,
      configured,
      error: typeof resource.error === "string" ? resource.error : "",
    };
  };
  return [
    item("node", "Node Exporter"),
    item("container", "cAdvisor"),
    item("collector", "Collector"),
    item("gpu", "GPU Exporter", true),
  ];
}
const serverTestItems = computed(() => serverTestItemsFor(serverTest.value));

function errorMessage(error: unknown): string {
  const raw = String(
    error instanceof Error ? error.message : error || "未知错误",
  );
  try {
    const body = JSON.parse(raw);
    if (Array.isArray(body?.detail))
      return body.detail
        .map((x: { msg?: string }) => x.msg || "参数错误")
        .join("；");
    if (typeof body?.detail === "string") return body.detail;
    if (typeof body?.message === "string") return body.message;
  } catch {
    /* API may return plain text */
  }
  return raw.replace(/^\s*"|"\s*$/g, "");
}

function show(text: string, error = false) {
  message.value = text;
  messageError.value = error;
}

function generateCollectorToken() {
  const random =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID().replace(/-/g, "")
      : Math.random().toString(36).slice(2) + Date.now().toString(36);
  serverForm.value.collector_token = `oncall-${random}`;
}

function setServerMode(mode: "existing" | "new") {
  serverMode.value = mode;
  if (mode === "new") {
    serverId.value = "";
    if (!serverForm.value.collector_token) generateCollectorToken();
  }
}

function projectPayload() {
  return {
    name: name.value.trim() || "待创建的 Python 项目",
    server_id: serverId.value,
    description: description.value.trim(),
    environment: "production",
    metrics_url: metricsUrl.value.trim(),
    database_url: databaseUrl.value.trim(),
    compose_project: composeProject.value.trim() || null,
    poll_interval: 30,
    enabled: false,
  };
}

async function copy(text: string, key: string) {
  try {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      const copied = document.execCommand("copy");
      textarea.remove();
      if (!copied) throw new Error("copy failed");
    }
    copiedCommand.value = key;
    show("复制成功");
    if (copyResetTimer) clearTimeout(copyResetTimer);
    copyResetTimer = setTimeout(() => {
      copiedCommand.value = "";
    }, 2200);
  } catch {
    show("浏览器无法自动复制，请手动选择命令", true);
  }
}

async function load() {
  loading.value = true;
  try {
    const [projects, serverRows] = await Promise.all([
      api<ProjectSummary[]>("/projects"),
      api<MonitoredServer[]>("/servers"),
    ]);
    rows.value = projects;
    servers.value = serverRows.filter((x) => x.enabled);
    if (serverId.value && !servers.value.some((x) => x.id === serverId.value))
      serverId.value = "";
    serverMode.value = "existing";
  } catch (error) {
    show("页面数据加载失败：" + errorMessage(error), true);
  } finally {
    loading.value = false;
  }
}

async function testNewServer() {
  if (!serverForm.value.name.trim()) {
    show("请填写服务器名称", true);
    return;
  }
  if (!serverForm.value.node_metrics_url.trim()) {
    show("请填写系统指标地址", true);
    return;
  }
  if (!serverForm.value.container_metrics_url.trim()) {
    show("请填写容器指标地址，cAdvisor 是必需的", true);
    return;
  }
  if (
    !serverForm.value.collector_url.trim() ||
    !serverForm.value.collector_token.trim()
  ) {
    show("请填写 Collector 地址和 Token", true);
    return;
  }
  testingServer.value = true;
  serverTest.value = null;
  try {
    const result = await api<ServerTestResult>("/servers/test", {
      method: "POST",
      body: JSON.stringify({
        ...serverForm.value,
        name: serverForm.value.name.trim(),
        container_metrics_url: serverForm.value.container_metrics_url.trim(),
        gpu_metrics_url: serverForm.value.gpu_metrics_url.trim() || null,
        collector_url: serverForm.value.collector_url.trim() || null,
        collector_token: serverForm.value.collector_token.trim() || null,
      }),
    });
    serverTest.value = result;
    testedServerSignature.value = serverSignature.value;
    show(
      result.ok
        ? "服务器连接正常，可以保存并继续"
        : `服务器连接失败：${result.error || "没有识别到有效指标"}`,
      !result.ok,
    );
  } catch (error) {
    show("服务器测试失败：" + errorMessage(error), true);
  } finally {
    testingServer.value = false;
  }
}

async function saveNewServer() {
  if (!serverForm.value.name.trim()) {
    show("请填写服务器名称", true);
    return;
  }
  if (!canSaveServer.value) {
    show("请先测试服务器连接", true);
    return;
  }
  savingServer.value = true;
  try {
    const created = await api<MonitoredServer>("/servers", {
      method: "POST",
      body: JSON.stringify({
        ...serverForm.value,
        name: serverForm.value.name.trim(),
        container_metrics_url: serverForm.value.container_metrics_url.trim(),
        gpu_metrics_url: serverForm.value.gpu_metrics_url.trim() || null,
        collector_url: serverForm.value.collector_url.trim() || null,
        collector_token: serverForm.value.collector_token.trim() || null,
      }),
    });
    servers.value.unshift({ ...created, project_count: 0 });
    serverId.value = created.id;
    serverMode.value = "existing";
    serverForm.value = {
      name: "",
      node_metrics_url: "",
      container_metrics_url: "",
      gpu_metrics_url: "",
      collector_url: "",
      collector_token: "",
      enabled: true,
    };
    serverTest.value = null;
    testedServerSignature.value = "";
    show("服务器已连接，继续填写 Python 项目信息");
  } catch (error) {
    show("保存服务器失败：" + errorMessage(error), true);
  } finally {
    savingServer.value = false;
  }
}

async function testSavedServer(row: MonitoredServer) {
  testingServerId.value = row.id;
  try {
    const result = await api<ServerTestResult>(`/servers/${row.id}/test`, {
      method: "POST",
    });
    savedServerTests.value = { ...savedServerTests.value, [row.id]: result };
    show(
      result.ok
        ? `${row.name} 连接正常`
        : `${row.name} 连接失败：${result.error || "未知错误"}`,
      !result.ok,
    );
  } catch (error) {
    const failed: ServerTestResult = {
      ok: false,
      signals: {},
      resource_signals: {},
      resources: {},
      error: errorMessage(error),
    };
    savedServerTests.value = { ...savedServerTests.value, [row.id]: failed };
    show(`${row.name} 测试失败：` + errorMessage(error), true);
  } finally {
    testingServerId.value = "";
  }
}

async function removeServer(row: MonitoredServer) {
  try {
    await ElMessageBox.confirm(
      `确定删除服务器“${row.name}”吗？有关联项目时系统会拒绝删除。`,
      "删除服务器",
      { type: "warning" },
    );
    deletingServerId.value = row.id;
    await api(`/servers/${row.id}`, { method: "DELETE" });
    servers.value = servers.value.filter((x) => x.id !== row.id);
    if (serverId.value === row.id) {
      serverId.value = "";
      testResult.value = null;
      testedSignature.value = "";
    }
    serverMode.value = "existing";
    show("服务器已删除");
  } catch (error) {
    if (error === "cancel" || error === "close") return;
    show("删除失败：" + errorMessage(error), true);
  } finally {
    deletingServerId.value = "";
  }
}

async function removeProject(row: ProjectSummary) {
  try {
    await ElMessageBox.confirm(
      `确定删除项目“${row.name}”吗？项目配置、历史告警和诊断记录会一并删除。`,
      "删除项目",
      { type: "warning" },
    );
    deletingProjectId.value = row.id;
    await api(`/projects/${row.id}`, { method: "DELETE" });
    rows.value = rows.value.filter((x) => x.id !== row.id);
    if (row.server_id) {
      servers.value = servers.value.map((server) =>
        server.id === row.server_id
          ? { ...server, project_count: Math.max(0, server.project_count - 1) }
          : server,
      );
    }
    show("项目已删除");
  } catch (error) {
    if (error === "cancel" || error === "close") return;
    show("删除失败：" + errorMessage(error), true);
  } finally {
    deletingProjectId.value = "";
  }
}

async function testDraft() {
  if (!canTest.value) {
    show(
      "请先选择服务器，并填写 /metrics、数据库连接串和 Compose 项目名",
      true,
    );
    return;
  }
  testing.value = true;
  testResult.value = null;
  try {
    const result = await api<ProjectDraftTestResult>(
      "/projects/onboard/python/test",
      { method: "POST", body: JSON.stringify(projectPayload()) },
    );
    testResult.value = result;
    testedSignature.value = signature.value;
    show(
      result.ok
        ? "服务器、应用、日志和数据库均已通过"
        : "存在连接失败，请检查下方结果",
      !result.ok,
    );
  } catch (error) {
    show("连接测试失败：" + errorMessage(error), true);
  } finally {
    testing.value = false;
  }
}

async function add() {
  if (creating.value) return;
  if (!name.value.trim()) {
    show("请填写项目名称", true);
    return;
  }
  if (!canCreate.value) {
    show("地址有变化或尚未测试，请重新测试全部连接", true);
    return;
  }
  creating.value = true;
  try {
    const result = await api<{ id: string }>("/projects/onboard/python", {
      method: "POST",
      body: JSON.stringify(projectPayload()),
    });
    await load();
    router.push(`/projects/${result.id}`);
  } catch (error) {
    show("创建失败：" + errorMessage(error), true);
  } finally {
    creating.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="page projects-page">
    <section class="fresh-hero compact-hero">
      <div class="hero-copy">
        <span class="eyebrow"><i></i> MONITORED PROJECTS</span>
        <h1>
          当前监控项目 <em>{{ rows.length }}</em> 个
        </h1>
        <p>统一接入服务器、应用指标、Docker 日志和 PostgreSQL 诊断。</p>
      </div>
    </section>

    <p v-if="message" class="page-message" :class="messageError ? 'err' : 'ok'">
      {{ message }}
    </p>

    <section class="onboarding-layout">
      <div class="card wizard-card">
        <div class="card-heading">
          <div>
            <span class="step-tag">QUICK ONBOARDING</span>
            <h2>添加 Python 监控项目</h2>
          </div>
          <span class="remote-chip">远程采集</span>
        </div>

        <div class="field-block">
          <div class="field-title">
            <span class="field-number">1</span>
            <div><b>项目运行在哪台服务器？</b></div>
          </div>
          <div class="mode-tabs">
            <button
              :class="{ active: serverMode === 'existing' }"
              @click="setServerMode('existing')"
            >
              <b>选择已有服务器</b
              ><small>{{
                servers.length
                  ? `${servers.length} 台服务器可选`
                  : "当前还没有服务器"
              }}</small>
            </button>
            <button
              :class="{ active: serverMode === 'new' }"
              @click="setServerMode('new')"
            >
              <b>添加新服务器</b><small>安装并连接采集器</small>
            </button>
          </div>

          <div v-if="serverMode === 'existing'" class="existing-server">
            <template v-if="servers.length">
              <div class="server-select-shell">
                <el-select
                  v-model="serverId"
                  size="large"
                  placeholder="请选择项目所在的服务器"
                >
                  <el-option
                    v-for="server in servers"
                    :key="server.id"
                    :value="server.id"
                    :label="server.name"
                  >
                    <span class="server-option-row"
                      ><span class="server-option-name">{{
                        server.name
                      }}</span></span
                    >
                  </el-option>
                </el-select>
                <span v-if="selectedServer" class="server-select-actions">
                  <button
                    type="button"
                    class="server-select-test"
                    :disabled="
                      Boolean(testingServerId) || Boolean(deletingServerId)
                    "
                    title="测试服务器"
                    aria-label="测试服务器"
                    @mousedown.stop
                    @click.stop="testSavedServer(selectedServer)"
                  >
                    {{
                      testingServerId === selectedServer.id ? "测试中" : "测试"
                    }}
                  </button>
                  <button
                    type="button"
                    class="server-select-delete"
                    :disabled="
                      Boolean(deletingServerId) ||
                      testingServerId === selectedServer.id
                    "
                    title="删除服务器"
                    aria-label="删除服务器"
                    @mousedown.stop
                    @click.stop="removeServer(selectedServer)"
                  >
                    删除
                  </button>
                </span>
              </div>
              <div
                v-if="serverId && savedServerTests[serverId]"
                class="saved-server-test-result"
                :class="savedServerTests[serverId]?.ok ? 'passed' : 'failed'"
              >
                <div class="server-test-summary">
                  <span>{{ savedServerTests[serverId]?.ok ? "✓" : "!" }}</span>
                  <div>
                    <b>{{
                      savedServerTests[serverId]?.ok
                        ? "服务器采集器全部通过"
                        : "服务器采集器测试未通过"
                    }}</b
                    ><small>{{
                      savedServerTests[serverId]?.ok
                        ? "Node Exporter、cAdvisor 和 Collector 均可访问"
                        : savedServerTests[serverId]?.error ||
                          "请检查地址、端口和采集器日志"
                    }}</small>
                  </div>
                </div>
                <div class="server-test-checks">
                  <div
                    v-for="item in serverTestItemsFor(
                      savedServerTests[serverId] || null,
                    )"
                    :key="item.key"
                    class="server-test-check"
                    :class="
                      item.ok
                        ? 'passed'
                        : item.optional && !item.configured
                          ? 'optional'
                          : 'failed'
                    "
                  >
                    <span>{{
                      item.ok
                        ? "✓"
                        : item.optional && !item.configured
                          ? "–"
                          : "×"
                    }}</span
                    ><b>{{ item.label }}</b
                    ><small>{{
                      item.optional && !item.configured
                        ? "未配置"
                        : item.ok
                          ? "测试通过"
                          : item.error || "测试失败"
                    }}</small>
                  </div>
                </div>
              </div>
            </template>
          </div>

          <div v-if="serverMode === 'new'" class="new-server-box">
            <el-collapse class="install-guide"
              ><el-collapse-item
                title="服务器还没安装采集器？展开查看命令"
                name="install"
              >
                <div class="collector-groups">
                  <section class="collector-group">
                    <div class="collector-group-head">
                      <span class="collector-group-index">一</span>
                      <div>
                        <b
                          >系统指标采集器
                          <em class="collector-required">*</em></b
                        >
                      </div>
                    </div>
                    <div class="collector-command-list">
                      <div class="command-row collector-command-row">
                        <div class="collector-command-content">
                          <b>安装或更新</b>
                          <div class="collector-command-line">
                            <code>{{ nodeCommand }}</code
                            ><el-button
                              class="copy-command-button"
                              size="small"
                              :class="{
                                'copy-command-button--done':
                                  copiedCommand === 'node-install',
                              }"
                              :type="
                                copiedCommand === 'node-install'
                                  ? 'success'
                                  : 'default'
                              "
                              @click="copy(nodeCommand, 'node-install')"
                              >{{
                                copiedCommand === "node-install" ? "✓" : "复制"
                              }}</el-button
                            >
                          </div>
                        </div>
                      </div>
                      <div
                        class="command-row collector-command-row verify-command-row"
                      >
                        <div class="collector-command-content">
                          <b>验证安装</b>
                          <div class="collector-command-line">
                            <code>{{ nodeCheckCommand }}</code
                            ><el-button
                              class="copy-command-button"
                              size="small"
                              plain
                              :class="{
                                'copy-command-button--done':
                                  copiedCommand === 'node-check',
                              }"
                              :type="
                                copiedCommand === 'node-check'
                                  ? 'success'
                                  : 'default'
                              "
                              @click="copy(nodeCheckCommand, 'node-check')"
                              >{{
                                copiedCommand === "node-check" ? "✓" : "复制"
                              }}</el-button
                            >
                          </div>
                          <small>成功时输出：系统指标采集器安装成功</small>
                        </div>
                      </div>
                      <div
                        class="command-row collector-command-row remove-command-row"
                      >
                        <div class="collector-command-content">
                          <b>删除采集器</b>
                          <div class="collector-command-line">
                            <code>{{ nodeRemoveCommand }}</code
                            ><el-button
                              class="copy-command-button"
                              size="small"
                              plain
                              type="danger"
                              :class="{
                                'copy-command-button--done':
                                  copiedCommand === 'node-remove',
                              }"
                              @click="copy(nodeRemoveCommand, 'node-remove')"
                              >{{
                                copiedCommand === "node-remove" ? "✓" : "复制"
                              }}</el-button
                            >
                          </div>
                          <small>执行后会删除 oncall-node-exporter 容器</small>
                        </div>
                      </div>
                    </div>
                  </section>
                  <section class="collector-group">
                    <div class="collector-group-head">
                      <span class="collector-group-index">二</span>
                      <div>
                        <b
                          >Docker 容器指标采集器
                          <em class="collector-required">*</em></b
                        >
                      </div>
                    </div>
                    <div class="collector-command-list">
                      <div class="command-row collector-command-row">
                        <div class="collector-command-content">
                          <b>安装或更新</b>
                          <div class="collector-command-line">
                            <code>{{ cadvisorCommand }}</code
                            ><el-button
                              class="copy-command-button"
                              size="small"
                              :class="{
                                'copy-command-button--done':
                                  copiedCommand === 'cadvisor-install',
                              }"
                              :type="
                                copiedCommand === 'cadvisor-install'
                                  ? 'success'
                                  : 'default'
                              "
                              @click="copy(cadvisorCommand, 'cadvisor-install')"
                              >{{
                                copiedCommand === "cadvisor-install"
                                  ? "✓"
                                  : "复制"
                              }}</el-button
                            >
                          </div>
                        </div>
                      </div>
                      <div
                        class="command-row collector-command-row verify-command-row"
                      >
                        <div class="collector-command-content">
                          <b>验证安装</b>
                          <div class="collector-command-line">
                            <code>{{ cadvisorCheckCommand }}</code
                            ><el-button
                              class="copy-command-button"
                              size="small"
                              plain
                              :class="{
                                'copy-command-button--done':
                                  copiedCommand === 'cadvisor-check',
                              }"
                              :type="
                                copiedCommand === 'cadvisor-check'
                                  ? 'success'
                                  : 'default'
                              "
                              @click="
                                copy(cadvisorCheckCommand, 'cadvisor-check')
                              "
                              >{{
                                copiedCommand === "cadvisor-check"
                                  ? "✓"
                                  : "复制"
                              }}</el-button
                            >
                          </div>
                          <small>成功时输出：cAdvisor 安装成功</small>
                        </div>
                      </div>
                      <div
                        class="command-row collector-command-row remove-command-row"
                      >
                        <div class="collector-command-content">
                          <b>删除采集器</b>
                          <div class="collector-command-line">
                            <code>{{ cadvisorRemoveCommandText }}</code
                            ><el-button
                              class="copy-command-button"
                              size="small"
                              plain
                              type="danger"
                              :class="{
                                'copy-command-button--done':
                                  copiedCommand === 'cadvisor-remove',
                              }"
                              @click="
                                copy(
                                  cadvisorRemoveCommandText,
                                  'cadvisor-remove',
                                )
                              "
                              >{{
                                copiedCommand === "cadvisor-remove"
                                  ? "✓"
                                  : "复制"
                              }}</el-button
                            >
                          </div>
                          <small>执行后会删除 oncall-cadvisor 容器</small>
                        </div>
                      </div>
                    </div>
                  </section>
                  <section class="collector-group">
                    <div class="collector-group-head">
                      <span class="collector-group-index">三</span>
                      <div>
                        <b
                          >日志与数据库采集器
                          <em class="collector-required">*</em></b
                        >
                      </div>
                    </div>
                    <div class="collector-command-list">
                      <div class="command-row collector-command-row">
                        <div class="collector-command-content">
                          <b>安装或更新</b>
                          <div class="collector-command-line">
                            <code>{{ collectorCommand }}</code
                            ><el-button
                              class="copy-command-button"
                              size="small"
                              :class="{
                                'copy-command-button--done':
                                  copiedCommand === 'collector-install',
                              }"
                              :type="
                                copiedCommand === 'collector-install'
                                  ? 'success'
                                  : 'default'
                              "
                              @click="
                                copy(collectorCommand, 'collector-install')
                              "
                              >{{
                                copiedCommand === "collector-install"
                                  ? "✓"
                                  : "复制"
                              }}</el-button
                            >
                          </div>
                        </div>
                      </div>
                      <div
                        class="command-row collector-command-row verify-command-row"
                      >
                        <div class="collector-command-content">
                          <b>验证安装</b>
                          <div class="collector-command-line">
                            <code>{{ collectorCheckCommand }}</code
                            ><el-button
                              class="copy-command-button"
                              size="small"
                              plain
                              :class="{
                                'copy-command-button--done':
                                  copiedCommand === 'collector-check',
                              }"
                              :type="
                                copiedCommand === 'collector-check'
                                  ? 'success'
                                  : 'default'
                              "
                              @click="
                                copy(collectorCheckCommand, 'collector-check')
                              "
                              >{{
                                copiedCommand === "collector-check"
                                  ? "✓"
                                  : "复制"
                              }}</el-button
                            >
                          </div>
                          <small>成功时输出：Collector 安装成功</small>
                        </div>
                      </div>
                      <div
                        class="command-row collector-command-row remove-command-row"
                      >
                        <div class="collector-command-content">
                          <b>删除采集器</b>
                          <div class="collector-command-line">
                            <code>{{ collectorRemoveCommandText }}</code
                            ><el-button
                              class="copy-command-button"
                              size="small"
                              plain
                              type="danger"
                              :class="{
                                'copy-command-button--done':
                                  copiedCommand === 'collector-remove',
                              }"
                              @click="
                                copy(
                                  collectorRemoveCommandText,
                                  'collector-remove',
                                )
                              "
                              >{{
                                copiedCommand === "collector-remove"
                                  ? "✓"
                                  : "复制"
                              }}</el-button
                            >
                          </div>
                          <small>执行后会删除 oncall-collector 容器</small>
                        </div>
                      </div>
                    </div>
                  </section>
                  <section class="collector-group collector-group-optional">
                    <div class="collector-group-head">
                      <span class="collector-group-index">四</span>
                      <div>
                        <b
                          >GPU 指标采集器
                          <span class="collector-optional">（可选）</span></b
                        >
                      </div>
                    </div>
                    <div class="collector-command-list">
                      <div class="command-row collector-command-row">
                        <div class="collector-command-content">
                          <b>安装或更新</b>
                          <div class="collector-command-line">
                            <code>{{ gpuCommand }}</code
                            ><el-button
                              class="copy-command-button"
                              size="small"
                              :class="{
                                'copy-command-button--done':
                                  copiedCommand === 'gpu-install',
                              }"
                              :type="
                                copiedCommand === 'gpu-install'
                                  ? 'success'
                                  : 'default'
                              "
                              @click="copy(gpuCommand, 'gpu-install')"
                              >{{
                                copiedCommand === "gpu-install" ? "✓" : "复制"
                              }}</el-button
                            >
                          </div>
                        </div>
                      </div>
                      <div
                        class="command-row collector-command-row verify-command-row"
                      >
                        <div class="collector-command-content">
                          <b>验证安装</b>
                          <div class="collector-command-line">
                            <code>{{ gpuCheckCommand }}</code
                            ><el-button
                              class="copy-command-button"
                              size="small"
                              plain
                              :class="{
                                'copy-command-button--done':
                                  copiedCommand === 'gpu-check',
                              }"
                              :type="
                                copiedCommand === 'gpu-check'
                                  ? 'success'
                                  : 'default'
                              "
                              @click="copy(gpuCheckCommand, 'gpu-check')"
                              >{{
                                copiedCommand === "gpu-check" ? "✓" : "复制"
                              }}</el-button
                            >
                          </div>
                          <small>成功时输出：GPU 采集器安装成功</small>
                        </div>
                      </div>
                      <div
                        class="command-row collector-command-row remove-command-row"
                      >
                        <div class="collector-command-content">
                          <b>删除采集器</b>
                          <div class="collector-command-line">
                            <code>{{ gpuRemoveCommand }}</code
                            ><el-button
                              class="copy-command-button"
                              size="small"
                              plain
                              type="danger"
                              :class="{
                                'copy-command-button--done':
                                  copiedCommand === 'gpu-remove',
                              }"
                              @click="copy(gpuRemoveCommand, 'gpu-remove')"
                              >{{
                                copiedCommand === "gpu-remove" ? "✓" : "复制"
                              }}</el-button
                            >
                          </div>
                          <small>执行后会删除 oncall-dcgm-exporter 容器</small>
                        </div>
                      </div>
                    </div>
                  </section>
                </div>
              </el-collapse-item></el-collapse
            >
            <div class="server-form-grid">
              <el-form-item label="服务器名称（必填）" required
                ><el-input
                  v-model="serverForm.name"
                  size="large"
                  placeholder="例如：服务器 B · 股票服务"
              /></el-form-item>
              <el-form-item label="系统指标地址（必填，默认 9100）" required
                ><el-input
                  v-model="serverForm.node_metrics_url"
                  size="large"
                  placeholder="http://10.0.0.22:9100/metrics"
              /></el-form-item>
              <el-form-item label="容器指标地址（必填，默认 8080）" required
                ><el-input
                  v-model="serverForm.container_metrics_url"
                  size="large"
                  placeholder="http://10.0.0.22:8080/metrics"
              /></el-form-item>
              <el-form-item label="Collector 地址（必填，默认 9910）" required
                ><el-input
                  v-model="serverForm.collector_url"
                  size="large"
                  placeholder="http://10.0.0.22:9910"
              /></el-form-item>
              <el-form-item label="Collector Token（系统自动生成）" required
                ><el-input
                  v-model="serverForm.collector_token"
                  type="password"
                  show-password
                  readonly
                  size="large"
                  placeholder="点击添加新服务器后自动生成"
              /></el-form-item>
              <el-form-item label="GPU 指标地址（可选，默认 9400）"
                ><el-input
                  v-model="serverForm.gpu_metrics_url"
                  size="large"
                  placeholder="无 NVIDIA GPU 留空，例如 http://10.0.0.22:9400/metrics"
              /></el-form-item>
            </div>
            <div
              v-if="serverTest && testedServerSignature === serverSignature"
              class="server-test-result"
              :class="serverTest.ok ? 'passed' : 'failed'"
            >
              <div class="server-test-summary">
                <span>{{ serverTest.ok ? "✓" : "!" }}</span>
                <div>
                  <b>{{
                    serverTest.ok
                      ? "服务器采集器全部通过"
                      : "服务器采集器测试未通过"
                  }}</b
                  ><small>{{
                    serverTest.ok
                      ? "Node Exporter、cAdvisor 和 Collector 均可访问"
                      : serverTest.error || "请检查地址、端口和采集器日志"
                  }}</small>
                </div>
              </div>
              <div class="server-test-checks">
                <div
                  v-for="item in serverTestItems"
                  :key="item.key"
                  class="server-test-check"
                  :class="
                    item.ok
                      ? 'passed'
                      : item.optional && !item.configured
                        ? 'optional'
                        : 'failed'
                  "
                >
                  <span>{{
                    item.ok
                      ? "✓"
                      : item.optional && !item.configured
                        ? "–"
                        : "×"
                  }}</span
                  ><b>{{ item.label }}</b
                  ><small>{{
                    item.optional && !item.configured
                      ? "未配置"
                      : item.ok
                        ? "测试通过"
                        : item.error || "测试失败"
                  }}</small>
                </div>
              </div>
            </div>
            <div class="inline-actions">
              <el-button
                size="large"
                :loading="testingServer"
                @click="testNewServer"
                >⚡ 测试服务器</el-button
              ><el-button
                type="primary"
                size="large"
                :loading="savingServer"
                :disabled="!canSaveServer"
                @click="saveNewServer"
                >保存并继续</el-button
              >
            </div>
          </div>
        </div>

        <div class="field-block" :class="{ locked: !serverId }">
          <div class="field-title">
            <span class="field-number">2</span>
            <div>
              <b>填写 Python 项目信息</b
              ><small>{{
                serverId
                  ? "日志自动识别；数据库只需一个只读连接串"
                  : "连接服务器后即可填写"
              }}</small>
            </div>
          </div>
          <template v-if="serverId">
            <div class="two-fields">
              <el-form-item label="项目名称" required
                ><el-input
                  v-model="name"
                  size="large"
                  placeholder="例如：股票行情 API" /></el-form-item
              ><el-form-item label="用途说明（可选）"
                ><el-input
                  v-model="description"
                  size="large"
                  placeholder="例如：生产环境行情服务"
              /></el-form-item>
            </div>
            <div class="endpoint-list">
              <div class="endpoint-row compose-project-row">
                <span class="endpoint-icon docker">DO</span>
                <div class="endpoint-label">
                  <b>Docker Compose 项目名 <em class="required-mark">*</em></b
                  ><small>可用 docker compose ls 查看</small>
                </div>
                <el-input
                  v-model="composeProject"
                  size="large"
                  placeholder="例如：tradingagents"
                />
              </div>
              <div class="endpoint-row">
                <span class="endpoint-icon metrics">⌁</span>
                <div class="endpoint-label">
                  <b>Prometheus 地址</b
                  ><small>获取请求、错误、延迟和进程指标</small>
                </div>
                <el-input
                  v-model="metricsUrl"
                  size="large"
                  placeholder="https://stock.example.com/metrics"
                />
              </div>
              <div class="endpoint-row">
                <span class="endpoint-icon database">DB</span>
                <div class="endpoint-label">
                  <b>PostgreSQL 只读连接串</b
                  ><small>连接率、事务、锁等数字指标进入 Prometheus</small>
                </div>
                <el-input
                  v-model="databaseUrl"
                  type="password"
                  show-password
                  size="large"
                  placeholder="postgresql://oncall_monitor:密码@postgres:5432/app"
                />
              </div>
              <div class="auto-log-note">
                <span>✓</span>
                <div>
                  <b>Docker stdout 日志手动匹配</b
                  ><small
                    >Collector 根据 Compose 项目名读取日志，不依赖 Nginx 或
                    HTTPS 端口自动推断。</small
                  >
                </div>
              </div>
            </div>
          </template>
          <div v-else class="locked-note">
            <span>→</span>请先在上方选择已有服务器，或连接一台新服务器。
          </div>
        </div>

        <div class="field-block final-block" :class="{ locked: !serverId }">
          <div class="field-title">
            <span class="field-number">3</span>
            <div>
              <b>一次验证，完成创建</b
              ><small>同时检查服务器、应用、Docker 日志和 PostgreSQL</small>
            </div>
          </div>
          <div
            v-if="testResult && testedSignature === signature"
            class="test-result"
            :class="testResult.ok ? 'passed' : 'failed'"
          >
            <div
              class="test-result-summary"
              :class="testResult.ok ? 'passed' : 'failed'"
            >
              <span>{{ testResult.ok ? "✓" : "!" }}</span>
              <div>
                <b>{{ testResultSummary }}</b
                ><small>{{ testResultHint }}</small>
              </div>
            </div>
            <div
              v-for="check in testResult.checks"
              :key="check.key"
              class="check-item"
              :class="check.ok ? 'passed' : 'failed'"
            >
              <span>{{ check.ok ? "✓" : "×" }}</span>
              <div>
                <b>{{ checkLabels[check.key] }}</b
                ><small class="check-status">{{
                  check.ok ? "测试通过 · 连接正常" : "测试失败"
                }}</small>
                <p
                  v-if="!check.ok && check.error"
                  class="check-error"
                  :title="check.error"
                >
                  {{ check.error }}
                </p>
              </div>
            </div>
            <p
              v-for="warning in testResult.warnings"
              :key="warning"
              class="test-warning"
            >
              提示：{{ warning }}
            </p>
          </div>
          <div class="wizard-actions">
            <div>
              <el-button
                size="large"
                :loading="testing"
                :disabled="!canTest"
                @click="testDraft"
                >⚡ 测试全部连接</el-button
              ><el-button
                type="primary"
                size="large"
                :loading="creating"
                :disabled="!canCreate"
                @click="add"
                >创建监控项目</el-button
              >
            </div>
          </div>
        </div>
      </div>

      <aside class="card coverage-card">
        <span class="step-tag">创建后自动获得</span>
        <h3>指标、日志、数据库联合监控</h3>
        <p>一次验证所有必备数据入口。</p>
        <div class="source-map">
          <div>
            <span class="coverage-icon mint">主机</span>
            <p><b>服务器采集器</b><small>CPU、内存、磁盘、网络、负载</small></p>
          </div>
          <div>
            <span class="coverage-icon coral">容器</span>
            <p><b>cAdvisor</b><small>项目容器 CPU、内存和状态</small></p>
          </div>
          <div>
            <span class="coverage-icon blue">应用</span>
            <p>
              <b>Prometheus</b><small>流量、错误率、P95/P99、进程资源</small>
            </p>
          </div>
          <div>
            <span class="coverage-icon violet">日志</span>
            <p>
              <b>Docker stdout</b><small>LLM 按告警查询错误与异常堆栈</small>
            </p>
          </div>
          <div>
            <span class="coverage-icon amber">DB</span>
            <p>
              <b>PostgreSQL</b><small>统计指标供 Prometheus；明细供 LLM</small>
            </p>
          </div>
        </div>
        <div class="arrow-down">↓</div>
        <div class="rule-note">
          <i></i
          ><span
            ><b>Prometheus + Alertmanager 自动处理</b
            ><small
              >Prometheus 判断数字指标，Alertmanager 合并去重后进入飞书告警和 AI
              分析链路。</small
            ></span
          >
        </div>
      </aside>
    </section>

    <section class="project-section">
      <div class="list-heading">
        <div>
          <span class="eyebrow plain">MONITORED SERVICES</span>
          <h2>已接入项目</h2>
          <p>{{ rows.length }} 个项目，{{ enabledCount }} 个正在监控</p>
        </div>
        <el-input
          v-model="search"
          clearable
          placeholder="搜索项目或服务器"
          class="project-search"
        />
      </div>
      <div class="project-grid">
        <article
          v-for="item in filteredRows"
          :key="item.id"
          class="project-tile"
          @click="router.push('/projects/' + item.id)"
        >
          <div class="tile-top">
            <span class="project-glyph"
              ><img src="/pulseops-icon.png" alt="巡脉图标"
            /></span>
            <div class="tile-actions">
              <span
                class="status-dot"
                :class="item.enabled ? 'online' : ''"
              ></span
              ><small>{{ item.enabled ? "监控中" : "未启用" }}</small
              ><button
                type="button"
                class="project-delete"
                :disabled="Boolean(deletingProjectId)"
                title="删除项目"
                aria-label="删除项目"
                @click.stop="removeProject(item)"
              >
                删除
              </button>
            </div>
          </div>
          <h3>{{ item.name }}</h3>
          <p>{{ item.description || "远程 Python 服务" }}</p>
          <div class="tile-bottom">
            <span>▣ {{ item.server_name || "未绑定服务器" }}</span
            ><span>{{ item.poll_interval }}s ↗</span>
          </div>
        </article>
        <div v-if="loading" class="project-empty">正在加载项目…</div>
        <div
          v-else-if="rows.length && !filteredRows.length"
          class="project-empty"
        >
          没有匹配的项目
        </div>
        <div v-else-if="!rows.length" class="project-empty">
          <span>＋</span><b>还没有项目</b>
          <p>完成上面的三步即可创建第一个监控项目。</p>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.projects-page {
  max-width: 1280px;
  padding-top: 26px;
}
.fresh-hero {
  position: relative;
  overflow: hidden;
  display: grid;
  grid-template-columns: 1.1fr 0.9fr;
  gap: 34px;
  align-items: center;
  min-height: 184px;
  margin-bottom: 20px;
  padding: 30px 34px;
  border: 1px solid #d7eee7;
  border-radius: 24px;
  background: linear-gradient(120deg, #f0fcf7 0%, #f7fbff 56%, #fff 100%);
  box-shadow: 0 18px 50px -34px rgba(21, 112, 85, 0.45);
}
.fresh-hero:after {
  content: "";
  position: absolute;
  width: 260px;
  height: 260px;
  right: -90px;
  top: -150px;
  border-radius: 50%;
  background: radial-gradient(
    circle,
    rgba(72, 207, 161, 0.18),
    transparent 68%
  );
}
.eyebrow {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: #27806a;
  font-size: 11px;
  font-weight: 750;
  letter-spacing: 0.13em;
}
.eyebrow i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #35b98f;
  box-shadow: 0 0 0 5px rgba(53, 185, 143, 0.12);
}
.eyebrow.plain {
  color: #6c847c;
}
.hero-copy h1 {
  margin: 10px 0 9px;
  font-size: 30px;
  line-height: 1.2;
  letter-spacing: -0.045em;
  color: #173b32;
}
.hero-copy h1 em {
  color: #1b9c77;
  font-style: normal;
}
.hero-copy p {
  max-width: 590px;
  margin: 0;
  color: #668078;
}
.page-message {
  margin: 0 0 16px;
  padding: 10px 14px;
  border-radius: 11px;
  font-size: 13px;
}
.page-message.ok {
  color: #17694f;
  background: #edf9f4;
  border: 1px solid #ccecdf;
}
.page-message.err {
  color: #ae3f46;
  background: #fff4f4;
  border: 1px solid #f4d6d8;
}
.onboarding-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 310px;
  gap: 16px;
  align-items: start;
}
.wizard-card {
  padding: 26px 28px;
  border-color: #dfeae6;
  box-shadow: 0 14px 42px -34px rgba(20, 67, 54, 0.5);
}
.card-heading {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding-bottom: 20px;
  border-bottom: 1px solid #edf2f0;
}
.card-heading h2 {
  margin: 5px 0 3px;
  font-size: 21px;
  color: #223b35;
}
.card-heading p,
.coverage-card > p {
  margin: 0;
  color: #7a8d87;
  font-size: 13px;
}
.step-tag {
  color: #2c9275;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.13em;
}
.remote-chip {
  padding: 5px 10px;
  border: 1px solid #cbeade;
  border-radius: 999px;
  color: #277b65;
  background: #f0fbf7;
  font-size: 11px;
}
.field-block {
  padding: 22px 0;
  border-bottom: 1px solid #edf2f0;
}
.field-block.locked {
  opacity: 0.63;
}
.final-block {
  border-bottom: 0;
  padding-bottom: 0;
}
.field-title {
  display: flex;
  align-items: center;
  gap: 11px;
  margin-bottom: 13px;
}
.field-title b,
.field-title small {
  display: block;
}
.field-title b {
  color: #2d433d;
  font-size: 14px;
}
.field-title small {
  color: #95a39f;
  font-size: 11px;
}
.field-number {
  width: 27px;
  height: 27px;
  display: grid;
  place-items: center;
  border-radius: 9px;
  color: #278067;
  background: #eaf8f3;
  font-size: 12px;
  font-weight: 750;
}
.mode-tabs {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-bottom: 14px;
}
.mode-tabs button {
  padding: 13px 15px;
  text-align: left;
  border: 1px solid #e0ebe7;
  border-radius: 13px;
  color: #667c75;
  background: #fbfdfc;
  cursor: pointer;
}
.mode-tabs button.active {
  color: #21735d;
  border-color: #77c9ae;
  background: #f0fbf7;
  box-shadow: 0 0 0 3px rgba(53, 185, 143, 0.07);
}
.mode-tabs b,
.mode-tabs small {
  display: block;
}
.mode-tabs b {
  font-size: 12px;
}
.mode-tabs small {
  margin-top: 2px;
  color: #95a49f;
  font-size: 10px;
}
.existing-server .el-select {
  width: 100%;
}
.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #b7c2be;
}
.status-dot.online {
  background: #34b98c;
  box-shadow: 0 0 0 4px rgba(52, 185, 140, 0.1);
}
.new-server-box {
  padding: 16px;
  border: 1px solid #dcece6;
  border-radius: 15px;
  background: #fbfefc;
}
.install-guide {
  margin: 4px 0 15px;
}
.command-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 0;
}
.command-row > div {
  min-width: 0;
  flex: 1;
}
.command-row b {
  display: block;
  margin-bottom: 4px;
  font-size: 11px;
}
.command-row code {
  display: block;
  overflow: auto;
  white-space: nowrap;
  padding: 8px;
  font-size: 10px;
}
.server-form-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
}
.server-form-grid :deep(.el-form-item) {
  margin-bottom: 4px;
}
.inline-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
}
.server-test-result {
  display: flex;
  align-items: center;
  gap: 9px;
  margin-top: 9px;
  padding: 9px 11px;
  border-radius: 10px;
}
.server-test-result.passed {
  color: #17694f;
  background: #edf9f4;
}
.server-test-result.failed {
  color: #ae3f46;
  background: #fff1f2;
}
.server-test-result > span {
  width: 23px;
  height: 23px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  color: #fff;
  background: #31ae84;
}
.server-test-result.failed > span {
  background: #df6570;
}
.server-test-result b,
.server-test-result small {
  display: block;
  font-size: 10px;
}
.two-fields {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}
.two-fields :deep(.el-form-item) {
  margin-bottom: 12px;
}
.endpoint-list {
  display: grid;
  gap: 10px;
}
.endpoint-row {
  display: grid;
  grid-template-columns: 38px 155px minmax(0, 1fr);
  align-items: center;
  gap: 11px;
  padding: 10px 12px;
  border: 1px solid #e4ece9;
  border-radius: 13px;
  background: #fbfdfc;
}
.endpoint-icon {
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  border-radius: 10px;
  font-weight: 750;
}
.endpoint-icon.health {
  color: #d65b65;
  background: #fff0f1;
}
.endpoint-icon.metrics {
  color: #397bb7;
  background: #edf6ff;
  font-size: 18px;
}
.endpoint-label b,
.endpoint-label small {
  display: block;
}
.endpoint-label b {
  color: #354b45;
  font-size: 12px;
}
.endpoint-label small {
  color: #9aa8a4;
  font-size: 10px;
}
.locked-note {
  padding: 18px;
  border: 1px dashed #ccdcd7;
  border-radius: 13px;
  color: #83958f;
  background: #fafcfb;
  font-size: 12px;
}
.locked-note span {
  margin-right: 8px;
  color: #37a382;
}
.test-result {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 15px;
  padding: 12px;
  border-radius: 13px;
  border: 1px solid #dfeae6;
  background: #f8fbfa;
}
.test-result.passed {
  border-color: #c5e9da;
  background: #f2fbf7;
}
.test-result.failed {
  border-color: #f0d8da;
  background: #fffafa;
}
.check-item {
  display: flex;
  align-items: center;
  gap: 8px;
}
.check-item > span {
  width: 25px;
  height: 25px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  color: #fff;
  background: #31ae84;
  font-weight: 800;
}
.failed .check-item > span {
  background: #df6570;
}
.check-item b,
.check-item small {
  display: block;
}
.check-item b {
  color: #355048;
  font-size: 11px;
}
.check-item small {
  max-width: 180px;
  overflow: hidden;
  color: #7d928b;
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.test-warning {
  grid-column: 1/-1;
  margin: 5px 2px 0;
  color: #9a690d;
  font-size: 11px;
}
.wizard-actions {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 16px;
}
.wizard-actions > span {
  color: #93a09c;
  font-size: 11px;
}
.wizard-actions > div {
  display: flex;
  gap: 8px;
}
.coverage-card {
  padding: 25px;
  border-color: #dce9e5;
  background: linear-gradient(160deg, #fff 0%, #f7fcfa 100%);
}
.coverage-card h3 {
  margin: 7px 0 5px;
  color: #2b443d;
  font-size: 17px;
}
.source-map {
  display: grid;
  gap: 5px;
  margin: 18px 0;
}
.source-map > div {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 10px 5px;
  border-bottom: 1px solid #e9f0ee;
}
.source-map > div:last-child {
  border: 0;
}
.coverage-icon {
  width: 38px;
  height: 38px;
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  border-radius: 11px;
  font-size: 10px;
  font-weight: 800;
}
.coverage-icon.mint {
  color: #20836a;
  background: #e4f7f0;
}
.coverage-icon.blue {
  color: #397bb7;
  background: #eaf4fd;
}
.coverage-icon.coral {
  color: #c95c62;
  background: #fff0f0;
}
.source-map p {
  margin: 0;
}
.source-map b,
.source-map small {
  display: block;
}
.source-map b {
  color: #3a514a;
  font-size: 12px;
}
.source-map small {
  color: #93a29d;
  font-size: 10px;
}
.arrow-down {
  text-align: center;
  color: #6cb89f;
}
.rule-note {
  display: flex;
  gap: 10px;
  padding: 13px;
  border-radius: 12px;
  background: #ecf8f4;
}
.rule-note i {
  width: 8px;
  height: 8px;
  margin-top: 5px;
  border-radius: 50%;
  background: #38b58d;
}
.rule-note span {
  flex: 1;
}
.rule-note b,
.rule-note small {
  display: block;
}
.rule-note b {
  color: #376257;
  font-size: 11px;
}
.rule-note small {
  color: #799087;
  font-size: 10px;
  line-height: 1.5;
}
.project-section {
  margin-top: 34px;
}
.list-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  margin-bottom: 14px;
}
.list-heading h2 {
  margin: 3px 0 0;
  color: #2c413b;
  font-size: 20px;
}
.list-heading p {
  margin: 0;
  color: #91a09b;
  font-size: 11px;
}
.project-search {
  max-width: 265px;
}
.project-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(245px, 1fr));
  gap: 13px;
}
.project-tile {
  min-height: 168px;
  padding: 18px;
  border: 1px solid #e1ebe7;
  border-radius: 17px;
  background: rgba(255, 255, 255, 0.9);
  cursor: pointer;
  transition: 0.2s ease;
}
.project-tile:hover {
  transform: translateY(-3px);
  border-color: #b8ded2;
  box-shadow: 0 16px 34px -26px rgba(23, 98, 76, 0.55);
}
.tile-top {
  display: flex;
  align-items: center;
  gap: 7px;
  color: #8b9b96;
  font-size: 11px;
}
.project-glyph {
  position: relative;
  width: 36px;
  height: 36px;
  display: grid;
  place-items: center;
  margin-right: 0;
  overflow: hidden;
  border: 1px solid #edf1f4;
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 7px 15px -11px rgba(15, 46, 68, 0.45);
}
.project-tile h3 {
  margin: 16px 0 2px;
  color: #2f463f;
  font-size: 15px;
}
.project-tile > p {
  min-height: 36px;
  margin: 0;
  color: #859690;
  font-size: 11px;
}
.tile-bottom {
  display: flex;
  justify-content: space-between;
  margin-top: 13px;
  padding-top: 11px;
  border-top: 1px solid #edf2f0;
  color: #80928c;
  font-size: 10px;
}
.project-empty {
  grid-column: 1/-1;
  display: grid;
  place-items: center;
  min-height: 150px;
  padding: 26px;
  border: 1px dashed #cfe0db;
  border-radius: 17px;
  color: #8fa09b;
  background: rgba(255, 255, 255, 0.62);
  text-align: center;
}
.project-empty > span {
  font-size: 27px;
  color: #59b89b;
}
.project-empty > b {
  color: #587068;
}
.project-empty p {
  margin: 0;
  font-size: 11px;
}
.compact-hero {
  grid-template-columns: 1fr;
  min-height: 132px;
  padding-top: 25px;
  padding-bottom: 25px;
}
.endpoint-icon.database {
  color: #7c57aa;
  background: #f3edfb;
  font-size: 11px;
}
.auto-log-note {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 11px 13px;
  border: 1px solid #cfeade;
  border-radius: 12px;
  background: #f1faf6;
}
.auto-log-note > span {
  width: 25px;
  height: 25px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  color: #fff;
  background: #31ae84;
}
.auto-log-note b,
.auto-log-note small {
  display: block;
}
.auto-log-note b {
  color: #315149;
  font-size: 11px;
}
.auto-log-note small {
  color: #7d928b;
  font-size: 10px;
}
.coverage-icon.violet {
  color: #7653a7;
  background: #f0eafa;
}
.coverage-icon.amber {
  color: #976810;
  background: #fff4d9;
}
@media (max-width: 1020px) {
  .fresh-hero {
    grid-template-columns: 1fr;
  }
  .onboarding-layout {
    grid-template-columns: 1fr;
  }
  .coverage-card {
    display: none;
  }
}
@media (max-width: 700px) {
  .projects-page {
    padding-top: 14px;
  }
  .fresh-hero {
    padding: 23px 20px;
    border-radius: 18px;
  }
  .hero-copy h1 {
    font-size: 25px;
  }
  .wizard-card {
    padding: 20px 17px;
  }
  .mode-tabs,
  .two-fields,
  .server-form-grid {
    grid-template-columns: 1fr;
  }
  .endpoint-row {
    grid-template-columns: 36px 1fr;
  }
  .endpoint-row .el-input {
    grid-column: 1/-1;
  }
  .test-result {
    grid-template-columns: 1fr;
  }
  .wizard-actions,
  .list-heading {
    align-items: stretch;
    flex-direction: column;
  }
  .wizard-actions > div {
    display: grid;
    grid-template-columns: 1fr 1fr;
  }
  .project-search {
    max-width: none;
  }
}
.server-form-grid :deep(.el-form-item) {
  display: flex;
  align-items: center;
  min-width: 0;
}
.server-form-grid :deep(.el-form-item__label) {
  width: 250px;
  flex: 0 0 250px;
  padding-right: 14px;
  text-align: left;
  white-space: nowrap;
}
.server-form-grid :deep(.el-form-item__content) {
  min-width: 0;
  flex: 1;
}
@media (max-width: 700px) {
  .server-form-grid :deep(.el-form-item) {
    display: block;
  }
  .server-form-grid :deep(.el-form-item__label) {
    width: auto;
    padding-right: 0;
    text-align: left;
    white-space: normal;
  }
  .server-form-grid :deep(.el-form-item__content) {
    width: 100%;
  }
}
.verify-command-row {
  margin: 0 0 5px;
  padding: 9px 10px;
  border: 1px solid #e5e9e7;
  border-radius: 10px;
  background: #f5f7f6;
  color: #74817d;
}
.verify-command-row b {
  color: #65726e;
}
.verify-command-row code {
  color: #697570;
  background: #ecefed;
}
.verify-command-row small {
  display: block;
  margin-top: 5px;
  color: #929c98;
  font-size: 9px;
}
.remove-command-row {
  margin: 0 0 5px;
  padding: 9px 10px;
  border: 1px solid #f0dfe0;
  border-radius: 10px;
  background: #fff8f8;
  color: #8b6f72;
}
.remove-command-row b {
  color: #a05258;
}
.remove-command-row code {
  color: #8b696d;
  background: #fff0f1;
}
.remove-command-row small {
  display: block;
  margin-top: 5px;
  color: #b18e91;
  font-size: 9px;
}
.collector-groups {
  display: grid;
  gap: 12px;
  margin: 10px 0 16px;
}
.collector-group {
  padding: 12px;
  border: 1px solid #dfe9e5;
  border-radius: 13px;
  background: #fff;
}
.collector-group-optional {
  border-color: #dfe5f0;
  background: #fbfcff;
}
.collector-group-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 9px;
}
.collector-group-index {
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  border-radius: 9px;
  color: #287b64;
  background: #e8f7f1;
  font-size: 12px;
  font-weight: 800;
}
.collector-group-optional .collector-group-index {
  color: #5473a0;
  background: #edf4ff;
}
.collector-group-head b,
.collector-group-head small {
  display: block;
}
.collector-group-head b {
  color: #345049;
  font-size: 12px;
}
.collector-group-head small {
  margin-top: 2px;
  color: #91a09b;
  font-size: 10px;
}
.collector-command-list {
  display: grid;
  gap: 6px;
}
.collector-command-row {
  margin: 0;
  padding: 9px 10px;
  border: 1px solid #e5ebe8;
  border-radius: 10px;
  background: #fbfdfc;
}
.collector-command-row > div {
  min-width: 0;
}
.collector-required {
  color: #d85d67;
  font-style: normal;
}
.collector-optional {
  color: #82928d;
  font-size: 11px;
  font-weight: 500;
}
.collector-command-row {
  display: block;
}
.collector-command-content {
  min-width: 0;
}
.collector-command-line {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.collector-command-line code {
  min-width: 0;
  padding-right: 8px;
}
.collector-command-line .copy-command-button {
  position: static;
  z-index: auto;
  display: inline-flex !important;
  align-items: center;
  justify-content: center;
  visibility: visible !important;
  opacity: 1 !important;
  min-width: 56px;
  height: 28px;
  margin: 0;
  white-space: nowrap;
  border: 1px solid #c9ded7;
  color: #347966;
  background: #fff;
}
.collector-command-line .copy-command-button:hover {
  border-color: #5ab99a;
  color: #20795f;
  background: #f0fbf7;
}
.collector-command-line .copy-command-button--done {
  border-color: #91d8bd !important;
  color: #218a69 !important;
  background: #edf9f4 !important;
  font-weight: 800;
}
.test-result-summary {
  grid-column: 1/-1;
  display: flex;
  align-items: center;
  gap: 9px;
  margin: -2px 0 2px;
  padding: 3px 2px 8px;
  border-bottom: 1px solid #e5efeb;
}
.test-result-summary > span {
  width: 25px;
  height: 25px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  color: #fff;
  background: #31ae84;
  font-weight: 800;
}
.test-result-summary.failed > span {
  background: #df6570;
}
.test-result-summary b,
.test-result-summary small {
  display: block;
}
.test-result-summary b {
  color: #2f5145;
  font-size: 11px;
}
.test-result-summary.failed b {
  color: #a33f48;
}
.test-result-summary small {
  margin-top: 2px;
  color: #789087;
  font-size: 10px;
}
.check-item {
  align-items: flex-start;
  min-width: 0;
  padding: 9px 8px;
  border: 1px solid #dfeae6;
  border-radius: 10px;
  background: #fff;
}
.check-item.passed {
  border-color: #bfe7d6;
  background: #f2fbf6;
}
.check-item.failed {
  border-color: #efc3c8;
  background: #fff1f2;
}
.check-item > span {
  flex: 0 0 auto;
}
.test-result .check-item.passed > span {
  background: #31ae84;
}
.test-result .check-item.failed > span {
  background: #df6570;
}
.check-item > div {
  min-width: 0;
}
.check-item.failed b {
  color: #9d4149;
}
.check-status {
  margin-top: 3px;
  color: #208363 !important;
  font-size: 10px !important;
}
.check-item.failed .check-status {
  color: #c44854 !important;
}
.check-error {
  margin: 4px 0 0;
  overflow-wrap: anywhere;
  color: #a55a61;
  font-size: 9px;
  line-height: 1.35;
}
@media (max-width: 700px) {
  .test-result {
    grid-template-columns: 1fr;
  }
}
.project-glyph img {
  display: block;
  width: 30px;
  height: 30px;
  object-fit: contain;
}
.server-option-row {
  display: flex;
  align-items: center;
  width: 100%;
  min-width: 0;
}
.server-option-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.project-delete {
  border: 0;
  padding: 3px 0 3px 5px;
  color: #c6535b;
  background: transparent;
  cursor: pointer;
  font: inherit;
  font-size: 11px;
}
.project-delete:hover {
  color: #a93b44;
  text-decoration: underline;
}
.project-delete:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
.tile-actions {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-left: auto;
}
.project-delete {
  padding-left: 8px;
}
.server-select-shell {
  position: relative;
}
.server-select-shell > .el-select {
  width: 100%;
}
.server-select-actions {
  position: absolute;
  top: 50%;
  right: 32px;
  z-index: 3;
  display: flex;
  align-items: center;
  gap: 10px;
  transform: translateY(-50%);
}
.server-select-actions button {
  border: 0;
  padding: 3px 0;
  background: transparent;
  cursor: pointer;
  font: inherit;
  font-size: 11px;
}
.server-select-actions button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
.server-select-test {
  color: #278269;
}
.server-select-test:hover {
  color: #1c6d56;
  text-decoration: underline;
}
.server-select-delete {
  color: #c6535b;
}
.server-select-delete:hover {
  color: #a93b44;
  text-decoration: underline;
}
.server-select-shell :deep(.el-input__wrapper) {
  padding-right: 122px;
}
.endpoint-icon.docker {
  color: #278269;
  background: #e8f8f2;
  font-size: 11px;
}
.required-mark {
  color: #e35d66;
  font-style: normal;
}
.saved-server-test-result {
  padding: 9px 10px;
  border-radius: 10px;
  border: 1px solid #dfeae6;
  background: #f8fbfa;
}
.saved-server-test-result.passed {
  border-color: #c5e9da;
  background: #f2fbf7;
}
.saved-server-test-result.failed {
  border-color: #f0d8da;
  background: #fffafa;
}
.server-test-result {
  display: block;
}
.server-test-summary {
  display: flex;
  align-items: center;
  gap: 9px;
}
.server-test-summary > span {
  width: 23px;
  height: 23px;
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  border-radius: 50%;
  color: #fff;
  background: #31ae84;
}
.server-test-result.failed .server-test-summary > span {
  background: #df6570;
}
.server-test-summary b,
.server-test-summary small {
  display: block;
}
.server-test-summary b {
  font-size: 10px;
}
.server-test-summary small {
  margin-top: 2px;
  font-size: 9px;
}
.server-test-checks {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 6px;
  margin-top: 9px;
}
.server-test-check {
  display: grid;
  grid-template-columns: auto 1fr;
  align-items: center;
  column-gap: 6px;
  padding: 6px 7px;
  border: 1px solid #dfeae6;
  border-radius: 8px;
  background: #fff;
}
.server-test-check > span {
  width: 17px;
  height: 17px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  color: #fff;
  background: #31ae84;
  font-size: 10px;
  font-weight: 800;
}
.server-test-check b,
.server-test-check small {
  grid-column: 2;
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.server-test-check b {
  color: #355048;
  font-size: 9px;
}
.server-test-check small {
  color: #208363;
  font-size: 8px;
}
.server-test-check.failed {
  border-color: #efc3c8;
  background: #fff8f8;
}
.server-test-check.failed > span {
  background: #df6570;
}
.server-test-check.failed b,
.server-test-check.failed small {
  color: #a33f48;
}
.server-test-check.optional {
  border-color: #e3e9ee;
  background: #fbfcfd;
}
.server-test-check.optional > span {
  color: #82919a;
  background: #e9eef1;
}
.server-test-check.optional small {
  color: #82919a;
}
@media (max-width: 700px) {
  .server-test-checks {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
