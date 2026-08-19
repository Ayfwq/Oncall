import asyncio
from oncall.rag.retrieval import KnowledgeRetriever

QUERIES = [
    "CPU 负载很高怎么排查",
    "load average 超过核数 4 倍 怎么办",
    "PostgreSQL 连接被拒绝 connection refused 怎么处理",
    "SQLSTATE 28000 认证失败 密码错误",
    "playwright 报 Executable doesn't exist 浏览器缺失",
    "libnss3 缺失 无法启动 chromium 沙箱",
    "pg_isready 端口 5432 没监听 listen_addresses",
]


async def main():
    r = KnowledgeRetriever()
    for q in QUERIES:
        res = await r.search(q, project_id=None, top_k=5)
        print('=' * 90)
        print('Q:', q, '| ok:', res.ok, '| hits:', len(res.data) if res.data else 0)
        if not res.ok:
            print('  error:', res.error_code, res.data)
            continue
        for i, item in enumerate(res.data[:3]):
            snippet = item.get('content', '').replace('\n', ' ')[:120]
            print(f'  #{i + 1} [{item.get("title")}] rrf={item.get("rrf_score"):.4f} rerank={item.get("rerank_score"):.3f}')
            print(f'      {snippet}')


asyncio.run(main())
