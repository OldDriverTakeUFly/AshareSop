import os

BACKEND_PORT = 8322
FRONTEND_URL = "http://localhost:3100"
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "http://localhost:3100").split(",")
