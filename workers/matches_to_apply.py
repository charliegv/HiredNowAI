import asyncio
import asyncpg
import os
import hashlib
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

MAX_APPLICATIONS_PER_DAY = 30


async def process_auto_applications():
    db_url = os.getenv("DATABASE_URL")
    conn = await asyncpg.connect(db_url)

    print("[AUTO WORKER] Starting auto application enqueue")

    # 1. Select active auto users correctly
    users = await conn.fetch("""
        SELECT p.user_id
        FROM profile p
        WHERE p.application_mode = 'auto'
          AND p.onboarding_complete = TRUE
          AND p.is_active = TRUE
    """)

    print(f"[AUTO WORKER] Found {len(users)} eligible auto users")

    for row in users:
        user_id = row["user_id"]
        print(f"\n[AUTO WORKER] Processing user {user_id}")

        # 2. Count how many applications this user already has today
        todays_count = await conn.fetchval("""
            SELECT COUNT(*)
            FROM applications
            WHERE user_id = $1
              AND DATE(created_at) = CURRENT_DATE
        """, user_id)

        print(f"[AUTO WORKER] User {user_id} has {todays_count} applications today")

        if todays_count >= MAX_APPLICATIONS_PER_DAY:
            print(f"[AUTO WORKER] Daily limit reached for user {user_id}, skipping")
            continue

        remaining_quota = MAX_APPLICATIONS_PER_DAY - todays_count
        print(f"[AUTO WORKER] User {user_id} can receive {remaining_quota} more applications today")

        # 3. Fetch only the remaining number of matches
        matches = await conn.fetch("""
            SELECT 
                m.job_id,
                j.job_url,
                j.title,
                j.company,
                j.city,
                j.salary_min,
                j.salary_max
            FROM matches m
            JOIN profile p ON p.user_id = m.user_id
            JOIN jobs j ON j.id = m.job_id
            LEFT JOIN applications a
              ON a.user_id = m.user_id
             AND a.job_id = j.id
            WHERE m.user_id = $1
              AND a.id IS NULL
              AND p.is_active = TRUE
              AND p.application_mode = 'auto'
              and j.expires_at >= now()
              AND p.onboarding_complete = TRUE
                              AND j.company NOT IN (
			        SELECT j2.company
			        FROM applications a2
			        JOIN jobs j2 ON j2.id = a2.job_id
			        WHERE a2.user_id = $1
			  )
            ORDER BY m.score DESC NULLS LAST
            LIMIT $2
        """, user_id, remaining_quota)

        if not matches:
            print(f"[AUTO WORKER] No new matches for user {user_id}")
            continue

        print(f"[AUTO WORKER] Preparing {len(matches)} applications for user {user_id}")

        now = datetime.utcnow()
        insert_rows = []

        for m in matches:
            salary_min = m["salary_min"]
            salary_max = m["salary_max"]

            if salary_min and salary_max:
                salary = f"{salary_min} - {salary_max}"
            elif salary_min:
                salary = str(salary_min)
            else:
                salary = None

            job_url_hash = hashlib.sha256(m["job_url"].encode()).hexdigest()

            insert_rows.append((
                user_id,
                m["job_url"],
                job_url_hash,
                m["title"],
                m["company"],
                m["city"],
                salary,
                "pending",
                now,
                now,
                m["job_id"]
            ))

        # Insert apps (deduped with ON CONFLICT)
        await conn.executemany("""
            INSERT INTO applications (
                user_id,
                job_url,
                job_url_hash,
                job_title,
                company,
                location,
                salary,
                status,
                created_at,
                updated_at,
                job_id
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            ON CONFLICT (user_id, job_url_hash) DO NOTHING
        """, insert_rows)

        print(f"[AUTO WORKER] Inserted {len(insert_rows)} applications for user {user_id}")

    await conn.close()
    print("\n[AUTO WORKER] Done\n")


if __name__ == "__main__":
    asyncio.run(process_auto_applications())
