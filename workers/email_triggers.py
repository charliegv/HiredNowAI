import sys
import os

# Add Render project root so matching/, bots/, utils/ are importable
PROJECT_ROOT = "/opt/render/project/src"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Add parent of /workers (local dev use)
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from models import db, User, Profile, UserSubscription, EmailEvent
from app import create_app
from emails.onboarding_bounce import send_onboarding_bounce_email
from sqlalchemy.exc import IntegrityError

def run():
    app = create_app()
    with app.app_context():
        now = datetime.utcnow()
        cutoff = now - timedelta(hours=1)

        users = (
            db.session.query(User, Profile)
            .join(Profile, Profile.user_id == User.id)
            .filter(Profile.onboarding_step == 5)
            .filter(Profile.updated_at < cutoff)
            .all()
        )

        for user, profile in users:
            # Skip if user has a subscription
            print(user)
            has_sub = (
                UserSubscription.query
                .filter_by(user_id=user.id)
                .filter(UserSubscription.status.in_(["active", "trialing"]))
                .first()
            )

            if has_sub:
                continue

            # Idempotency check
            already_sent = EmailEvent.query.filter_by(
                user_id=user.id,
                event_type="onboarding_plan_bounce"
            ).first()

            if already_sent:
                continue

            # Send email
            send_onboarding_bounce_email(user)

            #Record event (DB-enforced idempotency)
            try:
                db.session.add(
                    EmailEvent(
                        user_id=user.id,
                        event_type="onboarding_plan_bounce"
                    )
                )
                db.session.commit()
            except IntegrityError:
                db.session.rollback()

if __name__ == '__main__':
    run()