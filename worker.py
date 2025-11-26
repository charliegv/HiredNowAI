# ---- CLEAN + CORRECTED WORKER ----

import asyncio
import asyncpg
import os
import logging
import json
import aiohttp
import uuid

# from bots.greenhouse import GreenhouseBot
from bots.lever import LeverBot
# from bots.smartrec import SmartRecruitersBot
# from bots.workable import WorkableBot

from utils.description_parser import html_to_text
from utils.job_description_fetcher import scrape_job_description
from utils.cv_builder import generate_custom_cv
from utils.cv_loader import load_cv_text
from utils.s3_uploader import upload_to_s3


logging.basicConfig(level=logging.INFO, format="[Worker] %(message)s")


async def get_db():
    return await asyncpg.create_pool(os.getenv("DATABASE_URL"))


CLAIM_QUERY = """
WITH next_task AS (
    SELECT id
    FROM applications
    WHERE status = 'pending'
    ORDER BY created_at
    LIMIT 1
    FOR UPDATE SKIP LOCKED
)
UPDATE applications
SET status = 'processing', updated_at = now()
WHERE id = (SELECT id FROM next_task)
RETURNING id, user_id, job_id;
"""


async def load_job(pool, job_id):
    return await pool.fetchrow("SELECT * FROM jobs WHERE id = $1", job_id)


async def load_user_profile(pool, user_id):
    return await pool.fetchrow("SELECT * FROM profile WHERE user_id = $1", user_id)


def get_bot(ats_type):
    bots = {
        # "greenhouse": GreenhouseBot(),
        "lever": LeverBot(),
        # "smartrecruiters": SmartRecruitersBot(),
        # "workable": WorkableBot(),
    }
    return bots.get(ats_type.lower())


async def download_cv_to_tmp(cv_url: str) -> str:
    filename = f"/tmp/{uuid.uuid4()}.docx"

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(cv_url, ssl=False) as resp:
                if resp.status != 200:
                    raise Exception(f"HTTP {resp.status}")
                data = await resp.read()
        except Exception as e:
            raise Exception(f"Download error: {str(e)}")

    with open(filename, "wb") as f:
        f.write(data)

    return filename



async def mark_success(conn, app_id):
    await conn.execute("""
        UPDATE applications
        SET status = 'success', updated_at = now()
        WHERE id = $1
    """, app_id)


async def mark_failed(conn, app_id, error_msg):
    await conn.execute("""
        UPDATE applications
        SET status = 'failed', updated_at = now(), error_message = $2
        WHERE id = $1
    """, app_id, error_msg[:500])


async def mark_retry(conn, app_id, error_msg):
    await conn.execute("""
        UPDATE applications
        SET status = 'retry', updated_at = now(), error_message = $2
        WHERE id = $1
    """, app_id, error_msg[:500])


async def worker_loop():
    logging.info("Worker started")
    pool = await get_db()

    while True:
        async with pool.acquire() as conn:

            task = await conn.fetchrow(CLAIM_QUERY)
            if not task:
                await asyncio.sleep(3)
                continue

            app_id = task["id"]
            user_id = task["user_id"]
            job_id = task["job_id"]

            logging.info(f"Processing application {app_id} for user {user_id}")

            job = await load_job(conn, job_id)
            user = await load_user_profile(conn, user_id)

            if not job or not user:
                await mark_failed(conn, app_id, "Missing job or user profile data")
                continue

            ats_type = job.get("source_ats")
            if not ats_type:
                await mark_failed(conn, app_id, "ATS type missing")
                continue

            bot = get_bot(ats_type)
            if not bot:
                await mark_failed(conn, app_id, f"No ATS bot for {ats_type}")
                continue

            # 1 - Job description
            description_html = job["description"]
            if not description_html or len(description_html.strip()) < 50:
                try:
                    apply_url = job.get("apply_url") or job.get("job_url") or job.get("url")

                    if not apply_url:
                        await mark_failed(conn, app_id, "No apply_url or job_url found on job")
                        continue

                    scraped_html = await scrape_job_description(apply_url)

                    await conn.execute("""
                        UPDATE jobs SET description=$1 WHERE id=$2
                    """, scraped_html, job_id)
                    description_html = scraped_html
                except Exception as e:
                    await mark_retry(conn, app_id, f"JD scrape failed: {str(e)}")
                    continue

            job_text = html_to_text(description_html)

            # 2 - Base CV load
            try:
                base_cv_text = await load_cv_text(user["cv_location"])
            except Exception as e:
                await mark_failed(conn, app_id, f"CV load failed: {str(e)}")
                continue

            # 3 - Generate tailored CV
            try:
                cv_json, custom_cv_path = await generate_custom_cv(
                    base_cv_text=base_cv_text,
                    job_text=job_text,
                    user=user
                )
            except Exception as e:
                await mark_retry(conn, app_id, f"CV generation error: {str(e)}")
                continue

            # Store JSON variant
            try:
	            await conn.execute("""
                    UPDATE applications
                    SET cv_variant = $1
                    WHERE id = $2
                """, json.dumps(cv_json), app_id)
            except Exception as e:
                await mark_failed(conn, app_id, f"Failed saving CV variant JSON: {str(e)}")
                continue

            # 4 - Upload DOCX to S3
            try:
                cv_url = upload_to_s3(custom_cv_path)
            except Exception as e:
                await mark_retry(conn, app_id, f"CV upload failed: {str(e)}")
                continue

            # 5 - Apply ONCE
            # 5 - Download S3 CV variant to /tmp
            try:
	            local_cv_path = await download_cv_to_tmp(cv_url)
            except Exception as e:
	            await mark_retry(conn, app_id, f"CV download failed: {str(e)}")
	            continue
            try:
                result = await bot.apply(job, user, local_cv_path)

                # Save screenshot URL if bot returned one
                if hasattr(result, "screenshot_url") and result.screenshot_url:
                    await conn.execute("""
                        UPDATE applications
                        SET screenshot_url = $1
                        WHERE id = $2
                    """, result.screenshot_url, app_id)

                # Then continue with the status handling
                if result.status == "success":
                    await mark_success(conn, app_id)

                elif result.status == "retry":
                    await mark_retry(conn, app_id, result.message)

                else:
                    await mark_failed(conn, app_id, result.message)

            except Exception as e:
                logging.exception(f"Application {app_id} crashed")
                await mark_retry(conn, app_id, str(e))

        await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(worker_loop())
    except KeyboardInterrupt:
        logging.info("Worker shut down")
