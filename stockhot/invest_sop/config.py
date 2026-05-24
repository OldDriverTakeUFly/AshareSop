from stockhot.core.config import DB_PATH as INVEST_DB_PATH
from stockhot.core.config import REPORTS_DIR

INVEST_REPORTS_DIR = REPORTS_DIR / "invest_sop"
INVEST_REPORTS_DIR.mkdir(parents=True, exist_ok=True)

API_TIMEOUT = 30

from stockhot.storage.database import get_connection

DEFAULT_SECTOR_RULE = {"stop_loss_pct": -0.12, "target_pct": 0.20}


def get_sector_rule(sector: str) -> dict:
    """Return stop-loss/target rules for *sector*, falling back to 'default'."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT stop_loss_pct, target_pct FROM invest_sector_rules WHERE sector = ?",
            (sector,),
        ).fetchone()
        if row:
            return {"stop_loss_pct": row[0], "target_pct": row[1]}
        row = conn.execute(
            "SELECT stop_loss_pct, target_pct FROM invest_sector_rules WHERE sector = 'default'",
        ).fetchone()
        if row:
            return {"stop_loss_pct": row[0], "target_pct": row[1]}
        return DEFAULT_SECTOR_RULE
    finally:
        conn.close()
