import { describe, expect, it } from 'vitest'

import {
  COLLECTOR_IMAGE,
  NODE_EXPORTER_IMAGE,
  collectorInstallCommand,
  collectorVerifyCommand,
  nodeExporterInstallCommand,
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
    expect(command).toContain("echo 'Collector 安装成功'")
    expect(command).toContain("echo 'Collector 安装失败'; exit 1")
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
  })

  it('quotes manually entered shell metacharacters', () => {
    expect(shellQuote("a'b;$(bad)")).toBe("'a'\"'\"'b;$(bad)'")
  })
})
