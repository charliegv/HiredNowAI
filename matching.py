import os
import ast
from math import radians, sin, cos, sqrt, atan2
from typing import Optional, List

import numpy as np
import psycopg2
from psycopg2.extras import RealDictCursor
from openai import OpenAI
from dotenv import load_dotenv

import heapq

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

MIN_SCORE_THRESHOLD = 0.20

# --------------------------------------------------------
# NEW - Extract job titles cleanly (handles comma lists)
# --------------------------------------------------------
def extract_titles(raw: Optional[str]) -> List[str]:
    """
    Accepts:
      - a single title ("Product Manager")
      - a comma-separated list ("Product Manager, Product Owner")
    Returns a clean list: ["product manager", "product owner"]
    """
    if not raw:
        return []

    raw = raw.strip()
    titles = [t.strip().lower() for t in raw.split(",") if t.strip()]
    return titles


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


# --------------------------------------------------------
# MAIN MATCH FUNCTION
# --------------------------------------------------------
def match_user(conn, user_id: int, limit=200):
    import itertools
    counter = itertools.count()

    # --------------------------------------------------------
    # Load user profile
    # --------------------------------------------------------
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT
                id AS profile_id, user_id, job_titles, city, state, country,
                latitude, longitude, remote_preference, min_salary, max_salary,
                miles_distance, preference_embedding, application_mode, worldwide_remote
            FROM profile
            WHERE user_id = %s
        """, (user_id,))
        profile = cur.fetchone()

        if not profile:
            print(f"[MATCH] No profile for user {user_id}")
            return

        profile["country"] = normalize_country(profile["country"])

        # --------------------------------------------------------
        # NEW — split titles properly
        # --------------------------------------------------------
        title_list = extract_titles(profile["job_titles"])  # lowercased list

        # Create a combined string for embedding
        combined_title_text = " ".join(title_list) if title_list else ""

        # Ensure embedding exists
        if profile["preference_embedding"] is None:
            emb = generate_embedding(combined_title_text)
            cur.execute(
                "UPDATE profile SET preference_embedding=%s WHERE id=%s",
                (emb, profile["profile_id"])
            )
            conn.commit()
            profile["preference_embedding"] = emb

        user_emb = profile["preference_embedding"]

    # --------------------------------------------------------
    # COMBINED keyword list
    # --------------------------------------------------------
    keywords = title_list  # Already cleaned and lowercased

    max_miles = profile.get("miles_distance")
    max_km = max_miles * 1.60934 if max_miles else None

    # --------------------------------------------------------
    # Min heap for top N matches
    # --------------------------------------------------------
    heap = []

    # --------------------------------------------------------
    # Job stream
    # --------------------------------------------------------
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
              AND source_ats='workable'
        """, (profile["country"],))

        for job in cur:
            # -----------------------------
            # HARD FILTERING (no scoring yet)
            # -----------------------------

            job_country = normalize_country(job["country"])
            remote_pref = bool(profile.get("remote_preference"))
            worldwide_remote = bool(profile.get("worldwide_remote"))

            # ------------------------------------------------
            # NON-REMOTE JOBS
            # ------------------------------------------------
            if not job["is_remote"]:

                # Must be same country
                if job_country != profile["country"]:
                    continue

                # Apply distance limit
                if max_km:
                    dist = haversine(profile["latitude"], profile["longitude"],
                                     job["latitude"], job["longitude"])
                    if dist is None or dist > max_km:
                        continue

            # ------------------------------------------------
            # REMOTE JOBS
            # ------------------------------------------------
            else:

                # 👉 CASE 1 — User does NOT want remote roles
                # BUT allow "remote" jobs that have a nearby office
                if not remote_pref:

                    # Must be same country
                    if job_country != profile["country"]:
                        continue

                    # Must be within radius to count as "local-ish"
                    if max_km:
                        dist = haversine(profile["latitude"], profile["longitude"],
                                         job["latitude"], job["longitude"])
                        if dist is None or dist > max_km:
                            continue
                    else:
                        continue  # No radius defined, reject remote entirely

                # 👉 CASE 2 — User wants remote but ONLY nationwide
                elif remote_pref and not worldwide_remote:

                    # Must be same country
                    if job_country != profile["country"]:
                        continue

                # 👉 CASE 3 — Remote worldwide allowed → accept all remote jobs
                else:
                    pass

            title_emb = parse_emb(job["title_embedding"])
            desc_emb = parse_emb(job["desc_embedding"])

            sem_title = cosine_similarity(user_emb, title_emb) if title_emb else 0.0
            sem_desc  = cosine_similarity(user_emb, desc_emb)  if desc_emb else 0.0

            # --------------------------------------------------------
            # NEW combined keyword logic
            # Score boosts based on ANY matched title keyword
            # --------------------------------------------------------
            job_title_lower = (job["title"] or "").lower()
            job_desc_lower  = (job["description"] or "").lower()

            kw_score = 0.0
            for kw in keywords:
                if kw in job_title_lower:
                    kw_score += 0.7
                elif kw in job_desc_lower:
                    kw_score += 0.4

            # --------------------------------------------------------
            # Other scores
            # --------------------------------------------------------
            loc = location_score(profile, job)
            sal = salary_score(profile, job)
            remote_penalty = 0.4 if (not profile["remote_preference"] and job["is_remote"]) else 0.0

            final_score = (
                    sem_title * 0.45 +
                    sem_desc * 0.10 +
                    kw_score * 0.40 +
                    sal * 0.05
            )

            #print(f"{job_title_lower} - {final_score} - kw_score: {kw_score},  loc: {loc}, sem_title: {sem_title}, sem_desc: {sem_desc}, sal: {sal}, remote_penalty: {remote_penalty}")

            entry = (final_score, next(counter), job)
            if len(heap) < limit:
                heapq.heappush(heap, entry)
            else:
                if final_score > heap[0][0]:
                    heapq.heapreplace(heap, entry)

    # --------------------------------------------------------
    # Sort results
    # --------------------------------------------------------
    top_matches = sorted(heap, key=lambda x: x[0], reverse=True)

    # --------------------------------------------------------
    # Store matches
    # --------------------------------------------------------
    stored_count = 0
    with conn.cursor() as cur:
        for score, _, job in top_matches:
            if score < MIN_SCORE_THRESHOLD:
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
            stored_count += 1

    conn.commit()

    print(f"[MATCH] Found {len(top_matches)} matches in area for user {user_id}")
    print(f"[MATCH] Stored {stored_count} matches above matching threshold")

    return top_matches
