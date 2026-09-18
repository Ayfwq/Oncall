export const COLLECTOR_IMAGE = 'ghcr.io/ayfwq/oncall-collector:latest'
export const NODE_EXPORTER_IMAGE = 'quay.io/prometheus/node-exporter:v1.12.1'
export const DCGM_EXPORTER_IMAGE = 'nvcr.io/nvidia/k8s/dcgm-exporter:4.6.0-4.8.3-distroless'

export function shellQuote(value: string): string {
  return `'${value.replace(/'/g, `'"'"'`)}'`
}

export function nodeExporterInstallCommand(): string {
  return [
    `docker pull ${NODE_EXPORTER_IMAGE}`,
    `(docker rm -f oncall-node-exporter >/dev/null 2>&1 || true)`,
    `docker run -d --name oncall-node-exporter --restart unless-stopped --network host --pid host -v "/:/host:ro,rslave" ${NODE_EXPORTER_IMAGE} --path.rootfs=/host`,
    `echo '系统指标采集器安装命令执行成功，请继续运行验证命令'`,
  ].join(' && ')
}

export function nodeExporterVerifyCommand(): string {
  return `curl --fail --silent --show-error http://127.0.0.1:9100/metrics | grep -q '^node_exporter_build_info' && echo '系统指标采集器安装成功' || { echo '系统指标采集器安装失败'; exit 1; }`
}

export function gpuExporterInstallCommand(): string {
  return [
    `docker pull ${DCGM_EXPORTER_IMAGE}`,
    `(docker rm -f oncall-dcgm-exporter >/dev/null 2>&1 || true)`,
    `docker run -d --name oncall-dcgm-exporter --restart unless-stopped --gpus all --cap-add SYS_ADMIN -p 9400:9400 ${DCGM_EXPORTER_IMAGE}`,
    `echo 'GPU 采集器安装命令执行成功，请继续运行验证命令'`,
  ].join(' && ')
}

export function gpuExporterVerifyCommand(): string {
  return `curl --fail --silent --show-error http://127.0.0.1:9400/metrics | grep -q '^DCGM_FI_DEV_GPU_UTIL' && echo 'GPU 采集器安装成功' || { echo 'GPU 采集器安装失败，请检查 NVIDIA 驱动和容器日志'; exit 1; }`
}

export function collectorInstallCommand(token: string): string {
  const safeToken = shellQuote(token || '<页面自动生成的Token>')
  return [
    `docker pull ${COLLECTOR_IMAGE}`,
    `(docker rm -f oncall-collector >/dev/null 2>&1 || true)`,
    `docker run -d --name oncall-collector --restart unless-stopped -p 9910:9910 -e ONCALL_COLLECTOR_TOKEN=${safeToken} -v /var/run/docker.sock:/var/run/docker.sock:ro ${COLLECTOR_IMAGE}`,
    `echo 'Collector 安装命令执行成功，请继续运行验证命令'`,
  ].join(' && ')
}

export function collectorVerifyCommand(token: string): string {
  return `curl --fail --silent --show-error --header ${shellQuote(`X-Oncall-Token: ${token || '<页面自动生成的Token>'}`)} http://127.0.0.1:9910/health | grep -Eq '"ok"[[:space:]]*:[[:space:]]*true' && echo 'Collector 安装成功' || { echo 'Collector 安装失败'; exit 1; }`
}
