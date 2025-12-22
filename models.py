from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.exc import OperationalError
import time

db = SQLAlchemy()

def safe_query(func):
    try:
        return func()
    except OperationalError:
        time.sleep(0.5)
        return func()


def safe_db_commit(db):
    try:
        db.session.commit()
    except OperationalError as e:
        # likely Neon cold start, retry after 0.5s
        time.sleep(0.5)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)

    # Relationship: one user → one profile
    profile = db.relationship("Profile", backref="user", uselist=False, cascade="all, delete")



class Profile(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, unique=True)

    # Basic user info
    first_name = db.Column(db.String(100))
    last_name = db.Column(db.String(100))
    cv_location = db.Column(db.String(255))
    ai_cv_data = db.Column(JSON)   # <-- store parsed CV JSON
    account_type = db.Column(db.String(50), default="early_access")

    # Job preferences
    job_titles = db.Column(db.Text)            # comma-separated or JSON string list

    # Location preferences (structured)
    city = db.Column(db.String(100))           # e.g. “London”, “Los Angeles”
    state = db.Column(db.String(100))          # e.g. “England”, “CA”, “NY”
    country = db.Column(db.String(100))        # e.g. “UK”, “United States”, “Canada”

    remote_preference = db.Column(db.Boolean, default=False)
    worldwide_remote = db.Column(db.Boolean, default=False)

    location_scope = db.Column(db.String(50), default="nationwide")

    # Salary preferences
    min_salary = db.Column(db.Integer)
    max_salary = db.Column(db.Integer)
    miles_distance = db.Column(db.Integer)

    # Application automation
    application_frequency = db.Column(db.String(50), default="daily")

    # Templates & AI personalization
    cv_templates = db.Column(db.JSON, default=list)
    cover_letter_templates = db.Column(db.JSON, default=list)
    ai_settings = db.Column(db.JSON, default=dict)
    application_data = db.Column(db.JSON, default=dict)

    # Geographical matching (optional but powerful)
    latitude = db.Column(db.Float)     # based on city + state + country
    longitude = db.Column(db.Float)
    onboarding_complete = db.Column(db.Boolean, default=False)
    application_mode = db.Column(db.String, default="auto")
    match_mode = db.Column(db.String, default="standard")
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    deactivate_reason = db.Column(db.String(50))
    onboarding_step = db.Column(db.Integer, default=1, nullable=False)

class PendingApplication(db.Model):
    __tablename__ = "pending_applications"

    id = db.Column(db.Integer, primary_key=True)

    # User this pending item belongs to
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # Job unique identifiers
    job_url = db.Column(db.String, nullable=False)
    job_url_hash = db.Column(db.String, nullable=False, index=True)

    # Job metadata
    job_title = db.Column(db.String)
    company = db.Column(db.String)
    location = db.Column(db.String)
    salary = db.Column(db.String)

    # Workflow status
    status = db.Column(db.String, default="pending")
    # allowed values: pending / approved / rejected

    created_at = db.Column(db.DateTime, default=db.func.now())

    # Relationship (optional convenience)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    __table_args__ = (
	    db.UniqueConstraint("user_id", "job_url_hash", name="unique_pending_per_job"),
    )


class Application(db.Model):
    __tablename__ = "applications"

    id = db.Column(db.Integer, primary_key=True)

    # the user who applied
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    # job technical uniqueness
    job_url = db.Column(db.String, nullable=False)
    job_url_hash = db.Column(db.String, nullable=False, index=True)

    # job metadata
    job_title = db.Column(db.String(255))
    company = db.Column(db.String(255))
    location = db.Column(db.String(255))
    salary = db.Column(db.String(100))
    cv_variant_url = db.Column(db.String(500))

    # application workflow status
    status = db.Column(db.String(50), default="pending")
    # allowed values:
    # "pending", "success", "failed", "cancelled"

    # system-generated content
    cv_variant = db.Column(JSON)               # the customized CV used
    application_answers = db.Column(JSON)       # generated answers (if any)
    error_log = db.Column(db.Text)              # if failed
    screenshot_url = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=db.func.now())
    updated_at = db.Column(db.DateTime, default=db.func.now(), onupdate=db.func.now())
    job_id = db.Column(db.BigInteger, db.ForeignKey("jobs.id"))
    job = db.relationship("Job", lazy="joined")
    # convenience relationship
    user = db.relationship("User", backref="applications")
    manual_started = db.Column(db.Boolean, default=False)
    credit_consumed = db.Column(db.Boolean, default=False, nullable=False)

    # prevent duplicates for a user+job
    __table_args__ = (
        db.UniqueConstraint("user_id", "job_url_hash", name="unique_user_job_application"),
    )

