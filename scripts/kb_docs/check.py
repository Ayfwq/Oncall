import httpx
c = httpx.Client(base_url='http://127.0.0.1:9900', timeout=30)
c.post('/api/auth/login', json={'username': 'admin', 'password': 'oncall-local-dev'})
docs = c.get('/api/knowledge/documents').json()
print(f'== {len(docs)} documents ==')
for d in docs:
    print(f"  {d['title']} | status={d['status']} | version={d.get('active_version_id')}")
