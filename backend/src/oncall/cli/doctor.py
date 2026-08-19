from __future__ import annotations

import asyncio
import importlib.util
from sqlalchemy import text
from oncall.bootstrap.config import get_settings
from oncall.infrastructure.db.session import engine

REQUIRED_IMPORTS=('fastapi','sqlalchemy','asyncpg','langgraph','docling','pymilvus','psutil','httpx')
OPTIONAL_IMPORTS=('docker','lark_oapi')


async def main():
    s=get_settings();ok=True
    for name in REQUIRED_IMPORTS:
        found=importlib.util.find_spec(name) is not None
        print(('[OK] ' if found else '[FAIL] ')+f'python import {name}')
        ok=ok and found
    for name in OPTIONAL_IMPORTS:
        found=importlib.util.find_spec(name) is not None
        print(('[OK] ' if found else '[WARN] ')+f'optional import {name}')
    try:
        async with engine.connect() as c:await c.execute(text('select 1'));print('[OK] PostgreSQL')
    except Exception as e:ok=False;print('[FAIL] PostgreSQL',e)
    try:
        from pymilvus import MilvusClient
        c=MilvusClient(uri=s.milvus_uri,token=s.milvus_token);c.list_collections();print('[OK] Milvus')
    except Exception as e:ok=False;print('[FAIL] Milvus',e)
    print('[OK] data dir',s.data_dir.resolve())
    print('[INFO] LLM',s.model_provider,s.model_name,'configured='+(str(s.model_provider=='mock' or bool(s.model_api_key))))
    print('[INFO] Embedding',s.embedding_model,'api_configured='+str(bool(s.embedding_api_key or s.model_api_key)))
    print('[INFO] Rerank',s.rerank_model or '<fallback>','configured='+str(bool(s.rerank_base_url and s.rerank_api_key and s.rerank_model)))
    print('[INFO] Feishu enabled='+str(s.feishu_enabled),'configured='+str(bool(s.feishu_app_id and s.feishu_app_secret and s.feishu_default_receive_id)))
    if not s.secret_master_key:print('[WARN] ONCALL_SECRET_MASTER_KEY is empty; acceptable only for development')
    if s.admin_password=='change-me-now':print('[WARN] default admin password is still configured')
    raise SystemExit(0 if ok else 1)


def run():asyncio.run(main())
