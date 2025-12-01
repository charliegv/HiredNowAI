"""
process_xml_feed.py

Daily job ingestion:
- Fetch active feeds from the "feeds" table
- Download XML via S3 or web
- Stream parse XML jobs
- Upsert into the jobs table in Neon Postgres
"""

import os
import sys
import logging
import time
import requests
from typing import List, Tuple, Optional
from dotenv import load_dotenv

load_dotenv()

import psycopg2
from psycopg2.extras import execute_batch
import xml.etree.ElementTree as ET
import re
import tempfile

SALARY_PATTERN = re.compile(
    r'(\$|£)?\s?(\d{2,3}[,\d]{0,3})(?:k)?\s*(?:-|to|–)\s*(\d{2,3}[,\d]{0,3})(?:k)?',
    re.IGNORECASE
)

SINGLE_SALARY_PATTERN = re.compile(
    r'(\$|£)?\s?(\d{2,3}[,\d]{0,3})(?:k)',
    re.IGNORECASE
)

# -------------------------------------------------------------------
# Config
# -------------------------------------------------------------------

BATCH_SIZE = 500

DB_URL = os.environ.get("DATABASE_URL")
AWS_REGION = os.environ.get("AWS_REGION")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# global db connection reference for geocoding
db_conn_global = None

# in memory geocode cache: city_key -> (lat, lon)
geocode_dict: dict[str, Tuple[Optional[float], Optional[float]]] = {}

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
# Database helpers
# -------------------------------------------------------------------

def get_db():
    if not DB_URL:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(DB_URL)


def download_large_file(url):
    logger.info("Downloading large XML file: %s", url)

    tmp = tempfile.NamedTemporaryFile(delete=False)
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        for chunk in r.iter_content(chunk_size=1024 * 1024 * 5):  # 5 MB chunks
            if chunk:
                tmp.write(chunk)

    tmp.flush()
    tmp.close()
    return tmp.name  # path to the file


def extract_salary_from_description(description: str):
    if not description:
        return None, None

    desc = description.replace("&nbsp;", " ")

    m = SALARY_PATTERN.search(desc)
    if m:
        currency = m.group(1) or ""
        low = m.group(2).replace(",", "")
        high = m.group(3).replace(",", "")

        if low.lower().endswith("k"):
            low = int(float(low[:-1]) * 1000)
        else:
            low = int(low)

        if high.lower().endswith("k"):
            high = int(float(high[:-1]) * 1000)
        else:
            high = int(high)

        return low, high

    m2 = SINGLE_SALARY_PATTERN.search(desc)
    if m2:
        val = m2.group(2).replace(",", "")
        if val.lower().endswith("k"):
            val = int(float(val[:-1]) * 1000)
        else:
            val = int(val)
        return val, val

    return None, None


