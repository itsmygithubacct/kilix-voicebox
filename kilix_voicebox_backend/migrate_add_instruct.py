"""
Database migration script to add instruct column to generations table.

Run this once to update existing databases:
    python -m kilix_voicebox_backend.migrate_add_instruct
"""

import sqlite3
import os
from pathlib import Path


def migrate():
    """Add instruct column to generations table if it doesn't exist."""
    # Get data directory
    data_dir = os.environ.get("VOICEBOX_DATA_DIR")
    if data_dir:
        db_path = Path(data_dir) / "voicebox.db"
    else:
        db_path = Path.cwd() / "data" / "voicebox.db"

    if not db_path.exists():
        print("Database unavailable; migration skipped")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Check if instruct column already exists
    cursor.execute("PRAGMA table_info(generations)")
    columns = [row[1] for row in cursor.fetchall()]

    if 'instruct' in columns:
        print("instruct column already exists, skipping migration")
        conn.close()
        return

    # Add instruct column
    print("Adding instruct column to generations table...")
    cursor.execute("ALTER TABLE generations ADD COLUMN instruct TEXT")
    conn.commit()
    conn.close()

    print("Migration complete!")


if __name__ == "__main__":
    migrate()
