import os
import json
import sys
import psycopg2
import boto3
from datetime import datetime, date
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, "/opt/render/project/src")

DATABASE_URL = os.getenv("DATABASE_URL")
S3_BUCKET = "hired-now-ai"
S3_PREFIX = "snapshots"

SEO_SNAPSHOTS = [
    # -------------------------
    # REMOTE JOBS
    # -------------------------
    {
        "slug": "remote-jobs-us",
        "filters": {
            "country": "us",
            "is_remote": True
        },
        "limit": 50,
        "content": {
            "intro": "Browse the latest remote jobs in the United States from verified company career pages.",
            "what_are": "Remote jobs in the United States allow professionals to work from anywhere while being authorised to work in the US.",
            "how_to_apply": "Applying to remote US jobs manually is repetitive. An AI job application system automates this process.",
            "faq": [
                {
                    "q": "Are these real US remote jobs?",
                    "a": "Yes. All roles are sourced directly from verified company career pages."
                }
            ]
        }
    },
    {
        "slug": "remote-jobs-uk",
        "filters": {
            "country": "gb",
            "is_remote": True
        },
        "limit": 50,
        "content": {
            "intro": "Browse the latest remote jobs in the United Kingdom from verified employers.",
            "what_are": "UK remote jobs allow professionals to work flexibly while complying with UK employment regulations.",
            "how_to_apply": "AI job applications allow you to apply to multiple UK remote roles efficiently.",
            "faq": [
                {
                    "q": "Do I need UK work authorisation?",
                    "a": "Yes. Most UK remote roles require candidates to be authorised to work in the UK."
                }
            ]
        }
    },

    # -------------------------
    # PRODUCT MANAGER – US
    # -------------------------
    {
        "slug": "us-product-manager-jobs",
        "filters": {
            "country": "us",
            "title_contains": ["product manager"]
        },
        "limit": 50,
        "content": {
            "intro": "Find the latest product manager jobs in the United States from verified companies.",
            "what_are": "Product manager jobs in the United States focus on defining product strategy, working with engineering teams, and delivering customer focused solutions.",
            "how_to_apply": "Applying to multiple product manager roles manually is time consuming. An AI job application system tailors your CV and submits applications automatically.",
            "faq": [
                {
                    "q": "Are these real product manager jobs?",
                    "a": "Yes. All product manager roles are sourced directly from company career pages."
                },
                {
                    "q": "Do these roles include senior and junior positions?",
                    "a": "Yes. Listings may include associate, mid level, senior, and lead product manager roles."
                }
            ]
        }
    },

    # -------------------------
    # PRODUCT MANAGER – UK
    # -------------------------
    {
        "slug": "uk-product-manager-jobs",
        "filters": {
            "country": "gb",
            "title_contains": ["product manager"]
        },
        "limit": 50,
        "content": {
            "intro": "Browse the latest product manager jobs in the United Kingdom from verified employers.",
            "what_are": "Product manager jobs in the UK involve shaping digital products, working with cross functional teams, and prioritising customer needs.",
            "how_to_apply": "Using an AI job application system allows you to apply to multiple UK product manager roles efficiently without repetitive forms.",
            "faq": [
                {
                    "q": "Do I need UK work authorisation for these roles?",
                    "a": "Yes. Most UK product manager roles require the right to work in the UK."
                },
                {
                    "q": "Are remote product manager jobs included?",
                    "a": "Yes. Listings may include remote, hybrid, and on site roles depending on the employer."
                }
            ]
        }
    },
# -------------------------
# SOFTWARE ENGINEER – US
# -------------------------
{
    "slug": "us-software-engineer-jobs",
    "filters": {
        "country": "us",
        "title_contains": ["software engineer", "software developer"]
    },
    "limit": 50,
    "content": {
        "intro": "Browse the latest software engineer jobs in the United States from verified employers.",
        "what_are": "Software engineer jobs in the United States involve designing, building, and maintaining applications, platforms, and systems across a wide range of industries.",
        "how_to_apply": "Applying to software engineering roles often involves repetitive forms and tailored CVs. An AI job application system automates this process and submits relevant applications for you.",
        "faq": [
            {
                "q": "Are these real software engineering jobs?",
                "a": "Yes. All roles are sourced directly from company career pages."
            },
            {
                "q": "Do these include remote and on site roles?",
                "a": "Yes. Listings may include remote, hybrid, and on site software engineering positions."
            }
        ]
    }
},

# -------------------------
# DATA ANALYST – US
# -------------------------
{
    "slug": "us-data-analyst-jobs",
    "filters": {
        "country": "us",
        "title_contains": ["data analyst"]
    },
    "limit": 50,
    "content": {
        "intro": "Find the latest data analyst jobs in the United States from verified companies.",
        "what_are": "Data analyst jobs focus on analysing datasets, creating reports, and helping businesses make informed decisions using data.",
        "how_to_apply": "Applying to multiple data analyst roles manually can be time consuming. AI powered job applications tailor your CV and submit applications efficiently.",
        "faq": [
            {
                "q": "Are entry level data analyst roles included?",
                "a": "Yes. Listings may include junior, mid level, and senior data analyst roles."
            }
        ]
    }
},

# -------------------------
# DATA SCIENTIST – US
# -------------------------
{
    "slug": "us-data-scientist-jobs",
    "filters": {
        "country": "us",
        "title_contains": ["data scientist"]
    },
    "limit": 50,
    "content": {
        "intro": "Explore the latest data scientist jobs in the United States from verified employers.",
        "what_are": "Data scientist jobs involve building predictive models, analysing complex datasets, and working closely with engineering and product teams.",
        "how_to_apply": "AI job applications help data scientists apply to multiple roles quickly while tailoring CVs to each job description.",
        "faq": [
            {
                "q": "Do these roles require advanced technical skills?",
                "a": "Most data scientist roles require experience with programming, statistics, and data modelling."
            }
        ]
    }
},

# -------------------------
# UX DESIGNER – US
# -------------------------
{
    "slug": "us-ux-designer-jobs",
    "filters": {
        "country": "us",
        "title_contains": ["ux designer", "user experience designer"]
    },
    "limit": 50,
    "content": {
        "intro": "Browse the latest UX designer jobs in the United States from verified employers.",
        "what_are": "UX designer jobs focus on improving user experiences by researching, designing, and testing digital products.",
        "how_to_apply": "Applying to UX roles often requires tailored CVs and portfolios. AI job applications help streamline this process.",
        "faq": [
            {
                "q": "Are remote UX designer jobs included?",
                "a": "Yes. Many UX design roles offer remote or hybrid working options."
            }
        ]
    }
},

# -------------------------
# UI DESIGNER – US
# -------------------------
{
    "slug": "us-ui-designer-jobs",
    "filters": {
        "country": "us",
        "title_contains": ["ui designer", "user interface designer"]
    },
    "limit": 50,
    "content": {
        "intro": "Find the latest UI designer jobs in the United States from verified companies.",
        "what_are": "UI designer jobs focus on visual design, interface layouts, and creating intuitive digital experiences.",
        "how_to_apply": "AI job application tools help UI designers apply to multiple roles efficiently without repetitive manual work.",
        "faq": [
            {
                "q": "Do these roles require a design portfolio?",
                "a": "Most UI design roles require a portfolio showcasing previous design work."
            }
        ]
    }
},

# -------------------------
# MARKETING MANAGER – US
# -------------------------
{
    "slug": "us-marketing-manager-jobs",
    "filters": {
        "country": "us",
        "title_contains": ["marketing manager"]
    },
    "limit": 50,
    "content": {
        "intro": "Browse the latest marketing manager jobs in the United States from verified employers.",
        "what_are": "Marketing manager jobs involve planning campaigns, managing teams, and driving customer acquisition and growth.",
        "how_to_apply": "AI job applications help marketing professionals apply to multiple roles while tailoring CVs for each position.",
        "faq": [
            {
                "q": "Do these roles include digital marketing positions?",
                "a": "Yes. Listings may include digital, growth, and performance marketing manager roles."
            }
        ]
    }
},

# -------------------------
# CUSTOMER SUCCESS MANAGER – US
# -------------------------
{
    "slug": "us-customer-success-manager-jobs",
    "filters": {
        "country": "us",
        "title_contains": ["customer success manager"]
    },
    "limit": 50,
    "content": {
        "intro": "Explore the latest customer success manager jobs in the United States from verified companies.",
        "what_are": "Customer success manager jobs focus on building relationships, retaining customers, and driving long term satisfaction.",
        "how_to_apply": "Applying manually to customer success roles can be repetitive. AI job applications streamline the process.",
        "faq": [
            {
                "q": "Are these roles common in SaaS companies?",
                "a": "Yes. Customer success roles are especially common in SaaS and technology companies."
            }
        ]
    }
},

# -------------------------
# SALES MANAGER – US
# -------------------------
{
    "slug": "us-sales-manager-jobs",
    "filters": {
        "country": "us",
        "title_contains": ["sales manager"]
    },
    "limit": 50,
    "content": {
        "intro": "Browse the latest sales manager jobs in the United States from verified employers.",
        "what_are": "Sales manager jobs involve leading sales teams, managing pipelines, and driving revenue growth.",
        "how_to_apply": "AI job application systems help sales professionals apply to multiple roles efficiently.",
        "faq": [
            {
                "q": "Do these roles include remote sales positions?",
                "a": "Some sales manager roles offer remote or hybrid working arrangements."
            }
        ]
    }
},

# -------------------------
# FINANCE MANAGER – US
# -------------------------
{
    "slug": "us-finance-manager-jobs",
    "filters": {
        "country": "us",
        "title_contains": ["finance manager"]
    },
    "limit": 50,
    "content": {
        "intro": "Find the latest finance manager jobs in the United States from verified companies.",
        "what_are": "Finance manager jobs focus on budgeting, forecasting, financial planning, and supporting business strategy.",
        "how_to_apply": "AI job applications allow finance professionals to apply to multiple roles without repetitive manual work.",
        "faq": [
            {
                "q": "Are senior finance roles included?",
                "a": "Listings may include mid level, senior, and leadership finance manager positions."
            }
        ]
    }
},
# -------------------------
# SOFTWARE ENGINEER – UK
# -------------------------
{
    "slug": "uk-software-engineer-jobs",
    "filters": {
        "country": "gb",
        "title_contains": ["software engineer", "software developer"]
    },
    "limit": 50,
    "content": {
        "intro": "Browse the latest software engineer jobs in the United Kingdom from verified employers.",
        "what_are": "Software engineer jobs in the UK involve designing, building, and maintaining applications and systems across industries including technology, finance, and e commerce.",
        "how_to_apply": "Applying to software engineering roles in the UK often requires tailored CVs and repeated forms. An AI job application system automates this process efficiently.",
        "faq": [
            {
                "q": "Do I need the right to work in the UK?",
                "a": "Yes. Most UK software engineering roles require candidates to have the right to work in the United Kingdom."
            },
            {
                "q": "Are remote software engineer jobs included?",
                "a": "Yes. Listings may include remote, hybrid, and on site roles depending on the employer."
            }
        ]
    }
},

# -------------------------
# DATA ANALYST – UK
# -------------------------
{
    "slug": "uk-data-analyst-jobs",
    "filters": {
        "country": "gb",
        "title_contains": ["data analyst"]
    },
    "limit": 50,
    "content": {
        "intro": "Find the latest data analyst jobs in the United Kingdom from verified companies.",
        "what_are": "Data analyst jobs in the UK focus on analysing data, building reports, and supporting business decisions across a wide range of sectors.",
        "how_to_apply": "Applying manually to multiple data analyst roles can be time consuming. AI powered job applications help streamline the process.",
        "faq": [
            {
                "q": "Are junior data analyst roles included?",
                "a": "Yes. Listings may include entry level, mid level, and senior data analyst positions."
            }
        ]
    }
},

# -------------------------
# DATA SCIENTIST – UK
# -------------------------
{
    "slug": "uk-data-scientist-jobs",
    "filters": {
        "country": "gb",
        "title_contains": ["data scientist"]
    },
    "limit": 50,
    "content": {
        "intro": "Explore the latest data scientist jobs in the United Kingdom from verified employers.",
        "what_are": "Data scientist jobs in the UK involve building predictive models, analysing complex datasets, and working with engineering and product teams.",
        "how_to_apply": "AI job applications help data scientists apply to multiple UK roles efficiently while tailoring CVs for each position.",
        "faq": [
            {
                "q": "Do UK data scientist roles require advanced technical skills?",
                "a": "Most roles require experience with programming, statistics, and machine learning techniques."
            }
        ]
    }
},

# -------------------------
# UX DESIGNER – UK
# -------------------------
{
    "slug": "uk-ux-designer-jobs",
    "filters": {
        "country": "gb",
        "title_contains": ["ux designer", "user experience designer"]
    },
    "limit": 50,
    "content": {
        "intro": "Browse the latest UX designer jobs in the United Kingdom from verified employers.",
        "what_are": "UX designer jobs in the UK focus on researching user needs, designing intuitive experiences, and improving digital products.",
        "how_to_apply": "Applying to UX roles often requires tailored CVs and portfolios. AI job applications help reduce manual effort.",
        "faq": [
            {
                "q": "Are remote UX designer roles common in the UK?",
                "a": "Yes. Many UK UX designer roles offer remote or hybrid working arrangements."
            }
        ]
    }
},

# -------------------------
# UI DESIGNER – UK
# -------------------------
{
    "slug": "uk-ui-designer-jobs",
    "filters": {
        "country": "gb",
        "title_contains": ["ui designer", "user interface designer"]
    },
    "limit": 50,
    "content": {
        "intro": "Find the latest UI designer jobs in the United Kingdom from verified companies.",
        "what_are": "UI designer jobs focus on visual design, interface consistency, and creating accessible digital experiences.",
        "how_to_apply": "AI job application tools help UI designers apply to multiple roles efficiently without repetitive manual work.",
        "faq": [
            {
                "q": "Is a design portfolio required for UK UI designer roles?",
                "a": "Most UI design roles require a portfolio demonstrating previous design work."
            }
        ]
    }
},

# -------------------------
# MARKETING MANAGER – UK
# -------------------------
{
    "slug": "uk-marketing-manager-jobs",
    "filters": {
        "country": "gb",
        "title_contains": ["marketing manager"]
    },
    "limit": 50,
    "content": {
        "intro": "Browse the latest marketing manager jobs in the United Kingdom from verified employers.",
        "what_are": "Marketing manager jobs in the UK involve planning campaigns, managing budgets, and driving brand and customer growth.",
        "how_to_apply": "AI job applications help marketing professionals apply to multiple UK roles while tailoring CVs for each position.",
        "faq": [
            {
                "q": "Do these roles include digital marketing positions?",
                "a": "Yes. Many roles focus on digital, growth, and performance marketing."
            }
        ]
    }
},

# -------------------------
# CUSTOMER SUCCESS MANAGER – UK
# -------------------------
{
    "slug": "uk-customer-success-manager-jobs",
    "filters": {
        "country": "gb",
        "title_contains": ["customer success manager"]
    },
    "limit": 50,
    "content": {
        "intro": "Explore the latest customer success manager jobs in the United Kingdom from verified companies.",
        "what_are": "Customer success manager jobs in the UK focus on onboarding customers, driving retention, and building long term relationships.",
        "how_to_apply": "AI job applications streamline the process of applying to customer success roles without repetitive manual work.",
        "faq": [
            {
                "q": "Are customer success roles common in UK SaaS companies?",
                "a": "Yes. Customer success roles are widely used across UK SaaS and technology businesses."
            }
        ]
    }
},

# -------------------------
# SALES MANAGER – UK
# -------------------------
{
    "slug": "uk-sales-manager-jobs",
    "filters": {
        "country": "gb",
        "title_contains": ["sales manager"]
    },
    "limit": 50,
    "content": {
        "intro": "Browse the latest sales manager jobs in the United Kingdom from verified employers.",
        "what_are": "Sales manager jobs involve leading sales teams, managing pipelines, and driving revenue growth across UK markets.",
        "how_to_apply": "AI job application systems help sales professionals apply to multiple UK roles efficiently.",
        "faq": [
            {
                "q": "Do UK sales manager roles offer remote options?",
                "a": "Some roles offer remote or hybrid working depending on the employer."
            }
        ]
    }
},

# -------------------------
# FINANCE MANAGER – UK
# -------------------------
{
    "slug": "uk-finance-manager-jobs",
    "filters": {
        "country": "gb",
        "title_contains": ["finance manager"]
    },
    "limit": 50,
    "content": {
        "intro": "Find the latest finance manager jobs in the United Kingdom from verified companies.",
        "what_are": "Finance manager jobs in the UK focus on budgeting, forecasting, financial reporting, and supporting strategic decision making.",
        "how_to_apply": "AI job applications allow finance professionals to apply to multiple UK roles without repetitive manual work.",
        "faq": [
            {
                "q": "Are senior finance roles included?",
                "a": "Listings may include mid level, senior, and leadership finance manager positions."
            }
        ]
    }
}

]



