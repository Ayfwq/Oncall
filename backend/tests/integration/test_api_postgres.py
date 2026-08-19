"""HTTP-level integration tests against the live API + PostgreSQL.

These tests exercise the real FastAPI routes over HTTP (http://127.0.0.1:9900),
authenticate with the admin cookie session, verify database rows after writes,
verify owner scoping (401 unauthenticated / 404 cross-user), and clean up every
row they create so the suite is repeatable.

Run from the repo root:
    uv run pytest backend/tests/integration -q
"""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timedelta
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select

from oncall.bootstrap.config import get_settings
from oncall.infrastructure.db.models import (
    BackgroundJob,
    Conversation,
    Incident,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
    Message,
    MetricSample,
    MonitoringRun,
    Project,
    ProjectDatabaseProfile,
    Session,
    User,
)
from oncall.infrastructure.db.session import SessionFactory
from oncall.security.passwords import hash_password

pytestmark = pytest.mark.integration

BASE_URL = os.environ.get("ITEST_BASE_URL", "http://127.0.0.1:9900")
SETTINGS = get_settings()
ADMIN_USER = SETTINGS.admin_username
ADMIN_PASS = SETTINGS.admin_password

# A rule keyed on a metric that never appears in real snapshots, so the background
# monitor worker can never fire it: incidents are only ever created by the dev
# endpoint with an explicit value.
SYNTH_RULE = {
    "metric_key": "test.synthetic.metric",
    "resource_key": "synthetic",
    "operator": ">",
    "trigger_threshold": 1000.0,
    "trigger_for": 1,
    "recovery_threshold": 900.0,
    "recovery_for": 1,
    "severity": "critical",
    "enabled": True,
}


async def _scalar(stmt):
    async with SessionFactory() as db:
        return await db.scalar(stmt)


async def _scalars(stmt):
    async with SessionFactory() as db:
        return list((await db.scalars(stmt)).all())


async def _create_project(client: httpx.AsyncClient, name: str, **overrides) -> dict:
    body = {
        "name": name,
        "description": "integration test project",
        "enabled": True,
        "timezone": "Asia/Singapore",
        "poll_interval": 3600,
        "process_targets": [],
        "log_sources": [],
        "docker_targets": [],
        "database_profiles": [],
        "service_endpoints": [],
        "rules": [dict(SYNTH_RULE)],
    }
    body.update(overrides)
    r = await client.post("/api/projects", json=body)
    assert r.status_code == 200, f"create project failed: {r.status_code} {r.text[:300]}"
    return r.json()


