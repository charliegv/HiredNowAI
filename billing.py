import stripe
from flask import Blueprint, redirect, url_for, request
from flask_login import login_required, current_user
from models import db, SubscriptionPlan, UserSubscription, CreditLedger, CreditBalance, Profile, PendingCreditGrant
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
print(os.getenv("STRIPE_SECRET_KEY"))

billing_bp = Blueprint("billing", __name__)


def handle_invoice_paid(invoice):
    print("---------- handle invoice paid")

    subscription_id = extract_subscription_id(invoice)
    if not subscription_id:
        print("No subscription ID found on invoice")
        return

    print(subscription_id)
    user_sub = UserSubscription.query.filter_by(
        stripe_subscription_id=subscription_id
    ).first()

    if not user_sub:
        if not PendingCreditGrant.query.filter_by(
                stripe_invoice_id=invoice["id"]
        ).first():
            db.session.add(
                PendingCreditGrant(
                    stripe_invoice_id=invoice["id"],
                    stripe_subscription_id=subscription_id,
                )
            )
            db.session.commit()

        return

    # Idempotency
    if CreditLedger.query.filter_by(
        reference_id=invoice["id"],
        reason="subscription_credit"
    ).first():
        print("Invoice already processed")
        return

    # Get subscription line
    line = next(
        (l for l in invoice["lines"]["data"]
         if l.get("parent", {}).get("subscription_item_details")),
        None
    )

    if not line:
        print("No subscription line found")
        return

    price_id = (
        line.get("pricing", {})
            .get("price_details", {})
            .get("price")
    )

    if not price_id:
        print("No price ID found")
        return

    plan = SubscriptionPlan.query.filter_by(
        stripe_price_id=price_id,
        active=True
    ).first()

    if not plan:
        print("No plan found for price:", price_id)
        return

    credits = plan.credits_per_period

    balance = CreditBalance.query.filter_by(
        user_id=user_sub.user_id
    ).with_for_update().first()

    if not balance:
        balance = CreditBalance(
            user_id=user_sub.user_id,
            available_credits=0,
            lifetime_granted=0,
            lifetime_spent=0,
        )
        db.session.add(balance)
        db.session.flush()

    balance.available_credits += credits
    balance.lifetime_granted += credits

    db.session.add(
        CreditLedger(
            user_id=user_sub.user_id,
            change_amount=credits,
            reason="subscription_credit",
            reference_id=invoice["id"],
        )
    )

    period = line["period"]
    user_sub.current_period_start = datetime.utcfromtimestamp(period["start"])
    user_sub.current_period_end = datetime.utcfromtimestamp(period["end"])
    user_sub.status = "active"

    profile = Profile.query.filter_by(user_id=user_sub.user_id).first()
    if profile:
        profile.is_active = True
        profile.application_mode = "auto"
        profile.deactivate_reason = None

    db.session.commit()



def handle_checkout_completed(session):
    subscription_id = session.get("subscription")
    if not subscription_id:
        return

    # Idempotency
    existing = UserSubscription.query.filter_by(
        stripe_subscription_id=subscription_id
    ).first()
    if existing:
        return

    subscription = stripe.Subscription.retrieve(subscription_id)

    metadata = subscription.get("metadata") or {}
    user_id = metadata.get("user_id")
    plan_id = metadata.get("plan_id")

    if not user_id or not plan_id:
        # Stripe race condition - metadata not propagated yet
        return

    record = UserSubscription(
        user_id=int(user_id),
        plan_id=int(plan_id),
        stripe_customer_id=session["customer"],
        stripe_subscription_id=subscription.id,
        status=subscription.status,
        # DO NOT set current_period_* here
    )

    db.session.add(record)
    db.session.commit()

    pending = PendingCreditGrant.query.filter_by(
        stripe_subscription_id=subscription.id
    ).all()

    for grant in pending:
        handle_invoice_paid(
            stripe.Invoice.retrieve(grant.stripe_invoice_id)
        )
        db.session.delete(grant)

    db.session.commit()


def handle_payment_failed(invoice):
    subscription_id = invoice["subscription"]

    sub = UserSubscription.query.filter_by(
        stripe_subscription_id=subscription_id
    ).first()

    if not sub:
        return

    sub.status = "past_due"

    profile = Profile.query.filter_by(user_id=sub.user_id).first()
    if profile:
        profile.is_active = False
        profile.application_mode = "paused"
        profile.deactivate_reason = "payment_failed"

    db.session.commit()


def handle_subscription_deleted(subscription):
    sub = UserSubscription.query.filter_by(
        stripe_subscription_id=subscription["id"]
    ).first()

    if not sub:
        return

    # Update subscription status
    sub.status = "canceled"

    # Pause automation
    profile = Profile.query.filter_by(user_id=sub.user_id).first()
    if profile:
        profile.is_active = False
        profile.application_mode = "paused"
        profile.deactivate_reason = "subscription_cancelled"

    db.session.commit()


@billing_bp.route("/subscribe/<int:plan_id>")
@login_required
def subscribe(plan_id):
    plan = SubscriptionPlan.query.get_or_404(plan_id)

    session = stripe.checkout.Session.create(
        mode="subscription",
        customer_email=current_user.email,
        line_items=[{
            "price": plan.stripe_price_id,
            "quantity": 1,
        }],
	    success_url=url_for("onboarding.edit_cv", _external=True),
	    cancel_url=url_for("onboarding.onboarding_plan", _external=True),
	    subscription_data={
            "metadata": {
                "user_id": current_user.id,
                "plan_id": plan.id,
            }
        }
    )

    return redirect(session.url)

def extract_subscription_id(invoice):
    # Preferred location (new Stripe schema)
    parent = invoice.get("parent") or {}
    sub_details = parent.get("subscription_details") or {}
    if sub_details.get("subscription"):
        return sub_details["subscription"]

    # Fallback: line item parent
    for line in invoice.get("lines", {}).get("data", []):
        parent = line.get("parent") or {}
        sub_item = parent.get("subscription_item_details") or {}
        if sub_item.get("subscription"):
            return sub_item["subscription"]

    return None

@billing_bp.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature")

    try:
        event = stripe.Webhook.construct_event(
            payload,
            sig_header,
            os.getenv("STRIPE_WEBHOOK_SECRET"),
        )
    except Exception as e:
        print("Webhook signature error:", e)
        return "", 400

    event_type = event["type"]
    obj = event["data"]["object"]
    # print(event_type)
    # print(obj)

    try:
        if event_type == "checkout.session.completed":
            if obj.get("mode") == "subscription":
                handle_checkout_completed(obj)

        elif event_type == "invoice.paid":
            handle_invoice_paid(obj)


        elif event_type == "invoice.payment_failed":
            if obj.get("subscription"):
                handle_payment_failed(obj)

        elif event_type == "customer.subscription.deleted":
            handle_subscription_deleted(obj)

    except Exception as e:
        # IMPORTANT: log but still return 200
        # Stripe will retry if you return 500
        print("Webhook handler error:", e)

    return "", 200

@billing_bp.route("/billing/portal")
@login_required
def billing_portal():
    sub = (
        UserSubscription.query
        .filter_by(user_id=current_user.id)
        .order_by(UserSubscription.created_at.desc())
        .first()
    )

    if not sub:
        return redirect(url_for("onboarding.onboarding_plan"))

    session = stripe.billing_portal.Session.create(
        customer=sub.stripe_customer_id,
        return_url=url_for("dashboard.dashboard_home", _external=True),
    )

    return redirect(session.url)