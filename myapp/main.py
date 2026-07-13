from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Import routers from the different modules
from routes import router as auth_router
from group import router as group_router
from websocket import router as websocket_router

app = FastAPI(
    title="MyApp API",
    description="Main entry point for all API endpoints",
    version="1.0.0"
)

# Configure CORS if needed (allowing all for now as a default setup)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include the routers
app.include_router(auth_router)
app.include_router(group_router)
app.include_router(websocket_router)

from relay.routers.calls import router as calls_router
app.include_router(calls_router)

@app.get("/")
async def root():
    return {"message": "Welcome to MyApp API"}
