import os
import sys
import time
import psycopg2
from psycopg2.extras import RealDictCursor

# Allow imports of matching.py
sys.path.insert(0, "/opt/render/project/src")

from matching import match_user

DATABASE_URL = os.getenv("DATABASE_URL")


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def claim_job(conn):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            UPDATE match_queue
            SET status = 'processing'
            WHERE id = (
                SELECT id FROM match_queue
                WHERE status = 'pending'
                ORDER BY created_at
                LIMIT 1 FOR UPDATE SKIP LOCKED
            )
            RETURNING id, user_id;
        """)
        return cur.fetchone()


def mark_done(conn, job_id):
    with conn.cursor() as cur:
        cur.execute("UPDATE match_queue SET status='done' WHERE id=%s", (job_id,))
        conn.commit()


def match_worker_loop():
    print("[MATCH WORKER] Started")

    while True:
        conn = get_conn()
        job = claim_job(conn)

        if not job:
            time.sleep(2)
            conn.close()
            continue

        print(f"[MATCH WORKER] Processing user {job['user_id']}")

        try:
            match_user(conn, job["user_id"])
            mark_done(conn, job["id"])
            print(f"[MATCH WORKER] Completed matching for user {job['user_id']}")

        except Exception as e:
            print(f"[MATCH WORKER] Error: {e}")

        conn.close()
        time.sleep(1)


if __name__ == "__main__":
    match_worker_loop()
