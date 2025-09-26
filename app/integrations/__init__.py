"""
Integration runner to keep API simple.
We implement tiny “happy path” helpers and no-op if not configured.
"""

from typing import Dict, Any, Optional

from .slack import post_message as slack_post_message
from .google_workspace import gmail_draft_reply, gmail_read_latest
from .ms_graph import ms_read_latest, ms_draft_reply
from .crm import crm_upsert_contact
from .workable import workable_add_candidate


async def run_integration_action(integration: Dict[str, Any], output_text: str):
    """
    Dispatch an integration action after we have model output.
    The caller passes a dict like:
      {"kind":"slack.post_message","channel":"#general","thread_ts":""}
    """
    kind = (integration.get("kind") or "").strip()

    if kind == "slack.post_message":
        # Accept 'channel' OR 'channel_id'; slack.post_message can resolve names to IDs.
        channel: str = integration.get("channel") or integration.get("channel_id") or "#general"
        thread_ts: Optional[str] = integration.get("thread_ts") or None
        await slack_post_message(channel=channel, text=output_text, thread_ts=thread_ts)
        return

    elif kind == "gmail.draft_reply":
        await gmail_draft_reply(thread_id=integration["thread_id"], body_text=output_text)
        return

    elif kind == "gmail.read_latest":
        await gmail_read_latest()
        return

    elif kind == "ms.read_latest":
        await ms_read_latest()
        return

    elif kind == "ms.draft_reply":
        await ms_draft_reply(message_id=integration["message_id"], body_text=output_text)
        return

    elif kind == "crm.upsert_contact":
        await crm_upsert_contact(integration["email"], integration.get("name", ""))
        return

    elif kind == "workable.add_candidate":
        await workable_add_candidate(integration["job_shortcode"], integration["name"], integration["email"])
        return

    # Unknown kind: safe no-op (beginner-friendly)
    return
