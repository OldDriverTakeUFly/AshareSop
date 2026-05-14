from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from stockhot.api.auth import verify_credentials
from stockhot.api.config import settings

app = FastAPI(title="StockHot-CN API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

from stockhot.api.routers import (
    dragon_tiger,
    fund_flow,
    health,
    limit_up,
    risk_alert,
    trigger,
)

app.include_router(health.router)

app.include_router(
    limit_up.router, dependencies=[Depends(verify_credentials)]
)
app.include_router(
    dragon_tiger.router, dependencies=[Depends(verify_credentials)]
)
app.include_router(
    fund_flow.router, dependencies=[Depends(verify_credentials)]
)
app.include_router(
    risk_alert.router, dependencies=[Depends(verify_credentials)]
)
app.include_router(
    trigger.router, dependencies=[Depends(verify_credentials)]
)
