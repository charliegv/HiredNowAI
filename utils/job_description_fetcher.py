# utils/job_description_fetcher.py

import asyncio
from playwright.async_api import async_playwright


async def scrape_job_description(url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        await page.goto(url, timeout=30000)
        html = await page.content()

        await browser.close()
        return html
