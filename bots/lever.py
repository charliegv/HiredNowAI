import os
import json
import random
import asyncio
import mimetypes
from typing import List, Dict, Any, Optional

from dotenv import load_dotenv
from playwright.async_api import async_playwright
from openai import AsyncOpenAI

from bs4 import BeautifulSoup

from bots.base import BaseATSBot, ApplyResult
from utils.s3_uploader import upload_to_s3

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

        # Realistic desktop user agents
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        ]

    # ============================================================
    # Human style helpers
    # ============================================================
    async def human_sleep(self, min_s=0.4, max_s=1.2):
        await asyncio.sleep(random.uniform(min_s, max_s))

    async def human_mouse_move(self, page, target_x, target_y, steps=20):
        # Start from some random point in the viewport
        start_x = random.uniform(50, 200)
        start_y = random.uniform(80, 180)

        await page.mouse.move(start_x, start_y, steps=3)

        for i in range(steps):
            t = i / float(steps)
            xt = start_x + (target_x - start_x) * (t * t)
            yt = start_y + (target_y - start_y) * (t * t)
            await page.mouse.move(xt, yt, steps=1)
            await asyncio.sleep(random.uniform(0.01, 0.04))

    async def human_type(self, locator, text: str):
        try:
            await locator.click()
        except Exception:
            pass

        try:
            await locator.fill("")
        except Exception:
            pass

        for ch in text:
            await locator.type(ch, delay=random.randint(40, 120))

    async def move_mouse_to_locator(self, page, locator):
        try:
            box = await locator.bounding_box()
            if not box:
                return
            target_x = box["x"] + box["width"] / 2
            target_y = box["y"] + box["height"] / 2
            steps = random.randint(15, 35)
            await page.mouse.move(target_x, target_y, steps=steps)
        except Exception:
            pass

    async def random_scroll(self, page):
        try:
            delta = random.randint(200, 700)
            await page.mouse.wheel(0, delta)
            await self.human_sleep(0.2, 0.7)
        except Exception:
            pass

    # ============================================================
    # Smart answering logic
    # ============================================================
    def answer_question(self, question: str, ai_data: dict, profile_answers: dict) -> str:
        if not isinstance(profile_answers, dict):
            profile_answers = {}

        q = (question or "").strip().lower()

        # Profile stored answers
        if profile_answers:
            for key, val in profile_answers.items():
                key_l = str(key).lower()
                if q in key_l or key_l in q:
                    return str(val)

        # Simple resume based answers
        if "name" in q and "full" in q:
            first = ai_data.get("first_name", "")
            last = ai_data.get("last_name", "")
            full = f"{first} {last}".strip()
            if full:
                return full

        if "first name" in q:
            return ai_data.get("first_name", "") or ai_data.get("given_name", "")

        if "last name" in q or "surname" in q or "family name" in q:
            return ai_data.get("last_name", "") or ai_data.get("family_name", "")

        if "email" in q:
            return ai_data.get("email", "")

        if "phone" in q or "mobile" in q or "telephone" in q:
            return ai_data.get("phone", "")

        if "skills" in q:
            skills = ai_data.get("skills", [])
            if isinstance(skills, list):
                return ", ".join(skills[:8])
            return str(skills)

        if "experience" in q:
            return ai_data.get("summary", "I have strong relevant experience for this role.")

        # Eligibility and logistics
        if "authorized" in q or "authorised" in q:
            return "Yes"
        if "work permit" in q or "work authorization" in q or "work authorisation" in q:
            return "Yes"
        if "sponsor" in q or "visa" in q:
            return "No"
        if "relocate" in q or "relocation" in q:
            return "Yes"
        if "start" in q or "availability" in q or "available to begin" in q:
            return "Immediately"
        if "salary" in q or "compensation" in q or "pay expectation" in q:
            return "Open to a fair market rate"

        # Common yes or no style
        if "criminal" in q or "offence" in q or "offense" in q:
            return "No"
        if "disability" in q and "accommodation" in q:
            return "No accommodation is required."

        # Default positive answer
        return "Yes"

    # ============================================================
    # Deterministic Lever parsing
    # ============================================================
    def extract_lever_questions(self, html: str) -> List[Dict[str, Any]]:
        """
        Parse Lever application form questions using the consistent .application-question pattern.
        Returns list of:
        {
          "question": "...",
          "selector": "css selector",
          "type": "input" | "textarea" | "select" | "file",
          "options": [ { "value": "...", "label": "..." }, ... ]  # only for select
        }
        """
        soup = BeautifulSoup(html, "html.parser")
        results: List[Dict[str, Any]] = []

        blocks = soup.select(".application-question")

        for block in blocks:
            # Some questions use <div class="application-label">,
            # some use <label> with nested div.
            label_el = None

            if block.find("label"):
                label_el = block.find("label")
            elif block.find(class_="application-label"):
                label_el = block.find(class_="application-label")

            if not label_el:
                continue

            q_text = label_el.get_text(" ", strip=True)
            if not q_text:
                continue

            q_lower = q_text.lower()

            # Resume upload: special case
            resume_input = block.find("input", attrs={"type": "file"})
            if resume_input and resume_input.get("name"):
                selector = f"input[name='{resume_input.get('name')}']"
                results.append(
                    {
                        "question": q_text,
                        "selector": selector,
                        "type": "file",
                        "options": [],
                    }
                )
                continue

            # Other fields inside this question block
            input_el = None
            textarea_el = None
            select_el = None

            # Avoid hidden inputs where possible
            for el in block.find_all("input"):
                if el.get("type") in ("hidden", "submit", "button", "file"):
                    continue
                input_el = el
                break

            textarea_el = block.find("textarea")
            select_el = block.find("select")

            # Choose the main field for this question
            field_selector = None
            ftype = None
            options: List[Dict[str, str]] = []

            if input_el and input_el.get("name"):
                field_selector = f"input[name='{input_el.get('name')}']"
                ftype = "input"
            elif textarea_el and textarea_el.get("name"):
                field_selector = f"textarea[name='{textarea_el.get('name')}']"
                ftype = "textarea"
            elif select_el and select_el.get("name"):
                field_selector = f"select[name='{select_el.get('name')}']"
                ftype = "select"
                for opt in select_el.find_all("option"):
                    val = opt.get("value") or ""
                    label = opt.get_text(" ", strip=True)
                    options.append({"value": val, "label": label})

            if not field_selector or not ftype:
                continue

            results.append(
                {
                    "question": q_text,
                    "selector": field_selector,
                    "type": ftype,
                    "options": options,
                }
            )

        return results

    def choose_select_value(
        self,
        question: str,
        options: List[Dict[str, str]],
        ai_data: dict,
        profile_answers: dict,
    ) -> Optional[str]:
        if not options:
            return None

        # Remove empty or placeholder options
        filtered = []
        for opt in options:
            label = (opt.get("label") or "").strip()
            value = (opt.get("value") or "").strip()
            if not label and not value:
                continue
            low = label.lower()
            if "select" in low and "please" in low:
                continue
            if "choose" in low:
                continue
            filtered.append(opt)

        if not filtered:
            filtered = options

        # Simple heuristic: just choose first valid option
        chosen = filtered[0]
        return chosen.get("value") or chosen.get("label") or None

    def build_deterministic_actions(
        self,
        html: str,
        ai_data: dict,
        profile_answers: dict,
    ) -> List[Dict[str, Any]]:
        """
        Build auto actions from parsed Lever questions and smart answer logic.
        Used on every cycle before GPT actions.
        """
        actions: List[Dict[str, Any]] = []
        questions = self.extract_lever_questions(html)

        for q in questions:
            q_text = q["question"]
            selector = q["selector"]
            q_type = q["type"]

            if q_type == "file":
                # Let dedicated upload logic handle resume, skip here
                continue

            if q_type in ("input", "textarea"):
                answer = self.answer_question(q_text, ai_data, profile_answers)
                actions.append(
                    {
                        "action": "auto_answer",
                        "selector": selector,
                        "question": q_text,
                        "value": answer,
                    }
                )

            elif q_type == "select":
                value = self.choose_select_value(
                    q_text, q.get("options") or [], ai_data, profile_answers
                )
                if value:
                    actions.append(
                        {
                            "action": "select",
                            "selector": selector,
                            "value": value,
                            "question": q_text,
                        }
                    )

        return actions

    # ============================================================
    # Direct resume upload to Lever API
    # ============================================================
    async def upload_resume_to_lever(self, cv_path: str, account_id: str):
        url = "https://jobs.lever.co/parseResume"

        mime_type, _ = mimetypes.guess_type(cv_path)
        if not mime_type:
            mime_type = "application/octet-stream"

        with open(cv_path, "rb") as f:
            files = {"resume": (os.path.basename(cv_path), f, mime_type)}
            data = {"accountId": account_id}
            response = requests.post(url, files=files, data=data, timeout=30)

        response.raise_for_status()
        return response.json()

    # ============================================================
    # Proxy and user agent
    # ============================================================
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

    # ============================================================
    # GPT agent for navigation and submit
    # ============================================================
    async def call_agent(
        self,
        html: str,
        current_url: str,
        ai_data: dict,
        profile_answers: dict,
        cover_letter: str,
        state: dict,
    ) -> List[Dict[str, Any]]:
        truncated_html = html[:200000]

        prompt = f"""
You are an expert browser automation planner for Lever job applications.
You output ONLY a JSON array of actions.

Most text fields are already covered by a deterministic system that fills them from user data.
You should focus on:

- clicking navigation and apply buttons
- handling multi step forms
- clicking submit buttons
- waiting for new elements when needed

User resume data:
{json.dumps(ai_data, indent=2)}

User stored answers:
{json.dumps(profile_answers, indent=2)}

Cover letter (use only for fields that clearly request a full cover letter, otherwise keep answers short):
{cover_letter}

Current page url:
{current_url}

Recent state:
{json.dumps(state, indent=2)}

Lever specific guidance:

- Application form is inside <form id="application-form">.
- Question blocks use .application-question but text inputs are already handled for you.
- You MAY still use auto_answer for questions that look clearly missed or for new ones that appear after choices.
- Valid submit buttons include:
  * button[type='submit']
  * button.postings-btn
  * button.template-btn-submit
  * button:has-text("Apply")
  * button:has-text("Submit")

Before finishing you must click one submit style button unless validation errors or captcha blocks it.

Allowed actions (JSON only):

- {{"action": "goto", "url": "..."}}
- {{"action": "fill", "selector": "...", "value": "..."}}
- {{"action": "upload", "selector": "...", "value": "<cv>"}}
- {{"action": "click", "selector": "..."}}
- {{"action": "wait_for", "selector": "...", "timeout": 15000}}
- {{"action": "finish", "status": "...", "message": "..."}}
- {{"action": "auto_answer", "selector": "...", "question": "..."}}

HTML of the current page:
{truncated_html}

Plan the next small sequence of actions that moves the application closer to fully submitted.
Then end with a finish action.
"""

        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model="gpt-4.1-mini",
                    messages=[{"role": "user", "content": prompt}],
                ),
                timeout=12,
            )
        except asyncio.TimeoutError:
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
            if not isinstance(actions, list):
                raise ValueError("Agent did not return a list")
            return actions
        except Exception:
            return [
                {
                    "action": "finish",
                    "status": "retry",
                    "message": "Agent returned invalid JSON",
                }
            ]

    # ============================================================
    # Execute actions
    # ============================================================
    async def execute_actions(
        self,
        page,
        actions: List[Dict[str, Any]],
        cv_path: str,
        ai_data: dict,
        profile_answers: dict,
        state: dict,
    ) -> bool:
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

                sel_lower = sel.lower()

                if self.test_mode and ("submit" in sel_lower or "apply" in sel_lower):
                    print("[TEST MODE] Skipping submit click:", sel)
                    continue

                try:
                    locator = page.locator(sel)
                    await locator.scroll_into_view_if_needed()
                    await self.human_sleep(0.3, 0.9)
                    await self.move_mouse_to_locator(page, locator)
                    await self.human_sleep(0.2, 0.7)
                    await locator.click()
                    if "submit" in sel_lower or "apply" in sel_lower:
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

            elif action == "select":
                sel = step.get("selector")
                val = step.get("value")
                if sel and val is not None:
                    try:
                        locator = page.locator(sel)
                        await locator.scroll_into_view_if_needed()
                        await self.human_sleep(0.3, 0.9)
                        await self.move_mouse_to_locator(page, locator)
                        await self.human_sleep(0.2, 0.6)
                        await locator.select_option(val)
                    except Exception:
                        continue

            elif action == "auto_answer":
                sel = step.get("selector")
                question = step.get("question")
                if not sel:
                    continue

                # Use provided value if present, otherwise recalc
                answer = step.get("value")
                if answer is None:
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

    # ============================================================
    # Main apply flow
    # ============================================================
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

        ai_raw = user.get("ai_cv_data")
        if isinstance(ai_raw, str):
            try:
                ai_data = json.loads(ai_raw)
            except Exception:
                ai_data = {}
        else:
            ai_data = ai_raw or {}

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
                    context = await browser.new_context(
                        user_agent=user_agent,
                        viewport={"width": 1366, "height": 768},
                        locale="en-GB",
                    )

                    page = await context.new_page()

                    await page.goto(job_url, timeout=60000)
                    await page.wait_for_load_state("domcontentloaded")
                    await self.human_sleep(0.8, 1.6)
                    await self.random_scroll(page)

                    # Move to apply url if visible
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

                    # Control loop: deterministic answers plus GPT navigation
                    for _ in range(4):
                        html = await page.content()
                        current_url = page.url

                        deterministic_actions = self.build_deterministic_actions(
                            html, ai_data, profile_answers
                        )

                        gpt_actions = await self.call_agent(
                            html=html,
                            current_url=current_url,
                            ai_data=ai_data,
                            profile_answers=profile_answers,
                            cover_letter=cover_letter,
                            state=state,
                        )

                        actions = deterministic_actions + gpt_actions

                        finished = await self.execute_actions(
                            page, actions, cv_path, ai_data, profile_answers, state
                        )

                        if finished:
                            break

                        await self.human_sleep(0.8, 1.5)

                    # Hybrid resume upload fallback
                    if not state["cv_uploaded"]:
                        uploaded_by_browser = False

                        try:
                            file_input = page.locator(
                                "input[type='file'], "
                                "input[name='resume'], "
                                "input[name*='resume'], "
                                "input[id*='resume']"
                            )

                            if await file_input.count() > 0:
                                target = file_input.first
                                await target.scroll_into_view_if_needed()
                                await self.human_sleep(0.5, 1.0)

                                box = await target.bounding_box()
                                if box:
                                    await self.human_mouse_move(
                                        page,
                                        box["x"] + random.randint(5, 20),
                                        box["y"] + random.randint(5, 15),
                                        steps=random.randint(14, 25),
                                    )
                                    await self.human_sleep(0.4, 1.0)
                                    await page.mouse.down()
                                    await asyncio.sleep(random.uniform(0.05, 0.25))
                                    await page.mouse.up()
                                    await self.human_sleep(0.6, 1.3)

                                await target.set_input_files(cv_path)
                                state["cv_uploaded"] = True
                                uploaded_by_browser = True

                        except Exception as e:
                            print("[LeverBot] Human style CV upload failed:", e)

                        # Backend parseResume path kept disabled for now
                        # to avoid complexity and risk of mismatch

                    final_url = page.url
                    screenshot_path = f"/tmp/lever_agent_{user.get('user_id')}_{job.get('id')}.png"
                    screenshot_url = None

                    try:
                        await page.screenshot(path=screenshot_path, full_page=True)
                        screenshot_url = upload_to_s3(
                            screenshot_path, folder="screenshots"
                        )
                    except Exception:
                        pass

                    if self.test_mode:
                        return ApplyResult("success", "Test mode complete", screenshot_url)

                    # Captcha detection
                    try:
                        captcha_present = (
                            await page.locator("iframe[title*='captcha']").count() > 0
                            or await page.locator("div.g-recaptcha").count() > 0
                            or await page.locator("text=I am not a robot").count() > 0
                        )
                    except Exception:
                        captcha_present = False

                    if captcha_present:
                        return ApplyResult(
                            "retry", "CAPTCHA detected on Lever page", screenshot_url
                        )

                    if not state["submit_clicked"]:
                        return ApplyResult(
                            "retry", "Submit button never clicked", screenshot_url
                        )

                    if not state["cv_uploaded"]:
                        return ApplyResult(
                            "retry", "CV not uploaded", screenshot_url
                        )

                    # Confirmation detection
                    try:
                        thank_you_count = await page.locator("text=Thank you").count()
                        thanks_apply_count = await page.locator(
                            "text=Thanks for applying"
                        ).count()
                    except Exception:
                        thank_you_count = 0
                        thanks_apply_count = 0

                    if thank_you_count == 0 and thanks_apply_count == 0:
                        return ApplyResult(
                            "retry",
                            "No confirmation detected after submit",
                            screenshot_url,
                        )

                    msg = "Submitted Lever application, confirmation detected"
                    return ApplyResult("success", msg, screenshot_url)

                finally:
                    try:
                        await browser.close()
                    except Exception:
                        pass

        except Exception as e:
            return ApplyResult("retry", f"Lever agent error: {str(e)}")