def fetch_active_feeds(conn):
    sql = """
        SELECT feed_name, url, feed_mode, feed_format
        FROM feeds
        WHERE is_active = true
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()  # list of tuples


def upsert_jobs(conn, rows: List[Tuple]) -> int:
    if not rows:
        return 0

    sql = """
        INSERT INTO jobs (
            job_url,
            title,
            company,
            description,
            city,
            state,
            country,
            latitude,
            longitude,
            is_remote,
            salary_min,
            salary_max,
            posted_at,
            source_ats,
            source_job_id,
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
        ON CONFLICT (job_url, city, state, country, source_job_id) DO UPDATE SET
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
            source_job_id = EXCLUDED.source_job_id,
            feed_source = EXCLUDED.feed_source
    """

    with conn.cursor() as cur:
        execute_batch(cur, sql, rows, page_size=BATCH_SIZE)
    conn.commit()
    return len(rows)

# -------------------------------------------------------------------
# S3 / Web streaming
# -------------------------------------------------------------------

def s3_stream(url: str):
    logger.info("Downloading S3 XML via HTTP: %s", url)
    resp = requests.get(url, stream=True, timeout=30)
    resp.raise_for_status()
    return resp.raw


def web_stream(url: str):
    logger.info("Downloading XML from web: %s", url)
    resp = requests.get(url, stream=True, timeout=30)
    resp.raise_for_status()
    return resp.raw

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def safe_int(v: Optional[str]):
    if not v:
        return None
    try:
        return int(v)
    except Exception:
        return None


def safe_float(v: Optional[str]):
    if not v:
        return None
    try:
        return float(v)
    except Exception:
        return None


def text(elem, tag: str) -> Optional[str]:
    child = elem.find(tag)
    return child.text.strip() if child is not None and child.text else None


def normalize_country(raw: Optional[str]) -> str:
    """
    Normalize country names and codes to a consistent format, for example:
    United States, USA, U S A -> us
    United Kingdom, UK, Great Britain -> gb
    two letter codes are lowercased
    everything else is just lowercased
    """
    if not raw:
        return ""
    c = raw.strip().lower()
    if not c:
        return ""

    us_aliases = {
        "united states",
        "united states of america",
        "usa",
        "u.s.a.",
        "u.s.",
        "america",
        "us",
    }

    gb_aliases = {
        "united kingdom",
        "great britain",
        "gb",
        "g.b.",
        "uk",
        "u.k.",
        "england",
        "scotland",
        "wales",
        "northern ireland",
    }

    if c in us_aliases:
        return "us"
    if c in gb_aliases:
        return "gb"

    if len(c) == 2:
        return c

    return c


# -------------------------------------------------------------------
# Geocoding with cache and Nominatim fallback
# -------------------------------------------------------------------

def make_city_key(city: str, state: str, country: str) -> str:
    return f"{city}|{state}|{country}".lower().strip()


def geocode_lookup(conn, city: str, state: str, country: str):
    """
    Step 1: check in memory cache
    Step 2: if missing, query geocode_cache table only when inserting new value
    Step 3: if still missing, query Nominatim
    Step 4: store in geocode_cache and in memory dict
    """
    global geocode_dict

    if not city or city == "REMOTE" or country not in ("us", "gb"):
        return None, None

    city_key = make_city_key(city, state, country)

    # 1 in memory cache lookup
    if city_key in geocode_dict:
        return geocode_dict[city_key]

    # 2 Nominatim
    nom_friendly_country = country if country != "gb" else "uk"
    params = {
        "q": f"{city}, {state}, {nom_friendly_country}",
        "format": "json",
        "limit": 1,
    }

    lat = None
    lon = None

    try:
        # Nominatim polite rate limit
        time.sleep(1)

        r = requests.get(
            NOMINATIM_URL,
            params=params,
            timeout=10,
            headers={"User-Agent": "HiredNowAI/1.0 (contact: charlie@hirednowai.com)"},
        )
        r.raise_for_status()
        results = r.json()
        if results:
            lat = float(results[0]["lat"])
            lon = float(results[0]["lon"])
        else:
            logger.warning("No geocode result for %s", city_key)
            lat, lon = 0.0, 0.0
    except Exception as e:
        logger.warning("Geocode failed for %s: %s", city_key, e)

    # 3 store in db cache
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO geocode_cache (city_key, lat, lon)
            VALUES (%s, %s, %s)
            ON CONFLICT (city_key) DO NOTHING
            """,
            (city_key, lat, lon),
        )
        conn.commit()

    # 4 update in memory cache
    geocode_dict[city_key] = (lat, lon)

    return lat, lon

# -------------------------------------------------------------------
# XML parsing logic
# -------------------------------------------------------------------

def parse_workable_job(elem, feed_format) -> Tuple:
    def get(tag):
        child = elem.find(tag)
        return child.text.strip() if child is not None and child.text else None

    job_url = get("url")
    title = get("title")
    company = get("company")
    description = get("description")

    city_raw = (get("city") or "").strip()
    country_raw_original = (get("country") or "").strip()
    country_raw = normalize_country(country_raw_original)
    state = (get("state") or "").strip()

    remote_flag = get("remote")
    is_remote = bool(remote_flag and remote_flag.lower() == "true")

    if city_raw:
        city = city_raw
    elif is_remote:
        city = "REMOTE"
    elif country_raw:
        city = f"NO_CITY_{country_raw}"
    else:
        city = "NO_CITY_UNKNOWN"

    country = country_raw

    # geocoding
    if is_remote:
        lat, lon = None, None
    else:
        lat, lon = geocode_lookup(db_conn_global, city, state, country)

    salary_min, salary_max = extract_salary_from_description(description)
    posted_at = get("date")

    source_ats = "workable"
    source_job_id = get("referencenumber") or "NO_ID"

    return [
        job_url,
        title,
        company,
        description,
        city,
        state,
        country,
        lat,
        lon,
        is_remote,
        salary_min,
        salary_max,
        posted_at,
        source_ats,
        source_job_id,
        None,
    ]


def parse_standard_job(elem, feed_format) -> Tuple:
    job_id = text(elem, "id")
    job_url = text(elem, "url")
    title = text(elem, "title")
    company = text(elem, "company")
    description = None

    city_raw = (text(elem, "city") or "").strip()
    country_raw_original = (text(elem, "country") or "").strip()
    country_raw = normalize_country(country_raw_original)
    state = (text(elem, "state") or "").strip()

    remote_text = text(elem, "remote")
    is_remote = bool(remote_text and remote_text.lower() == "true")

    if city_raw:
        city = city_raw
    elif is_remote:
        city = "REMOTE"
    elif country_raw:
        city = f"NO_CITY_{country_raw}"
    else:
        city = "NO_CITY_UNKNOWN"

    country = country_raw

    salary_raw = text(elem, "salary")
    salary_min = safe_int(salary_raw)
    salary_max = safe_int(salary_raw)

    posted_at = None

    xml_source_ats = text(elem, "ats")

    if xml_source_ats:
        source_ats = xml_source_ats
    elif feed_format != "standard":
        source_ats = feed_format
    else:
        source_ats = "standard"

    source_job_id = job_id or "NO_ID"

    if not is_remote:
        is_remote = (
            (city and "remote" in city.lower())
            or (state and "remote" in state.lower())
            or (country and "remote" in country.lower())
        )

    # geocoding
    if is_remote:
        lat, lon = None, None
    else:
        lat, lon = geocode_lookup(db_conn_global, city, state, country)

    return [
        job_url,
        title,
        company,
        description,
        city,
        state,
        country,
        lat,
        lon,
        is_remote,
        salary_min,
        salary_max,
        posted_at,
        source_ats,
        source_job_id,
        None,
    ]


