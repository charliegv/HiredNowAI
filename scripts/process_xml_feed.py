import os
import sys
import logging
import time
import requests
from typing import List, Tuple, Optional
from dotenv import load_dotenv
import tempfile

load_dotenv()

import psycopg2
from psycopg2.extras import execute_batch
import xml.etree.ElementTree as ET
import re

# -------------------------------------------------------------------
# Config
# -------------------------------------------------------------------

BATCH_SIZE = 100                     # lower memory batches
DESCRIPTION_LIMIT = 5000             # max characters to keep
DOWNLOAD_CHUNK = 1024 * 256          # 256 KB chunks (safe buffer)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
DB_URL = os.environ.get("DATABASE_URL")

# lazy in-memory geocode cache
geocode_dict = {}

# keep global DB connection for reuse
db_conn_global = None

# -------------------------------------------------------------------
# Logging
# -------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# Regexes
# -------------------------------------------------------------------

SALARY_PATTERN = re.compile(
    r'(\$|£)?\s?(\d{2,3}[,\d]{0,3})(?:k)?\s*(?:-|to|–)\s*(\d{2,3}[,\d]{0,3})(?:k)?',
    re.IGNORECASE
)

SINGLE_SALARY_PATTERN = re.compile(
    r'(\$|£)?\s?(\d{2,3}[,\d]{0,3})(?:k)',
    re.IGNORECASE
)

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def get_db():
    if not DB_URL:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(DB_URL)


def extract_salary(description: str):
    if not description:
        return None, None
    text = description.replace("&nbsp;", " ")

    m = SALARY_PATTERN.search(text)
    if m:
        low = m.group(2).replace(",", "")
        high = m.group(3).replace(",", "")

        low = int(low) * 1000 if low.lower().endswith("k") else int(low)
        high = int(high) * 1000 if high.lower().endswith("k") else int(high)

        return low, high

    m2 = SINGLE_SALARY_PATTERN.search(text)
    if m2:
        val = m2.group(2).replace(",", "")
        val = int(val) * 1000 if val.lower().endswith("k") else int(val)
        return val, val

    return None, None


def text(elem, tag):
    child = elem.find(tag)
    return child.text.strip() if child is not None and child.text else None


def normalize_country(raw: Optional[str]) -> str:
    if not raw:
        return ""
    c = raw.lower().strip()

    us_aliases = {"united states", "united states of america", "usa", "u.s.a.", "u.s.", "america", "us"}
    gb_aliases = {
        "united kingdom", "great britain", "gb", "uk",
        "england", "scotland", "wales", "northern ireland"
    }

    if c in us_aliases:
        return "us"
    if c in gb_aliases:
        return "gb"
    if len(c) == 2:
        return c
    return c


def make_city_key(city: str, state: str, country: str):
    return f"{city}|{state}|{country}".lower().strip()


