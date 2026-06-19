from sqlachemy.ext.asyncio import AsyncSession, create_async_engine
from sqlachemy.orm import sessionmaker

database_url = "postgresql+asyncpg://user:password@localhost/fastapi_db"

engine = create_async_engine(
    database_url,
    echo=true;
)

async_localsession = sessionmaker(
    bind=engine,
    class=AsyncSession,
    expire_on_commit=False
)

async def get_db():
    async with async_localsession() as session:
        yield session