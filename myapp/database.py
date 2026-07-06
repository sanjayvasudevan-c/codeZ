from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


database_url = "postgresql+asyncpg://user:password@localhost/fastapi_db"

engine = create_async_engine(
    database_url,
    echo=True
)

async_localsession = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

async def get_db():
    async with async_localsession() as session:
        yield session


from motor.motor_asyncio import AsyncIOMotorClient

MONGO_DETAILS = "mongodb://localhost:27017"

client = AsyncIOMotorClient(MONGO_DETAILS)
database = client.chat_app
group_collection = database.get_collection("groups")
message_collection = database.get_collection("messages")

def get_mongo_db():
    return database