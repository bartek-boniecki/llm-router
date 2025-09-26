"""
Simple generic CRM upsert (pretend REST endpoint) for cost-friendly CRMs.
In real life: use Zoho/Pipedrive/Freshsales SDKs or REST endpoints.
Here we just no-op unless CRM_BASE_URL and CRM_API_KEY are set.
"""

import httpx
import os

CRM_BASE = os.getenv("CRM_BASE_URL")
CRM_KEY = os.getenv("CRM_API_KEY")


async def crm_upsert_contact(email: str, name: str):
    if not CRM_BASE or not CRM_KEY:
        return
    headers = {"Authorization": f"Bearer {CRM_KEY}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=20) as client:
        await client.post(f"{CRM_BASE}/contacts:upsert", headers=headers, json={"email": email, "name": name})
