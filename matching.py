import os
import ast
from math import radians, sin, cos, sqrt, atan2
from typing import Optional, List

import numpy as np
import psycopg2
from psycopg2.extras import RealDictCursor
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# -----------------------------
# Helpers
# -----------------------------
def normalize_country(raw: Optional[str]) -> str:
    if not raw:
        return ""
    c = raw.strip().lower()
    us = {"usa","us","u.s.","america","united states","united states of america"}
    gb = {"uk","u.k.","gb","g.b.","united kingdom","great britain","england","scotland","wales","northern ireland"}
    if c in us: return "us"
    if c in gb: return "gb"
    return c if len(c) == 2 else c


def cosine_similarity(a: List[float], b: List[float]) -> float:
    a_arr = np.array(a, dtype=np.float32)
    b_arr = np.array(b, dtype=np.float32)
    denom = (np.linalg.norm(a_arr) * np.linalg.norm(b_arr)) + 1e-9
    return float(np.dot(a_arr, b_arr) / denom)


def parse_emb(val):
    """Parse embeddings safely regardless of type."""
    if val is None:
        return None
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            parsed = ast.literal_eval(val)
            return parsed if isinstance(parsed, list) else None
        except:
            return None
    return None


def haversine(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return None
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    return 2 * atan2(sqrt(a), sqrt(1.0 - a)) * R


def location_score(profile, job):
    if job.get("is_remote"): return 0.9
    dist = haversine(profile["latitude"], profile["longitude"],
                     job["latitude"], job["longitude"])
    if dist is None: return 0.3
    if dist <= 15: return 1.0
    if dist <= 30: return 0.85
    if dist <= 60: return 0.65
    if dist <= 100: return 0.45
    if dist <= 200: return 0.25
    return 0.1


def salary_score(profile, job):
    try: jmin = int(job.get("salary_min")) if job.get("salary_min") is not None else None
    except: jmin = None
    try: jmax = int(job.get("salary_max")) if job.get("salary_max") is not None else None
    except: jmax = None
    if jmin is None or jmax is None:
        return 0.3
    try: tmin = int(profile.get("min_salary") or 0)
    except: tmin = 0
    try: tmax = int(profile.get("max_salary") or 9999999)
    except: tmax = 9999999
    overlap = min(jmax, tmax) - max(jmin, tmin)
    if overlap >= 0: return 1.0
    return max(0.15, 1.0 - abs(overlap)/20000)


def generate_embedding(text: str):
    resp = client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return resp.data[0].embedding


# ----------------------------------------------------------------
# MAIN MATCHING (STREAMING + PARSED EMBEDDINGS + LOW MEMORY)
# ----------------------------------------------------------------
def match_user(conn, user_id: int, limit=200):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT
                id AS profile_id, user_id, job_titles, city, state, country,
                latitude, longitude, remote_preference, min_salary, max_salary,
                miles_distance, preference_embedding, application_mode
            FROM profile
            WHERE user_id = %s
        """, (user_id,))
        profile = cur.fetchone()

        if not profile:
            print(f"[MATCH] No profile for user {user_id}")
            return

        profile["country"] = normalize_country(profile["country"])

        # ensure embedding exists
        if profile["preference_embedding"] is None:
            emb = generate_embedding(profile["job_titles"] or "")
            cur.execute(
                "UPDATE profile SET preference_embedding=%s WHERE id=%s",
                (emb, profile["profile_id"])
            )
            conn.commit()
            profile["preference_embedding"] = emb

        user_emb = profile["preference_embedding"]

    # --------------------------
    # STREAM JOBS INSTEAD OF LOADING ALL
    # --------------------------
    scored = []
    keywords = [
        k.strip().lower()
        for k in (profile["job_titles"] or "").split(",")
        if k.strip()
    ]

    max_miles = profile.get("miles_distance")
    max_km = max_miles * 1.60934 if max_miles and max_miles > 0 else None

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.itersize = 2000
        cur.execute("""
            SELECT
                id, job_url, title, description, city, state, country,
                latitude, longitude, is_remote, salary_min, salary_max,
                title_embedding, desc_embedding, company, posted_at
            FROM jobs
            WHERE (country=%s OR is_remote=true)
              AND expires_at >= NOW()
              and (source_ats = 'workable' or (source_ats = 'greenhouse' and job_url like '%%job-boards.greenhouse%%'))
        """, (profile["country"],))

        for job in cur:
            # distance filter
            if max_km and not job["is_remote"]:
                dist = haversine(
                    profile["latitude"], profile["longitude"],
                    job["latitude"], job["longitude"]
                )
                if dist is None or dist > max_km:
                    continue

            # parse embeddings
            title_emb = parse_emb(job["title_embedding"])
            desc_emb = parse_emb(job["desc_embedding"])

            sem_title = cosine_similarity(user_emb, title_emb) if title_emb else 0.0
            sem_desc  = cosine_similarity(user_emb, desc_emb)  if desc_emb else 0.0

            # keywords
            kw_score = 0.0
            t = (job["title"] or "").lower()
            d = (job["description"] or "").lower()
            for kw in keywords:
                if kw in t: kw_score += 0.7
                elif kw in d: kw_score += 0.4

            loc = location_score(profile, job)
            sal = salary_score(profile, job)

            remote_penalty = 0.4 if not profile["remote_preference"] and job["is_remote"] else 0.0

            final_score = (
                sem_title*0.45 + sem_desc*0.10 + kw_score*0.25
                + loc*0.12 + sal*0.08 - remote_penalty
            )

            scored.append((final_score, job))
            # keep memory bounded to N
            if len(scored) > limit * 3:
                scored.sort(key=lambda x: x[0], reverse=True)
                scored = scored[:limit]

    # final sorting
    scored.sort(key=lambda x: x[0], reverse=True)
    top_matches = scored[:limit]

    # -----------------------------------
    # Write matches
    # -----------------------------------
    with conn.cursor() as cur:
        for score, job in top_matches:
            if score < 0.20:
                continue
            cur.execute("""
                INSERT INTO matches (user_id, job_url, job_id, score, is_remote)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id, job_url)
                DO UPDATE SET
                    score=EXCLUDED.score,
                    job_id=EXCLUDED.job_id,
                    is_remote=EXCLUDED.is_remote,
                    matched_at=NOW()
            """, (
                profile["user_id"],
                job["job_url"],
                job["id"],
                score,
                job["is_remote"],
            ))

    conn.commit()
    print(f"[MATCH] Stored {len(top_matches)} matches for user {user_id}")
    return top_matches
