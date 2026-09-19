export function formatBeijingTime(value?: string | null) {
  if (!value) return '未知'

  // Python 的 datetime 可能带 6 位微秒，浏览器 Date 只稳定支持毫秒。
  const normalized = value.replace(/(\.\d{3})\d+(?=(?:Z|[+-]\d{2}:\d{2})$)/, '$1')
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return value

  const parts = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    calendar: 'gregory',
    numberingSystem: 'latn',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(date)
  const values: Record<string, string> = {}
  for (const part of parts) {
    if (part.type !== 'literal') values[part.type] = part.value
  }
  return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}:${values.second}`
}
