import os
import asyncio
import random
import re
import json

from playwright.async_api import async_playwright
from openai import AsyncOpenAI

from bots.base import BaseATSBot, ApplyResult
from utils.s3_uploader import upload_to_s3  # noqa: F401 (kept for parity with other bots)
from dotenv import load_dotenv

load_dotenv()


client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))


class GreenhouseBot(BaseATSBot):
    """
    Greenhouse application bot, version 2.2 (ai_data aware, LLM only for real questions)
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.show_browser = os.getenv("SHOW_BROWSER", "false").lower() == "true"
        self.debug = (
            os.getenv("GREENHOUSE_DEBUG", "false").lower() == "true"
            or os.getenv("DEBUG_MODE", "false").lower() == "true"
        )

    # ----------------- logging / helpers -----------------

    def should_show_browser(self) -> bool:
        return self.show_browser

    def log(self, message: str):
        if self.debug:
            print(f"[Greenhouse DEBUG] {message}")



    def g(self, obj, field: str, default=None):
        """
        Safe accessor for:
        - asyncpg Record
        - dict
        - object with attributes
        """
        if obj is None:
            return default

        # mapping style
        try:
            if hasattr(obj, "get"):
                v = obj.get(field, default)
                if v is not None:
                    return v
        except Exception:
            pass

        # attribute style
        try:
            if hasattr(obj, field):
                v = getattr(obj, field)
                if v is not None:
                    return v
        except Exception:
            pass

        # index style
        try:
            return obj[field]
        except Exception:
            return default

    async def is_required_field(self, element, label_text):
        try:
            # aria-required
            aria = await element.get_attribute("aria-required")
            if aria and aria.lower() == "true":
                return True
        except:
            pass

        try:
            # HTML required attribute
            req = await element.get_attribute("required")
            if req is not None:
                return True
        except:
            pass

        # Label contains *
        if "*" in (label_text or ""):
            return True

        return False

    async def ai_pick_from_options(self, question: str, options: list[str], profile_answers: dict, ai_data) -> str:
        """
        Smart dropdown selection with full debug logging.
        """

        self.log(f"[Dropdown] Question: {question}")
        self.log(f"[Dropdown] Options: {options}")

        if not options:
            self.log("[Route] No options provided → return ''")
            return ""

        if len(options) == 1:
            self.log(f"[Route] Only one option → {options[0]}")
            return options[0]

        q = question.lower().strip()
        pa = {k.lower(): v for k, v in (profile_answers or {}).items()}
        self.log(f"[Profile] Normalized Profile: {pa}")

        opt_norm = [o.lower().strip() for o in options]

        def match_by_value(value):
            if not value:
                return None
            v = str(value).lower().strip()

            # strict match first
            for raw, norm in zip(options, opt_norm):
                if v == norm:
                    self.log(f"[Route] strict match_by_value matched {value} -> {raw}")
                    return raw

            # fuzzy match ONLY when the option is multi-word, not “male” / “female”
            for raw, norm in zip(options, opt_norm):
                if len(norm) > 4 and (v in norm or norm in v):
                    self.log(f"[Route] fuzzy match_by_value matched {value} -> {raw}")
                    return raw

            return None

        # ---------------------------------------------------------
        # DEMOGRAPHICS
        # ---------------------------------------------------------
        if "race" in q or "ethnicity" in q:
            self.log("[Route] Demographics: race/ethnicity branch")
            chosen = match_by_value(pa.get("race"))
            if chosen:
                self.log(f"[Return] race match -> {chosen}")
                return chosen

        if "gender" in q:
            self.log("[Route] Demographics: gender branch")
            chosen = match_by_value(pa.get("gender"))
            if chosen:
                self.log(f"[Return] gender match -> {chosen}")
                return chosen

        if "disability" in q:
            self.log("[Route] Demographics: disability branch")
            chosen = match_by_value(pa.get("disability_status"))
            if chosen:
                self.log(f"[Return] disability -> {chosen}")
                return chosen

        if "veteran" in q:
            self.log("[Route] Demographics: veteran branch")
            chosen = match_by_value(pa.get("veteran_status"))
            if chosen:
                self.log(f"[Return] veteran -> {chosen}")
                return chosen

        # ---------------------------------------------------------
        # WORK AUTHORIZATION
        # ---------------------------------------------------------
        if "authorization" in q or "authorized" in q or "right to work" in q:
            self.log("[Route] Work authorization branch")
            allowed = pa.get("legally_allowed")
            if allowed is True:
                for o in opt_norm:
                    if o == "yes" or "authorized" in o:
                        chosen = options[opt_norm.index(o)]
                        self.log(f"[Return] authorized -> {chosen}")
                        return chosen
            if allowed is False:
                for o in opt_norm:
                    if o == "no":
                        chosen = options[opt_norm.index(o)]
                        self.log(f"[Return] not authorized -> {chosen}")
                        return chosen

        if "visa" in q or "sponsor" in q or "sponsorship" in q:
            self.log("[Route] Visa/sponsorship branch")
            needs = pa.get("sponsorship_required")
            if needs is True:
                for o in opt_norm:
                    if o == "yes" or "require" in o:
                        chosen = options[opt_norm.index(o)]
                        self.log(f"[Return] sponsorship required -> {chosen}")
                        return chosen
            if needs is False:
                for o in opt_norm:
                    if o == "no" or "not" in o:
                        chosen = options[opt_norm.index(o)]
                        self.log(f"[Return] no sponsorship required -> {chosen}")
                        return chosen

        # ---------------------------------------------------------
        # RELOCATION
        # ---------------------------------------------------------
        if "relocat" in q:
            self.log("[Route] Relocation branch")
            relocate = pa.get("willing_to_relocate")
            if relocate is True:
                for o in opt_norm:
                    if o == "yes" or "willing" in o:
                        chosen = options[opt_norm.index(o)]
                        self.log(f"[Return] willing to relocate -> {chosen}")
                        return chosen
            if relocate is False:
                for o in opt_norm:
                    if o == "no" or "not" in o:
                        chosen = options[opt_norm.index(o)]
                        self.log(f"[Return] not willing -> {chosen}")
                        return chosen

        # ---------------------------------------------------------
        # SALARY
        # ---------------------------------------------------------
        if "salary" in q or "compensation" in q or "pay" in q:
            self.log("[Route] Salary branch")
            desired = str(pa.get("desired_salary", "")).lower()
            chosen = match_by_value(desired)
            if chosen:
                self.log(f"[Return] salary -> {chosen}")
                return chosen

        # ---------------------------------------------------------
        # NOTICE PERIOD
        # ---------------------------------------------------------
        if "notice" in q:
            self.log("[Route] Notice period branch")
            desired = str(pa.get("notice_period", "")).lower()
            chosen = match_by_value(desired)
            if chosen:
                self.log(f"[Return] notice -> {chosen}")
                return chosen

        # ---------------------------------------------------------
        # YEARS EXPERIENCE
        # ---------------------------------------------------------
        if "experience" in q and "year" in q:
            self.log("[Route] Years of experience branch")
            num = str(pa.get("years_experience", "")).strip()
            chosen = match_by_value(num)
            if chosen:
                self.log(f"[Return] years experience -> {chosen}")
                return chosen

        # ---------------------------------------------------------
        # YES/NO GENERIC
        # ---------------------------------------------------------
        yes_opts = [o for o in options if o.lower() in ("yes", "y", "true")]
        no_opts = [o for o in options if o.lower() in ("no", "n", "false")]

        # if yes_opts and no_opts:
        #     self.log("[Route] Generic yes/no branch")
        #     if any(k in q for k in ["related", "conflict", "family", "employee", "relatives", "spouse", "employed", "previously interviewed"]):
        #         self.log(f"[Return] choosing NO because risk keyword")
        #         return no_opts[0]
        #
        #     if "not" in q or "no" in q:
        #         self.log(f"[Return] question negative -> NO")
        #         return no_opts[0]
        #
        #     self.log(f"[Return] default YES -> {yes_opts[0]}")
        #     return yes_opts[0]

        # ---------------------------------------------------------
        # NUMERIC OPTIONS
        # ---------------------------------------------------------
        numeric_opts = [o for o in opt_norm if o.replace(" ", "").isdigit()]
        if numeric_opts:
            chosen = options[opt_norm.index(numeric_opts[0])]
            self.log(f"[Route] Numeric options -> {chosen}")
            return chosen

        # ---------------------------------------------------------
        # AI FALLBACK
        # ---------------------------------------------------------
        self.log("[Route] AI Fallback triggered")

        try:
            prompt = f"""
            Select the best dropdown answer for a Greenhouse job application.

            QUESTION:
            {question}

            Candidate profile context:
            {json.dumps(profile_answers, indent=2)}

            OPTIONS (choose EXACTLY one):
            {json.dumps(options, indent=2)}

            Further context from the candidate CV/Resume:
            {ai_data}

            RULES:
            - Return EXACTLY one option from OPTIONS.
            - Do NOT invent or modify text.
            - Prefer the answer most likely to help the candidate receive an interview.
            """

            resp = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Return ONLY one exact item from OPTIONS."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=50,
                temperature=0.0,
            )

            choice = resp.choices[0].message.content.strip()
            self.log(f"[AI Raw Choice] {choice}")

            # Exact match
            for opt in options:
                if choice.lower() == opt.lower():
                    self.log(f"[Return] AI exact match -> {opt}")
                    return opt

            # Fuzzy match
            for opt in options:
                if choice.lower() in opt.lower() or opt.lower() in choice.lower():
                    self.log(f"[Return] AI fuzzy match -> {opt}")
                    return opt

            self.log(f"[AI Fallback] Unmatched AI output ({choice}), using fallback")

        except Exception as e:
            self.log(f"[AI Error] {e}")

        self.log(f"[Return] FINAL fallback -> {options[0]}")
        return options[0]

    async def handle_react_select(self, element, question, profile_answers, ai_data):
        page = element.page

        # 1. Open dropdown
        control = element.locator("xpath=ancestor::*[contains(@class,'select__control')]")

        if await control.count() > 0:
            try:
                await control.first.click(force=True)
            except:
                self.log("Normal click failed, forcing JS click")
                await page.evaluate("(el) => el.click()", await control.first.element_handle())
        else:
            await element.click(force=True)

        await asyncio.sleep(0.3)

        # 2. Trigger loading of all options
        await element.fill(" ")
        await asyncio.sleep(0.4)

        # 3. Find menu in portal
        menu = page.locator(
            ".select__menu, div[role='listbox'], div[class*='menu'], div.select__menu-list"
        )
        menu = page.locator(
            ".select__menu, div[role='listbox'], div[class*='menu'], div.select__menu-list"
        )

        try:
            await menu.first.wait_for(state="visible", timeout=2500)
        except:
            self.log(f"React select menu did not appear for question '{question}'. Skipping.")
            # Click outside to break focus
            await page.mouse.click(5, 5)
            return

        options = menu.locator(".select__option, [data-testid='select-option'], div[role='option']")

        items = []
        for i in range(await options.count()):
            txt = (await options.nth(i).inner_text()).strip()
            items.append(txt)

        self.log(f"Dropdown options detected: {items}")

        # 4. Ask AI which one to choose
        chosen = await self.ai_pick_from_options(question, items, profile_answers, ai_data)
        self.log(f"AI chose dropdown answer: {chosen}")

        # 5. Click chosen option
        for i in range(await options.count()):
            opt = options.nth(i)
            if (await opt.inner_text()).strip() == chosen:
                await opt.click()
                await asyncio.sleep(0.2)
                return

        # fallback: first option
        await options.first.click()
        await asyncio.sleep(0.2)

    def _format_phone_smart(self, raw_phone, country):
        """
        Same logic as WorkableBot for consistent phone formatting.
        """
        if not raw_phone:
            return ""
        s = re.sub(r"[^0-9+]", "", str(raw_phone).strip())
        if s.startswith("+"):
            digits = re.sub(r"\D", "", s[1:])
            return "+" + digits if digits else ""

        national = re.sub(r"\D", "", s)
        if not national:
            return ""

        country = (country or "").lower().strip()
        if country in {"united kingdom", "uk", "gb"}:
            if national.startswith("0"):
                national = national[1:]
            return "+44" + national
        if country in {"united states", "us", "usa"}:
            return "+1" + national

        return national

    # ----------------- frame / form detection -----------------

    async def find_greenhouse_frame(self, page):
        for frame in page.frames:
            try:
                url = frame.url or ""
                if "greenhouse" in url.lower() or "grnhse" in url.lower():
                    self.log(f"Greenhouse frame by url: {url}")
                    return frame

                if await frame.locator("form#application-form").count() > 0:
                    self.log("Greenhouse form frame found by id")
                    return frame

                if await frame.locator("form.application--form").count() > 0:
                    self.log("Greenhouse form frame found by class")
                    return frame
            except Exception as e:
                self.log(f"Error checking frame: {e}")
                continue

        return None

    async def wait_for_greenhouse_form(self, page):
        """
        Detects hosted, embedded, or self-hosted Greenhouse forms.
        Much more robust than previous version.
        """

        selectors = [
            "form#application-form",
            "form.application--form",
            "form[action*='apply']",
            "form[action*='/applications']",
            "form[action*='job']"
        ]

        for attempt in range(20):
            self.log(f"[Form Detection] Attempt {attempt + 1}")

            # 1. direct form selectors
            for sel in selectors:
                form = page.locator(sel)
                if await form.count() > 0:
                    self.log(f"[Form Detection] Found form via selector {sel}")
                    return form.first

            # 2. any form with typical GH fields
            forms = page.locator("form")
            count = await forms.count()

            for i in range(count):
                f = forms.nth(i)

                # Greenhouse identity fields
                if await f.locator("input[name='first_name'], input#first_name").count() > 0:
                    return f

                if await f.locator("input[name='last_name'], input#last_name").count() > 0:
                    return f

                if await f.locator("input[name='email'], input#email").count() > 0:
                    return f

                # custom question fields
                if await f.locator("input, textarea, select").count() > 10:
                    self.log("[Form Detection] Large form detected → likely GH")
                    return f

            # 3. iframe scan
            for frame in page.frames:
                try:
                    if "greenhouse" in (frame.url or "").lower():
                        f = frame.locator("form")
                        if await f.locator("input, textarea, select").count() > 5:
                            self.log("[Form Detection] Found form in iframe")
                            return f.first
                except:
                    continue

            await page.wait_for_timeout(500)

        raise Exception("No Greenhouse-style form detected after retries.")

    async def capture_final_screenshot(self, page, user, job, prefix="greenhouse"):
        """
        Scrolls through the entire page, renders fully,
        captures a full-page screenshot, uploads to S3,
        and returns the S3 URL.
        """
        try:
            uid = user.get("user_id") if isinstance(user, dict) else self.g(user, "id", "u")
            jid = job.get("id") if isinstance(job, dict) else self.g(job, "id", "j")

            screenshot_path = f"/tmp/{prefix}_{uid}_{jid}.png"

            # Smooth scroll through full height
            full_height = await page.evaluate("() => document.body.scrollHeight")
            for _ in range(0, full_height, 600):
                await page.mouse.wheel(0, 600)
                await asyncio.sleep(0.25)

            # Let async rendering settle
            await asyncio.sleep(1.2)

            # Scroll back to the top
            await page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(0.2)

            # Capture screenshot
            await page.screenshot(path=screenshot_path, full_page=True)

            # Upload
            screenshot_url = upload_to_s3(screenshot_path, folder="screenshots")
            self.log(f"Screenshot captured and uploaded: {screenshot_url}")

            return screenshot_url

        except Exception as e:
            self.log(f"Screenshot capture failed: {e}")
            return None

    # ----------------- job context extraction -----------------

    async def extract_job_context(self, page, job):
        """
        Light job context, similar idea to WorkableBot.
        Helpful for LLM answers on real questions.
        """
        job_title = ""
        company_name = ""
        job_description = ""

        # title
        try:
            title_loc = page.locator("h1, h2")
            if await title_loc.count() > 0:
                job_title = (await title_loc.first.inner_text()).strip()
        except Exception:
            pass

        # company name
        try:
            comp_loc = page.locator("[data-company], .company-name, .company")
            if await comp_loc.count() > 0:
                company_name = (await comp_loc.first.inner_text()).strip()
        except Exception:
            pass

        if not company_name:
            company_name = job.get("company") or ""

        if not company_name:
            try:
                from urllib.parse import urlparse

                parsed = urlparse(
                    job.get("apply_url") or job.get("job_url") or job.get("url") or ""
                )
                host = parsed.hostname or ""
                base = host.split(".")[0]
                base = base.replace("-", " ")
                if base:
                    company_name = base.title()
            except Exception:
                pass

        if not company_name:
            company_name = "the company"

        # description
        selectors = [
            ".job-description",
            "[data-qa='job-description']",
            "section[role='main']",
        ]
        for sel in selectors:
            try:
                loc = page.locator(sel)
                if await loc.count() > 0:
                    jd = await loc.first.inner_text()
                    jd = re.sub(r"\s+", " ", jd).strip()
                    if jd:
                        job_description = jd
                        break
            except Exception:
                pass

        if len(job_description) > 2000:
            job_description = job_description[:2000]

        if self.debug:
            self.log(f"Job title: {job_title}")
            self.log(f"Company: {company_name}")
            self.log(f"JD length: {len(job_description)}")

        return job_title, company_name, job_description

    # ----------------- main apply -----------------

    async def apply(self, job, user, cv_path) -> ApplyResult:
        url = job.get("apply_url") or job.get("job_url") or job.get("url")
        if not url:
            return ApplyResult(status="failed", message="No job URL found")

        self.log(f"Starting Greenhouse apply to: {url}")

        # parse ai_cv_data
        ai_raw = self.g(user, "ai_cv_data")
        if isinstance(ai_raw, str):
            try:
                ai_data = json.loads(ai_raw)
            except Exception:
                ai_data = {}
        elif isinstance(ai_raw, dict):
            ai_data = ai_raw
        else:
            ai_data = {}

        # parse profile application answers
        raw_app = self.g(user, "application_data")
        if isinstance(raw_app, str):
            try:
                profile_answers = json.loads(raw_app)
            except Exception:
                profile_answers = {}
        elif isinstance(raw_app, dict):
            profile_answers = raw_app
        else:
            profile_answers = {}

        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=not self.should_show_browser())
        context = await browser.new_context()
        page = await context.new_page()

        try:
            self.log("Navigating to job page")
            await page.goto(url, timeout=60000)

            self.log("Waiting briefly for Apply button to render")
            await page.wait_for_timeout(1500)

            await self.click_cookie_banners(page)

            await page.wait_for_timeout(1500)

            apply_btn = page.locator("button:has-text('Apply'), a:has-text('Apply')")
            btn_count = await apply_btn.count()
            self.log(f"Apply button count: {btn_count}")

            if btn_count > 0:
                self.log("Clicking Apply button")
                await apply_btn.first.click()
                await page.wait_for_timeout(2000)

            self.log("Waiting for Greenhouse form to load")
            form = await self.wait_for_greenhouse_form(page)
            self.log("Greenhouse form found and ready")

            # job context for LLM answers later
            job_title, company_name, job_description = await self.extract_job_context(page, job)

            # core profile fields from ai_data
            first_name = ai_data.get("first_name") or self.g(user, "first_name", "") or ""
            last_name = ai_data.get("last_name") or self.g(user, "last_name", "") or ""
            email = ai_data.get("email") or self.g(user, "email", "") or ""

            self.log("Filling standard profile fields")
            await self.safe_fill(form, "#first_name", first_name)
            await self.safe_fill(form, "#last_name", last_name)
            await self.safe_fill(form, "#email", email)

            self.log("Handling phone field")
            await self.handle_phone(form, ai_data, user)

            self.log("Uploading resume")
            await self.handle_resume_upload(form, cv_path)

            self.log("Handling custom questions")
            await self.handle_custom_questions(
                form,
                ai_data,
                profile_answers,
                job_title,
                company_name,
                job_description,
            )

            screenshot_url = await self.capture_final_screenshot(page, user, job, prefix="gh_success")
            self.log("Submitting application")
            submit = form.locator(
                "button[type='submit'], input[type='submit'], button:has-text('Submit'), button:has-text('Apply'), input[value*='Apply']"
            )
            if await submit.count() > 0:
                await submit.first.click()
            else:
                self.log("Submit button not found")
                await browser.close()
                await playwright.stop()
                return ApplyResult(status="failed", message="Submit button not found")

            await page.wait_for_timeout(4000)
            body = await page.text_content("body") or ""
            self.log(f"Submission check body length: {len(body)}")

            lower_body = body.lower()
            success_phrases = [
                "thank you for applying",
                "thank you for your application",
                "application received",
                "thank you for submitting",
            ]
            if any(p in lower_body for p in success_phrases):
                self.log("Application appears successful")
                await browser.close()
                await playwright.stop()
                return ApplyResult(status="success", message="Application appears successful", screenshot_url=screenshot_url)

            self.log("Application submitted but success message not detected")
            screenshot_url = await self.capture_final_screenshot(page, user, job, prefix="gh_unknown")

            await browser.close()
            await playwright.stop()

            return ApplyResult(
                status="manual_required",
                message="unknown submission state",
                screenshot_url=screenshot_url
            )


        except Exception as e:
            self.log(f"Error occurred: {e}")

            screenshot_url = None
            try:
                screenshot_url = await self.capture_final_screenshot(page, user, job, prefix="gh_error")
            except:
                pass

            try:
                await browser.close()
            except:
                pass

            try:
                await playwright.stop()
            except:
                pass

            return ApplyResult(
                status="failed",
                message=str(e),
                screenshot_url=screenshot_url
            )


    # ----------------- field handlers -----------------

    async def safe_fill(self, container, selector: str, value):
        value = "" if value is None else str(value)
        if not value.strip():
            self.log(f"Skipping empty value for {selector}")
            return

        loc = container.locator(selector)
        try:
            count = await loc.count()
        except Exception as e:
            self.log(f"Locator error on {selector}: {e}")
            return

        if count > 0:
            self.log(f"Filling field {selector} with '{value}'")
            try:
                await loc.fill(value)
            except Exception as e:
                self.log(f"Failed to fill {selector}: {e}")
        else:
            self.log(f"Field {selector} not found, skipping")

    async def click_cookie_banners(self, page):
        """
        Universal cookie-banner accepter.
        Attempts multiple common provider selectors.
        Fails silently and never blocks normal execution.
        """

        selectors = [
            # Text-based common buttons
            "button:has-text('Accept')",
            "button:has-text('I Accept')",
            "button:has-text('Agree')",
            "button:has-text('Allow')",
            "button:has-text('OK')",
            "button:has-text('Got it')",
            "button:has-text('Continue')",
            "button:has-text('I understand')",

            # Input-based accept buttons
            "input[type='submit'][value*='Accept']",
            "input[type='button'][value*='Accept']",
            "input[type='submit'][value*='OK']",

            # OneTrust
            "#onetrust-accept-btn-handler",
            "button#onetrust-accept-btn-handler",

            # Cookiebot
            ".CybotCookiebotDialogBodyButtonAccept",
            "button.CybotCookiebotDialogBodyButton",
            "button[id*='CybotCookiebotDialogBodyButton']",

            # HubSpot cookie banner
            "button#hs-eu-confirmation-button",

            # Generic cookie class heuristics
            "button[class*='cookie']",
            "button[id*='cookie']",
            "button[name*='cookie']",

            # Div click fallback (some custom modals)
            "div.cookie-banner button",
            "div.cookie-consent button",
        ]

        for selector in selectors:
            try:
                btn = page.locator(selector)
                if await btn.count() > 0:
                    self.log(f"[CookieBanner] Clicking: {selector}")
                    await btn.first.click(timeout=2000)
                    await page.wait_for_timeout(300)
                    return True
            except Exception as e:
                self.log(f"[CookieBanner] Failed selector {selector}: {e}")

        # Some banners hide accept button inside shadow DOM
        # Try a shadow DOM script injection payload
        try:
            self.log("[CookieBanner] Trying shadow DOM accept methods")
            await page.evaluate("""
                () => {
                    function clickShadowAccept(root) {
                        const buttons = root.querySelectorAll('button, input[type="button"], input[type="submit"]');
                        for (let btn of buttons) {
                            const t = btn.innerText?.toLowerCase() || btn.value?.toLowerCase() || "";
                            if (t.includes('accept') || t.includes('agree') || t.includes('ok')) {
                                btn.click();
                                return true;
                            }
                        }
                        return false;
                    }

                    const walker = document.createTreeWalker(document, NodeFilter.SHOW_ELEMENT);
                    let node;
                    while (node = walker.nextNode()) {
                        if (node.shadowRoot) {
                            if (clickShadowAccept(node.shadowRoot)) return;
                        }
                    }
                }
            """)
        except Exception as e:
            self.log(f"[CookieBanner] Shadow DOM attempt failed: {e}")

        self.log("[CookieBanner] No cookie banner found.")
        return False

    async def handle_phone(self, form, ai_data, user):
        raw_phone = ai_data.get("phone") or self.g(user, "phone", "") or ""
        country = self.g(user, "country", "") or ""
        phone_number = self._format_phone_smart(raw_phone, country)

        country_code = self.g(user, "country_code", "") or ""

        # country picker
        dropdown = form.locator("button.iti__selected-country")
        try:
            if await dropdown.count() > 0 and country_code:
                self.log(f"Clicking country dropdown for {country_code}")
                await dropdown.click()

                country_item = form.locator(
                    f"li[data-country-code='{country_code.lower()}']"
                )
                if await country_item.count() > 0:
                    self.log(f"Selecting country: {country_code}")
                    await country_item.click()
                else:
                    self.log(f"No item found for country code {country_code}")
        except Exception as e:
            self.log(f"Country selection error: {e}")

        # phone input
        try:
            if await form.locator("#phone").count() > 0 and phone_number:
                self.log(f"Filling phone number: {phone_number}")
                await form.locator("#phone").fill(phone_number)
            else:
                self.log("Phone field not found or phone value empty")
        except Exception as e:
            self.log(f"Phone fill error: {e}")

    async def handle_resume_upload(self, form, cv_path: str):
        if not cv_path:
            self.log("No cv_path provided, skipping resume upload")
            return

        file_input = form.locator("input[type='file']")
        try:
            count = await file_input.count()
        except Exception as e:
            self.log(f"File input locator error: {e}")
            return

        if count > 0:
            self.log(f"Uploading CV file: {cv_path}")
            try:
                await file_input.first.set_input_files(cv_path)
            except Exception as e:
                self.log(f"Resume upload failed: {e}")
        else:
            self.log("Resume upload field not found")

    async def extract_checkbox_group(self, element):
        """
        Detects *any* Greenhouse checkbox group (hosted or self-hosted).
        Returns (question_text, [(checkbox, label), ...]) or None.
        """

        # Find nearest fieldset
        fieldset = element.locator("xpath=ancestor::fieldset[.//input[@type='checkbox']][1]")
        if not await fieldset.count():
            return None

        # Read question/legend
        try:
            legend = await fieldset.locator("legend").inner_text()
            question_text = legend.strip()
        except:
            question_text = "Multiple choice"

        # Collect all checkboxes in this group
        cbs = fieldset.locator("input[type='checkbox']")
        total = await cbs.count()

        if total < 2:
            return None  # Not a group

        group = []
        for i in range(total):
            cb = cbs.nth(i)
            cb_id = await cb.get_attribute("id")
            lbl = ""

            try:
                lbl = await fieldset.locator(f"label[for='{cb_id}']").inner_text()
                lbl = lbl.strip()
            except:
                lbl = f"Option {i + 1}"

            group.append((cb, lbl))

        return question_text, group

    async def ai_pick_checkbox_options(self, question, options, ai_data, profile_answers):
        """
        AI chooses 0-N checkbox labels.
        """

        prompt = f"""
        You are filling a Greenhouse job application.

        The question is:
        "{question}"

        Options (checkbox multi-select):
        {json.dumps(options, indent=2)}

        Candidate profile:
        {json.dumps(profile_answers, indent=2)}

        Candidate CV data:
        {json.dumps(ai_data, indent=2)}

        RULES:
        - Choose ONLY relevant options.
        - Return ONLY a JSON list of selected option labels.
        - If none apply, return an empty list [].
        - Consider job title, CV skills, and geographic relevance.
        """

        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Return only a JSON array."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=120,
            temperature=0.2,
        )

        raw = resp.choices[0].message.content.strip()

        try:
            selected = json.loads(raw)
            if isinstance(selected, list):
                return selected
        except:
            pass

        return []

    async def extract_greenhouse_checkbox_group(self, form, element):
        """
        Detects a checkbox group inside a <fieldset class='checkbox'>.
        Returns a tuple: (question_label, group_list)
        where group_list = [(checkbox_element, option_label), ...]
        """

        # Find the parent fieldset
        fieldset = element.locator("xpath=ancestor::fieldset[contains(@class,'checkbox')]").first
        if not fieldset:
            return None

        # Read the legend (this is the question)
        try:
            legend = await fieldset.locator("legend").inner_text()
            question_text = legend.strip()
        except:
            question_text = "Multiple choice"

        # Find all checkboxes inside this fieldset
        checkboxes = fieldset.locator("input[type='checkbox']")
        total = await checkboxes.count()

        if total <= 1:
            return None  # Not actually a group

        group = []
        for i in range(total):
            cb = checkboxes.nth(i)

            # Find label for this checkbox
            try:
                cb_id = await cb.get_attribute("id")
                label_el = fieldset.locator(f"label[for='{cb_id}']")
                label = await label_el.inner_text()
                label = label.strip()
            except:
                label = "Option"

            group.append((cb, label))

        return question_text, group

    async def handle_custom_questions(
            self,
            form,
            ai_data: dict,
            profile_answers: dict,
            job_title: str,
            company_name: str,
            job_description: str,
    ):
        self.log("Scanning for custom question fields")

        locator = form.locator("input, textarea, select")
        try:
            total = await locator.count()
        except Exception as e:
            self.log(f"Error counting form fields: {e}")
            return

        for i in range(total):
            element = locator.nth(i)

            try:
                # --- SPEED BOOST: SKIP invisible items immediately ---
                try:
                    if not await element.is_visible():
                        self.log(f"Skipping invisible element #{i}")
                        continue
                except:
                    continue

                # --- SPEED BOOST: Skip zero-size ghost elements ---
                try:
                    box = await element.bounding_box()
                    if not box or box["width"] < 3 or box["height"] < 3:
                        self.log(f"Skipping tiny/hidden element #{i}")
                        continue
                except:
                    continue

                # Now safe to inspect attributes
                try:
                    name = await element.get_attribute("name")
                    tag = await element.evaluate("e => e.tagName.toLowerCase()")
                    etype = await element.get_attribute("type")
                    css_class = (await element.get_attribute("class") or "")
                    if "iti__" in css_class:
                        self.log("Skipping phone widget internal field")
                        continue
                except Exception as e:
                    self.log(f"Error inspecting element {i}: {e}")
                    continue
                if "iti__" in css_class:
                    self.log("Skipping phone-widget internal field")
                    continue
            except Exception as e:
                self.log(f"Error inspecting element {i}: {e}")
                continue

            current_value = await element.input_value()
            if current_value and "select__single-value" in (await element.evaluate("e => e.parentElement.innerHTML")):
                self.log("React-select already has a value, skipping.")
                continue

            self.log(f"current value: {current_value}")

            # skip identity and control fields
            if name in ["first_name", "last_name", "email", "phone"]:
                continue
            if etype in ["hidden", "submit", "button", "file"]:
                continue
            # Skip if this is a core identity field by ID
            el_id = await element.get_attribute("id")
            if el_id in ["first_name", "last_name", "email", "phone"]:
                self.log(f"Skipping core identity field by ID: {el_id}")
                continue


            label = await self.find_label(form, element)
            self.log(f"Found question: '{label}' (name={name}, type={tag}/{etype})")
            # Skip identity fields by label, in case name/id are missing
            if label.lower().strip() in ["first name*", "last name*", "email*", "email", "first name", "last name"]:
                self.log(f"Skipping core identity field by label: {label}")
                continue

            maxlength = await element.get_attribute("maxlength")
            if maxlength:
                try:
                    maxlength = int(maxlength)
                except:
                    maxlength = None

            # generate answer
            answer = await self.route_question_answer(
                label,
                element,
                ai_data,
                profile_answers,
                job_title,
                company_name,
                job_description,
                maxlength,
            )

            # Detect required field
            is_required = await self.is_required_field(element, label)

            if (not answer or not str(answer).strip()):
                if is_required:
                    self.log(
                        f"[REQUIRED] No answer generated for required field '{label}'. Forcing default safe answer.")
                    answer = "N/A" if (await element.get_attribute("type")) == "text" else "Yes"
                else:
                    self.log("Answer empty or whitespace, skipping optional field")
                    continue

            self.log(f"Generated answer: {answer}")

            # ------------------------------------------------------------
            # 1. REACT-SELECT HANDLER (must come before all other logic!)
            # ------------------------------------------------------------

            is_combobox = (await element.get_attribute("role")) == "combobox"
            is_react = "select__input" in css_class or "css-" in css_class  # self-host often uses css hash classes
            if is_combobox or is_react:
                self.log(f"Detected react-select input for '{label}'")
                await self.handle_react_select(element, label, profile_answers, ai_data)
                await asyncio.sleep(random.uniform(0.9, 1.4))
                continue

            # ------------------------------------------------------------
            # 2. STANDARD FIELD HANDLERS
            # ------------------------------------------------------------
            try:
                if tag == "input" and (etype in ["text", None, "search", "tel", "url", "email"]):
                    await element.fill(str(answer))

                elif tag == "textarea":
                    await element.fill(str(answer))

                elif tag == "input" and etype == "number":
                    num = self.pick_numeric_value(str(answer))
                    self.log(f"Using numeric answer: {num}")
                    await element.fill(str(num))

                elif tag == "select":
                    self.log("Selecting option for <select>")
                    await self.select_random_option(element)

                elif tag == "input" and etype == "checkbox":

                    # First detect Greenhouse-style fieldset group
                    group_data = await self.extract_checkbox_group(element)

                    if group_data:
                        question_text, group = group_data

                        self.log(f"[Checkbox Group] '{question_text}' with {len(group)} options")

                        # AI selects relevant options
                        option_labels = [lbl for (_, lbl) in group]
                        selected = await self.ai_pick_checkbox_options(
                            question_text, option_labels, ai_data, profile_answers
                        )

                        self.log(f"[Checkbox Group AI] Selected: {selected}")

                        # Apply selections
                        for cb, lbl in group:
                            if lbl in selected:
                                await cb.check()
                            else:
                                try:
                                    await cb.uncheck()
                                except:
                                    pass

                        continue

                    # Otherwise it's a normal single checkbox
                    # ans = str(answer).lower().strip()
                    # should_check = ans in ("yes", "y", "true", "1")
                    #
                    # if should_check:
                    self.log(f"Checking single checkbox for '{label}'")
                    await element.check()

                    continue


                elif tag == "input" and etype == "radio":
                    self.log("Selecting radio option")
                    await element.check()

            except Exception as e:
                self.log(f"Failed to fill custom field {name}: {e}")

            # small pause between fields
            await asyncio.sleep(random.uniform(2.3, 3.4))

    async def select_random_option(self, select_element):
        try:
            opts = select_element.locator("option")
            count = await opts.count()
        except Exception as e:
            self.log(f"Option locator error: {e}")
            return

        values = []
        for i in range(count):
            try:
                value = await opts.nth(i).get_attribute("value")
                if (value or "").strip():
                    values.append(value)
            except Exception as e:
                self.log(f"Error reading option {i}: {e}")

        if values:
            choice = random.choice(values)
            self.log(f"Selecting option: {choice}")
            try:
                await select_element.select_option(choice)
            except Exception as e:
                self.log(f"Failed to select option {choice}: {e}")
        else:
            self.log("No valid options found for dropdown")

    async def find_label(self, form, element):
        """
        Robust multi-strategy label extraction that works for self-hosted forms.
        """

        # 1. label[for=id]
        try:
            el_id = await element.get_attribute("id")
            if el_id:
                lbl = form.locator(f"label[for='{el_id}']")
                if await lbl.count() > 0:
                    txt = await lbl.first.inner_text()
                    if txt:
                        return txt.strip()
        except:
            pass

        # 2. Closest ancestor <label>
        try:
            wrapper = element.locator("xpath=ancestor::label")
            if await wrapper.count() > 0:
                txt = await wrapper.first.inner_text()
                if txt:
                    return txt.strip()
        except:
            pass

        # 3. Self-hosted field wrappers like: <div class="field"> <label>Question</label>
        try:
            wrapper = element.locator("xpath=ancestor::*[label][1]")
            if await wrapper.count() > 0:
                txt = await wrapper.locator("label").first.inner_text()
                if txt:
                    return txt.strip()
        except:
            pass

        return "Question"

    # ----------------- routing and LLM -----------------

    async def route_question_answer(
        self,
        label: str,
        element,
        ai_data: dict,
        profile_answers: dict,
        job_title: str,
        company_name: str,
        job_description: str,
        maxlength: int | None,
    ):
        """
        Decide whether to answer deterministically from ai_data
        or with LLM, and in which style.
        """
        label_norm = (label or "").lower().strip()

        # PERSONAL INFO
        if "phone" in label_norm:
            return ai_data.get("phone") or self.g(profile_answers, "phone", "")

        if "email" in label_norm:
            return ai_data.get("email") or self.g(profile_answers, "email", "")

        if "preferred first" in label_norm:
            return ai_data.get("first_name") or self.g(profile_answers, "first_name", "")

        if "first name" in label_norm and "preferred" not in label_norm:
            return ai_data.get("first_name") or self.g(profile_answers, "first_name", "")

        if "last name" in label_norm:
            return ai_data.get("last_name") or self.g(profile_answers, "last_name", "")

        if "address" in label_norm and "email" not in label_norm:
            return ai_data.get("address") or self.g(profile_answers, "address", "")

        if "city" in label_norm and "state" not in label_norm:
            addr = ai_data.get("address") or self.g(profile_answers, "address", "")
            if addr and "," in addr:
                parts = [p.strip() for p in addr.split(",") if p.strip()]
                if len(parts) >= 2:
                    return parts[1]
            return self.g(profile_answers, "city", "")

        # SOCIAL
        add_det = ai_data.get("additional_details", {}) if isinstance(ai_data.get("additional_details"), dict) else {}

        if "linkedin" in label_norm:
            return add_det.get("linkedin", "")

        if "website" in label_norm or "portfolio" in label_norm:
            return add_det.get("portfolio", "")

        # HIGH-RISK EMPLOYABILITY QUESTIONS (ALWAYS ANSWER SAFELY)
        q = label_norm

        # Non-compete, confidentiality, restrictive covenants
        if "restrictive" in q or "covenant" in q or "non compete" in q or "non-compete" in q:
            return "No"

        # Legal limitations on duties
        if "limited" in q and "job" in q:
            return "No"

        # Can you provide a copy of the agreement?
        if "agreement" in q and "copy" in q:
            return "N/A"

        # Previous interview within past year (safest is No)
        if "previously interviewed" in q or "interviewed with" in q:
            return "No"

        # Willingness to commute / local area questions
        if "local" in q or "commute" in q:
            return "Yes"  # Always positive unless profile says otherwise

        # EDUCATION
        edu = ai_data.get("education", []) if isinstance(ai_data.get("education"), list) else []
        if edu:
            degree = edu[0].get("degree", "")
            institution = edu[0].get("institution", "")
            graduation = edu[0].get("graduation_year", "")

            if "school" in label_norm or "university" in label_norm or "institution" in label_norm:
                return institution

            if "degree" in label_norm:
                return degree

            if "discipline" in label_norm:
                if "nurs" in degree.lower():
                    return "Nursing"
                return degree.split()[0] if degree else "General Studies"

            if "graduation" in label_norm or ("year" in label_norm and "start" not in label_norm and "end" not in label_norm):
                return graduation or "2012"

        # EXPERIENCE
        exp = ai_data.get("experience", []) if isinstance(ai_data.get("experience"), list) else []
        if exp:
            role = exp[0].get("role", "") or exp[0].get("title", "")
            company = exp[0].get("company", "")
            start_date = exp[0].get("start_date", "")
            end_date = exp[0].get("end_date", "")

            def extract_month(s):
                m = re.search(r"[A-Za-z]+", s)
                return m.group(0) if m else "January"

            def extract_year(s):
                m = re.search(r"\d{4}", s)
                return m.group(0) if m else "2020"

            if "start date month" in label_norm:
                return extract_month(start_date)

            if "start date year" in label_norm:
                return extract_year(start_date)

            if "end date month" in label_norm:
                return extract_month(end_date)

            if "end date year" in label_norm:
                return extract_year(end_date)

            if "company" in label_norm:
                return company

            if "role" in label_norm or "title" in label_norm:
                return role

        # TRULY GENERIC LABEL
        if not label_norm or label_norm == "question" or len(label_norm) < 3:
            return await self.llm_generate_answer(
                label,
                ai_data,
                profile_answers,
                job_title,
                company_name,
                job_description,
                short_mode=True,
            )

        # DECIDE SHORT OR LONG LLM ANSWER
        long_keywords = [
            "why",
            "explain",
            "describe",
            "tell us",
            "motivation",
            "cover letter",
            "biggest strength",
            "biggest weakness",
            "challenge",
            "impact",
            "project",
            "situation",
            "example",
        ]
        is_long = any(k in label_norm for k in long_keywords)

        return await self.llm_generate_answer(
            label,
            ai_data,
            profile_answers,
            job_title,
            company_name,
            job_description,
            max_length=maxlength,
        )

    async def llm_generate_answer(
        self,
        question: str,
        ai_data: dict,
        profile_answers: dict,
        job_title: str,
        company_name: str,
        job_description: str,
        max_length: int | None,
    ):
        summary = ai_data.get("summary", "")
        skills = ai_data.get("skills", [])
        titles = ai_data.get("job_titles", [])[:3]
        experience = ai_data.get("experience", [])
        if not max_length:
            # Default max for text fields
            max_length = 255

        experience_snippets = []
        for exp in experience[:2]:
            if not isinstance(exp, dict):
                continue
            experience_snippets.append(
                {
                    "title": exp.get("role") or exp.get("title"),
                    "company": exp.get("company"),
                    "description": exp.get("description", ""),
                }
            )

        safe_app = dict(profile_answers or {})
        for key in ["race", "gender", "disability_status", "veteran_status"]:
            safe_app.pop(key, None)

        is_salary_question = any(
            kw in (question or "").lower()
            for kw in [
                "salary",
                "compensation",
                "pay range",
                "remuneration",
                "annual pay",
                "expected pay",
                "expected earnings",
                "ctc",
            ]
        )

        system_msg = (
            "You help candidates answer Greenhouse job application questions. "
            "Your goal is to provide responses that maximize the candidate’s chance of progressing to an interview. "
            "If the candidate profile does not provide a clear answer, choose the safest, most positive, generally acceptable answer. "
            "Never select or generate an answer that could disadvantage the candidate unless their profile explicitly requires it. "
            "Do NOT invent factual information such as degrees, immigration status, job history, or legal restrictions. "
            "If uncertain, choose the response most commonly expected by employers and least risky to the candidate. "
            "Maintain a professional, concise tone. "
            "For long questions, produce thoughtful but safe answers. "
            "For short questions or yes/no questions, choose the most interview-friendly option. "
            f"- Your answer MUST NOT exceed {max_length} characters. If necessary, shorten or summarise the answer."
        )

        user_msg = {
            "question": question,
            "job_title": job_title,
            "company_name": company_name,
            "job_description": job_description,
            "cv_summary": summary,
            "skills": skills,
            "target_job_titles": titles,
            "experience_highlights": experience_snippets,
            "application_data": safe_app,
            "max_character_length": max_length,
            "allow_salary_mention": is_salary_question,
        }

        try:
            resp = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": json.dumps(user_msg)},
                ],
                max_tokens=260,
                temperature=0.45,
            )
            answer = resp.choices[0].message.content.strip()
        except Exception as e:
            if self.debug:
                print("[Greenhouse DEBUG] LLM error:", e)
            return (
                "My skills and experience align well with this position and I am motivated "
                "to contribute strong results for the team."
            )

        if len(answer) > max_length:
            answer = answer[:max_length].rstrip()

        return answer[:900]

    def pick_numeric_value(self, text: str) -> int:
        nums = re.findall(r"\d+", text or "")
        if nums:
            try:
                return int(nums[0])
            except Exception:
                pass
        return random.randint(10, 100)
