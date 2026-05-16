from stockhot.core.config import DB_PATH as INVEST_DB_PATH
from stockhot.core.config import REPORTS_DIR

INVEST_REPORTS_DIR = REPORTS_DIR / "invest_sop"
INVEST_REPORTS_DIR.mkdir(parents=True, exist_ok=True)

API_TIMEOUT = 30
