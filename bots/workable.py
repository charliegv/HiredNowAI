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
from utils.capsolver import CapSolverClient
import requests

load_dotenv()

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

import re

def submit_to_workable_api(job_id, fields, turnstile_token, user_agent, cookies=None):
    """
    Submit the application directly to Workable /apply API.
    Returns (success: bool, response_text)
    """

    url = f"https://apply.workable.com/api/v1/jobs/{job_id}/apply"
    payload = { "candidate": fields }

    headers = {
        "Content-Type": "application/json",
        "User-Agent": user_agent,
        "Origin": "https://apply.workable.com",
        "Referer": f"https://apply.workable.com/j/{job_id}/apply/",
        "x-turnstile-token": turnstile_token,
        "Accept": "application/json, text/plain, */*",
    }

    # cookies optional — only needed if company enforces them
    resp = requests.post(url, json=payload, headers=headers, cookies=cookies)

    # Workable uses `201 Created` for success
    if resp.status_code == 201:
        return True, resp.text

    return False, resp.text


def extract_job_id(url: str) -> str | None:
    """
    Extracts the Workable job ID from the job URL.
    Returns a string such as '2791597AD3', or None if not found.
    """
    match = re.search(r"/j/([A-Za-z0-9]+)/", url)
    return match.group(1) if match else None


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

        async def find_turnstile_site_key(page) -> str | None:
            """
            Detects and returns the Cloudflare Turnstile site key from the page.
            Searches visible DOM, hidden DOM, iframes, and script variables.
            Returns None if not found.
            """

            # 1) Standard location in DOM
            loc = page.locator("[data-sitekey]")
            if await loc.count() > 0:
                site_key = await loc.first.get_attribute("data-sitekey")
                if site_key:
                    return site_key

            # 2) Inside Turnstile iframe
            iframe = page.frame_locator("iframe[src*='turnstile']")
            frame_loc = iframe.locator("[data-sitekey]")
            if await frame_loc.count() > 0:
                site_key = await frame_loc.first.get_attribute("data-sitekey")
                if site_key:
                    return site_key

            # 3) Sometimes Turnstile uses a key named "siteKey" in JavaScript
            js_probe = await page.evaluate("""
                () => {
                    let found = null;
                    function scan(obj) {
                        for (const k in obj) {
                            try {
                                const v = obj[k];
                                if (typeof v === "string" && v.startsWith("0x") && v.length > 10) {
                                    found = v;
                                    return;
                                }
                                if (typeof v === "object" && v !== null) {
                                    scan(v);
                                }
                            } catch {}
                        }
                    }
                    scan(window);
                    return found;
                }
            """)
            if js_probe and isinstance(js_probe, str):
                return js_probe

            # 4) As a fallback: inspect shallow inline scripts for a value like "sitekey": "..."
            script_extract = await page.evaluate("""
                () => {
                    let scripts = Array.from(document.scripts).map(s => s.textContent || "");
                    for (const txt of scripts) {
                        const match = txt.match(/sitekey["']?\s*[:=]\s*["'](0x[a-zA-Z0-9-_]+)["']/);
                        if (match) return match[1];
                    }
                    return null;
                }
            """)
            if script_extract:
                return script_extract

            return None

        async def extract_fields(page):
            """
            Extract candidate field values from the Workable form.
            Handles:
              - text/email/tel/url/textarea (single string)
              - select (single string)
              - radio (array)
              - checkbox (array)
            Returns a list of {name: str, value: str | list[str]}
            """

            fields = []

            # TEXT INPUTS + TEXTAREA
            text_like = page.locator(
                "input[type='text'], input[type='email'], input[type='tel'], "
                "input[type='url'], input[type='number'], textarea"
            )
            for i in range(await text_like.count()):
                el = text_like.nth(i)
                name = await el.get_attribute("name")
                if not name:
                    continue
                if not await el.is_visible():
                    continue

                value = (await el.input_value()).strip()
                if value:
                    fields.append({"name": name, "value": value})

            # SELECT DROPDOWNS
            selects = page.locator("select")
            for i in range(await selects.count()):
                el = selects.nth(i)
                name = await el.get_attribute("name")
                if not name:
                    continue

                # Multi-select vs single
                is_multi = await el.get_attribute("multiple") is not None

                # single selection
                if not is_multi:
                    value = await el.input_value()
                    if value:
                        fields.append({"name": name, "value": value})
                    continue

                # multi selection → array
                options = el.locator("option:checked")
                selected = []
                for j in range(await options.count()):
                    selected.append(await options.nth(j).get_attribute("value"))
                if selected:
                    fields.append({"name": name, "value": selected})

            # RADIO GROUPS → boolean OR array
            radios = page.locator("input[type='radio']")
            if await radios.count() > 0:
                names = set()
                for i in range(await radios.count()):
                    name = await radios.nth(i).get_attribute("name")
                    if name:
                        names.add(name)

                for name in names:
                    checked = page.locator(f"input[type='radio'][name='{name}']:checked")
                    if await checked.count() == 0:
                        continue

                    value = await checked.first.get_attribute("value")

                    # Case 1: boolean radio ("true" / "false")
                    if value in ("true", "false"):
                        fields.append({"name": name, "value": value == "true"})
                        continue

                    # Case 2: regular multi-choice → array of IDs
                    fields.append({"name": name, "value": [value]})

            # CHECKBOX GROUPS (multi-select) + GDPR (boolean)
            # CHECKBOX GROUPS → detect single-choice disguised as checkboxes
            checkboxes = page.locator("input[type='checkbox']")
            if await checkboxes.count() > 0:

                # Group by container (Workable uses data-ui="QA_xxxx")
                containers = page.locator("[data-ui^='QA_']")
                for c in range(await containers.count()):
                    container = containers.nth(c)
                    group_boxes = container.locator("input[type='checkbox']")

                    if await group_boxes.count() == 0:
                        continue

                    # Check if all are required → Workable "single choice checkbox"
                    all_required = True
                    for i in range(await group_boxes.count()):
                        req = await group_boxes.nth(i).get_attribute("required")
                        if not req:
                            all_required = False
                            break

                    field_name = await container.get_attribute("data-ui")

                    if all_required:
                        # Single-choice disguised as checkbox group — exactly ONE must be checked.
                        checked = None
                        for i in range(await group_boxes.count()):
                            el = group_boxes.nth(i)
                            if await el.is_checked():
                                checked = await el.get_attribute("value") or await el.get_attribute("name")
                                break

                        # If user didn't check anything, fallback to first checkbox value
                        if not checked:
                            checked = await group_boxes.first.get_attribute("value")

                        fields.append({
                            "name": field_name,
                            "value": checked
                        })

                    else:
                        # REAL multi-select → return array
                        selected = []
                        for i in range(await group_boxes.count()):
                            el = group_boxes.nth(i)
                            if await el.is_checked():
                                val = await el.get_attribute("value") or await el.get_attribute("name")
                                selected.append(val)

                        if selected:
                            fields.append({
                                "name": field_name,
                                "value": selected
                            })

            # ---- PATCH: Force-capture GDPR consent ----
            # Look specifically for the standard Workable GDPR checkbox pattern
            gdpr_box = page.locator("input[type='checkbox'][name='gdpr']")
            if await gdpr_box.count() > 0:
                el = gdpr_box.first
                if await el.is_checked():
                    fields.append({
                        "name": "gdpr",
                        "value": True
                    })

            # ----- NEW: capture hidden Workable value inputs -----
            hidden = page.locator(
                "form[data-ui='application-form'] input[type='hidden'][name][value]"
            )
            for i in range(await hidden.count()):
                el = hidden.nth(i)
                name = await el.get_attribute("name")
                value = await el.get_attribute("value")

                # Skip resume (already handled)
                if name in {"resume", "csrf", "causal_token"}:
                    continue

                # Add only if not already in fields
                if name and value and not any(f["name"] == name for f in fields):
                    fields.append({"name": name, "value": value})

            # ----- Workable "fake hidden" inputs for combo values -----
            backend_inputs = page.locator(
                "form[data-ui='application-form'] input[name][value]"
                ":not([type='file']):not([type='checkbox']):not([type='radio'])"
            )

            for i in range(await backend_inputs.count()):
                el = backend_inputs.nth(i)

                name = await el.get_attribute("name")
                value = await el.get_attribute("value")

                if not name or not value:
                    continue

                # Skip fields we explicitly handle elsewhere
                if name in {"resume", "csrf", "causal_token"}:
                    continue

                # CRITICAL CHANGE:
                # Do NOT skip visible elements — Workable combo values are "visible" to Playwright
                # even though they are logically hidden.

                # Don't overwrite existing fields
                if not any(f["name"] == name for f in fields):
                    fields.append({"name": name, "value": value})

            # ----- Safe numeric conversion for QA fields only -----
            # ----- Safe numeric conversion for QA fields only -----
            for field in fields:
                name = field["name"]
                value = field["value"]

                # Only numeric conversion for QA_* fields, and only if the value is a string
                if not name.startswith(("QA_", "CA_")):
                    continue
                if not isinstance(value, str):
                    continue

                hidden_input = page.locator(
                    f"input[name='{name}'][type='hidden'], input[name='{name}']:not([type])")
                if await hidden_input.count() > 0:
                    continue

                raw = value.strip()

                # Detect Workable salary range encoding: "100,000,200,000"
                parts = raw.split(",")
                if len(parts) == 4:
                    # Example: ["100", "000", "200", "000"]
                    try:
                        low = int(parts[0] + parts[1])  # "100" + "000" = 100000
                        field["value"] = low
                    except:
                        pass
                    continue

                # Otherwise normal handling
                raw = raw.replace(",", "")
                if raw.isdigit():
                    try:
                        field["value"] = int(raw)
                    except:
                        pass

            return fields

        async def extract_resume(page):
            """
            Extract resume metadata (signed S3 URL + file name)
            Returns dict: { "url": "...", "name": "cv.pdf" }
            """
            return await page.evaluate("""
            () => {
                // Newer Workable pattern: attribute "data-value"
                const el = document.querySelector("input[name='resume'], input[data-field='resume']");
                if (!el) return null;

                // Some versions store JSON in data-value
                const dv = el.getAttribute("data-value");
                if (dv) {
                    try {
                        const data = JSON.parse(dv);
                        if (data.url && data.name) return data;
                    } catch {}
                }

                // Fallback - value might be JSON string
                if (el.value) {
                    try {
                        const data = JSON.parse(el.value);
                        if (data.url && data.name) return data;
                    } catch {}
                }

                return null;
            }
            """)

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

        if self.debug:
            print(f"[Workable DEBUG] Proxy selected: {proxy_config}")

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
                    resume_url = await self.upload_cv(page, cv_path)
                    cv_uploaded = True if resume_url else False

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
                    await self.handle_checkboxes(page)

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
                    await asyncio.sleep(10)

                    # Check for captcha
                    try:
                        locator = page.locator(
                                    "div[id^='turnstile-container']:not([hidden])"
                                )
                        captcha_present = (await locator.count()) > 0
                    except Exception:
                        captcha_present = False

                    fields = await extract_fields(page)
                    if resume_url:
                        fields.append({
                            "name": "resume",
                            "value": {"url": resume_url, "name": os.path.basename(cv_path)}
                        })
                    job_id = extract_job_id(page.url)
                    if captcha_present:
                        print("Captcha detected")

                        site_key = await page.evaluate("""
                            () => {
                                return window?.careers?.config?.turnstileWidgetSiteKey || null;
                            }
                        """)
                        if not site_key:
                            print("Unable to extract Turnstile site key")
                            return ApplyResult(status="retry", message="Turnstile site key not found")

                        print(f"Turnstile site key: {site_key}")

                        solver = CapSolverClient()
                        token = solver.solve_turnstile(website_key=site_key, website_url=page.url)

                        if not token:
                            print("Captcha solve failed")
                            return ApplyResult(status="manual_required", message="Captcha solve failed")

                        print(f"Got token ({len(token)} chars)")

                        print(job_id, fields, token, user_agent)

                        ok, resp_body  = submit_to_workable_api(job_id, fields, token, user_agent)
                        print(resp_body)



                        if not ok:
                            return ApplyResult(
                                status="manual_required",
                                message=f"API submit failed — requires manual application: {resp_body}",
                                screenshot_url=screenshot_url,
                            )
                        return ApplyResult(
                            status="success",
                            message="Workable application submitted via API",
                            screenshot_url=screenshot_url,
                        )

                    if not cv_uploaded:
                        return ApplyResult(
                            status="failed",
                            message="Job may be deactivated",
                            screenshot_url=screenshot_url,
                        )

                    success_ui = page.locator("div[data-ui='success'], h1:has-text('Thank')")
                    if await success_ui.count() > 0:
                        return ApplyResult(
                            status="success",
                            message="Submitted Workable application",
                            screenshot_url=screenshot_url,
                        )
                    return ApplyResult(
                        status="manual_required",
                        message="Error submitting workable application",
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
    async def upload_cv(self, page, cv_path: str) -> str | None:
        """
        Upload CV if required. Returns the resume downloadUrl needed for the
        /apply API request. Returns None if CV not required.
        """

        # Capture resume S3 upload link
        resume_download = {"url": None}

        async def on_response(response):
            if "/form/upload/resume" in response.url:
                try:
                    data = await response.json()
                    resume_download["url"] = data.get("downloadUrl")
                    if self.debug:
                        print("[Workable DEBUG] Resume download URL captured:", resume_download["url"])
                except:
                    pass

        # Listener MUST be attached before upload
        page.on("response", on_response)

        # --- STEP 1: Find ALL visible file inputs ---
        all_file_inputs = page.locator("input[type='file']")
        count = await all_file_inputs.count()
        if count == 0:
            if self.debug:
                print("[Workable DEBUG] No file inputs found — skipping.")
            return None

        # --- STEP 2: Identify ONLY resume inputs based on accepted formats ---
        resume_inputs = []
        for i in range(count):
            el = all_file_inputs.nth(i)

            if not await el.is_visible():
                continue

            accept_attr = (await el.get_attribute("accept") or "").lower()

            # Resume file types
            is_resume = any(t in accept_attr for t in ["pdf", "doc", "docx", "rtf", "odt"])
            # Image/photo file types
            is_image = any(t in accept_attr for t in ["jpg", "jpeg", "png", "gif", "image/"])

            # We want ONLY document upload fields
            if is_resume and not is_image:
                resume_inputs.append(el)

        if not resume_inputs:
            if self.debug:
                print("[Workable DEBUG] No resume input detected — found only PHOTO upload fields.")
            return None

        # Use the first valid resume input
        locator = resume_inputs[0]

        if self.debug:
            print("[Workable DEBUG] Using resume input with accept=", await locator.get_attribute("accept"))

        # --- STEP 3: Required detection ---
        is_required = True

        # 1. Input attributes
        required_attr = await locator.get_attribute("required")
        aria_required = await locator.get_attribute("aria-required")
        if required_attr is not None or aria_required == "true":
            is_required = True

        # 2. Wrapper element
        wrapper = await locator.evaluate_handle("e => e.closest('[data-role=\"dropzone\"]')")
        if wrapper:
            w_req = await wrapper.get_attribute("aria-required")
            w_req2 = await wrapper.get_attribute("required")
            if w_req == "true" or w_req2 is not None:
                is_required = True

        # 3. Label contains a '*'
        try:
            label_id = await locator.get_attribute("aria-labelledby")
            if label_id:
                label_text = await page.inner_text(f"#{label_id}")
                if "*" in label_text:
                    is_required = True
        except:
            pass

        if self.debug:
            print(f"[Workable DEBUG] Resume required = {is_required}")

        if not is_required:
            if self.debug:
                print("[Workable DEBUG] Resume optional — skipping upload.")
            return None

        # --- STEP 4: Perform upload ---
        try:
            await locator.scroll_into_view_if_needed()
            await self.human_sleep(0.4, 1.0)
            await locator.set_input_files(cv_path)
            await self.human_sleep(1.2, 2.2)

            # Wait for Workable to respond with downloadUrl
            for _ in range(50):
                if resume_download["url"]:
                    return resume_download["url"]
                await asyncio.sleep(0.1)

            if self.debug:
                print("[Workable DEBUG] Upload finished but downloadUrl not captured.")

            return None

        except Exception as e:
            if self.debug:
                print("[Workable DEBUG] Resume upload failed:", e)
            return None

    async def ask_ai_checkbox_selection(self, question, options):
        """
        Returns a list of option labels the AI thinks should be checked.
        Used for multi-checkbox questions.
        """
        # --- Force demographic questions to single-choice ---
        demographic_keywords = [
            "ethnicity", "race", "racial", "gender", "sex",
            "sexual orientation", "orientation",
            "disability", "disabled",
            "veteran", "armed forces", "military",
            "diversity", "equal opportunities"
        ]

        q = question.lower()

        if any(k in q for k in demographic_keywords):
            # Choose exactly ONE option using simple AI logic
            prompt = f"""
        You MUST choose exactly ONE answer for this question.
        It is a demographic equal opportunities question.

        QUESTION:
        {question}

        OPTIONS:
        {options}

        Return ONLY one label from the options.
        Return it as a plain string, not a list.
                """

            try:
                result = await client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=20,
                )
                answer = result.choices[0].message["content"].strip()
                return [answer]  # return list with ONE element
            except:
                return [options[0]]  # fallback to first option

        prompt = f"""
    The user must answer the following job application question using checkboxes:

    QUESTION:
    {question}

    OPTIONS:
    {options}

    Return ONLY the labels that should be checked, based on what will most likely get the application approved.
    Return as a Python list of strings. For example:
    ["Yes", "I agree"]
        """

        try:
            result = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=50,
            )

            answer = result.choices[0].message["content"]
            parsed = json.loads(answer)
            return parsed if isinstance(parsed, list) else []
        except:
            return []

    async def handle_checkboxes(self, page):
        """
        Handles all checkbox logic for Workable, including:
        - Required checkbox groups (single choice)
        - Regular multi-checkbox groups
        - GDPR / consent boxes
        - AI-driven decision selection
        """

        # 1. Find all high-level checkbox groups
        checkbox_groups = await page.query_selector_all("div[role='group']")

        for group in checkbox_groups:
            # Extract the label for the question
            label_id = await group.get_attribute("aria-labelledby")
            question_text = ""
            if label_id:
                try:
                    question_text = await page.inner_text(f"#{label_id}")
                except:
                    pass

            # Extract all checkbox <input> elements inside the group
            checkboxes = await group.query_selector_all("input[type='checkbox']")
            if not checkboxes:
                continue

            # Build list of checkbox items
            items = []
            for cb in checkboxes:
                wrapper = await cb.evaluate_handle("e => e.closest('[role=checkbox], [data-ui=\"gdpr\"]')")
                label_el = await cb.evaluate_handle("e => e.closest('label')")
                label_text = ""

                try:
                    label_text = await label_el.inner_text()
                except:
                    pass

                items.append({
                    "cb": cb,
                    "wrapper": wrapper,
                    "label": (label_text or "").strip()
                })

            # -------------------------------------------------------------------
            # 2. Decide if this is a REQUIRED single-choice group
            # -------------------------------------------------------------------
            required_count = 0
            for item in items:
                req = await item["cb"].get_attribute("required")
                aria_req = await item["cb"].get_attribute("aria-required")
                if req is not None or aria_req == "true":
                    required_count += 1

            # Workable pattern: ALL boxes required => SINGLE CHOICE
            is_single_choice_required = (required_count == len(items) and len(items) > 1)

            # -------------------------------------------------------------------
            # 3. Ask AI which labels should be checked
            # -------------------------------------------------------------------
            try:
                ai_answer = await self.ask_ai_checkbox_selection(
                    question_text,
                    [i["label"] for i in items]
                )
            except:
                ai_answer = []

            # Normalize AI labels
            ai_labels = [x.lower() for x in ai_answer if isinstance(x, str)]

            # -------------------------------------------------------------------
            # 4. APPLY LOGIC
            # -------------------------------------------------------------------

            if is_single_choice_required:
                # -------------------------------------------------------------
                # REQUIRED GROUP - EXACTLY ONE CHECKED
                # -------------------------------------------------------------

                # If AI produced nothing, choose first option
                selected_label = None
                if ai_labels:
                    selected_label = ai_labels[0]
                else:
                    selected_label = items[0]["label"].lower()

                # Enforce single selection
                for item in items:
                    label = item["label"].lower()
                    cb = item["cb"]

                    if label == selected_label:
                        # Check this one
                        try:
                            await cb.check()
                        except:
                            if item["wrapper"]:
                                await item["wrapper"].click()
                    else:
                        # Uncheck all others
                        try:
                            await cb.uncheck()
                        except:
                            pass

                continue

            # -------------------------------------------------------------------
            # 5. MULTI-SELECT GROUP
            # -------------------------------------------------------------------
            for item in items:
                label = item["label"].lower()
                cb = item["cb"]

                should_check = False

                # AI wants it
                if label in ai_labels:
                    should_check = True

                # If checkbox is required (but not all required, otherwise handled above)
                req = await cb.get_attribute("required")
                aria_req = await cb.get_attribute("aria-required")
                if req is not None or aria_req == "true":
                    should_check = True

                if should_check:
                    try:
                        await cb.check()
                    except:
                        if item["wrapper"]:
                            await item["wrapper"].click()
                else:
                    try:
                        await cb.uncheck()
                    except:
                        pass

        # -------------------------------------------------------------------
        # 6. Catch any remaining orphan checkboxes still required and unchecked
        # -------------------------------------------------------------------
        all_required = await page.query_selector_all("input[type='checkbox'][required]")
        for cb in all_required:
            if not await cb.is_checked():
                try:
                    await cb.check()
                except:
                    wrapper = await cb.evaluate_handle("e => e.closest('[role=checkbox], [data-ui=\"gdpr\"]')")
                    if wrapper:
                        await wrapper.click()

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

            # Extract the label
            label_text = await self._extract_label_text(block)
            if not label_text:
                continue
            label_norm = self._normalise_text(label_text)

            # --------------------------------------------------------------------
            # 1. Workable custom checkbox & radio groups (MUST BE FIRST)
            # --------------------------------------------------------------------
            custom_checkbox = block.locator(
                "[class*='checkboxOption'], [class*='radioOption']"
            )
            if await custom_checkbox.count() > 0:
                try:
                    txt = await block.inner_text()
                    norm = self._normalise_text(txt)

                    should_click = False

                    # Heuristics - always click "agree/yes"
                    if any(k in norm for k in [
                        "right to work",
                        "work in the united kingdom",
                        "eligible to work",
                        "work authorisation",
                        "resident in the uk",
                        "continuously resident",
                        "security clear",
                        "clearable",
                        "enhanced clearance",
                        "not aware of any reason",
                        "pass police",
                        "national security",
                        "privacy notice",
                        "consent",
                        "i have read",
                        "i agree",
                        "i accept",
                    ]):
                        should_click = True

                    # Also click ANY required checkbox groups
                    aria_required = await block.get_attribute("aria-required")
                    if aria_required == "true":
                        should_click = True

                    if should_click:
                        clickable = block.locator("[class*='checkbox'], [class*='radio']")
                        if await clickable.count() > 0:
                            await clickable.first.scroll_into_view_if_needed()
                            await self.human_sleep(0.2, 0.4)
                            await clickable.first.click()
                            await self.human_sleep(0.2, 0.4)
                    continue

                except Exception as e:
                    if self.debug:
                        print("[Workable DEBUG] custom checkbox handler error:", e)
                continue

            # --------------------------------------------------------------------
            # 1b. Workable new-style radiogroup <fieldset role="radiogroup">
            # --------------------------------------------------------------------
            radiogroup = block.locator("fieldset[role='radiogroup']")
            if await radiogroup.count() > 0:
                try:
                    # Extract all radio options
                    options = radiogroup.locator("div[role='radio']")
                    option_count = await options.count()
                    if option_count == 0:
                        continue

                    # Build list of labels
                    labels = []
                    for n in range(option_count):
                        try:
                            label_el = options.nth(n).locator("xpath=following-sibling::span[1]")
                            if await label_el.count() > 0:
                                labels.append(await label_el.inner_text())
                            else:
                                labels.append(None)
                        except:
                            labels.append(None)

                    # Decide the answer
                    desired = None

                    # Try profile mapping (short questions, work auth, availability etc)
                    direct = self._high_conf_profile_answer(
                        label_norm,
                        profile_answers,
                        ai_data,
                        user,
                        current_employer,
                        short_mode=True,
                    )
                    if direct:
                        desired = direct

                    # Otherwise ask AI
                    if not desired:
                        desired = await self._generate_ai_answer(
                            label_norm,
                            ai_data,
                            profile_answers,
                            job_title,
                            company_name,
                            job_description,
                            short_mode=True,
                        )

                    desired_norm = self._normalise_text(desired)

                    # Try to click matching option
                    clicked = False
                    for idx, lbl in enumerate(labels):
                        if lbl and desired_norm in self._normalise_text(lbl):
                            opt = options.nth(idx)
                            await opt.scroll_into_view_if_needed()
                            await self.human_sleep(0.2, 0.4)
                            await opt.click()
                            clicked = True
                            break

                    # Fallback: click first radio
                    if not clicked:
                        first_opt = options.first
                        await first_opt.scroll_into_view_if_needed()
                        await self.human_sleep(0.2, 0.4)
                        await first_opt.click()

                    continue

                except Exception as e:
                    if self.debug:
                        print("[Workable DEBUG] new radiogroup handler error:", e)
                continue
            # --------------------------------------------------------------------
            # 1c. Workable GDPR/consent checkbox <div role="checkbox">
            # --------------------------------------------------------------------
            gdpr_checkbox = block.locator("div[role='checkbox']")
            if await gdpr_checkbox.count() > 0:
                try:
                    # Extract the visible text
                    txt = await block.inner_text()
                    norm = self._normalise_text(txt)

                    should_click = False

                    # Privacy consent / GDPR always needs to be ticked
                    if any(k in norm for k in [
                        "privacy notice",
                        "gdpr",
                        "consent",
                        "i have read",
                        "i accept",
                        "i agree",
                        "processing of my data",
                        "personal data",
                        "accept the content",
                    ]):
                        should_click = True

                    # Check if required
                    required_attr = await gdpr_checkbox.first.get_attribute("aria-required")
                    if required_attr == "true":
                        should_click = True

                    if should_click:
                        await gdpr_checkbox.first.scroll_into_view_if_needed()
                        await self.human_sleep(0.2, 0.4)
                        await gdpr_checkbox.first.click()
                        await self.human_sleep(0.2, 0.4)

                    continue

                except Exception as e:
                    if self.debug:
                        print("[Workable DEBUG] GDPR checkbox handler error:", e)
                continue

            # --- NEW: force-detect Workable special dropdowns before standard inputs ---
            combo_input = block.locator("input[role='combobox']")
            if await combo_input.count() > 0:
                # Extract profile or AI answer
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

                if self.debug:
                    print(f"[Workable DEBUG] Combo detected: '{label_text}' => '{desired}'")

                await self.handle_workable_combobox(block, page, desired)
                continue

            # --------------------------------------------------------------------
            # 2. Standard inputs (textarea, select, input)
            # --------------------------------------------------------------------
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

            # Skip identity fields
            if data_ui_attr in {"firstname", "lastname", "email", "phone", "resume"}:
                continue
            if name_attr in {
                "firstname", "first_name",
                "lastname", "last_name",
                "email", "phone",
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

            # --------------------------------------------------------------------
            # 3. Location/address
            # --------------------------------------------------------------------
            if data_ui_attr == "address" or "address" in label_norm or "location" in label_norm:
                city = user.get("city") or ""
                country = user.get("country") or ""
                address = ", ".join(x for x in [city, country] if x)
                if address:
                    try:
                        await input_el.scroll_into_view_if_needed()
                        await self.move_mouse_to_locator(page, input_el)
                        await self.human_sleep(0.2, 0.5)
                        await self.human_type(input_el, address)
                    except Exception as e:
                        if self.debug:
                            print("[Workable DEBUG] address write error:", e)
                continue

            # --------------------------------------------------------------------
            # 4. Workable combo-box <input role="combobox">
            # --------------------------------------------------------------------
            if await block.locator("input[role='combobox']").count() > 0:
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

            # --------------------------------------------------------------------
            # 5. Salary inputs
            # --------------------------------------------------------------------
            is_salary_question = any(
                kw in label_norm
                for kw in [
                    "salary", "compensation", "pay range", "remuneration",
                    "annual pay", "expected pay", "expected earnings", "ctc",
                ]
            )
            if is_salary_question and tag_name == "input" and input_type == "number":
                raw_sal = profile_answers.get("desired_salary") or ""
                numeric_sal = self._extract_salary_lower_bound(raw_sal) or "50000"

                try:
                    await input_el.scroll_into_view_if_needed()
                    await self.move_mouse_to_locator(page, input_el)
                    await self.human_sleep(0.2, 0.4)
                    await self.human_type(input_el, numeric_sal)
                except Exception as e:
                    if self.debug:
                        print("[Workable DEBUG] numeric salary fill error:", e)
                continue

            # --------------------------------------------------------------------
            # 6. Select elements
            # --------------------------------------------------------------------
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

            if tag_name == "select":
                desired = (
                    direct_answer
                    if direct_answer is not None
                    else await self._generate_ai_answer(
                        label_norm,
                        ai_data,
                        profile_answers,
                        job_title,
                        company_name,
                        job_description,
                        short_mode=is_short,
                    )
                )
                await self._select_option_by_text(block, desired)
                continue

            # --------------------------------------------------------------------
            # 7. Textareas and text inputs
            # --------------------------------------------------------------------
            if tag_name in {"textarea", "input"} and input_type in {"text", ""}:
                answer = (
                    direct_answer
                    if direct_answer is not None
                    else await self._generate_ai_answer(
                        label_norm,
                        ai_data,
                        profile_answers,
                        job_title,
                        company_name,
                        job_description,
                        short_mode=is_short,
                    )
                )
                try:
                    await input_el.scroll_into_view_if_needed()
                    await self.move_mouse_to_locator(page, input_el)
                    await self.human_sleep(0.2, 0.5)
                    await self.human_type(input_el, answer)
                except Exception as e:
                    if self.debug:
                        print("[Workable DEBUG] text answer fill error:", e)
                continue

            # --------------------------------------------------------------------
            # 8. Native radio/checkbox fallback (rare on Workable)
            # --------------------------------------------------------------------
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
            # Wrapper that opens the dropdown
            wrapper = block.locator("[data-input-type='select'], [data-ui][data-input-type='select']")
            if await wrapper.count() == 0:
                wrapper = block

            # Combo input
            combo = wrapper.locator("input[role='combobox']").first
            await combo.scroll_into_view_if_needed()
            await self.human_sleep(0.3, 0.5)

            # open dropdown
            await wrapper.click()
            await self.human_sleep(0.4, 0.7)

            # --- FIX: restrict listbox search to this block ONLY ---
            listbox = block.locator(
                "[role='listbox'], div[data-ui='listbox'], div[role='option-list']"
            )

            # fallback if Workable renders listbox outside block
            if await listbox.count() == 0:
                # find the *closest active* listbox near the combo
                listbox = page.locator(
                    f"#{await combo.get_attribute('aria-controls')}"
                )

            # wait only for THIS listbox
            await listbox.first.wait_for(state="visible", timeout=5000)

            options = listbox.locator("[role='option'], div[data-ui='option']")
            count = await options.count()
            if count == 0:
                return False

            desired_norm = self._normalise_text(desired_text or "")

            # Try match
            for i in range(count):
                txt = await options.nth(i).inner_text()
                if desired_norm and desired_norm in self._normalise_text(txt):
                    await options.nth(i).click()
                    await self.human_sleep(0.3, 0.6)
                    return True

            # fallback to first visible
            await options.first.click()
            await self.human_sleep(0.3, 0.6)
            return True

        except Exception as e:
            if self.debug:
                print("[Workable DEBUG] NEW dropdown handler error:", e)
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
