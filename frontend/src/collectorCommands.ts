export const COLLECTOR_IMAGE = 'ghcr.io/ayfwq/oncall-collector:latest'

export function shellQuote(value: string): string {
  return `'${value.replace(/'/g, `'"'"'`)}'`
}

export function collectorInstallCommand(token: string): string {
  const safeToken = shellQuote(token || '<页面自动生成的Token>')
  return [
    `docker pull ${COLLECTOR_IMAGE}`,
    `(docker rm -f oncall-collector >/dev/null 2>&1 || true)`,
    `docker run -d --name oncall-collector --restart unless-stopped -p 9910:9910 -e ONCALL_COLLECTOR_TOKEN=${safeToken} -v /var/run/docker.sock:/var/run/docker.sock:ro ${COLLECTOR_IMAGE}`,
  ].join(' && ')
}

export function collectorVerifyCommand(token: string): string {
  return `curl --fail --silent --show-error --header ${shellQuote(`X-Oncall-Token: ${token || '<页面自动生成的Token>'}`)} http://127.0.0.1:9910/health && echo`
}
