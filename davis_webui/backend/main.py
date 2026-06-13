from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from davis_webui.backend.config import CORS_ORIGINS
from davis_webui.backend.routers import (
    checklists,
    distress,
    health,
    reports,
    screening,
    stocks,
    trends,
)

app = FastAPI(title="Davis Analyzer WebUI API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(screening.router, prefix="/api/screening", tags=["screening"])
app.include_router(stocks.router, prefix="/api/stocks", tags=["stocks"])
app.include_router(reports.router, prefix="/api/reports", tags=["reports"])
app.include_router(checklists.router, prefix="/api/checklists", tags=["checklists"])
app.include_router(trends.router, prefix="/api/trends", tags=["trends"])
app.include_router(distress.router, prefix="/api/distress", tags=["distress"])