def get_conn():
    return psycopg2.connect(DATABASE_URL)


def run():
    print("[SEO SNAPSHOT WORKER] Started")

    conn = get_conn()
    s3 = boto3.client("s3")

    for snapshot in SEO_SNAPSHOTS:
        slug = snapshot["slug"]
        print(f"[SEO SNAPSHOT WORKER] Generating {slug}")

        rows = fetch_jobs(conn, snapshot)

        if not rows:
            print(f"[SEO SNAPSHOT WORKER] No jobs found for {slug}, skipping")
            continue

        payload = build_payload(rows, snapshot)
        upload_snapshot(s3, slug, payload)

        print(f"[SEO SNAPSHOT WORKER] Uploaded {slug} ({len(rows)} jobs)")

    conn.close()
    print("[SEO SNAPSHOT WORKER] Completed")
    print("[SEO SNAPSHOT WORKER] Generating sitemap")

    sitemap_xml = generate_sitemap(SEO_SNAPSHOTS)
    upload_sitemap(s3, sitemap_xml)

    print("[SEO SNAPSHOT WORKER] Sitemap uploaded")


def fetch_jobs(conn, snapshot):
    filters = snapshot["filters"]
    limit = snapshot["limit"]

    where_clauses = [
        "expires_at >= CURRENT_DATE"
    ]
    params = []

    if "country" in filters:
        where_clauses.append("country = %s")
        params.append(filters["country"])

    if filters.get("is_remote") is True:
        where_clauses.append("is_remote = TRUE")

    if "title_contains" in filters:
        title_conditions = []
        for keyword in filters["title_contains"]:
            title_conditions.append("LOWER(title) LIKE %s")
            params.append(f"%{keyword.lower()}%")
        where_clauses.append(f"({' OR '.join(title_conditions)})")

    query = f"""
        SELECT
            id,
            title,
            company,
            job_url,
            posted_at,
            scraped_at
        FROM jobs
        WHERE {' AND '.join(where_clauses)}
        ORDER BY
            COALESCE(posted_at, scraped_at) DESC
        LIMIT {limit};
    """

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query, params)
        return cur.fetchall()