def geocode_lookup(conn, city: str, state: str, country: str):
    global geocode_dict

    if not city or city == "REMOTE" or country not in ("us", "gb"):
        return None, None

    key = make_city_key(city, state, country)

    # memory cache hit
    if key in geocode_dict:
        return geocode_dict[key]

    # DB lookup
    with conn.cursor() as cur:
        cur.execute("SELECT lat, lon FROM geocode_cache WHERE city_key = %s", (key,))
        row = cur.fetchone()

    if row:
        geocode_dict[key] = (row[0], row[1])
        return row[0], row[1]

    # Nominatim fallback
    try:
        time.sleep(1)
        resp = requests.get(
            NOMINATIM_URL,
            params={"q": f"{city}, {state}, {country}", "format": "json", "limit": 1},
            timeout=10,
            headers={"User-Agent": "HiredNowAI ingestion bot"}
        )
        resp.raise_for_status()
        res = resp.json()

        lat = float(res[0]["lat"]) if res else 0.0
        lon = float(res[0]["lon"]) if res else 0.0

    except Exception:
        lat, lon = 0.0, 0.0

    # store in DB
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO geocode_cache (city_key, lat, lon)
               VALUES (%s, %s, %s)
               ON CONFLICT (city_key) DO NOTHING""",
            (key, lat, lon)
        )
        conn.commit()

    geocode_dict[key] = (lat, lon)
    return lat, lon

# -------------------------------------------------------------------
# Resumable download (safe for XML)
# -------------------------------------------------------------------

def download_with_resume(url, tmp_path, chunk_size=DOWNLOAD_CHUNK, max_retries=20):
    """
    Fully resumable HTTP download — this avoids XML corruption issues
    caused by Workable's unreliable chunked transfer.
    """
    downloaded = 0

    if os.path.exists(tmp_path):
        downloaded = os.path.getsize(tmp_path)

    while True:
        headers = {}
        if downloaded > 0:
            headers["Range"] = f"bytes={downloaded}-"

        resp = requests.get(url, stream=True, timeout=(10, 300), headers=headers)
        resp.raise_for_status()

        try:
            with open(tmp_path, "ab") as f:
                for chunk in resp.iter_content(chunk_size=chunk_size):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)

            return tmp_path  # download completed

        except (requests.exceptions.ChunkedEncodingError,
                requests.exceptions.ConnectionError):
            logger.warning("Download interrupted at %s bytes, retrying...", downloaded)
            time.sleep(1)
            continue

# -------------------------------------------------------------------
# XML streaming parse from file (correct & low-memory)
# -------------------------------------------------------------------

def parse_xml_file(file_path, feed_name, fmt, conn):
    context = ET.iterparse(file_path, events=("end",))
    _, root = next(context)

    for event, elem in context:
        if elem.tag != "job":
            continue

        job_url = text(elem, "url")
        title = text(elem, "title")
        company = text(elem, "company")
        description = text(elem, "description")

        if description and len(description) > DESCRIPTION_LIMIT:
            description = description[:DESCRIPTION_LIMIT]

        city_raw = (text(elem, "city") or "").strip()
        country_raw = normalize_country(text(elem, "country"))
        state = (text(elem, "state") or "").strip()

        remote_flag = (text(elem, "remote") or "").lower()
        is_remote = remote_flag == "true"

        city = (
                city_raw
                or ("REMOTE" if is_remote else f"NO_CITY_{country_raw}" if country_raw else "NO_CITY_UNKNOWN")
        )

        lat, lon = (None, None)
        if not is_remote:
            lat, lon = geocode_lookup(conn, city, state, country_raw)

        salary_min, salary_max = extract_salary(description)
        posted_at = text(elem, "date")

        ats_tag = text(elem, "ats")
        if ats_tag:
            source_ats = ats_tag.strip().lower()
        else:
            source_ats = fmt if fmt != "standard" else "standard"

        # source_job_id also depends on feed
        # workable uses <referencenumber>, standard uses <id>
        ref = text(elem, "referencenumber")  # workable
        jid = text(elem, "id")  # standard

        source_job_id = ref or jid or "NO_ID"

        row = (
            job_url, title, company, description,
            city, state, country_raw,
            lat, lon,
            is_remote,
            salary_min, salary_max,
            posted_at,
            source_ats, source_job_id,
            feed_name
        )

        yield row

        elem.clear()
        root.clear()


# -------------------------------------------------------------------
# DB batch upsert
# -------------------------------------------------------------------

def upsert_jobs(conn, rows: List[Tuple]) -> int:
    if not rows:
        return 0

    sql = """
        INSERT INTO jobs (
            job_url, title, company, description,
            city, state, country,
            latitude, longitude,
            is_remote,
            salary_min, salary_max,
            posted_at,
            source_ats, source_job_id,
            feed_source
        )
        VALUES (
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s,
            %s,
            %s, %s,
            %s,
            %s, %s,
            %s
        )
        ON CONFLICT (job_url, city, state, country, source_job_id)
        DO UPDATE SET
            title = EXCLUDED.title,
            company = EXCLUDED.company,
            description = EXCLUDED.description,
            city = EXCLUDED.city,
            state = EXCLUDED.state,
            country = EXCLUDED.country,
            latitude = EXCLUDED.latitude,
            longitude = EXCLUDED.longitude,
            is_remote = EXCLUDED.is_remote,
            salary_min = EXCLUDED.salary_min,
            salary_max = EXCLUDED.salary_max,
            posted_at = EXCLUDED.posted_at,
            scraped_at = NOW(),
            source_ats = EXCLUDED.source_ats,
            feed_source = EXCLUDED.feed_source;
    """

    with conn.cursor() as cur:
        execute_batch(cur, sql, rows, page_size=BATCH_SIZE)

    conn.commit()
    return len(rows)

# -------------------------------------------------------------------
# Main feed processing
# -------------------------------------------------------------------

def process_feed(conn, feed_name, url, mode, fmt):
    logger.info("Processing feed: %s (%s, %s)", feed_name, mode, fmt)

    # always use resumable temp file for Workable XML
    tmp_file = tempfile.NamedTemporaryFile(delete=False).name

    try:
        download_with_resume(url, tmp_file)
        iterator = parse_xml_file(tmp_file, feed_name, fmt, conn)

        batch = []
        total = 0

        for row in iterator:
            job_url, title, *_ = row
            if not job_url or not title:
                continue

            batch.append(row)

            if len(batch) >= BATCH_SIZE:
                total += upsert_jobs(conn, batch)
                batch = []

        if batch:
            total += upsert_jobs(conn, batch)

        logger.info("Feed %s complete. Total upserted: %s", feed_name, total)
        return total

    finally:
        try:
            os.remove(tmp_file)
        except OSError:
            pass

# -------------------------------------------------------------------
# Entry Point
# -------------------------------------------------------------------

def fetch_active_feeds(conn):
    sql = "SELECT feed_name, url, feed_mode, feed_format FROM feeds WHERE is_active = true"
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def main():
    logger.info("Starting XML ingestion job...")

    with get_db() as conn:
        global db_conn_global
        db_conn_global = conn

        feeds = fetch_active_feeds(conn)
        logger.info("Found %s active feeds", len(feeds))

        total_all = 0
        for feed_name, url, mode, fmt in feeds:
            try:
                total_all += process_feed(conn, feed_name, url, mode, fmt)
            except Exception as e:
                logger.exception("Error processing feed %s: %s", feed_name, e)

        logger.info("All feeds complete. Total upserted = %s", total_all)


if __name__ == "__main__":
    main()
