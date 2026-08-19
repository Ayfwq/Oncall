import os, sys
import httpx

BASE = 'http://127.0.0.1:9900'
USERNAME = 'admin'
PASSWORD = os.environ.get('ONCALL_ADMIN_PASSWORD') or 'oncall-local-dev'

with httpx.Client(base_url=BASE, timeout=120) as c:
    c.post('/api/auth/login', json={'username': USERNAME, 'password': PASSWORD})
    for path in sys.argv[1:]:
        name = os.path.basename(path)
        with open(path, 'rb') as fh:
            r = c.post('/api/knowledge/documents', files={'file': (name, fh, 'application/octet-stream')})
        if r.status_code == 200:
            d = r.json()
            print(f"UPLOADED {name} doc_id={d.get('id')} job_id={d.get('job_id')}")
        else:
            print(f"FAILED {name} {r.status_code}: {r.text[:200]}")
