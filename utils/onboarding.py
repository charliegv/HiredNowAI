from flask import redirect, url_for

ONBOARDING_ROUTES = {
    1: "onboarding.step1",
    2: "onboarding.step2",
    3: "onboarding.step3",
    4: "onboarding.step4",
	5: "onboarding.onboarding_intent",
    6: "onboarding.onboarding_plan",
	7: "onboarding.edit_cv",
}


def resume_onboarding(profile):
    route = ONBOARDING_ROUTES.get(profile.onboarding_step)
    if route:
        return redirect(url_for(route))
    return None

def recommend_plan(intent, weekly_apps):
    if intent == "fast" and weekly_apps >= 25:
        return "pro"
    if weekly_apps >= 10:
        return "starter"
    return "credits"