def build_payload(rows, snapshot):
    jobs = []

    for row in rows:
        posted_date = row["posted_at"] or row["scraped_at"].date()

        jobs.append({
            "id": row["id"],
            "title": row["title"],
            "company": row["company"] or "",
            "location": "Remote",
            "posted_at": humanize_date(posted_date),
            "apply_url": row["job_url"]
        })

    return {
        "meta": {
            "slug": snapshot["slug"],
            "updated_at": datetime.utcnow().isoformat()
        },
        "content": snapshot.get("content", {}),
        "jobs": jobs
    }



def upload_snapshot(s3, slug, payload):
    key = f"{S3_PREFIX}/{slug}.json"

    s3.put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=json.dumps(payload),
        ContentType="application/json",
        CacheControl="public, max-age=3600"
    )

def upload_sitemap(s3, xml):
    s3.put_object(
        Bucket=S3_BUCKET,
        Key="sitemap.xml",
        Body=xml,
        ContentType="application/xml",
        CacheControl="public, max-age=3600"
    )

def humanize_date(d: date):
    delta = date.today() - d
    days = delta.days

    if days <= 0:
        return "Today"
    if days == 1:
        return "1 day ago"
    return f"{days} days ago"


def build_sitemap_xml(urls):
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    ]

    for url in urls:
        lines.append("  <url>")
        lines.append(f"    <loc>{url['loc']}</loc>")
        lines.append(f"    <changefreq>{url['changefreq']}</changefreq>")
        lines.append(f"    <priority>{url['priority']}</priority>")
        lines.append("  </url>")

    lines.append("</urlset>")

    return "\n".join(lines)


def generate_sitemap(snapshots):
    urls = []

    BASE_URL = "https://hirednowai.com"

    # Static pages
    static_paths = [
        "/",
        "/ai-job-application",
        "/ai-job-application-tool",
        "/privacy",
        "/terms"
    ]

    for path in static_paths:
        urls.append({
            "loc": f"{BASE_URL}{path}",
            "changefreq": "weekly",
            "priority": "0.8"
        })

    # Job pages
    for snapshot in snapshots:
        slug = snapshot["slug"]
        urls.append({
            "loc": f"{BASE_URL}/jobs/{slug}",
            "changefreq": "daily",
            "priority": "0.9"
        })

    return build_sitemap_xml(urls)


if __name__ == "__main__":
    run()
