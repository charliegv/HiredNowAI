import os
from math import radians, sin, cos, sqrt, atan2
from typing import Optional, List

import numpy as np
import psycopg2
from psycopg2.extras import RealDictCursor
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# ---------------------------------------------
# Normalize country
# ---------------------------------------------
def normalize_country(raw: Optional[str]) -> str:
    if not raw:
        return ""

    c = raw.strip().lower()

    us = {
        "usa",
        "us",
        "u.s.",
        "america",
        "united states",
        "united states of america",
    }
    gb = {
        "uk",
        "u.k.",
        "gb",
        "g.b.",
        "united kingdom",
        "great britain",
        "england",
        "scotland",
        "wales",
        "northern ireland",
    }

    if c in us:
        return "us"
    if c in gb:
        return "gb"

    if len(c) == 2:
        return c

    return c


# ---------------------------------------------
# Cosine similarity
# ---------------------------------------------
def cosine_similarity(a: List[float], b: List[float]) -> float:
    a_arr = np.array(a, dtype=np.float32)
    b_arr = np.array(b, dtype=np.float32)
    denom = (np.linalg.norm(a_arr) * np.linalg.norm(b_arr)) + 1e-9
    return float(np.dot(a_arr, b_arr) / denom)


# ---------------------------------------------
# Distance and location scoring
# ---------------------------------------------
def haversine(lat1: Optional[float], lon1: Optional[float],
              lat2: Optional[float], lon2: Optional[float]) -> Optional[float]:
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return None

    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)

    a = sin(dlat / 2.0) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2.0) ** 2
    c = 2.0 * atan2(sqrt(a), sqrt(1.0 - a))

    return R * c


def location_score(profile: dict, job: dict) -> float:
    if job.get("is_remote"):
        return 0.9

    dist = haversine(
        profile.get("latitude"),
        profile.get("longitude"),
        job.get("latitude"),
        job.get("longitude"),
    )

    if dist is None:
        return 0.3

    if dist <= 15:
        return 1.0
    if dist <= 30:
        return 0.85
    if dist <= 60:
        return 0.65
    if dist <= 100:
        return 0.45
    if dist <= 200:
        return 0.25
    return 0.1


# ---------------------------------------------
# Salary scoring
# ---------------------------------------------
def salary_score(profile: dict, job: dict) -> float:
    jmin = job.get("salary_min")
    jmax = job.get("salary_max")

    # Coerce to integers if possible
    try:
        jmin = int(jmin) if jmin is not None else None
    except Exception:
        jmin = None

    try:
        jmax = int(jmax) if jmax is not None else None
    except Exception:
        jmax = None

    if jmin is None or jmax is None:
        return 0.3

    tmin = profile.get("min_salary")
    tmax = profile.get("max_salary")

    try:
        tmin = int(tmin) if tmin is not None else 0
    except Exception:
        tmin = 0

    try:
        tmax = int(tmax) if tmax is not None else 9_999_999
    except Exception:
        tmax = 9_999_999

    overlap = min(jmax, tmax) - max(jmin, tmin)

    if overlap >= 0:
        return 1.0

    distance = abs(overlap)
    return max(0.15, 1.0 - (distance / 20_000.0))


# ---------------------------------------------
# Embeddings
# ---------------------------------------------
def generate_embedding(text: str) -> List[float]:
    resp = client.embeddings.create(
        model="text-embedding-3-small",
        input=text,
    )
    return resp.data[0].embedding


