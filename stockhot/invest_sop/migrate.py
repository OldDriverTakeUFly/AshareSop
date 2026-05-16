"""Standalone migration script for invest_sop tables.

Usage: python stockhot/invest_sop/migrate.py
"""

from stockhot.storage.database import init_database


def main() -> None:
    init_database()
    print("invest_sop tables migrated successfully.")


if __name__ == "__main__":
    main()
