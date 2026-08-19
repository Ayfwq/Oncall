__version__ = "0.1.0"

# langgraph-checkpoint-postgres (v3.x) uses psycopg in async mode, which is
# incompatible with the Windows default ProactorEventLoop. Pin the selector loop
# policy before any asyncio.run()/uvicorn loop is created. asyncpg supports the
# selector loop on Windows too, so this is safe for the business database engine.
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