# ---------------------------------------------
# Main matching logic
# ---------------------------------------------
def match_user(conn, user_id: int, limit: int = 200):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Fetch profile by user_id but capture profile primary key as profile_id
        cur.execute(
            """
            SELECT
                id AS profile_id,
                user_id,
                job_titles,
                city,
                state,
                country,
                latitude,
                longitude,
                remote_preference,
                min_salary,
                max_salary,
                miles_distance,
                preference_embedding,
                application_mode
            FROM profile
            WHERE user_id = %s
            """,
            (user_id,),
        )
        profile = cur.fetchone()

        if not profile:
            print(f"[MATCH] No profile for user {user_id}")
            return

        profile["country"] = normalize_country(profile.get("country"))

        # Generate embedding if missing
        if profile.get("preference_embedding") is None:
            emb = generate_embedding(profile.get("job_titles") or "")
            cur.execute(
                """
                UPDATE profile
                SET preference_embedding = %s
                WHERE id = %s
                """,
                (emb, profile["profile_id"]),
            )
            conn.commit()
            profile["preference_embedding"] = emb

        user_emb = profile["preference_embedding"]

        # Fetch candidate jobs
        cur.execute(
            """
            SELECT
                id,
                job_url,
                title,
                description,
                city,
                state,
                country,
                latitude,
                longitude,
                is_remote,
                salary_min,
                salary_max,
                title_embedding,
                desc_embedding,
                company,
                posted_at
            FROM jobs
            WHERE (country = %s OR is_remote = true)
              AND posted_at >= NOW() - INTERVAL '60 days'
            """,
            (profile["country"],),
        )
        jobs = cur.fetchall()
        print(f"[MATCH] {len(jobs)} raw jobs for user {user_id}")

    # Distance filter settings
    max_miles = profile.get("miles_distance")
    max_km = max_miles * 1.60934 if max_miles and max_miles > 0 else None

    # Keyword list from job_titles
    keywords = [
        k.strip().lower()
        for k in (profile.get("job_titles") or "").split(",")
        if k.strip()
    ]

    scored = []

    for job in jobs:
        # Distance filter for non remote jobs
        if max_km and not job.get("is_remote"):
            dist = haversine(
                profile.get("latitude"),
                profile.get("longitude"),
                job.get("latitude"),
                job.get("longitude"),
            )
            if dist is None or dist > max_km:
                continue

        # Semantic similarity
        sem_title = 0.0
        if job.get("title_embedding"):
            sem_title = cosine_similarity(user_emb, job["title_embedding"])

        sem_desc = 0.0
        if job.get("desc_embedding"):
            sem_desc = cosine_similarity(user_emb, job["desc_embedding"])

        # Keyword based score
        kw_score = 0.0
        title_lower = (job.get("title") or "").lower()
        desc_lower = (job.get("description") or "").lower()
        for kw in keywords:
            if kw in title_lower:
                kw_score += 0.7
            elif kw in desc_lower:
                kw_score += 0.4

        # Location and salary
        loc = location_score(profile, job)
        sal = salary_score(profile, job)

        # Remote penalty if user does not want remote
        remote_penalty = 0.0
        if not profile.get("remote_preference") and job.get("is_remote"):
            remote_penalty = 0.4

        # Final score
        final_score = (
            sem_title * 0.45
            + sem_desc * 0.10
            + kw_score * 0.25
            + loc * 0.12
            + sal * 0.08
            - remote_penalty
        )

        scored.append((final_score, job))

    # Sort and keep best N
    scored.sort(key=lambda x: x[0], reverse=True)
    top_matches = scored[:limit]

    # Store matches, using profile_id as the foreign key value
    with conn.cursor() as cur:
        for score, job in top_matches:
            if score < 0.20:
                continue

            cur.execute(
                """
                INSERT INTO matches (user_id, job_url, job_id, score, is_remote)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id, job_url)
                DO UPDATE SET
                    score = EXCLUDED.score,
                    job_id = EXCLUDED.job_id,
                    is_remote = EXCLUDED.is_remote,
                    matched_at = NOW()
                """,
                (
                    profile["profile_id"],
                    job["job_url"],
                    job["id"],
                    score,
                    job.get("is_remote", False),
                ),
            )

    conn.commit()
    print(f"[MATCH] Stored {len(top_matches)} matches for user {user_id}")

    return top_matches
