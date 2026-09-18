import { describe, expect, it } from 'vitest'

import { COLLECTOR_IMAGE, collectorInstallCommand, collectorVerifyCommand, shellQuote } from './collectorCommands'

describe('collector commands', () => {
  it('installs the published image without project source files', () => {
    const command = collectorInstallCommand('oncall-test-token')

    expect(command).toContain(`docker pull ${COLLECTOR_IMAGE}`)
    expect(command).toContain('docker rm -f oncall-collector')
    expect(command).toContain('-e ONCALL_COLLECTOR_TOKEN=\'oncall-test-token\'')
    expect(command).toContain('/var/run/docker.sock:/var/run/docker.sock:ro')
    expect(command).not.toContain('docker compose')
    expect(command).not.toContain('deploy/collector.compose.yaml')
  })

  it('verifies the authenticated health endpoint', () => {
    expect(collectorVerifyCommand('oncall-test-token')).toBe(
      "curl --fail --silent --show-error --header 'X-Oncall-Token: oncall-test-token' http://127.0.0.1:9910/health && echo",
    )
  })

  it('quotes manually entered shell metacharacters', () => {
    expect(shellQuote("a'b;$(bad)")).toBe("'a'\"'\"'b;$(bad)'")
  })
})

