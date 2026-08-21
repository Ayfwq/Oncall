import type { SSEEvent, SSEEventType } from './types'

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch('/api' + path, {
    ...init,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(init.headers || {}) },
  })
  if (r.status === 401) {
    window.dispatchEvent(new CustomEvent('oncall:unauthorized'))
    throw new ApiError(401, 'unauthorized')
  }
  if (!r.ok) throw new ApiError(r.status, await r.text())
  return r.json()
}

export type StreamEventHandler = (event: SSEEvent) => void

export async function streamChat(
  id: string,
  content: string,
  onEvent: StreamEventHandler,
): Promise<void> {
  const r = await fetch(`/api/conversations/${id}/messages:stream`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
  if (!r.ok) throw new ApiError(r.status, await r.text())
  const reader = r.body!.getReader()
  const dec = new TextDecoder()
  let buf = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    let idx
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      const lines = block.split('\n')
      const type = (lines.find(x => x.startsWith('event:')) || 'event: message').slice(6).trim()
      const raw = (lines.find(x => x.startsWith('data:')) || 'data: {}').slice(5).trim()
      onEvent({ type: type as SSEEventType, data: JSON.parse(raw) } as SSEEvent)
    }
  }
}
