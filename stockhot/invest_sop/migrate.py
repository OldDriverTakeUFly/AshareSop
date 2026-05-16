"""Standalone migration script for invest_sop tables.

Usage: python stockhot/invest_sop/migrate.py
"""

from stockhot.storage.database import get_connection, init_database


def migrate_add_us_vix() -> None:
    conn = get_connection()
    try:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(invest_overseas_market)")]
        if "us_vix" not in cols:
            conn.execute("ALTER TABLE invest_overseas_market ADD COLUMN us_vix REAL")
            conn.commit()
            print("Added us_vix column to invest_overseas_market.")
        else:
            print("us_vix column already exists in invest_overseas_market.")
    finally:
        conn.close()


def main() -> None:
    init_database()
    migrate_add_us_vix()
    print("invest_sop tables migrated successfully.")


if __name__ == "__main__":
    main()
