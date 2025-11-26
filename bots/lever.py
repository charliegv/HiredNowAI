import os
import json
import random

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
    """
    Full agent driven Lever bot with strict validation + CV upload.

    Flow:
      1. Go to job_url
      2. Force navigation to /apply page if possible
      3. Agent reads DOM and returns JSON actions
      4. Browser executes actions (click, fill, upload, goto, wait_for, finish)
      5. After cycles, deterministic CV upload fallback if needed
      6. Strictly validate:
         - did we reach an /apply URL
         - did we click a submit-like button
    """

    def __init__(self):
        proxy_file = os.getenv("PROXY_FILE", "/mnt/data/Webshare_1000_proxies.txt")

        if os.path.exists(proxy_file):
            with open(proxy_file, "r") as f:
                self.proxies = [line.strip() for line in f if line.strip()]
        else:
            self.proxies = []

        # If true, do not enforce real success, just treat as successful dry run
        self.test_mode = os.getenv("TEST_MODE", "false").lower() == "true"

        # If true, show real browser window with slow motion for debugging
        self.show_browser = os.getenv("SHOW_BROWSER", "false").lower() == "true"

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

    async def call_agent(self, html, current_url, ai_data, cover_letter):
        """
        Ask o1 to plan next actions in JSON.

        Allowed actions:
          - {"action": "goto", "url": "..."}
          - {"action": "click", "selector": "..."}
          - {"action": "fill", "selector": "...", "value": "..."}
          - {"action": "upload", "selector": "...", "value": "<cv>"}
          - {"action": "wait_for", "selector": "...", "timeout": 10000}
          - {"action": "finish", "status": "success" | "retry" | "failed", "message": "..."}
        """

        # Let the agent see enough of the DOM to find the form + file inputs
        truncated_html = html[:200000]

        prompt = f"""
You are an expert browser automation planner for Lever job applications.

You control a browser through a JSON plan.
You see only the current page HTML and some user data.
Your goal is to make sure a job application is actually submitted.

Current page URL:
{current_url}

User data (from ai_cv_data):
{json.dumps(ai_data, indent=2)}

Cover letter text:
{cover_letter}

Page HTML (truncated):
{truncated_html}

Very important about Lever:

- Job description page often contains an <a> link whose href ends with "/apply".
- The real application form lives on the "/apply" page (not the description page).
- Resume/CV upload fields are usually:
  - input[type="file"]
  - input[name*="resume"]
  - input[id*="resume"]
  - input[id*="file"]

Rules:

1. If you see a link or href that contains "/apply" for this job, you SHOULD prefer a direct navigation step:
   - An early action should be:
     {{"action": "goto", "url": "https://jobs.lever.co/.../apply"}}

2. After reaching the apply page, find the application form and:
   - Fill name, email, phone where appropriate
   - Fill cover letter into a textarea if present
   - Upload the CV using an upload action
   - Answer required questions with short neutral text like "N/A"
   - Use selectors like:
     - input[type="file"]
     - input[name*="resume"]
     - input[id*="resume"]
     - input[id*="file"]

3. The CV file path is not known to you. The executor will inject the real path whenever you set:
   - {{"action": "upload", "selector": "...", "value": "<cv>"}}
   So always use the literal string "<cv>" for the value in upload actions.

4. At the end, you must click the submit button for the application.
   This is usually a button[type="submit"] near the bottom of the form or a button with "Apply" text.

5. After planning the submit, you should include a finish action:
   - {{"action": "finish", "status": "success", "message": "Submitted Lever application"}}

Allowed actions:

[
  {{"action": "goto", "url": "https://jobs.lever.co/.../apply"}},
  {{"action": "wait_for", "selector": "form.lever-application", "timeout": 15000}},
  {{"action": "fill", "selector": "input[name='name']", "value": "John Doe"}},
  {{"action": "upload", "selector": "input[type='file']", "value": "<cv>"}},
  {{"action": "click", "selector": "button[type='submit']"}},
  {{"action": "finish", "status": "success", "message": "Submitted"}}
]

Output requirements:

- Output ONLY a JSON array of actions
- Do NOT include any extra commentary or code fencing
"""

        response = await client.chat.completions.create(
            model="o1",
            messages=[{"role": "user", "content": prompt}],
        )

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

    async def execute_actions(self, page, actions, cv_path, state):
        """
        Execute a list of agent actions.

        state is a dict used to track:
          - state["submit_clicked"] bool
          - state["cv_uploaded"] bool
          - state["agent_finish_status"] str or None
          - state["agent_finish_message"] str or None

        Returns:
          finished: bool (True if a finish action was seen)
        """

        for step in actions:
            action = step.get("action")
            if not action:
                continue

            if action == "goto":
                url = step.get("url")
                if not url:
                    continue
                try:
                    await page.goto(url, timeout=60000)
                except Exception:
                    continue

            elif action == "click":
                selector = step.get("selector")
                if not selector:
                    continue
                try:
                    await page.locator(selector).click()
                    # Heuristic: did we probably click submit or apply
                    lower_sel = selector.lower()
                    if "submit" in lower_sel or "apply" in lower_sel:
                        state["submit_clicked"] = True
                except Exception:
                    continue

            elif action == "fill":
                selector = step.get("selector")
                value = step.get("value", "")
                if not selector:
                    continue
                try:
                    await page.locator(selector).fill(value)
                except Exception:
                    continue

            elif action == "upload":
                selector = step.get("selector")
                value = step.get("value")
                if not selector:
                    continue
                # enforce literal "<cv>" sentinel so agent cannot try random paths
                if value != "<cv>":
                    continue
                try:
                    loc = page.locator(selector)
                    await loc.scroll_into_view_if_needed()
                    await loc.set_input_files(cv_path)
                    state["cv_uploaded"] = True
                except Exception:
                    continue

            elif action == "wait_for":
                selector = step.get("selector")
                timeout = step.get("timeout", 10000)
                if not selector:
                    continue
                try:
                    await page.wait_for_selector(selector, timeout=timeout)
                except Exception:
                    continue

            elif action == "finish":
                state["agent_finish_status"] = step.get("status", "success")
                state["agent_finish_message"] = step.get("message", "")
                return True  # stop after finish action

        return False

    async def apply(self, job, user, cv_path):
        """
        job: row or dict with job_url like fields
        user: row or dict with ai_cv_data JSON
        cv_path: local filesystem path to CV file
        """

        # Resolve job URL
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

        proxy_config = self.pick_proxy()

        try:
            async with async_playwright() as p:
                launch_args = {
                    "headless": not self.show_browser,
                    "proxy": proxy_config,
                }
                if self.show_browser:
                    launch_args["slow_mo"] = 300  # bump to 1000 if you want it super slow

                browser = await p.chromium.launch(**launch_args)
                page = await browser.new_page()

                # Start on job description page
                await page.goto(job_url, timeout=60000)

                # ---------------------------------------------------------
                # FORCE NAVIGATION TO APPLY URL BEFORE AGENT MODE
                # ---------------------------------------------------------
                try:
                    apply_link = page.locator("a.postings-btn[href*='/apply']")
                    if await apply_link.count() > 0:
                        apply_url = await apply_link.first.get_attribute("href")
                        if apply_url:
                            await page.goto(apply_url, timeout=60000)
                            await page.wait_for_load_state("networkidle")
                            await page.wait_for_timeout(1500)
                except Exception as e:
                    print(f"[LeverBot] Apply-link pre-navigation failed: {e}")

                # Track agent state
                state = {
                    "submit_clicked": False,
                    "cv_uploaded": False,
                    "agent_finish_status": None,
                    "agent_finish_message": None,
                }

                max_cycles = 4
                for _ in range(max_cycles):
                    html = await page.content()
                    current_url = page.url

                    actions = await self.call_agent(
                        html=html,
                        current_url=current_url,
                        ai_data=ai_data,
                        cover_letter=cover_letter,
                    )

                    finished = await self.execute_actions(
                        page=page, actions=actions, cv_path=cv_path, state=state
                    )

                    if finished:
                        break
                # ---------------------------------------------------------
                # DETERMINISTIC CV UPLOAD FALLBACK (DIRECT API VERSION)
                # ---------------------------------------------------------
                if not state.get("cv_uploaded"):
                    try:
                        print("[LeverBot] Starting direct-resume-upload fallback")

                        # 1. Extract accountId from the form
                        try:
                            account_id = await page.locator("input[name='accountId']").input_value()
                        except:
                            account_id = None

                        if not account_id:
                            raise Exception("Cannot find Lever accountId")

                        print("[LeverBot] accountId:", account_id)

                        # 2. Upload resume directly to Lever API (no browser involved)
                        profile = await self.upload_resume_to_lever(cv_path, account_id)
                        resume_id = profile.get("resumeStorageId")

                        if not resume_id:
                            raise Exception("Lever API returned no resumeStorageId")

                        print("[LeverBot] resumeStorageId:", resume_id)

                        # 3. Write resumeStorageId into hidden field
                        await page.fill("input[name='resumeStorageId']", resume_id)

                        # 4. Optionally pre-fill name, email, phone if empty
                        async def set_if_empty(selector, value):
                            if not value:
                                return
                            loc = page.locator(selector)
                            if await loc.count() == 0:
                                return
                            current = await loc.input_value()
                            if not current.strip():
                                await loc.fill(value)

                        await set_if_empty("input[name='name']", profile.get("name"))
                        await set_if_empty("input[name='email']", profile.get("email"))
                        await set_if_empty("input[name='phone']", profile.get("phone"))

                        # 5. Visually update UI to resemble a successful upload
                        await page.evaluate("""
                            () => {
                                const filename = document.querySelector('.application-question.resume .filename');
                                const defaultLabel = document.querySelector('.application-question.resume .default-label');
                                const success = document.querySelector('.resume-upload-success');
                                const working = document.querySelector('.resume-upload-working');
                                const failure = document.querySelector('.resume-upload-failure');

                                if (failure) failure.style.display = 'none';
                                if (working) working.style.display = 'none';
                                if (success) success.style.display = 'inline';

                                if (defaultLabel) defaultLabel.style.display = 'none';
                                if (filename) filename.textContent = 'CV uploaded';
                            }
                        """)

                        print("[LeverBot] Direct resume upload successful")
                        state["cv_uploaded"] = True

                    except Exception as e:
                        print(f"[LeverBot] CV upload failed: {e}")

                # Final page state for validation
                final_url = page.url
                reached_apply = "/apply" in final_url

                # Screenshot at end of run
                user_id = user.get("user_id") or user.get("id") or "unknown"
                job_id = job.get("id") or job.get("job_id") or "unknown"
                screenshot_path = f"/tmp/lever_agent_{user_id}_{job_id}.png"

                screenshot_url = None
                try:
                    await page.screenshot(path=screenshot_path, full_page=True)
                    try:
                        screenshot_url = upload_to_s3(screenshot_path)
                    except Exception:
                        screenshot_url = None
                except Exception:
                    screenshot_url = None

                # Test mode: treat as success regardless of strict checks
                if self.test_mode:
                    await browser.close()
                    return ApplyResult(
                        status="success",
                        message="Test mode agent run completed",
                        screenshot_url=screenshot_url,
                    )

                # Strict validation

                if not reached_apply:
                    await browser.close()
                    return ApplyResult(
                        status="retry",
                        message="Agent did not reach Lever apply page",
                        screenshot_url=screenshot_url,
                    )

                if not state.get("submit_clicked"):
                    await browser.close()
                    return ApplyResult(
                        status="retry",
                        message="Agent never clicked a submit-like button",
                        screenshot_url=screenshot_url,
                    )

                # Optional: also require CV uploaded for "success"
                if not state.get("cv_uploaded"):
                    await browser.close()
                    return ApplyResult(
                        status="retry",
                        message="CV was not uploaded successfully",
                        screenshot_url=screenshot_url,
                    )

                # Optional simple confirmation check
                try:
                    thank_you_count = await page.locator("text=Thank you").count()
                except Exception:
                    thank_you_count = 0

                msg = "Lever application submitted via agent"
                if thank_you_count > 0:
                    msg += " (confirmation detected)"

                await browser.close()

                return ApplyResult(
                    status="success",
                    message=msg,
                    screenshot_url=screenshot_url,
                )

        except Exception as e:
            return ApplyResult(
                status="retry",
                message=f"Lever agent error: {str(e)}",
            )
