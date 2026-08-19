import os, time
import httpx

BASE = 'http://127.0.0.1:9900'
TARGETS = {'rfc9293-tcp.pdf', 'rfc9110-http-semantics.pdf', 'postgresql-17-manual.pdf'}

c = httpx.Client(base_url=BASE, timeout=60)
c.post('/api/auth/login', json={'username': 'admin', 'password': os.environ.get('ONCALL_ADMIN_PASSWORD') or 'oncall-local-dev'})

seen = None
for i in range(360):  # up to ~3 hours
    try:
        docs = c.get('/api/knowledge/documents').json()
        cur = {d['title']: d['status'] for d in docs if d['title'] in TARGETS}
        if cur != seen:
            seen = cur
            print(f"[{time.strftime('%H:%M:%S')}] {cur}", flush=True)
        if len(cur) == len(TARGETS):
            if all(v == 'ready' for v in cur.values()):
                print('ALL_READY', flush=True)
                break
            if any(v == 'dead' for v in cur.values()):
                print('SOME_DEAD', flush=True)
                break
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] ERROR {e}", flush=True)
    time.sleep(20)
else:
    print('MONITOR_TIMEOUT', flush=True)
