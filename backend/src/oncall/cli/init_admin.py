import asyncio
from oncall.application.auth_service import AuthService
from oncall.infrastructure.db.session import SessionFactory
async def main():
    async with SessionFactory() as db:
        u=await AuthService(db).ensure_admin();print(f'admin ready: {u.username}')
def run():asyncio.run(main())
