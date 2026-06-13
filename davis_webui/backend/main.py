from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from davis_webui.backend.config import CORS_ORIGINS
from davis_webui.backend.routers import health

app = FastAPI(title="Davis Analyzer WebUI API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api", tags=["health"])
