# seed_dummy_applications.py

from app import app
from models import db, Application
from datetime import datetime
import hashlib


def hash_url(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def seed_dummy_data():
    with app.app_context():
        user_id = 1

        print("Seeding dummy applications for user_id = 1 ...")

        # Clear existing
        Application.query.filter_by(user_id=user_id).delete()
        db.session.commit()

        # Example full CV variant structure
        marketing_cv_variant = {
            "first_name": "Charlie",
            "last_name": "V",
            "contact": {
                "email": "charlie@example.com",
                "phone": "+44 7123 456789",
                "location": "London, UK"
            },
            "summary": (
                "Marketing professional with strong experience in data-driven strategy, "
                "growth experimentation, and CRM-led customer activation. "
                "Tailored for HubSpot role with emphasis on inbound methodology."
            ),
            "skills": [
                "SEO", "Content Strategy", "Funnel Optimization",
                "Google Analytics", "Copywriting", "A/B Testing"
            ],
            "experience": [
                {
                    "role": "Marketing Manager",
                    "company": "TechBrand Ltd",
                    "start_date": "Jan 2021",
                    "end_date": "Present",
                    "description": (
                        "Led inbound marketing initiatives resulting in a 40% YoY increase "
                        "in pipeline growth. Built SEO roadmap, content strategy, and "
                        "customer lifecycle communications."
                    ),
                },
                {
                    "role": "Growth Specialist",
                    "company": "StartupX",
                    "start_date": "Jun 2019",
                    "end_date": "Dec 2020",
                    "description": (
                        "Developed experimentation frameworks and reporting pipelines.\n"
                        "Improved lead-to-MQL conversion by 22% through targeted messaging."
                    ),
                },
            ],
            "education": [
                {
                    "degree": "BA Marketing",
                    "institution": "University of Leeds",
                    "graduation_year": "2019",
                }
            ],
            "certifications": ["Google Analytics Certified", "HubSpot Inbound Certification"],
            "languages": ["English"],
            "additional_details": {
                "publications": ["How Inbound Funnels Scale SaaS Growth (2023)"],
                "github": "",
                "linkedin": "https://linkedin.com/in/charliev",
                "portfolio": "",
                "awards": ["Top 30 Under 30 - UK Marketing"],
                "interests": ["Writing", "SaaS metrics", "Behavioural psychology"],
            },
            "customizations": {
                "role_focus": "Marketing Manager",
                "keywords_used": ["SEO", "Inbound", "Lifecycle"],
                "sections_modified": ["summary", "experience", "skills"]
            }
        }

        data_analyst_cv_variant = {
            "first_name": "Charlie",
            "last_name": "V",
            "contact": {
                "email": "charlie@example.com",
                "phone": "+44 7123 456789",
                "location": "Manchester, UK"
            },
            "summary": (
                "Data analyst with strong SQL, Python, and statistical modelling skills. "
                "Optimised for Deliveroo Analyst role focusing on marketplace operations."
            ),
            "skills": ["Python", "SQL", "Tableau", "A/B Testing", "Data Cleaning"],
            "experience": [
                {
                    "role": "Data Analyst",
                    "company": "Retail Insights Ltd",
                    "start_date": "Feb 2020",
                    "end_date": "Present",
                    "description": (
                        "Created dashboards tracking customer retention and acquisition. "
                        "Ran product experiments using Bayesian A/B testing models."
                    ),
                }
            ],
            "education": [
                {
                    "degree": "BSc Data Science",
                    "institution": "University of Manchester",
                    "graduation_year": "2020",
                }
            ],
            "additional_details": {"interests": ["Machine learning", "Food delivery insights"]},
            "customizations": {
                "role_focus": "Data Analyst",
                "keywords_used": ["SQL", "Python", "Experimentation"],
                "sections_modified": ["summary", "skills"]
            }
        }

        dummy_apps = [
            # SUCCESSFUL (with full CV variant + answers)
            {
                "job_url": "https://jobs.example.com/marketing-manager",
                "job_title": "Marketing Manager",
                "company": "HubSpot",
                "location": "London, UK",
                "salary": "£55k",
                "status": "success",
                "cv_variant": marketing_cv_variant,
                "application_answers": {
                    "Why do you want this job?": "HubSpot’s focus on inbound aligns with my career mission.",
                    "Relevant experience": "5+ years driving organic growth and lifecycle journeys."
                },
                "error_log": None,
            },

            # PENDING
            {
                "job_url": "https://jobs.example.com/product-designer",
                "job_title": "Product Designer",
                "company": "Monzo",
                "location": "Remote (UK)",
                "salary": "£65k",
                "status": "pending",
                "cv_variant": None,
                "application_answers": None,
                "error_log": None,
            },

            # FAILED (with full CV variant + error log)
            {
                "job_url": "https://jobs.example.com/data-analyst",
                "job_title": "Data Analyst",
                "company": "Deliveroo",
                "location": "Manchester, UK",
                "salary": "£45k",
                "status": "failed",
                "cv_variant": data_analyst_cv_variant,
                "application_answers": None,
                "error_log": "SubmitError: Deliveroo API timed out during application upload.",
            },

            # CANCELLED
            {
                "job_url": "https://jobs.example.com/sales-executive",
                "job_title": "Sales Executive",
                "company": "Salesforce",
                "location": "London, UK",
                "salary": "£50k",
                "status": "cancelled",
                "cv_variant": None,
                "application_answers": None,
                "error_log": None,
            },
        ]

        for item in dummy_apps:
            entry = Application(
                user_id=user_id,
                job_url=item["job_url"],
                job_url_hash=hash_url(item["job_url"]),
                job_title=item["job_title"],
                company=item["company"],
                location=item["location"],
                salary=item["salary"],
                status=item["status"],
                cv_variant=item["cv_variant"],
                application_answers=item["application_answers"],
                error_log=item["error_log"],
                created_at=datetime.utcnow(),
            )
            db.session.add(entry)

        db.session.commit()
        print("Dummy applications inserted successfully!")


if __name__ == "__main__":
    seed_dummy_data()
