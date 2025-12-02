import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

from matching import match_user

def get_connection():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def get_all_user_ids(conn):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT user_id FROM profile WHERE onboarding_complete = true and is_active = true")
        rows = cur.fetchall()
        return [r["user_id"] for r in rows]


def run_daily_matching():
    print("[DAILY MATCH] Starting refresh...")

    conn = get_connection()
    user_ids = get_all_user_ids(conn)

    print(f"[DAILY MATCH] Found {len(user_ids)} onboarded users")

    for uid in user_ids:
        try:
            print(f"[DAILY MATCH] Matching user {uid}")
            # Reuse the same connection for performance
            match_user(conn, uid)
        except Exception as e:
            print(f"[DAILY MATCH] Error matching user {uid}: {e}")

    conn.close()
    print("[DAILY MATCH] Complete")


if __name__ == "__main__":
    run_daily_matching()