def iter_standard_jobs(stream, feed_source: str, fmt: str):
    context = ET.iterparse(stream, events=("end",))
    _, root = next(context)

    for event, elem in context:
        if elem.tag == "job":
            row = parse_standard_job(elem, fmt)
            row[-1] = feed_source
            yield tuple(row)
            root.clear()


def iter_workable_jobs(file_path, feed_source: str, fmt: str):
    context = ET.iterparse(file_path, events=("end",))
    _, root = next(context)

    for event, elem in context:
        if elem.tag != "job":
            continue

        row = parse_workable_job(elem, fmt)

        job_url, title, company, description, city, state, country, *_ = row

        country_code = (country or "").strip().upper()

        remote_flag = text(elem, "remote")
        is_remote = bool(remote_flag and remote_flag.lower() == "true")

        if not (country_code in ("GB", "US") or is_remote):
            elem.clear()
            root.clear()
            continue

        row[-1] = feed_source

        yield tuple(row)

        elem.clear()
        root.clear()

# -------------------------------------------------------------------
# Main feed processor
# -------------------------------------------------------------------

def process_feed(conn, feed_name, url, mode, fmt) -> int:
    logger.info("Processing feed: %s (%s, %s)", feed_name, mode, fmt)

    # Workable is large, use temp file
    if fmt == "workable":
        file_path = download_large_file(url)
        try:
            iterator = iter_workable_jobs(file_path, feed_name, fmt)
            total = 0
            batch: List[Tuple] = []

            for row in iterator:
                job_url, title, *_ = row
                if not job_url or not title:
                    continue

                batch.append(row)
                if len(batch) >= BATCH_SIZE:
                    upserted = upsert_jobs(conn, batch)
                    total += upserted
                    logger.info(
                        "Upserted %s rows (running total %s) for feed %s",
                        upserted,
                        total,
                        feed_name,
                    )
                    batch = []

            if batch:
                upserted = upsert_jobs(conn, batch)
                total += upserted
                logger.info(
                    "Final upsert of %s rows (total %s) for feed %s",
                    upserted,
                    total,
                    feed_name,
                )

            logger.info("Feed %s complete. Total rows upserted=%s", feed_name, total)
            return total

        finally:
            try:
                os.remove(file_path)
                logger.info("Deleted temp file: %s", file_path)
            except OSError:
                logger.warning("Could not delete temp file: %s", file_path)

    # Standard and other supported formats via stream
    if mode == "s3":
        stream = s3_stream(url)
    elif mode == "web":
        stream = web_stream(url)
    else:
        logger.error("Unknown feed_mode=%s", mode)
        return 0

    if fmt == "standard":
        iterator = iter_standard_jobs(stream, feed_name, fmt)
    else:
        logger.error("Unsupported feed_format=%s", fmt)
        return 0

    total = 0
    batch: List[Tuple] = []

    for row in iterator:
        job_url, title, *_ = row

        if not job_url or not title:
            continue

        batch.append(row)
        if len(batch) >= BATCH_SIZE:
            upserted = upsert_jobs(conn, batch)
            total += upserted
            logger.info(
                "Upserted %s rows (running total %s) for feed %s",
                upserted,
                total,
                feed_name,
            )
            batch = []

    if batch:
        upserted = upsert_jobs(conn, batch)
        total += upserted
        logger.info(
            "Final upsert of %s rows (total %s) for feed %s",
            upserted,
            total,
            feed_name,
        )

    logger.info("Feed %s complete. Total rows upserted=%s", feed_name, total)
    return total

# -------------------------------------------------------------------
# Entry point
# -------------------------------------------------------------------

def main():
    logger.info("Starting XML ingestion job...")

    with get_db() as conn:
        global db_conn_global, geocode_dict
        db_conn_global = conn

        # preload geocode_cache into memory
        with conn.cursor() as cur:
            cur.execute("SELECT city_key, lat, lon FROM geocode_cache")
            rows = cur.fetchall()
            geocode_dict = {row[0]: (row[1], row[2]) for row in rows}
        logger.info("Loaded %s geocodes into memory cache", len(geocode_dict))

        feeds = fetch_active_feeds(conn)
        if not feeds:
            logger.warning("No active feeds found in the feeds table.")
            return

        logger.info("Found %s active feeds", len(feeds))

        total_all = 0
        for feed_name, url, mode, fmt in feeds:
            try:
                total_all += process_feed(conn, feed_name, url, mode, fmt)
            except Exception as e:
                logger.exception("Error processing feed %s: %s", feed_name, str(e))

        logger.info("All feeds completed. Total jobs upserted=%s", total_all)


if __name__ == "__main__":
    main()