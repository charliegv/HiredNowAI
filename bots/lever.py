import os
import json
import random
import asyncio

from dotenv import load_dotenv
from playwright.async_api import async_playwright
from openai import AsyncOpenAI

from bots.base import BaseATSBot, ApplyResult
from utils.s3_uploader import upload_to_s3

import mimetypes
import requests

load_dotenv()

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))


class LeverBot(BaseATSBot):

    def __init__(self):
        proxy_file = os.getenv("PROXY_FILE", "/mnt/data/Webshare_1000_proxies.txt")

        if os.path.exists(proxy_file):
            with open(proxy_file, "r") as f:
                self.proxies = [line.strip() for line in f if line.strip()]
        else:
            self.proxies = []

        self.test_mode = os.getenv("TEST_MODE", "false").lower() == "true"
        self.show_browser = os.getenv("SHOW_BROWSER", "false").lower() == "true"

        # A small pool of realistic desktop user agents
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        ]

    # ----------------------------------------------------------------------
    # Small helpers for human like behaviour
    # ----------------------------------------------------------------------
    async def human_sleep(self, min_s=0.4, max_s=1.2):
        await asyncio.sleep(random.uniform(min_s, max_s))

    async def human_mouse_move(self, page, target_x, target_y, steps=20):
        """
        Human-like mouse movement without relying on mouse.position (not available in async playwright)
        Uses a bezier-like curve between random start offset and target.
        """

        # Pick a random starting point near the top-left of the viewport
        # since we do not know the real current mouse position
        start_x = random.uniform(50, 200)
        start_y = random.uniform(80, 180)

        # Move mouse instantly to starting point (off element)
        await page.mouse.move(start_x, start_y, steps=3)

        # Now simulate curved human movement to target
        for i in range(steps):
            t = i / float(steps)
            # Bezier-like easing
            xt = start_x + (target_x - start_x) * (t * t)
            yt = start_y + (target_y - start_y) * (t * t)
            await page.mouse.move(xt, yt, steps=1)
            await asyncio.sleep(random.uniform(0.01, 0.04))

    async def human_type(self, locator, text: str):
        """Type text character by character with random delays."""
        # Clear field first in a realistic way
        try:
            await locator.click()
        except Exception:
            pass
        try:
            await locator.fill("")
        except Exception:
            # If fill fails, continue and type anyway
            pass

        for ch in text:
            await locator.type(ch, delay=random.randint(40, 120))

    async def move_mouse_to_locator(self, page, locator):
        """Move mouse smoothly to center of an element."""
        try:
            box = await locator.bounding_box()
            if not box:
                return
            target_x = box["x"] + box["width"] / 2
            target_y = box["y"] + box["height"] / 2
            steps = random.randint(15, 35)
            await page.mouse.move(target_x, target_y, steps=steps)
        except Exception:
            # If we cannot get box, ignore and let normal click handle it
            pass

    async def random_scroll(self, page):
        """Small random scroll to look more like a user browsing."""
        try:
            delta = random.randint(200, 700)
            await page.mouse.wheel(0, delta)
            await self.human_sleep(0.2, 0.7)
        except Exception:
            pass

    # ----------------------------------------------------------------------
    # SMART QUESTION ANSWERING LAYER
    # ----------------------------------------------------------------------
    def answer_question(self, question, ai_data, profile_answers):
        """
        Smart fallback answer system:
        1. Use profile.application_data
        2. Use resume JSON
        3. Positive fallback
        """

        if not isinstance(profile_answers, dict):
            profile_answers = {}

        q = (question or "").lower()

        # 1. Check stored profile answers
        if profile_answers:
            for key, val in profile_answers.items():
                if key.lower() in q or q in key.lower():
                    return val

        # 2. Resume based answers
        if "name" in q:
            return f"{ai_data.get('first_name', '')} {ai_data.get('last_name', '')}".strip()
        if "email" in q:
            return ai_data.get("email", "")
        if "phone" in q:
            return ai_data.get("phone", "")
        if "skills" in q:
            return ", ".join(ai_data.get("skills", []))
        if "experience" in q:
            return ai_data.get("summary", "I have strong relevant experience.")

        # 3. Smart acceptance focused fallbacks
        if "authorized" in q or "authorised" in q:
            return "Yes"
        if "sponsor" in q or "visa" in q:
            return "No"
        if "relocate" in q:
            return "Yes"
        if "start" in q or "availability" in q:
            return "Immediately"
        if "salary" in q or "compensation" in q:
            return "Open to market rate"

        return "Yes"

    # ----------------------------------------------------------------------
    # DIRECT RESUME UPLOAD TO LEVER API (UNCHANGED LOGIC)
    # ----------------------------------------------------------------------
    async def upload_resume_to_lever(self, cv_path: str, account_id: str):
        """
        Upload resume directly to Lever /parseResume endpoint.
        Returns parsed profile JSON including resumeStorageId.
        """
        import mimetypes
        import requests

        url = "https://jobs.lever.co/parseResume"

        mime_type, _ = mimetypes.guess_type(cv_path)
        if not mime_type:
            mime_type = "application/octet-stream"

        with open(cv_path, "rb") as f:
            files = {
                "resume": (os.path.basename(cv_path), f, mime_type),
            }
            data = {"accountId": account_id}

            response = requests.post(url, files=files, data=data, timeout=30)

        response.raise_for_status()
        return response.json()

    # ----------------------------------------------------------------------
    def pick_proxy(self):
        if not self.proxies:
            return None

        raw = random.choice(self.proxies)
        parts = raw.split(":")

        if len(parts) == 4:
            ip, port, user, password = parts
            return {
                "server": f"http://{ip}:{port}",
                "username": user,
                "password": password,
            }

        if len(parts) == 2:
            ip, port = parts
            return {"server": f"http://{ip}:{port}"}

        return None

    def pick_user_agent(self):
        return random.choice(self.user_agents)

    # ----------------------------------------------------------------------
    # MAIN AI AGENT CALL (SMART ANSWERS + TIMEOUT)
    # ----------------------------------------------------------------------
    async def call_agent(self, html, current_url, ai_data, profile_answers, cover_letter):

        truncated_html = html[:350000]

        prompt = f"""
You are an expert browser automation planner for Lever job applications.
You output ONLY a JSON array of actions.

You receive:

USER RESUME DATA:
{json.dumps(ai_data, indent=2)}

USER STORED APPLICATION ANSWERS:
{json.dumps(profile_answers, indent=2)}

Use ALL available user data to fill fields correctly.

RULES FOR ANSWERING QUESTIONS:
- If profile answers match the question, use them.
- If the user's resume data (name, email, phone, skills, summary) can answer the question, use that.
- If the question is unknown, answer in a way that maximises acceptance:
    * Work eligibility -> "Yes"
    * Sponsorship -> "No"
    * Relocation -> "Yes"
    * Start date -> "Immediately"
    * Salary -> "Open to market rate"
    * Anything else -> brief positive "Yes"

- NEVER leave required fields empty.
- NEVER write long paragraphs in answer fields.
- Cover letter goes into any textarea asking for one.

IMPORTANT: Lever custom questions follow a consistent pattern.

To answer questions, follow this detection logic:

Look for question text inside:
- <label>...</label>
- <span>...</span>
- <div class="application-question">...</div>
- <div class="application-label">...</div>
- Any element whose innerText ends with ':' or '?'

Then the associated input/select field is typically:
- The next <input> element
- Or an <input> sibling within the same parent block
- Or <textarea> or <select> inside the same container

When you locate a question + field pair:
Output an auto_answer action:

{{
  "action": "auto_answer",
  "selector": "input[name='...'] or css path",
  "question": "The question text you detected"
}}

You MUST generate auto_answer for ALL visible questions, even if they are not required.

IMPORTANT: Submit button detection.
You MUST click a submit button before finish, unless you are blocked by validation or CAPTCHA.

Valid submit button selectors include ANY of:
- button[type='submit']
- button:has-text("Apply")
- button:has-text("Submit")
- button[class*="apply"]
- button[data-qa*="apply"]
- input[type="submit"]

Click at least one of these before returning a finish action.

Allowed actions (JSON only):
- {{"action": "goto", "url": "..."}}
- {{"action": "fill", "selector": "...", "value": "..."}}
- {{"action": "upload", "selector": "...", "value": "<cv>"}}
- {{"action": "click", "selector": "..."}}
- {{"action": "wait_for", "selector": "...", "timeout": 15000}}
- {{"action": "finish", "status": "...", "message": "..."}}
- {{"action": "auto_answer", "selector": "...", "question": "..."}}

Current Page URL:
{current_url}

Cover Letter:
{cover_letter}

HTML:
{truncated_html}

Plan your next actions to navigate to the /apply page, fill all fields,
answer all questions, upload the CV using "<cv>", and click submit.
Then return a finish action.
"""

        try:
            # Hard timeout so the agent cannot hang forever
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model="gpt-4.1-mini",
                    messages=[{"role": "user", "content": prompt}],
                ),
                timeout=12,
            )
        except asyncio.TimeoutError:
            # If the model stalls, request a retry
            return [
                {
                    "action": "finish",
                    "status": "retry",
                    "message": "Agent timed out",
                }
            ]

        content = response.choices[0].message.content
        cleaned = content.replace("```json", "").replace("```", "").strip()

        try:
            actions = json.loads(cleaned)
            if isinstance(actions, dict):
                actions = [actions]
            return actions
        except Exception:
            return [
                {
                    "action": "finish",
                    "status": "retry",
                    "message": "Agent returned invalid JSON",
                }
            ]

    # ----------------------------------------------------------------------
    # EXECUTE BROWSER ACTIONS (WITH auto_answer SUPPORT + HUMANISATION)
    # ----------------------------------------------------------------------
    async def execute_actions(self, page, actions, cv_path, ai_data, profile_answers, state):
        for step in actions:
            action = step.get("action")

            if action == "goto":
                url = step.get("url")
                if not url:
                    continue
                try:
                    await self.human_sleep(0.6, 1.5)
                    await page.goto(url, timeout=60000)
                    await page.wait_for_load_state("domcontentloaded")
                    await self.random_scroll(page)
                    await self.human_sleep(0.6, 1.4)
                except Exception:
                    continue

            elif action == "click":
                sel = step.get("selector")
                if not sel:
                    continue

                lower_sel = sel.lower()

                # In test mode skip submit clicks
                if self.test_mode and ("submit" in lower_sel or "apply" in lower_sel):
                    print("[TEST MODE] Skipping submit click:", sel)
                    continue

                try:
                    locator = page.locator(sel)
                    await locator.scroll_into_view_if_needed()
                    await self.human_sleep(0.3, 0.9)
                    await self.move_mouse_to_locator(page, locator)
                    await self.human_sleep(0.2, 0.7)
                    await locator.click()
                    if "submit" in lower_sel or "apply" in lower_sel:
                        state["submit_clicked"] = True
                except Exception:
                    continue

            elif action == "fill":
                sel = step.get("selector")
                val = step.get("value", "")
                if sel:
                    try:
                        locator = page.locator(sel)
                        await locator.scroll_into_view_if_needed()
                        await self.human_sleep(0.3, 0.9)
                        await self.move_mouse_to_locator(page, locator)
                        await self.human_sleep(0.2, 0.6)
                        await self.human_type(locator, val)
                    except Exception:
                        continue

            # Agent provided smart answers
            elif action == "auto_answer":
                sel = step.get("selector")
                question = step.get("question")
                if sel:
                    answer = self.answer_question(question, ai_data, profile_answers)
                    try:
                        locator = page.locator(sel)
                        await locator.scroll_into_view_if_needed()
                        await self.human_sleep(0.3, 0.9)
                        await self.move_mouse_to_locator(page, locator)
                        await self.human_sleep(0.2, 0.6)
                        await self.human_type(locator, answer)
                    except Exception:
                        continue

            elif action == "upload":
                sel = step.get("selector")
                val = step.get("value")
                if sel and val == "<cv>":
                    try:
                        locator = page.locator(sel)
                        await locator.scroll_into_view_if_needed()
                        await self.human_sleep(0.6, 1.2)
                        await self.move_mouse_to_locator(page, locator)
                        await self.human_sleep(0.3, 0.8)
                        await locator.set_input_files(cv_path)
                        state["cv_uploaded"] = True
                    except Exception:
                        continue

            elif action == "wait_for":
                sel = step.get("selector")
                timeout = step.get("timeout", 10000)
                if sel:
                    try:
                        await page.wait_for_selector(sel, timeout=timeout)
                    except Exception:
                        continue

            elif action == "finish":
                state["agent_finish_status"] = step.get("status")
                state["agent_finish_message"] = step.get("message")
                return True

        return False

    # ----------------------------------------------------------------------
    # MAIN APPLY LOGIC
    # ----------------------------------------------------------------------
    async def apply(self, job, user, cv_path):

        job_url = (
            job.get("apply_url")
            or job.get("job_url")
            or job.get("url")
            or job.get("redirect_url")
        )
        if not job_url:
            return ApplyResult(status="failed", message="No job URL found")

        cover_letter = (
            user.get("cover_letter_text")
            or "Thank you for considering my application."
        )

        # CV JSON variant
        ai_raw = user.get("ai_cv_data")
        if isinstance(ai_raw, str):
            try:
                ai_data = json.loads(ai_raw)
            except Exception:
                ai_data = {}
        else:
            ai_data = ai_raw or {}

        # Stored answers
        raw = user.get("application_data")
        if not raw:
            profile_answers = {}
        elif isinstance(raw, dict):
            profile_answers = raw
        else:
            try:
                profile_answers = json.loads(raw)
                if not isinstance(profile_answers, dict):
                    profile_answers = {}
            except Exception:
                profile_answers = {}

        proxy_config = self.pick_proxy()
        user_agent = self.pick_user_agent()

        try:
            async with async_playwright() as p:
                launch_args = {"headless": not self.show_browser}
                if proxy_config:
                    launch_args["proxy"] = proxy_config
                if self.show_browser:
                    launch_args["slow_mo"] = 300

                browser = await p.chromium.launch(**launch_args)

                try:
                    # Use a real browser context with user agent and viewport
                    context = await browser.new_context(
                        user_agent=user_agent,
                        viewport={"width": 1366, "height": 768},
                        locale="en-GB",
                    )

                    page = await context.new_page()

                    await page.goto(job_url, timeout=60000)
                    await page.wait_for_load_state("domcontentloaded")
                    await self.human_sleep(0.8, 1.6)

                    # Small initial scroll to look more human
                    await self.random_scroll(page)

                    # Try pre navigation to apply page
                    try:
                        apply_link = page.locator("a.postings-btn[href*='/apply']")
                        if await apply_link.count() > 0:
                            apply_url = await apply_link.first.get_attribute("href")
                            if apply_url:
                                await self.human_sleep(0.5, 1.0)
                                await page.goto(apply_url, timeout=60000)
                                await page.wait_for_load_state("domcontentloaded")
                                await self.human_sleep(0.8, 1.6)
                                await self.random_scroll(page)
                    except Exception:
                        pass

                    state = {
                        "submit_clicked": False,
                        "cv_uploaded": False,
                        "agent_finish_status": None,
                        "agent_finish_message": None,
                    }

                    # ----------------------------------------------------
                    # MULTI CYCLE AGENT CONTROL LOOP (BOUNDED)
                    # ----------------------------------------------------
                    for _ in range(4):
                        html = await page.content()
                        current_url = page.url

                        actions = await self.call_agent(
                            html=html,
                            current_url=current_url,
                            ai_data=ai_data,
                            profile_answers=profile_answers,
                            cover_letter=cover_letter,
                        )

                        finished = await self.execute_actions(
                            page, actions, cv_path, ai_data, profile_answers, state
                        )

                        if finished:
                            break

                        await self.human_sleep(0.8, 1.5)

                    # ----------------------------------------------------
                    # HYBRID CV UPLOAD FALLBACK
                    # ----------------------------------------------------
                    if not state["cv_uploaded"]:

                        uploaded_by_browser = False

                        try:
                            file_input = page.locator(
                                "input[type='file'], input[name*='resume'], input[id*='resume']"
                            )

                            if await file_input.count() > 0:

                                await file_input.first.scroll_into_view_if_needed()
                                await self.human_sleep(0.5, 1.0)

                                box = await file_input.first.bounding_box()

                                if box:
                                    # Human-like move near the element
                                    await self.human_mouse_move(
                                        page,
                                        box["x"] + random.randint(5, 20),
                                        box["y"] + random.randint(5, 15),
                                        steps=random.randint(14, 25),
                                    )

                                    await self.human_sleep(0.4, 1.0)

                                    # Human click
                                    await page.mouse.down()
                                    await asyncio.sleep(random.uniform(0.05, 0.25))
                                    await page.mouse.up()

                                    await self.human_sleep(0.6, 1.3)

                                # Now upload the file
                                await file_input.first.set_input_files(cv_path)
                                state["cv_uploaded"] = True

                        except Exception as e:
                            print("[LeverBot] Humanized CV upload failed:", e)

                        # 2. Backend /parseResume upload only if browser upload didn't work
                        if not uploaded_by_browser and False:
                            try:
                                account_id = await page.locator("input[name='accountId']").input_value()
                                profile = await self.upload_resume_to_lever(cv_path, account_id)
                                resume_id = profile.get("resumeStorageId")

                                if not resume_id:
                                    raise Exception("No resumeStorageId returned")

                                print("[LeverBot] Backend /parseResume upload succeeded.")

                                # 3. Check if hidden input is writable BEFORE trying to fill it
                                try:
                                    is_editable = await page.evaluate("""
                                        const el = document.querySelector("input[name='resumeStorageId']");
                                        if (!el) return false;
                                        return !el.disabled && !el.readOnly && el.offsetParent !== null;
                                    """)
                                except Exception:
                                    is_editable = False

                                # 4. If editable, fill the hidden field (your chosen behavior)
                                if is_editable:
                                    try:
                                        await page.fill("input[name='resumeStorageId']", resume_id)
                                        print("[LeverBot] resumeStorageId field updated successfully.")
                                        state["cv_uploaded"] = True
                                    except Exception as e:
                                        print(f"[LeverBot] Hidden field fill failed (skipped): {e}")
                                else:
                                    print("[LeverBot] resumeStorageId input not editable — skipping fill.")

                                # 5. Fill name/email/phone IF blank
                                async def set_if_empty(sel, val):
                                    if not val:
                                        return
                                    loc = page.locator(sel)
                                    if await loc.count() == 0:
                                        return
                                    curr = await loc.input_value()
                                    if not curr.strip():
                                        await loc.fill(val)

                                await set_if_empty("input[name='name']", profile.get("name"))
                                await set_if_empty("input[name='email']", profile.get("email"))
                                await set_if_empty("input[name='phone']", profile.get("phone"))

                            except Exception as e:
                                print(f"[LeverBot] Backend CV upload failed: {e}")

                    # ----------------------------------------------------
                    # FINAL VALIDATION AND CAPTCHA AVOIDANCE CHECKS
                    # ----------------------------------------------------
                    final_url = page.url
                    screenshot_path = f"/tmp/lever_agent_{user.get('user_id')}_{job.get('id')}.png"
                    screenshot_url = None

                    try:
                        await page.screenshot(path=screenshot_path, full_page=True)
                        screenshot_url = upload_to_s3(screenshot_path, folder="screenshots")
                    except Exception:
                        pass

                    # Test mode: never actually apply
                    if self.test_mode:
                        return ApplyResult("success", "Test mode complete", screenshot_url)

                    # Basic reach check
                    if "/apply" not in final_url:
                        return ApplyResult("retry", "Did not reach apply page", screenshot_url)

                    # CAPTCHA detection, avoid false success
                    try:
                        captcha_present = (
                            await page.locator("iframe[title*='captcha']").count() > 0
                            or await page.locator("div.g-recaptcha").count() > 0
                            or await page.locator("text=I am not a robot").count() > 0
                        )
                    except Exception:
                        captcha_present = False

                    if captcha_present:
                        return ApplyResult("retry", "CAPTCHA detected on Lever page", screenshot_url)

                    if not state["submit_clicked"]:
                        return ApplyResult("retry", "Submit button never clicked", screenshot_url)

                    if not state["cv_uploaded"]:
                        return ApplyResult("retry", "CV not uploaded", screenshot_url)

                    # Confirmation detection
                    try:
                        thank_you_count = await page.locator("text=Thank you").count()
                    except Exception:
                        thank_you_count = 0

                    if thank_you_count == 0:
                        return ApplyResult("retry", "No confirmation detected after submit", screenshot_url)

                    msg = "Submitted Lever application (confirmation detected)"

                    return ApplyResult("success", msg, screenshot_url)

                finally:
                    try:
                        await browser.close()
                    except Exception:
                        pass

        except Exception as e:
            return ApplyResult("retry", f"Lever agent error: {str(e)}")
