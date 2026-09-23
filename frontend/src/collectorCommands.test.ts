import { describe, expect, it } from 'vitest'

import {
  CADVISOR_IMAGE,
  COLLECTOR_IMAGE,
  DCGM_EXPORTER_IMAGE,
  cadvisorInstallCommand,
  cadvisorVerifyCommand,
  NODE_EXPORTER_IMAGE,
  collectorInstallCommand,
  collectorRemoveCommand,
  collectorVerifyCommand,
  gpuExporterInstallCommand,
  gpuExporterRemoveCommand,
  gpuExporterVerifyCommand,
  nodeExporterInstallCommand,
  nodeExporterRemoveCommand,
  nodeExporterVerifyCommand,
  shellQuote,
} from './collectorCommands'

describe('collector commands', () => {
  it('installs the published image without project source files', () => {
    const command = collectorInstallCommand('oncall-test-token')

    expect(command).toContain(`docker pull ${COLLECTOR_IMAGE}`)
    expect(command).toContain('docker rm -f oncall-collector')
    expect(command).toContain('-e ONCALL_COLLECTOR_TOKEN=\'oncall-test-token\'')
    expect(command).toContain('/var/run/docker.sock:/var/run/docker.sock:ro')
    expect(command).not.toContain('docker compose')
    expect(command).not.toContain('deploy/collector.compose.yaml')
    expect(command).toContain("echo 'Collector 安装命令执行成功，请继续运行验证命令'")
  })

  it('verifies the authenticated health endpoint', () => {
    const command = collectorVerifyCommand('oncall-test-token')

    expect(command).toContain("--header 'X-Oncall-Token: oncall-test-token'")
    expect(command).toContain('--connect-timeout 5 --max-time 10')
    expect(command).toContain("echo 'Collector 安装成功'")
    expect(command).toContain("echo 'Collector 安装失败'; exit 1")
  })

  it('removes the Collector container and reports the result', () => {
    const command = collectorRemoveCommand()

    expect(command).toContain('docker rm -f oncall-collector')
    expect(command).toContain("echo 'Collector 删除成功'")
    expect(command).toContain("echo 'Collector 删除失败'")
  })

  it('installs and verifies Node Exporter with human-readable output', () => {
    const installCommand = nodeExporterInstallCommand()
    const verifyCommand = nodeExporterVerifyCommand()

    expect(installCommand).toContain(`docker pull ${NODE_EXPORTER_IMAGE}`)
    expect(installCommand).toContain('docker rm -f oncall-node-exporter')
    expect(installCommand).toContain("echo '系统指标采集器安装命令执行成功，请继续运行验证命令'")
    expect(verifyCommand).toContain("grep -q '^node_exporter_build_info'")
    expect(verifyCommand).toContain("echo '系统指标采集器安装成功'")
    expect(verifyCommand).toContain("echo '系统指标采集器安装失败'; exit 1")

    const removeCommand = nodeExporterRemoveCommand()
    expect(removeCommand).toContain('docker rm -f oncall-node-exporter')
    expect(removeCommand).toContain("echo '系统指标采集器删除成功'")
  })

  it('installs cAdvisor from the official GHCR image and waits for metrics', () => {
    const installCommand = cadvisorInstallCommand()
    const verifyCommand = cadvisorVerifyCommand()

    expect(installCommand).toContain(`docker pull ${CADVISOR_IMAGE}`)
    expect(installCommand).toContain("curl --fail --silent --show-error --connect-timeout 2 --max-time 3 http://127.0.0.1:8080/metrics")
    expect(installCommand).toContain('docker logs --tail=80 oncall-cadvisor')
    expect(verifyCommand).toContain("grep -q '^container_'")
  })

  it('installs and verifies DCGM Exporter with real GPU metrics', () => {
    const installCommand = gpuExporterInstallCommand()
    const verifyCommand = gpuExporterVerifyCommand()

    expect(installCommand).toContain(`docker pull ${DCGM_EXPORTER_IMAGE}`)
    expect(installCommand).toContain('docker rm -f oncall-dcgm-exporter')
    expect(installCommand).toContain('--gpus all')
    expect(installCommand).toContain("echo 'GPU 采集器安装命令执行成功，请继续运行验证命令'")
    expect(verifyCommand).toContain("grep -q '^DCGM_FI_DEV_GPU_UTIL'")
    expect(verifyCommand).toContain("echo 'GPU 采集器安装成功'")
    expect(verifyCommand).toContain("echo 'GPU 采集器安装失败，请检查 NVIDIA 驱动和容器日志'; exit 1")

    const removeCommand = gpuExporterRemoveCommand()
    expect(removeCommand).toContain('docker rm -f oncall-dcgm-exporter')
    expect(removeCommand).toContain("echo 'GPU 采集器删除成功'")
  })

  it('quotes manually entered shell metacharacters', () => {
    expect(shellQuote("a'b;$(bad)")).toBe("'a'\"'\"'b;$(bad)'")
  })
})