class Job(db.Model):
    __tablename__ = "jobs"

    id = db.Column(db.BigInteger, primary_key=True)

    job_url = db.Column(db.Text, nullable=False)
    title = db.Column(db.Text, nullable=False)
    company = db.Column(db.Text)
    description = db.Column(db.Text)
    city = db.Column(db.Text)
    state = db.Column(db.Text)
    country = db.Column(db.Text)
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    is_remote = db.Column(db.Boolean, default=False)
    salary_min = db.Column(db.Integer)
    salary_max = db.Column(db.Integer)
    posted_at = db.Column(db.Date)
    scraped_at = db.Column(db.DateTime)
    expires_at = db.Column(db.Date)
    source_ats = db.Column(db.Text)
    source_job_id = db.Column(db.Text)
    feed_source = db.Column(db.Text)

    # optional: for fast matching UI
    def display_location(self):
        if self.city and self.country:
            return f"{self.city}, {self.country.upper()}"
        return self.city or "Remote"


class Match(db.Model):
    __tablename__ = "matches"

    id = db.Column(db.Integer, primary_key=True)   # ⭐ NEW

    user_id = db.Column(db.Integer, db.ForeignKey("profile.id"), nullable=False)
    job_url = db.Column(db.Text, nullable=False)
    job_id = db.Column(db.BigInteger)
    score = db.Column(db.Float)
    is_remote = db.Column(db.Boolean, default=False)
    matched_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("user_id", "job_url", name="unique_user_job"),
    )

class CreditBalance(db.Model):
    __tablename__ = "credit_balance"

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        primary_key=True
    )

    available_credits = db.Column(db.Integer, nullable=False, default=0)
    lifetime_granted = db.Column(db.Integer, nullable=False, default=0)
    lifetime_spent = db.Column(db.Integer, nullable=False, default=0)

    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    user = db.relationship("User", backref=db.backref("credit_balance", uselist=False))

class CreditLedger(db.Model):
    __tablename__ = "credit_ledger"

    id = db.Column(db.BigInteger, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    change_amount = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(50), nullable=False)
    reference_id = db.Column(db.String(255))

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow
    )

    user = db.relationship("User", backref="credit_ledger_entries")


class SubscriptionPlan(db.Model):
    __tablename__ = "subscription_plans"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    stripe_price_id = db.Column(db.String(255), unique=True, nullable=False)

    credits_per_period = db.Column(db.Integer, nullable=False)
    billing_interval = db.Column(db.String(20), nullable=False)

    active = db.Column(db.Boolean, default=True, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    currency = db.Column(db.Text, nullable=False)

    price_amount = db.Column(db.Integer, nullable=False)

class UserSubscription(db.Model):
    __tablename__ = "user_subscriptions"

    id = db.Column(db.BigInteger, primary_key=True)

    user_id = db.Column(
        db.BigInteger,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    plan_id = db.Column(
        db.Integer,
        db.ForeignKey("subscription_plans.id"),
        nullable=False,
        index=True,
    )

    stripe_customer_id = db.Column(db.Text, nullable=False)
    stripe_subscription_id = db.Column(db.Text, nullable=False, unique=True)

    status = db.Column(db.Text, nullable=False)

    current_period_start = db.Column(db.DateTime(timezone=True), nullable=False)
    current_period_end = db.Column(db.DateTime(timezone=True), nullable=False)

    created_at = db.Column(
        db.DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    # -------------------------
    # Relationships (optional but recommended)
    # -------------------------
    user = db.relationship("User", backref=db.backref("subscriptions", lazy="dynamic"))
    plan = db.relationship("SubscriptionPlan")


class PendingCreditGrant(db.Model):
    __tablename__ = "pending_credit_grants"

    id = db.Column(db.BigInteger, primary_key=True)

    # Stripe identifiers
    stripe_invoice_id = db.Column(
        db.String(255),
        nullable=False,
        unique=True,
        index=True,
    )

    stripe_subscription_id = db.Column(
        db.String(255),
        nullable=False,
        index=True,
    )

    # Optional but useful for debugging / reconciliation
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )

    def __repr__(self):
        return (
            f"<PendingCreditGrant invoice={self.stripe_invoice_id} "
            f"subscription={self.stripe_subscription_id}>"
        )


class ContactMessage(db.Model):
    __tablename__ = "contact_messages"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    subject = db.Column(db.String(255), nullable=True)
    message = db.Column(db.Text, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)