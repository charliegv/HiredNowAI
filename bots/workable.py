import os
import json
import random
import asyncio
import re
from urllib.parse import urlparse

from dotenv import load_dotenv
from playwright.async_api import async_playwright
from openai import AsyncOpenAI

from bots.base import BaseATSBot, ApplyResult
from utils.s3_uploader import upload_to_s3

load_dotenv()

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))


class WorkableBot(BaseATSBot):
    def __init__(self):
        proxy_file = os.getenv("PROXY_FILE", "/mnt/data/Webshare_1000_proxies.txt")

        if os.path.exists(proxy_file):
            with open(proxy_file, "r") as f:
                self.proxies = [line.strip() for line in f if line.strip()]
        else:
            self.proxies = []

        self.test_mode = os.getenv("TEST_MODE", "false").lower() == "true"
        self.show_browser = os.getenv("SHOW_BROWSER", "false").lower() == "true"
        self.debug = os.getenv("DEBUG_MODE", "false").lower() == "true"

        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        ]

    # =============================================================
    # Human helpers
    # =============================================================
    async def human_sleep(self, min_s=0.4, max_s=1.2):
        await asyncio.sleep(random.uniform(min_s, max_s))

    async def human_mouse_move(self, page, target_x, target_y, steps=20):
        start_x = random.uniform(50, 200)
        start_y = random.uniform(80, 180)

        await page.mouse.move(start_x, start_y, steps=3)

        for i in range(steps):
            t = i / float(steps)
            xt = start_x + (target_x - start_x) * (t * t)
            yt = start_y + (target_y - start_y) * (t * t)
            await page.mouse.move(xt, yt, steps=1)
            await asyncio.sleep(random.uniform(0.01, 0.04))

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

    async def random_scroll(self, page):
        try:
            delta = random.randint(200, 700)
            await page.mouse.wheel(0, delta)
            await self.human_sleep(0.2, 0.7)
        except Exception:
            pass

    # =============================================================
    # Proxy and UA
    # =============================================================
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

    # =============================================================
    # Cookie banner and app tab
    # =============================================================
    async def accept_cookies_if_present(self, page):
        try:
            banner = page.locator("[data-ui='cookie-consent']")
            if await banner.count() > 0 and await banner.is_visible():
                if self.debug:
                    print("[Workable DEBUG] Cookie banner detected, clicking accept")
                accept_btn = banner.locator("[data-ui='cookie-consent-accept']")
                if await accept_btn.count() > 0:
                    await accept_btn.click()
        except Exception as e:
            if self.debug:
                print("[Workable DEBUG] Cookie banner error:", e)

    async def go_to_application_tab(self, page):
        try:
            if await page.locator("form[data-ui='application-form']").count() > 0:
                return True

            tab = page.locator("[data-ui='application-form-tab']")
            if await tab.count() > 0:
                await tab.first.scroll_into_view_if_needed()
                await self.human_sleep(0.2, 0.5)
                await tab.first.click()
                await self.human_sleep(0.8, 1.3)

            return await page.locator("form[data-ui='application-form']").count() > 0
        except Exception:
            return False

    # =============================================================
    # Job context extraction
    # =============================================================
    async def extract_job_context(self, page, job):
        job_title = ""
        company_name = ""
        job_description = ""

        # Title
        try:
            title_loc = page.locator("h1")
            if await title_loc.count() > 0:
                job_title = (await title_loc.first.inner_text()).strip()
        except Exception:
            pass

        # Company name via explicit element
        try:
            comp_loc = page.locator("[data-ui='company-name']")
            if await comp_loc.count() > 0:
                company_name = (await comp_loc.first.inner_text()).strip()
        except Exception:
            pass

        # Breadcrumb fallback with filters
        INVALID_COMPANY_TERMS = {
            "overview",
            "about",
            "jobs",
            "careers",
            "openings",
            "roles",
            "application",
            "apply",
        }

        if not company_name:
            try:
                crumbs = page.locator("nav a")
                count = await crumbs.count()
                for i in range(count):
                    txt = (await crumbs.nth(i).inner_text()).strip()
                    norm = txt.lower()
                    if not txt:
                        continue
                    if norm in INVALID_COMPANY_TERMS:
                        continue
                    if len(txt) < 3:
                        continue
                    if txt.isupper():
                        continue
                    if job_title and txt.lower() in job_title.lower():
                        continue
                    company_name = txt
                    break
            except Exception:
                pass

        # Job dict fallback
        if not company_name:
            company_name = job.get("company") or ""

        # Domain fallback
        if not company_name:
            try:
                parsed = urlparse(job.get("apply_url") or job.get("job_url") or "")
                host = parsed.hostname or ""
                base = host.split(".")[0]
                base = base.replace("-", " ")
                if base:
                    company_name = base.title()
            except Exception:
                pass

        # Final fallback
        if not company_name:
            company_name = "the company"

        # Description
        selectors = [
            "[data-ui='job-description']",
            "section[data-ui='job-description']",
            "div[data-ui='job-description']",
            "div.styles--O7O7H",
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
            print(f"[Workable DEBUG] Job title: {job_title}")
            print(f"[Workable DEBUG] Company: {company_name}")
            print(f"[Workable DEBUG] JD length: {len(job_description)}")

        return job_title, company_name, job_description

    # =============================================================
    # Smart phone formatting
    # =============================================================
    def _format_phone_smart(self, raw_phone, country):
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

    # =============================================================
    # Salary parsing helper (lower bound)
    # =============================================================
    def _extract_salary_lower_bound(self, text: str) -> str | None:
        if not text:
            return None
        t = text.lower()
        matches = re.findall(r"(\d+)\s*k?", t)
        if not matches:
            return None
        nums = []
        for m in matches:
            val = int(m)
            if val < 1000:
                val = val * 1000
            nums.append(val)
        if not nums:
            return None
        return str(min(nums))

    # =============================================================
    # Short question detection
    # =============================================================
    def _is_short_question(self, question: str) -> bool:
        if not question:
            return True
        return len(question.strip()) < 200

    async def _is_short_input(self, input_el) -> bool:
        """
        Determine if a question expects a short answer
        by inspecting the input/textarea element's maxlength.
        """
        try:
            maxlength = await input_el.get_attribute("maxlength")

            # If Workable sets a maxlength
            if maxlength is not None:
                try:
                    ml = int(maxlength)
                    return ml <= 200
                except:
                    return True

            # If it's an input[type=text] with no maxlength → short
            tag_name = await input_el.evaluate("el => el.tagName.toLowerCase()")
            input_type = (await input_el.get_attribute("type")) or ""

            if tag_name == "input" and input_type in {"text", ""}:
                return True

            # textarea with no maxlength is typically long form
            if tag_name == "textarea":
                return False

        except:
            pass

        # Fallback: treat as long
        return False

    # =============================================================
    # Main apply
    # =============================================================
    async def apply(self, job, user, cv_path):
        job_url = (
            job.get("apply_url")
            or job.get("job_url")
            or job.get("url")
            or job.get("redirect_url")
        )
        if not job_url:
            return ApplyResult(status="failed", message="No job URL found")

        if self.debug:
            print(f"[Workable DEBUG] Starting apply for {job_url}")

        # Parse ai_cv_data
        ai_raw = user.get("ai_cv_data")
        if isinstance(ai_raw, str):
            try:
                ai_data = json.loads(ai_raw)
            except Exception:
                ai_data = {}
        elif isinstance(ai_raw, dict):
            ai_data = ai_raw
        else:
            ai_data = {}

        # Parse profile application answers
        raw = user.get("application_data")
        if isinstance(raw, str):
            try:
                profile_answers = json.loads(raw)
            except Exception:
                profile_answers = {}
        elif isinstance(raw, dict):
            profile_answers = raw
        else:
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

                    # Cookies
                    await self.human_sleep(4.8, 5.6)
                    await self.accept_cookies_if_present(page)
                    await self.human_sleep(1.8, 2.6)

                    # Application tab
                    await self.go_to_application_tab(page)
                    try:
                        await page.wait_for_selector(
                            "form[data-ui='application-form']",
                            timeout=20000,
                        )
                    except Exception:
                        if self.debug:
                            print("[Workable DEBUG] app form selector wait failed")

                    # Job context
                    job_title, company_name, job_description = await self.extract_job_context(
                        page, job
                    )

                    await self.human_sleep(0.8, 1.6)
                    await self.random_scroll(page)

                    # Core info
                    await self.fill_basic_info(page, ai_data, user)

                    # CV
                    cv_uploaded = await self.upload_cv(page, cv_path)

                    # Questions
                    await self.answer_custom_questions(
                        page,
                        ai_data,
                        profile_answers,
                        user,
                        job_title,
                        company_name,
                        job_description,
                    )

                    # Screenshot after full render
                    screenshot_path = f"/tmp/workable_{user.get('user_id')}_{job.get('id')}.png"

                    full_height = await page.evaluate("() => document.body.scrollHeight")
                    for _ in range(0, full_height, 600):
                        await page.mouse.wheel(0, 600)
                        await asyncio.sleep(0.25)

                    await asyncio.sleep(1.2)
                    await page.evaluate("window.scrollTo(0, 0)")
                    await asyncio.sleep(0.2)

                    await page.screenshot(path=screenshot_path, full_page=True)
                    screenshot_url = upload_to_s3(
                        screenshot_path, folder="screenshots"
                    )

                    if self.test_mode:
                        return ApplyResult(
                            status="success",
                            message="Workable test mode complete. Form filled but not submitted.",
                            screenshot_url=screenshot_url,
                        )

                    submitted = await self.click_submit(page)

                    if not submitted:
                        return ApplyResult(
                            status="retry",
                            message="Could not click Workable submit button",
                            screenshot_url=screenshot_url,
                        )

                    # Quick check for captcha
                    try:
                        captcha_present = (
                            await page.locator(
                                "div[id^='turnstile-container']:not([hidden])"
                            ).count()
                            > 0
                        )
                    except Exception:
                        captcha_present = False

                    if captcha_present:
                        return ApplyResult(
                            status="retry",
                            message="Turnstile captcha detected on Workable page",
                            screenshot_url=screenshot_url,
                        )

                    if not cv_uploaded:
                        return ApplyResult(
                            status="retry",
                            message="CV not uploaded on Workable form",
                            screenshot_url=screenshot_url,
                        )

                    return ApplyResult(
                        status="success",
                        message="Submitted Workable application",
                        screenshot_url=screenshot_url,
                    )

                finally:
                    try:
                        await browser.close()
                    except Exception:
                        pass

        except Exception as e:
            if self.debug:
                print("[Workable DEBUG] Top level error:", e)
            return ApplyResult(status="retry", message=str(e))

    # =============================================================
    # Basic info
    # =============================================================
    async def fill_basic_info(self, page, ai_data, user):
        fn = ai_data.get("first_name") or user.get("first_name") or ""
        ln = ai_data.get("last_name") or user.get("last_name") or ""
        email = ai_data.get("email") or user.get("email") or ""
        raw_phone = user.get("phone") or ai_data.get("phone") or ""
        country = user.get("country") or ""
        phone = self._format_phone_smart(raw_phone, country)

        city = user.get("city") or ""
        addr_country = user.get("country") or ""
        address = ", ".join(x for x in [city, addr_country] if x)

        async def set_field(selector, value, label):
            if not value:
                return
            loc = page.locator(selector)
            if await loc.count() == 0:
                return
            await loc.first.scroll_into_view_if_needed()
            await self.human_sleep(0.3, 0.7)
            await self.move_mouse_to_locator(page, loc.first)
            await self.human_sleep(0.2, 0.5)
            if self.debug:
                print(f"[Workable DEBUG] Filling {label}: {value}")
            await self.human_type(loc.first, value)

        await set_field("input[name='firstname']", fn, "first_name")
        await set_field("input[name='lastname']", ln, "last_name")
        await set_field("input[type='email']", email, "email")
        await set_field("input[type='tel']", phone, "phone")

        if address:
            await set_field(
                "input[data-ui='address'], input#address",
                address,
                "address",
            )

    # =============================================================
    # Resume upload
    # =============================================================
    async def upload_cv(self, page, cv_path: str) -> bool:
        file_input = page.locator("input[data-ui='resume']")
        if await file_input.count() == 0:
            if self.debug:
                print("[Workable DEBUG] Resume input not found")
            return False

        locator = file_input.first
        await locator.scroll_into_view_if_needed()
        await self.human_sleep(0.4, 1.0)

        try:
            if self.debug:
                print(f"[Workable DEBUG] Uploading CV from {cv_path}")
            await locator.set_input_files(cv_path)
            await self.human_sleep(1.5, 2.5)
            return True
        except Exception as e:
            if self.debug:
                print("[Workable DEBUG] Resume upload failed:", e)
            return False
    # =============================================================
    # Custom questions
    # =============================================================
    async def answer_custom_questions(
        self,
        page,
        ai_data,
        profile_answers,
        user,
        job_title,
        company_name,
        job_description,
    ):
        if not isinstance(profile_answers, dict):
            profile_answers = {}

        fields = page.locator(
            "form[data-ui='application-form'] [data-ui='field'], "
            "form[data-ui='application-form'] div.styles--3IYUq"
        )
        count = await fields.count()
        if self.debug:
            print(f"[Workable DEBUG] Found {count} potential field blocks")

        # get current employer from ai_data experience if available
        current_employer = None
        try:
            exp_list = ai_data.get("experience") or []
            if exp_list:
                current_employer = exp_list[0].get("company")
        except Exception:
            current_employer = None

        for i in range(count):
            block = fields.nth(i)

            label_text = await self._extract_label_text(block)
            if not label_text:
                continue
            label_norm = self._normalise_text(label_text)

            input_locator = block.locator("textarea, select, input")
            if await input_locator.count() == 0:
                continue

            input_el = input_locator.first
            is_short = await self._is_short_input(input_el)
            tag_name = await input_el.evaluate("el => el.tagName.toLowerCase()")
            input_type = (await input_el.get_attribute("type")) or ""
            data_ui_attr = await input_el.get_attribute("data-ui") or ""
            name_attr = (await input_el.get_attribute("name") or "").lower()

            # Required flag
            is_required = False
            try:
                if await input_el.get_attribute("required") is not None:
                    is_required = True
                if await input_el.get_attribute("aria-required") == "true":
                    is_required = True
                if await block.locator(
                    "span.styles--33eUF strong, strong:has-text('*')"
                ).count() > 0:
                    is_required = True
            except Exception:
                pass

            # Skip core identity fields
            if data_ui_attr in {"firstname", "lastname", "email", "phone", "resume"}:
                continue
            if name_attr in {
                "firstname",
                "first_name",
                "lastname",
                "last_name",
                "email",
                "phone",
            }:
                continue
            if input_type == "email":
                continue

            if self.debug:
                print(
                    f"[Workable DEBUG] Field {i}: '{label_text}' "
                    f"type={tag_name}/{input_type} data-ui={data_ui_attr} "
                    f"name={name_attr} required={is_required} short={is_short}"
                )

            # Deterministic address or location
            if data_ui_attr == "address" or "address" in label_norm or "location" in label_norm:
                city = user.get("city") or ""
                country = user.get("country") or ""
                addr = ", ".join(x for x in [city, country] if x)
                if addr:
                    try:
                        await input_el.scroll_into_view_if_needed()
                        await self.move_mouse_to_locator(page, input_el)
                        await self.human_sleep(0.2, 0.5)
                        await self.human_type(input_el, addr)
                    except Exception as e:
                        if self.debug:
                            print("[Workable DEBUG] address write error:", e)
                continue

            # Workable combobox
            if await block.locator("input[role='combobox']").count() > 0:
                # high confidence mapping for employer or similar
                direct = self._high_conf_profile_answer(
                    label_norm,
                    profile_answers,
                    ai_data,
                    user,
                    current_employer,
                    short_mode=is_short,
                )
                if direct is None:
                    desired = await self._generate_ai_answer(
                        label_norm,
                        ai_data,
                        profile_answers,
                        job_title,
                        company_name,
                        job_description,
                        short_mode=is_short,
                    )
                else:
                    desired = direct
                await self.handle_workable_combobox(block, page, desired)
                continue

            # Salary numeric handling
            is_salary_question = any(
                kw in label_norm
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
            if is_salary_question and tag_name == "input" and input_type == "number":
                raw_sal = profile_answers.get("desired_salary") or ""
                numeric_sal = self._extract_salary_lower_bound(raw_sal)
                if not numeric_sal:
                    numeric_sal = "50000"
                try:
                    await input_el.scroll_into_view_if_needed()
                    await self.move_mouse_to_locator(page, input_el)
                    await self.human_sleep(0.2, 0.4)
                    await self.human_type(input_el, numeric_sal)
                except Exception as e:
                    if self.debug:
                        print("[Workable DEBUG] numeric salary fill error:", e)
                continue

            # Try high confidence profile mapping for short questions
            direct_answer = None
            if is_short:
                direct_answer = self._high_conf_profile_answer(
                    label_norm,
                    profile_answers,
                    ai_data,
                    user,
                    current_employer,
                    short_mode=True,
                )

            # Normal select
            if tag_name == "select":
                if direct_answer is None:
                    desired = await self._generate_ai_answer(
                        label_norm,
                        ai_data,
                        profile_answers,
                        job_title,
                        company_name,
                        job_description,
                        short_mode=is_short,
                    )
                else:
                    desired = direct_answer
                await self._select_option_by_text(block, desired)
                continue

            # Text and textarea
            if tag_name in {"textarea", "input"} and input_type in {"text", ""}:
                if direct_answer is None:
                    answer = await self._generate_ai_answer(
                        label_norm,
                        ai_data,
                        profile_answers,
                        job_title,
                        company_name,
                        job_description,
                        short_mode=is_short,
                    )
                else:
                    answer = direct_answer
                try:
                    await input_el.scroll_into_view_if_needed()
                    await self.move_mouse_to_locator(page, input_el)
                    await self.human_sleep(0.2, 0.5)
                    await self.human_type(input_el, answer)
                except Exception as e:
                    if self.debug:
                        print("[Workable DEBUG] text answer fill error:", e)
                continue

            # Required radio or checkbox as last fallback
            if is_required and input_type in {"radio", "checkbox"}:
                await self._handle_yes_no_radio(block, value=True)

    # =============================================================
    # High confidence profile mapping for short questions
    # =============================================================
    def _high_conf_profile_answer(
        self,
        label_norm: str,
        profile_answers: dict,
        ai_data: dict,
        user: dict,
        current_employer: str | None,
        short_mode: bool,
    ) -> str | None:
        # Employer name
        if "employer" in label_norm or ("current" in label_norm and "company" in label_norm):
            if current_employer:
                return current_employer if not short_mode else current_employer[:50]

        # Notice period
        if "notice" in label_norm or "availability" in label_norm:
            np = profile_answers.get("notice_period")
            if np:
                return np if not short_mode else np[:50]

        # Work authorization
        if any(
            k in label_norm
            for k in [
                "right to work",
                "work authorization",
                "work authorisation",
                "eligible to work",
                "legal right to work",
                "legally allowed",
                "visa status",
            ]
        ):
            wa = profile_answers.get("work_authorization")
            if wa:
                return wa if not short_mode else wa[:50]

        # Years of experience
        if "years of experience" in label_norm or "experience in this field" in label_norm:
            ye = profile_answers.get("years_experience")
            if ye:
                if short_mode:
                    return f"{ye} years"
                return f"{ye} years of experience"

        # Location preference
        if any(k in label_norm for k in ["remote", "hybrid", "work preference"]):
            lp = profile_answers.get("location_preference")
            if lp:
                return lp if not short_mode else lp[:50]

        # If short mode and we reached here, let AI handle with very short style
        return None

    # =============================================================
    # Workable combobox
    # =============================================================
    async def handle_workable_combobox(self, block, page, desired_text):
        try:
            combobox = block.locator("input[role='combobox']")
            if await combobox.count() == 0:
                return False
            cb = combobox.first
            await cb.scroll_into_view_if_needed()
            await self.human_sleep(0.2, 0.5)
            await cb.click()
            await self.human_sleep(0.3, 0.5)

            listbox = page.locator("div[role='listbox']")
            await listbox.wait_for(state="visible", timeout=6000)
            options = listbox.locator("[role='option']")
            count = await options.count()
            if count == 0:
                return False

            desired_norm = self._normalise_text(desired_text or "")

            for i in range(count):
                txt = await options.nth(i).inner_text()
                if desired_norm and desired_norm in self._normalise_text(txt):
                    await options.nth(i).click()
                    await self.human_sleep(0.3, 0.6)
                    return True

            await options.first.click()
            await self.human_sleep(0.3, 0.6)
            return True
        except Exception as e:
            if self.debug:
                print("[Workable DEBUG] combobox handler error:", e)
            return False

    # =============================================================
    # AI answer generator
    # =============================================================
    async def _generate_ai_answer(
        self,
        question: str,
        ai_data: dict,
        profile_answers: dict,
        job_title: str,
        company_name: str,
        job_description: str,
        short_mode: bool,
    ) -> str:
        summary = ai_data.get("summary", "")
        skills = ai_data.get("skills", [])
        titles = ai_data.get("job_titles", [])[:3]
        experience = ai_data.get("experience", [])

        experience_snippets = []
        for exp in experience[:2]:
            experience_snippets.append(
                {
                    "title": exp.get("title"),
                    "company": exp.get("company"),
                    "achievements": exp.get("achievements", [])[:2],
                }
            )

        app_data_safe = dict(profile_answers or {})
        for key in ["race", "gender", "disability_status", "veteran_status"]:
            app_data_safe.pop(key, None)

        is_salary_question = any(
            kw in question
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
            "You help candidates answer Workable job application questions. "
            "Use a professional and confident tone. "
            "For long, open questions, write strong mini cover letter style answers of around three to five sentences. "
            "For short questions, respond with a very concise factual answer. "
            "If answer_style is 'short', respond with one to three words only, no greetings, no sign offs, no candidate name. "
            "Use the job title, company name and job description to tailor the answer when longer context is needed. "
            "Explicitly match the candidate skills and experience to the role where relevant. "
            "Only mention salary or compensation if the question explicitly asks about it. "
            "Never include greetings, sign offs, or the candidate name. "
            "Do not include closings such as best regards or yours sincerely. "
            "Do not invent employers, dates or qualifications."
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
            "application_data": app_data_safe,
            "answer_style": "short" if short_mode else "long",
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
                print("[Workable DEBUG] AI call error:", e)
            if short_mode:
                return "Yes"
            return (
                "My experience and skills align well with this position and I am motivated "
                "to contribute to strong results for the team."
            )

        if short_mode:
            # restrict to one to three words, no trailing punctuation
            tokens = answer.strip().split()
            tokens = tokens[:3]
            clean_tokens = [re.sub(r"[^\w£€$]+", "", t) for t in tokens]
            clean_tokens = [t for t in clean_tokens if t]
            return " ".join(clean_tokens)[:60] if clean_tokens else "Yes"

        return answer[:900]

    # =============================================================
    # Selects, radios, labels, submit
    # =============================================================
    async def _handle_yes_no_radio(self, block, value: bool):
        labels = block.locator("label")
        count = await labels.count()
        if count == 0:
            return

        preferred = "yes" if value else "no"

        for i in range(count):
            try:
                txt = await labels.nth(i).inner_text()
            except Exception:
                continue
            norm = self._normalise_text(txt)
            if preferred in norm:
                try:
                    await labels.nth(i).click()
                    await self.human_sleep(0.2, 0.4)
                except Exception:
                    pass
                return

    async def _select_option_by_text(self, block, desired_text: str):
        try:
            select_el = block.locator("select").first
        except Exception:
            return False

        options = select_el.locator("option")
        count = await options.count()
        desired_norm = self._normalise_text(desired_text or "")

        # direct match
        for i in range(count):
            txt = (await options.nth(i).inner_text()).strip()
            txt_norm = self._normalise_text(txt)
            if desired_norm and (desired_norm in txt_norm or txt_norm in desired_norm):
                val = await options.nth(i).get_attribute("value")
                if val:
                    await select_el.select_option(val)
                    await self.human_sleep(0.2, 0.4)
                    return True

        # fallback first non empty option
        for i in range(count):
            val = await options.nth(i).get_attribute("value")
            txt = (await options.nth(i).inner_text()).strip()
            if val and txt:
                await select_el.select_option(val)
                await self.human_sleep(0.2, 0.4)
                return True

        return False

    async def _extract_label_text(self, block):
        try:
            span = block.locator("label span[id$='_label']")
            if await span.count() > 0:
                txt = (await span.first.inner_text()).strip()
                if txt:
                    return txt
        except Exception:
            pass

        try:
            label_el = block.locator("label").first
            if await label_el.count() > 0:
                txt = (await label_el.inner_text()).strip()
                if txt:
                    return txt.split("\n")[0]
        except Exception:
            pass

        try:
            txt = (await block.inner_text()).strip()
            return txt.split("\n")[0]
        except Exception:
            return None

    def _normalise_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", text or "").strip().lower()

    async def click_submit(self, page):
        submit = page.locator(
            "button[data-ui='apply-button'], button[type='submit']"
        )
        if await submit.count() == 0:
            if self.debug:
                print("[Workable DEBUG] Submit button not found")
            return False
        btn = submit.first
        try:
            await btn.scroll_into_view_if_needed()
            await self.human_sleep(0.3, 0.7)
            await btn.click()
            return True
        except Exception as e:
            if self.debug:
                print("[Workable DEBUG] Submit click error:", e)
            return False
