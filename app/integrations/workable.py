"""
Workable API (create candidate)
Docs: https://workable.readme.io/  (create job candidate endpoint)
Requires WORKABLE_SUBDOMAIN + WORKABLE_TOKEN
"""

import httpx
import os

SUBDOMAIN = os.getenv("WORKABLE_SUBDOMAIN")  # e.g. "acme"
TOKEN = os.getenv("WORKABLE_TOKEN")


async def workable_add_candidate(job_shortcode: str, name: str, email: str):
    if not SUBDOMAIN or not TOKEN:
        return
    headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    data = {"name": name, "email": email, "sourced": True}
    async with httpx.AsyncClient(timeout=30) as client:
        url = f"https://{SUBDOMAIN}.workable.com/spi/v3/jobs/{job_shortcode}/candidates"
        r = await client.post(url, headers=headers, json=data)
        r.raise_for_status()
