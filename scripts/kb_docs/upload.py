import os, sys, time
import httpx

BASE = os.environ.get('ONCALL_API', 'http://127.0.0.1:9900')
USERNAME = 'admin'
PASSWORD = os.environ.get('ONCALL_ADMIN_PASSWORD') or 'oncall-local-dev'


def main(files):
    with httpx.Client(base_url=BASE, timeout=60) as c:
        r = c.post('/api/auth/login', json={'username': USERNAME, 'password': PASSWORD})
        r.raise_for_status()

        for path in files:
            name = os.path.basename(path)
            print(f'--- uploading {name} ---')
            with open(path, 'rb') as fh:
                r = c.post('/api/knowledge/documents', files={'file': (name, fh, 'application/octet-stream')})
            if r.status_code != 200:
                print(f'  upload failed {r.status_code}: {r.text[:200]}')
                continue
            doc = r.json()
            jid = doc.get('job_id')
            print(f'  doc_id={doc.get("id")} job_id={jid}')
            # poll job
            for i in range(300):
                j = c.get(f'/api/knowledge/jobs/{jid}').json()
                if j.get('status') == 'done':
                    print(f'  DONE (attempts={j.get("attempts")})')
                    break
                if j.get('status') == 'dead':
                    print(f'  DEAD: {j.get("last_error")}')
                    break
                if i % 30 == 0:
                    print(f'  ... {j.get("status")}')
                time.sleep(2)
            else:
                print('  TIMEOUT waiting for job')


if __name__ == '__main__':
    main(sys.argv[1:])