class Cleanup:
    """Tracks every row a test creates and removes it, so runs are repeatable."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.projects: list[str] = []
        self.conversations: list[str] = []
        self.documents: list[str] = []
        self.job_ids: list[str] = []
        self.user_ids: list[str] = []

    def track_project(self, pid: str) -> str:
        self.projects.append(pid)
        return pid

    def track_conversation(self, cid: str) -> str:
        self.conversations.append(cid)
        return cid

    def track_document(self, did: str) -> str:
        self.documents.append(did)
        return did

    def track_job(self, jid: str) -> str:
        self.job_ids.append(jid)
        return jid

    def track_user(self, uid: str) -> str:
        self.user_ids.append(uid)
        return uid

    async def run(self) -> None:
        for cid in reversed(self.conversations):
            try:
                await self.client.delete(f"/api/conversations/{cid}")
            except Exception:
                pass
        for did in reversed(self.documents):
            try:
                await self.client.delete(f"/api/knowledge/documents/{did}")
            except Exception:
                pass
        for pid in reversed(self.projects):
            try:
                await self.client.delete(f"/api/projects/{pid}")
            except Exception:
                pass
        async with SessionFactory() as db:
            for jid in self.job_ids:
                job = await db.get(BackgroundJob, UUID(jid))
                if job:
                    await db.delete(job)
            for uid in self.user_ids:
                user = await db.get(User, UUID(uid))
                if user:
                    await db.delete(user)
            await db.commit()
        try:
            await self.client.post("/api/auth/logout")
        except Exception:
            pass


@pytest.fixture
async def admin_client():
    async with httpx.AsyncClient(base_url=BASE_URL, follow_redirects=True, timeout=60.0) as client:
        r = await client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
        assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text[:200]}"
        cleanup = Cleanup(client)
        yield client, cleanup
        await cleanup.run()


@pytest.fixture
async def second_user():
    """A second user created directly in PostgreSQL (there is no registration API)."""
    username = f"itest-{uuid.uuid4().hex[:10]}@example.com"
    password = uuid.uuid4().hex  # never printed
    async with SessionFactory() as db:
        user = User(username=username, password_hash=hash_password(password))
        db.add(user)
        await db.commit()
        await db.refresh(user)
        uid = str(user.id)
    client = httpx.AsyncClient(base_url=BASE_URL, follow_redirects=True, timeout=60.0)
    r = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, "second user login failed"
    cleanup = Cleanup(client)
    cleanup.track_user(uid)
    try:
        yield client, cleanup, username, uid
    finally:
        await cleanup.run()
        await client.aclose()


async def _admin_id(client: httpx.AsyncClient) -> str:
    r = await client.get("/api/auth/me")
    assert r.status_code == 200
    return r.json()["id"]


# --------------------------------------------------------------------------- auth


async def test_auth_login_logout_me_and_session_rows():
    async with httpx.AsyncClient(base_url=BASE_URL, follow_redirects=True, timeout=30.0) as anon:
        for url in (
            "/api/auth/me",
            "/api/conversations",
            "/api/projects",
            "/api/incidents",
            "/api/knowledge/documents",
            "/api/settings/readiness",
            "/api/settings/tool-contracts",
        ):
            assert (await anon.get(url)).status_code == 401, url
        assert (await anon.post("/api/auth/login", json={"username": ADMIN_USER, "password": "definitely-wrong"})).status_code == 401
        assert (await anon.post("/api/auth/logout")).status_code == 200  # no-op without a session

    async with httpx.AsyncClient(base_url=BASE_URL, follow_redirects=True, timeout=30.0) as client:
        r = await client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
        assert r.status_code == 200
        body = r.json()
        assert body["username"] == ADMIN_USER

        token = client.cookies.get("oncall_session")
        assert token

        r = await client.get("/api/auth/me")
        assert r.status_code == 200
        assert r.json()["id"] == body["id"] and r.json()["username"] == ADMIN_USER

        # DB: a session row holding the sha256 token hash exists and is unexpired
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        row = await _scalar(select(Session).where(Session.token_hash == token_hash))
        assert row is not None
        assert row.expires_at > datetime.now().astimezone()

        r = await client.post("/api/auth/logout")
        assert r.status_code == 200
        assert (await client.get("/api/auth/me")).status_code == 401
        # DB: the session row was deleted
        assert await _scalar(select(Session).where(Session.token_hash == token_hash)) is None


async def test_health_is_public():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        r = await client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["ok"] is True and r.json()["database"] is True


# ----------------------------------------------------------------------- projects


async def test_projects_crud_dryrun_snapshot_password_redaction(admin_client):
    client, cleanup = admin_client
    suffix = uuid.uuid4().hex[:10]
    name = f"itest-project-{suffix}"
    db_profile = {
        "type": "postgresql",
        "host": "127.0.0.1",
        "port": 5432,
        "database": "oncall",
        "username": "oncall",
        "password": "s3cret-placeholder",
        "sslmode": "prefer",
        "enabled": True,
    }
    p = await _create_project(client, name, database_profiles=[db_profile])
    pid = cleanup.track_project(p["id"])
    assert p["name"] == name

    # DB row exists and is owned by admin
    admin_id = await _admin_id(client)
    row = await _scalar(select(Project).where(Project.id == UUID(pid)))
    assert row is not None and str(row.user_id) == admin_id and row.name == name

    # list contains it
    assert pid in [x["id"] for x in (await client.get("/api/projects")).json()]

    # detail: db password is redacted, rule survives
    r = await client.get(f"/api/projects/{pid}")
    assert r.status_code == 200
    detail = r.json()
    assert detail["name"] == name
    assert detail["database_profiles"][0]["password"] is None
    assert len(detail["rules"]) == 1 and detail["rules"][0]["metric_key"] == "test.synthetic.metric"

    # update: rename; password=None sent must preserve the stored encrypted secret
    updated = dict(detail)
    updated["name"] = name + "-renamed"
    for dbp in updated["database_profiles"]:
        dbp["password"] = None
    r = await client.put(f"/api/projects/{pid}", json=updated)
    assert r.status_code == 200
    r = await client.get(f"/api/projects/{pid}")
    assert r.json()["name"] == name + "-renamed"
    assert len(r.json()["rules"]) == 1
    dbp_row = await _scalar(select(ProjectDatabaseProfile).where(ProjectDatabaseProfile.project_id == UUID(pid)))
    assert dbp_row is not None and dbp_row.encrypted_password is not None

    # validation failure -> 422
    bad = dict(updated)
    bad["poll_interval"] = 5
    assert (await client.put(f"/api/projects/{pid}", json=bad)).status_code == 422

    # dry-run collect
    r = await client.post(f"/api/projects/{pid}/test")
    assert r.status_code == 200
    snap = r.json()
    for key in ("project_id", "observed_at", "signals", "resources", "collector_status"):
        assert key in snap

    # snapshot: seed a completed MonitoringRun and verify the endpoint surfaces it
    seeded_snapshot = {
        "signals": {"test.synthetic.metric": 42.0},
        "resources": {"host": {"counters": {}}},
        "collector_status": {"host": {"ok": True}},
    }
    future = datetime.now().astimezone() + timedelta(hours=1)
    async with SessionFactory() as db:
        db.add(MonitoringRun(
            project_id=UUID(pid),
            status="completed",
            started_at=future,
            finished_at=future,
            snapshot=seeded_snapshot,
            collector_status={"host": {"ok": True}},
        ))
        await db.commit()
    r = await client.get(f"/api/projects/{pid}/snapshot")
    assert r.status_code == 200
    body = r.json()
    assert body["snapshot"] == seeded_snapshot
    assert body["collector_status"] == {"host": {"ok": True}}
    assert body["observed_at"] is not None

    # monitoring metrics: seed samples and verify ascending order
    ts1 = datetime.now().astimezone() - timedelta(minutes=2)
    ts2 = datetime.now().astimezone() - timedelta(minutes=1)
    async with SessionFactory() as db:
        db.add_all([
            MetricSample(project_id=UUID(pid), metric_key="test.synthetic.metric", resource_key="synthetic", ts=ts1, value=1.5),
            MetricSample(project_id=UUID(pid), metric_key="test.synthetic.metric", resource_key="synthetic", ts=ts2, value=2.5),
        ])
        await db.commit()
    r = await client.get("/api/monitoring/metrics", params={"project_id": pid, "metric_key": "test.synthetic.metric"})
    assert r.status_code == 200
    assert [x["value"] for x in r.json()] == [1.5, 2.5]

    # delete + DB gone
    assert (await client.delete(f"/api/projects/{pid}")).status_code == 200
    assert await _scalar(select(Project).where(Project.id == UUID(pid))) is None
    assert (await client.get(f"/api/projects/{pid}")).status_code == 404
    assert (await client.post(f"/api/projects/{pid}/test")).status_code == 404


async def test_projects_owner_scope_and_unauthenticated(admin_client, second_user):
    client, cleanup = admin_client
    sclient, scleanup, _, _ = second_user

    apid = cleanup.track_project((await _create_project(client, f"itest-admin-{uuid.uuid4().hex[:8]}"))["id"])
    spid = scleanup.track_project((await _create_project(sclient, f"itest-user2-{uuid.uuid4().hex[:8]}"))["id"])

    # admin cannot touch user2's project
    assert (await client.get(f"/api/projects/{spid}")).status_code == 404
    assert (await client.put(f"/api/projects/{spid}", json={"name": "x", "poll_interval": 60})).status_code == 404
    assert (await client.delete(f"/api/projects/{spid}")).status_code == 404
    assert (await client.post(f"/api/projects/{spid}/test")).status_code == 404
    assert (await client.get(f"/api/projects/{spid}/snapshot")).status_code == 404
    assert (await client.get("/api/monitoring/metrics", params={"project_id": spid, "metric_key": "x"})).status_code == 404
    assert (await client.post("/api/dev/incidents/trigger", json={"project_id": spid})).status_code == 404

    # user2 cannot touch admin's project
    assert (await sclient.get(f"/api/projects/{apid}")).status_code == 404
    assert (await sclient.delete(f"/api/projects/{apid}")).status_code == 404
    assert (await sclient.post(f"/api/projects/{apid}/test")).status_code == 404

    # each user's list is scoped
    admin_ids = [x["id"] for x in (await client.get("/api/projects")).json()]
    user2_ids = [x["id"] for x in (await sclient.get("/api/projects")).json()]
    assert apid in admin_ids and spid not in admin_ids
    assert spid in user2_ids and apid not in user2_ids


# ------------------------------------------------------------------- conversations


async def test_conversations_crud_search_archive_messages_stream(admin_client):
    client, cleanup = admin_client
    suffix = uuid.uuid4().hex[:10]
    pid = cleanup.track_project((await _create_project(client, f"itest-convproj-{suffix}"))["id"])

    title_a = f"itest-conv-a-{suffix}"
    title_b = f"itest-conv-b-{suffix}"
    ra = await client.post("/api/conversations", json={"title": title_a, "project_id": pid})
    assert ra.status_code == 200
    cid_a = cleanup.track_conversation(ra.json()["id"])
    rb = await client.post("/api/conversations", json={"title": title_b})
    assert rb.status_code == 200
    cid_b = cleanup.track_conversation(rb.json()["id"])

    # DB rows owned by admin
    admin_id = await _admin_id(client)
    ca = await _scalar(select(Conversation).where(Conversation.id == UUID(cid_a)))
    assert ca is not None and str(ca.user_id) == admin_id and ca.title == title_a and str(ca.project_id) == pid

    # list + search
    ids = [c["id"] for c in (await client.get("/api/conversations")).json()]
    assert cid_a in ids and cid_b in ids
    assert [c["id"] for c in (await client.get("/api/conversations", params={"q": title_a})).json()] == [cid_a]
    assert (await client.get("/api/conversations", params={"q": "zzz-no-such-title"})).json() == []

    # rename
    r = await client.patch(f"/api/conversations/{cid_a}", json={"title": f"{title_a}-renamed"})
    assert r.status_code == 200 and r.json()["title"] == f"{title_a}-renamed"

    # archive: hidden by default, visible with include_archived
    r = await client.patch(f"/api/conversations/{cid_a}", json={"archived": True})
    assert r.status_code == 200 and r.json()["archived"] is True
    default_ids = [c["id"] for c in (await client.get("/api/conversations")).json()]
    assert cid_a not in default_ids and cid_b in default_ids
    archived_ids = [c["id"] for c in (await client.get("/api/conversations", params={"include_archived": "true"})).json()]
    assert cid_a in archived_ids
    assert (await _scalar(select(Conversation).where(Conversation.id == UUID(cid_a)))).archived is True

    # messages: empty before streaming
    assert (await client.get(f"/api/conversations/{cid_a}/messages")).json() == []

    # a real agent turn over SSE
    user_text = f"integration test message {suffix}"
    events: list[str] = []
    async with client.stream(
        "POST",
        f"/api/conversations/{cid_a}/messages:stream",
        json={"content": user_text, "channel": "web"},
        timeout=httpx.Timeout(120.0),
    ) as sr:
        assert sr.status_code == 200
        assert "text/event-stream" in sr.headers.get("content-type", "")
        async for line in sr.aiter_lines():
            if line.startswith("event:"):
                events.append(line.split(":", 1)[1].strip())
    assert "final" in events, f"expected a final SSE event, got {events}"

    # both the user message and the assistant answer were persisted
    msgs = (await client.get(f"/api/conversations/{cid_a}/messages")).json()
    assert len(msgs) >= 2, msgs
    assert msgs[0]["role"] == "user" and msgs[0]["content"] == user_text
    assert "assistant" in [m["role"] for m in msgs]
    rows = await _scalars(select(Message).where(Message.conversation_id == UUID(cid_a)).order_by(Message.created_at.asc()))
    assert len(rows) == len(msgs) and rows[0].content == user_text

    # delete b
    assert (await client.delete(f"/api/conversations/{cid_b}")).status_code == 200
    assert (await client.get(f"/api/conversations/{cid_b}/messages")).status_code == 404
    assert (await client.delete(f"/api/conversations/{cid_b}")).status_code == 404
    assert await _scalar(select(Conversation).where(Conversation.id == UUID(cid_b))) is None


async def test_conversations_owner_scope(admin_client, second_user):
    client, cleanup = admin_client
    sclient, scleanup, _, _ = second_user
    acid = cleanup.track_conversation((await client.post("/api/conversations", json={"title": f"admin-conv-{uuid.uuid4().hex[:8]}"})).json()["id"])
    scid = scleanup.track_conversation((await sclient.post("/api/conversations", json={"title": f"user2-conv-{uuid.uuid4().hex[:8]}"})).json()["id"])

    # admin cannot touch user2's conversation
    assert (await client.patch(f"/api/conversations/{scid}", json={"title": "x"})).status_code == 404
    assert (await client.delete(f"/api/conversations/{scid}")).status_code == 404
    assert (await client.get(f"/api/conversations/{scid}/messages")).status_code == 404
    assert (await client.post(f"/api/conversations/{scid}/messages:stream", json={"content": "hi"})).status_code == 404

    # user2 cannot touch admin's conversation
    assert (await sclient.patch(f"/api/conversations/{acid}", json={"title": "x"})).status_code == 404
    assert (await sclient.delete(f"/api/conversations/{acid}")).status_code == 404
    assert (await sclient.get(f"/api/conversations/{acid}/messages")).status_code == 404

    # lists are scoped
    admin_ids = [c["id"] for c in (await client.get("/api/conversations")).json()]
    user2_ids = [c["id"] for c in (await sclient.get("/api/conversations")).json()]
    assert acid in admin_ids and scid not in admin_ids
    assert scid in user2_ids and acid not in user2_ids


# ----------------------------------------------------------------------- incidents


async def test_incidents_lifecycle_via_dev_trigger(admin_client):
    client, cleanup = admin_client
    suffix = uuid.uuid4().hex[:10]
    pid = cleanup.track_project((await _create_project(client, f"itest-inc-{suffix}"))["id"])

    # dev endpoints require the development environment (assert the running env)
    readiness = (await client.get("/api/settings/readiness")).json()
    assert readiness["environment"].lower() == "development"

    # trigger -> creates incident + incident conversation
    r = await client.post(
        "/api/dev/incidents/trigger",
        json={"project_id": pid, "metric_key": "test.synthetic.metric", "value": 2000.0},
    )
    assert r.status_code == 200, r.text
    iid = r.json()["incident_id"]
    icid = r.json()["conversation_id"]
    assert icid
    cleanup.track_conversation(icid)
    assert r.json()["status"] in {"open", "investigating", "diagnosed"}

    # DB rows
    inc = await _scalar(select(Incident).where(Incident.id == UUID(iid)))
    assert inc is not None and str(inc.project_id) == pid
    assert inc.anomaly_type == "test.synthetic.metric" and inc.status in {"open", "investigating", "diagnosed"}
    iconv = await _scalar(select(Conversation).where(Conversation.id == UUID(icid)))
    assert iconv is not None and iconv.type == "incident" and str(iconv.incident_id) == iid

    # list + detail
    assert iid in [x["id"] for x in (await client.get("/api/incidents")).json()]
    r = await client.get(f"/api/incidents/{iid}")
    assert r.status_code == 200
    detail = r.json()
    assert detail["id"] == iid and detail["conversation_id"] == icid
    for key in ("status", "severity", "summary", "anomaly_type", "resource_key", "first_seen", "last_seen", "evidence", "diagnosis"):
        assert key in detail

    # trace
    r = await client.get(f"/api/incidents/{iid}/trace")
    assert r.status_code == 200
    trace = r.json()
    assert "agent_runs" in trace and "notifications" in trace

    # conversation endpoint is idempotent
    r = await client.post(f"/api/incidents/{iid}/conversation")
    assert r.status_code == 200 and r.json()["conversation_id"] == icid

    # investigate enqueues a background job
    r = await client.post(f"/api/incidents/{iid}/investigate")
    assert r.status_code == 200
    jid = r.json()["job_id"]
    cleanup.track_job(jid)
    assert r.json()["conversation_id"] == icid
    job = await _scalar(select(BackgroundJob).where(BackgroundJob.id == UUID(jid)))
    assert job is not None and job.type == "incident_investigate"

    # resolve
    r = await client.post(f"/api/incidents/{iid}/resolve")
    assert r.status_code == 200 and r.json()["status"] == "resolved"
    r = await client.get(f"/api/incidents/{iid}")
    assert r.json()["status"] == "resolved" and r.json()["resolved_at"] is not None
    assert (await _scalar(select(Incident).where(Incident.id == UUID(iid)))).status == "resolved"

    # a new trigger while the old incident is resolved creates a NEW incident
    r = await client.post(
        "/api/dev/incidents/trigger",
        json={"project_id": pid, "metric_key": "test.synthetic.metric", "value": 2500.0},
    )
    assert r.status_code == 200
    iid2 = r.json()["incident_id"]
    icid2 = r.json()["conversation_id"]
    cleanup.track_conversation(icid2)
    assert iid2 != iid

    # dev recover resolves it
    r = await client.post(f"/api/dev/incidents/{iid2}/recover")
    assert r.status_code == 200 and r.json()["status"] == "resolved"
    assert (await client.get(f"/api/incidents/{iid2}")).json()["status"] == "resolved"

    # edge cases: unknown project, unknown metric, malformed/missing project_id
    assert (await client.post("/api/dev/incidents/trigger", json={"project_id": str(uuid.uuid4())})).status_code == 404
    assert (await client.post("/api/dev/incidents/trigger", json={"project_id": pid, "metric_key": "no.such.metric"})).status_code == 400
    assert (await client.post("/api/dev/incidents/trigger", json={"project_id": "not-a-uuid"})).status_code == 400
    assert (await client.post("/api/dev/incidents/trigger", json={})).status_code == 400
    assert (await client.post(f"/api/dev/incidents/{uuid.uuid4()}/recover")).status_code == 404
    assert (await client.get(f"/api/incidents/{uuid.uuid4()}")).status_code == 404
    assert (await client.get(f"/api/incidents/{uuid.uuid4()}/trace")).status_code == 404
    assert (await client.post(f"/api/incidents/{uuid.uuid4()}/resolve")).status_code == 404
    assert (await client.post(f"/api/incidents/{uuid.uuid4()}/conversation")).status_code == 404
    assert (await client.post(f"/api/incidents/{uuid.uuid4()}/investigate")).status_code == 404

    # the dev trigger also enqueues an 'initial' investigation job the API never
    # exposes; drop leftover jobs referencing the test incidents
    async with SessionFactory() as db:
        for inc_id in (iid, iid2):
            jobs = (await db.scalars(select(BackgroundJob).where(BackgroundJob.payload["incident_id"].astext == inc_id))).all()
            for job in jobs:
                await db.delete(job)
        await db.commit()


async def test_incidents_owner_scope(admin_client, second_user):
    client, cleanup = admin_client
    sclient, scleanup, _, _ = second_user
    spid = scleanup.track_project((await _create_project(sclient, f"itest-inc-user2-{uuid.uuid4().hex[:8]}"))["id"])
    r = await sclient.post("/api/dev/incidents/trigger", json={"project_id": spid, "metric_key": "test.synthetic.metric", "value": 3000.0})
    assert r.status_code == 200
    iid = r.json()["incident_id"]
    icid = r.json()["conversation_id"]
    scleanup.track_conversation(icid)

    # admin cannot see or act on user2's incident
    assert (await client.get(f"/api/incidents/{iid}")).status_code == 404
    assert (await client.get(f"/api/incidents/{iid}/trace")).status_code == 404
    assert (await client.post(f"/api/incidents/{iid}/investigate")).status_code == 404
    assert (await client.post(f"/api/incidents/{iid}/resolve")).status_code == 404
    assert (await client.post(f"/api/incidents/{iid}/conversation")).status_code == 404
    assert (await client.post(f"/api/dev/incidents/{iid}/recover")).status_code == 404
    assert iid not in [x["id"] for x in (await client.get("/api/incidents")).json()]

    # user2 can see it
    assert (await sclient.get(f"/api/incidents/{iid}")).status_code == 200

    # drop the dev-enqueued 'initial' investigation job referencing this incident
    async with SessionFactory() as db:
        jobs = (await db.scalars(select(BackgroundJob).where(BackgroundJob.payload["incident_id"].astext == iid))).all()
        for job in jobs:
            await db.delete(job)
        await db.commit()


# ----------------------------------------------------------------------- knowledge


async def test_knowledge_documents_upload_jobs_reindex_delete(admin_client):
    client, cleanup = admin_client
    suffix = uuid.uuid4().hex[:10]
    filename = f"itest-knowledge-{suffix}.md"
    content = f"# Integration knowledge doc {suffix}\n\nSome unique content: {suffix}\n"

    # upload
    r = await client.post("/api/knowledge/documents", files={"file": (filename, content.encode(), "text/markdown")})
    assert r.status_code == 200, r.text
    up = r.json()
    vid, jid = up["version_id"], up["job_id"]
    cleanup.track_job(jid)
    assert up["status"] == "uploaded"

    # identical re-upload is idempotent (same version + job)
    r2 = await client.post("/api/knowledge/documents", files={"file": (filename, content.encode(), "text/markdown")})
    assert r2.status_code == 200
    assert r2.json()["version_id"] == vid and r2.json()["job_id"] == jid

    # DB rows: document (admin-owned), version with 64-hex checksum, rag_ingest job
    admin_id = await _admin_id(client)
    doc = await _scalar(select(KnowledgeDocument).where(KnowledgeDocument.title == filename))
    assert doc is not None and str(doc.user_id) == admin_id
    cleanup.track_document(str(doc.id))
    ver = await _scalar(select(KnowledgeDocumentVersion).where(KnowledgeDocumentVersion.id == UUID(vid)))
    assert ver is not None and ver.document_id == doc.id and len(ver.checksum) == 64
    job = await _scalar(select(BackgroundJob).where(BackgroundJob.id == UUID(jid)))
    assert job is not None and job.type == "rag_ingest" and job.payload.get("version_id") == vid

    # job endpoint
    r = await client.get(f"/api/knowledge/jobs/{jid}")
    assert r.status_code == 200
    jb = r.json()
    assert jb["id"] == jid and jb["type"] == "rag_ingest"
    assert jb["status"] in {"pending", "running", "done", "failed", "dead"}

    # documents list contains it
    assert str(doc.id) in [d["id"] for d in (await client.get("/api/knowledge/documents")).json()]

    # reindex: only possible once ingestion produced an active version (no rag
    # worker is running here, so expect 404 unless the env already ingested it)
    doc2 = await _scalar(select(KnowledgeDocument).where(KnowledgeDocument.id == doc.id))
    r = await client.post(f"/api/knowledge/documents/{doc.id}/reindex")
    if doc2.active_version_id:
        assert r.status_code == 200
        cleanup.track_job(r.json()["job_id"])
    else:
        assert r.status_code == 404

    # delete + DB gone
    assert (await client.delete(f"/api/knowledge/documents/{doc.id}")).status_code == 200
    assert await _scalar(select(KnowledgeDocument).where(KnowledgeDocument.id == doc.id)) is None
    assert (await client.delete(f"/api/knowledge/documents/{doc.id}")).status_code == 404
    assert str(doc.id) not in [d["id"] for d in (await client.get("/api/knowledge/documents")).json()]


async def test_knowledge_owner_scope_and_errors(admin_client, second_user):
    client, cleanup = admin_client
    sclient, scleanup, _, _ = second_user
    spid = scleanup.track_project((await _create_project(sclient, f"itest-kd-user2-{uuid.uuid4().hex[:8]}"))["id"])

    # scope to another user's project -> 404; malformed scope -> 400; bad ext -> 400
    r = await client.post(
        "/api/knowledge/documents",
        files={"file": (f"bad-scope-{uuid.uuid4().hex[:8]}.md", b"# x", "text/markdown")},
        data={"project_scope": spid},
    )
    assert r.status_code == 404
    r = await client.post(
        "/api/knowledge/documents",
        files={"file": (f"bad-scope-uuid-{uuid.uuid4().hex[:8]}.md", b"# x", "text/markdown")},
        data={"project_scope": "not-a-uuid"},
    )
    assert r.status_code == 400
    r = await client.post(
        "/api/knowledge/documents",
        files={"file": (f"bad-ext-{uuid.uuid4().hex[:8]}.exe", b"MZ", "application/octet-stream")},
    )
    assert r.status_code == 400

    # user2 uploads a document; admin cannot see, delete or reindex it
    r = await sclient.post(
        "/api/knowledge/documents",
        files={"file": (f"user2-doc-{uuid.uuid4().hex[:8]}.md", b"# u2", "text/markdown")},
    )
    assert r.status_code == 200
    up = r.json()
    u2_jid = up["job_id"]
    u2_docs = (await sclient.get("/api/knowledge/documents")).json()
    u2_doc_id = u2_docs[0]["id"]
    scleanup.track_document(u2_doc_id)
    scleanup.track_job(u2_jid)

    admin_doc_ids = [d["id"] for d in (await client.get("/api/knowledge/documents")).json()]
    assert u2_doc_id not in admin_doc_ids
    assert (await client.delete(f"/api/knowledge/documents/{u2_doc_id}")).status_code == 404
    assert (await client.post(f"/api/knowledge/documents/{u2_doc_id}/reindex")).status_code == 404
    assert (await client.get(f"/api/knowledge/jobs/{u2_jid}")).status_code == 404


# ------------------------------------------------------------------------ settings


async def test_settings_readiness_and_tool_contracts(admin_client):
    client, cleanup = admin_client
    r = await client.get("/api/settings/readiness")
    assert r.status_code == 200
    body = r.json()
    assert body["environment"].lower() == SETTINGS.env.lower()
    for key in ("llm", "embedding", "rerank", "feishu", "security", "storage"):
        assert key in body
    assert isinstance(body["llm"]["configured"], bool)
    assert body["storage"]["database"] == "postgresql"

    r = await client.get("/api/settings/tool-contracts")
    assert r.status_code == 200
    tools = r.json()["tools"]
    assert isinstance(tools, list) and len(tools) > 0
    for tool in tools:
        assert "name" in tool and "description" in tool and "parameters" in tool


# ------------------------------------------------------------------ malformed ids


async def test_malformed_ids_never_500(admin_client):
    client, cleanup = admin_client
    bad = "not-a-uuid"
    cases = [
        ("GET", f"/api/projects/{bad}", None),
        ("PUT", f"/api/projects/{bad}", {"name": "x", "poll_interval": 60}),
        ("DELETE", f"/api/projects/{bad}", None),
        ("POST", f"/api/projects/{bad}/test", None),
        ("GET", f"/api/projects/{bad}/snapshot", None),
        ("GET", f"/api/incidents/{bad}", None),
        ("GET", f"/api/incidents/{bad}/trace", None),
        ("POST", f"/api/incidents/{bad}/investigate", None),
        ("POST", f"/api/incidents/{bad}/resolve", None),
        ("POST", f"/api/incidents/{bad}/conversation", None),
        ("POST", f"/api/dev/incidents/{bad}/recover", None),
        ("PATCH", f"/api/conversations/{bad}", {"title": "x"}),
        ("DELETE", f"/api/conversations/{bad}", None),
        ("GET", f"/api/conversations/{bad}/messages", None),
        ("POST", f"/api/conversations/{bad}/messages:stream", {"content": "hi"}),
        ("GET", f"/api/knowledge/jobs/{bad}", None),
        ("POST", f"/api/knowledge/documents/{bad}/reindex", None),
        ("DELETE", f"/api/knowledge/documents/{bad}", None),
        ("GET", "/api/monitoring/metrics?project_id=not-a-uuid&metric_key=x", None),
    ]
    for method, url, payload in cases:
        r = await client.request(method, url, json=payload)
        assert r.status_code != 500, f"{method} {url} -> 500 {r.text[:200]}"
        assert r.status_code in (400, 404, 422), f"{method} {url} -> {r.status_code} {r.text[:200]}"
