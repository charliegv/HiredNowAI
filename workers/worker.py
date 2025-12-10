# ---- CLEAN + CORRECTED WORKER ----
import os
import sys

print("[DEBUG] __file__:", __file__)
print("[DEBUG] cwd:", os.getcwd())
print("[DEBUG] initial sys.path:", sys.path)
PROJECT_ROOT = "/opt/render/project/src"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
import json
import aiohttp
import uuid

import asyncio
import asyncpg


from bots.greenhouse import GreenhouseBot
# from bots.lever import LeverBot
# from bots.smartrec import SmartRecruitersBot
from bots.workable import WorkableBot

from utils.description_parser import html_to_text
from utils.job_description_fetcher import scrape_job_description
from utils.cv_builder import generate_custom_cv
from utils.cv_loader import load_cv_text
from utils.s3_uploader import upload_to_s3
from dotenv import load_dotenv

load_dotenv()


logging.basicConfig(level=logging.INFO, format="[Worker] %(message)s")


async def get_db():
    return await asyncpg.create_pool(os.getenv("DATABASE_URL"))


CLAIM_QUERY = """
WITH next_task AS (
    SELECT id
    FROM applications
    WHERE status = 'pending'
    ORDER BY RANDOM()
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
         "greenhouse": GreenhouseBot(),
        # "lever": LeverBot(),
        # "smartrecruiters": SmartRecruitersBot(),
        "workable": WorkableBot(),
    }
    return bots.get(ats_type.lower())


async def download_cv_to_tmp(cv_url: str, custom_cv_name) -> str:
    filename = f"/tmp/{custom_cv_name}"

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



async def mark_success(pool, app_id):
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE applications
            SET status = 'success', updated_at = now()
            WHERE id = $1
        """, app_id)


async def mark_failed(pool, app_id, error_msg):
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE applications
            SET status = 'failed', updated_at = now(), error_message = $2
            WHERE id = $1
        """, app_id, error_msg[:500])


async def mark_retry(pool, app_id, error_msg):
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE applications
            SET status = 'retry', updated_at = now(), error_message = $2
            WHERE id = $1
        """, app_id, error_msg[:500])

async def mark_manual_required(pool, app_id, error_msg, cv_url=None):
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE applications
            SET status = 'manual_required',
                updated_at = now(),
                error_message = $2,
                cv_variant_url = COALESCE($3, cv_variant_url)
            WHERE id = $1
        """, app_id, error_msg[:500], cv_url)



async def worker_loop():
    logging.info("Worker started")
    pool = await get_db()
    CV_GENERATION_TEST = os.getenv("CV_GENERATION_TEST", "false").lower() == "true"

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
                await mark_manual_required(pool, app_id, "Missing job or user profile — manual apply required")
                continue

            ats_type = job.get("source_ats")
            if not ats_type:
                await mark_manual_required(pool, app_id, "ATS type Missing — manual apply required")
                continue

            bot = get_bot(ats_type)
            if not bot:
                # Unsupported ATS → manual apply needed
                await mark_manual_required(pool, app_id, f"Unsupported ATS: {ats_type}")
                continue

            # 1 - Job description
            description_html = job["description"]
            if not description_html or len(description_html.strip()) < 50:
                try:
                    apply_url = job.get("apply_url") or job.get("job_url") or job.get("url")

                    if not apply_url:
                        await mark_failed(pool, app_id, "No apply_url or job_url found on job")
                        continue

                    scraped_html = await scrape_job_description(apply_url)

                    await conn.execute("""
                        UPDATE jobs SET description=$1 WHERE id=$2
                    """, scraped_html, job_id)
                    description_html = scraped_html
                except Exception as e:
                    await mark_manual_required(pool, app_id, f"JD scrape failed: {str(e)}", cv_url=None)
                    continue

            job_text = html_to_text(description_html)

            # 2 - Base CV load
            try:
                base_cv_text = str(user["ai_cv_data"])
            except Exception as e:
                await mark_manual_required(pool, app_id, "CV load failed — please apply manually", cv_url=None)
                continue

            # 3 - Generate tailored CV
            try:
                cv_json, custom_cv_path, custom_cv_name = await generate_custom_cv(
                    base_cv_text=base_cv_text,
                    job_text=job_text,
                    user=user
                )
            except Exception as e:
                await mark_retry(pool, app_id, f"CV generation error: {str(e)}")
                continue

            # Store JSON variant
            try:
                await conn.execute("""
                    UPDATE applications
                    SET cv_variant = $1
                    WHERE id = $2
                """, json.dumps(cv_json), app_id)
            except Exception as e:
                await mark_failed(pool, app_id, f"Failed saving CV variant JSON: {str(e)}")
                continue

            # 4 - Upload DOCX to S3
            try:
                cv_url = upload_to_s3(custom_cv_path, folder="cv-variants", custom_filename=custom_cv_name)

                # Save CV file URL to applications.cv_variant_url
                await conn.execute("""
                    UPDATE applications
                    SET cv_variant_url = $1
                    WHERE id = $2
                """, cv_url, app_id)
                # ---- TEST MODE: stop here ----
                if CV_GENERATION_TEST:
                    logging.info("CV_GENERATION_TEST mode enabled - skipping ATS apply step")

                    await conn.execute("""
                        UPDATE applications
                        SET status = 'success',
                            cv_variant_url = $1,
                            cv_variant = $2,
                            updated_at = now()
                        WHERE id = $3
                    """, cv_url, json.dumps(cv_json), app_id)

                    continue  # Go to next job without applying


            except Exception as e:
                await mark_retry(pool, app_id, f"CV upload failed: {str(e)}")
                continue

            # 5 - Apply ONCE
            # 5 - Download S3 CV variant to /tmp
            try:
                local_cv_path = await download_cv_to_tmp(cv_url, custom_cv_name)
            except Exception as e:
                await mark_retry(pool, app_id, f"CV download failed: {str(e)}")
                continue
            try:
                result = await bot.apply(job, user, local_cv_path)
                logging.info(f"[Worker] Result for {app_id}: {result.status} — {result.message}")

                # Save screenshot URL if bot returned one
                if hasattr(result, "screenshot_url") and result.screenshot_url:
                    async with pool.acquire() as conn2:
                        await conn2.execute("""
                            UPDATE applications
                            SET screenshot_url = $1
                            WHERE id = $2
                        """, result.screenshot_url, app_id)

                if result.status == "success":
                    await mark_success(pool, app_id)

                elif result.status == "retry":
                    await mark_retry(pool, app_id, result.message)

                elif result.status == "manual_required":
                    await mark_manual_required(pool, app_id, result.message, cv_url=cv_url)

                else:
                    await mark_failed(pool, app_id, result.message)



            except Exception as e:
                logging.exception(f"Application {app_id} crashed")
                # If a CV variant exists, send the user to manual apply instead of retry loop
                await mark_manual_required(pool, app_id, f"Bot crashed: {str(e)}", cv_url=cv_url)



        await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(worker_loop())
    except KeyboardInterrupt:
        logging.info("Worker shut down")
