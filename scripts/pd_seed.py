# scripts/pd_seed.py
# Seed basic Pipedrive data (persons, deals, activities, email-like notes)
import os, sys, time, random, datetime as dt
import requests

API_BASE = os.getenv("PIPEDRIVE_API_BASE", "https://api.pipedrive.com/v1")
API_TOKEN = os.getenv("PIPEDRIVE_API_TOKEN")

def pd_post(path, json):
    r = requests.post(f"{API_BASE}{path}", params={"api_token": API_TOKEN}, json=json, timeout=30)
    if not r.ok:
        raise RuntimeError(f"POST {path} failed {r.status_code}: {r.text}")
    return r.json()["data"]

def pd_get(path, params=None):
    params = params or {}
    params["api_token"] = API_TOKEN
    r = requests.get(f"{API_BASE}{path}", params=params, timeout=30)
    if not r.ok:
        raise RuntimeError(f"GET {path} failed {r.status_code}: {r.text}")
    return r.json()["data"]

def ensure_token():
    if not API_TOKEN:
        print("❌ Missing PIPEDRIVE_API_TOKEN in environment")
        sys.exit(1)

def seed_persons():
    firsts = ["Anna","Bartek","Chris","Daria","Ewa"]
    lasts  = ["Kowalska","Boniecki","Smith","Nowak","Lee"]
    emails = ["anna@acme.test","bartek@prospect.co","chris@coldmail.net","daria@startup.dev","ewa@company.org"]
    persons = []
    for i in range(5):
        p = pd_post("/persons", {
            "name": f"{firsts[i]} {lasts[i]}",
            "email": emails[i],
            "phone": f"+48 123 000 10{i}",
            "visible_to": 3  # Entire company (safe on new accounts)
        })
        persons.append(p)
    return persons

def seed_deals(persons):
    titles = [
        "ACME – Pilot for v1.2.3",
        "ProspectCo – License 5 seats",
        "ColdMail – Trial discussion",
        "Startup.dev – Integration POC",
        "Company.org – Renewal"
    ]
    values = [1200, 500, 0, 800, 300]
    deals = []
    for i, person in enumerate(persons):
        d = pd_post("/deals", {
            "title": titles[i],
            "person_id": person["id"],
            "value": values[i],
            "currency": "USD"
        })
        deals.append(d)
    return deals

def seed_activities(deals):
    # Create 3 activities: one overdue, one in future, one missing for “stalled” signal
    now = dt.datetime.utcnow()
    acts = []
    # For first deal: overdue call yesterday
    acts.append(pd_post("/activities", {
        "subject": "Call about next steps",
        "type": "call",
        "due_date": (now - dt.timedelta(days=1)).strftime("%Y-%m-%d"),
        "due_time": "10:00",
        "duration": "00:30",
        "deal_id": deals[0]["id"],
        "done": 0
    }))
    # For second deal: meeting tomorrow
    acts.append(pd_post("/activities", {
        "subject": "Demo tomorrow",
        "type": "meeting",
        "due_date": (now + dt.timedelta(days=1)).strftime("%Y-%m-%d"),
        "due_time": "11:00",
        "duration": "01:00",
        "deal_id": deals[1]["id"],
        "done": 0
    }))
    # Third deal intentionally no activity (so it looks stalled)
    return acts

def seed_email_like_notes(deal, thread_index):
    # Simulate a short “email thread” as a single Note body
    notes = [
        {
            "from": "anna@acme.test",
            "to":   "you@yourdomain.tld",
            "subject": "Re: Pilot for v1.2.3",
            "body": "Thanks for the demo. What would the next steps be if we want to start next week?"
        },
        {
            "from": "you@yourdomain.tld",
            "to":   "anna@acme.test",
            "subject": "Re: Pilot for v1.2.3",
            "body": "Happy to proceed. We can begin with a 2-week pilot. Would Tuesday 10:00 work for a kickoff?"
        },
        {
            "from": "anna@acme.test",
            "to":   "you@yourdomain.tld",
            "subject": "Re: Pilot for v1.2.3",
            "body": "Tuesday 10:00 works. Please send a short SoW and I’ll loop procurement."
        },
    ]
    text_lines = [f"[Thread {thread_index}] Email transcript (latest first):", ""]
    for msg in reversed(notes):
        text_lines += [
            f"From: {msg['from']}",
            f"To: {msg['to']}",
            f"Subject: {msg['subject']}",
            "Body:",
            msg["body"],
            "-"*50
        ]
    body = "\n".join(text_lines)
    return pd_post("/notes", {"deal_id": deal["id"], "content": body})

def main():
    ensure_token()
    me = pd_get("/users/me")
    print("Seeding for company:", me.get("company_name"), "as", me.get("email"))

    persons = seed_persons()
    deals   = seed_deals(persons)
    acts    = seed_activities(deals)

    # Add a thread note to deal 1 and 4:
    seed_email_like_notes(deals[0], thread_index=1)
    seed_email_like_notes(deals[3], thread_index=2)

    print("✅ Seed complete.")
    print("Persons:", [p['name'] for p in persons])
    print("Deals  :", [d['title'] for d in deals])
    print("Activities created:", len(acts))

if __name__ == "__main__":
    main()
