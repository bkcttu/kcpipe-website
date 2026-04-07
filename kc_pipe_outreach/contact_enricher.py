"""
Contact Enrichment — finds the actual procurement/tubular buyer at each operator.

Priority: Apollo.io → Hunter.io → AI Web Research

STRICT title filtering — only returns contacts with relevant procurement/drilling
titles. Never returns HR, Finance, Legal, Land, HSE, or other non-buyer roles.
"""

import logging
import time
from datetime import datetime, timedelta

import requests

from outreach_config import Config
from outreach_db import get_db, upsert_contact, get_contact_by_operator

logger = logging.getLogger(__name__)

# ── Title Hierarchy (Byron's exact ranking) ──────────────────────────────────

# Tier 1: They own the pipe buy (score 100+)
TIER1_TITLES = [
    "tubular coordinator",
    "octg coordinator",
    "tubular goods coordinator",
    "pipe coordinator",
    "casing and tubing coordinator",
    "casing & tubing coordinator",
    "octg buyer",
    "tubular buyer",
    "tubular specialist",
    "tubular manager",
]

# Tier 2: Controls the spend (score 70+)
TIER2_TITLES = [
    "procurement manager",
    "supply chain manager",
    "materials manager",
    "purchasing manager",
    "drilling materials manager",
    "procurement director",
    "supply chain director",
    "procurement specialist",
    "purchasing agent",
    "materials coordinator",
    "procurement coordinator",
    "supply chain coordinator",
]

# Tier 3: For small operators where one person does everything (score 40+)
TIER3_TITLES = [
    "drilling engineer",
    "completions engineer",
    "well site manager",
    "drilling superintendent",
    "drilling foreman",
    "senior drilling engineer",
    "completions manager",
]

# Tier 4: Decision maker but harder to reach (score 20+)
TIER4_TITLES = [
    "vp of drilling",
    "vp drilling",
    "vice president drilling",
    "vice president of drilling",
    "director of drilling",
    "director drilling",
    "vp of operations",
    "vp operations",
    "vice president operations",
    "vice president of operations",
    "director of operations",
    "director operations",
    "drilling manager",
]

# SKIP ENTIRELY — these people don't buy pipe
SKIP_TITLES = [
    "company man",
    "tool pusher",
    "rig manager",
    "hse", "safety", "health safety",
    "landman", "land manager", "land director",
    "accounting", "accountant", "controller",
    "finance", "cfo", "treasurer",
    "human resources", "hr director", "hr manager",
    "legal", "counsel", "attorney",
    "geologist", "geophysicist", "geoscien",
    "marketing", "communications", "public relations",
    "it manager", "it director", "information technology",
    "receptionist", "administrative", "office manager",
]


def _should_skip(title):
    """Return True if this title should be skipped entirely."""
    title_lower = title.lower()
    for skip in SKIP_TITLES:
        if skip in title_lower:
            return True
    return False


def _score_title(title):
    """Score a title based on Byron's hierarchy. Returns 0 if no match."""
    title_lower = title.lower()

    # Check skip list first
    if _should_skip(title_lower):
        return -1

    # Tier 1: Tubular/OCTG coordinator (best)
    for i, kw in enumerate(TIER1_TITLES):
        if kw in title_lower:
            return 100 + (len(TIER1_TITLES) - i)

    # Tier 2: Procurement/Supply Chain
    for i, kw in enumerate(TIER2_TITLES):
        if kw in title_lower:
            return 70 + (len(TIER2_TITLES) - i)

    # Tier 3: Drilling/Completions engineers
    for i, kw in enumerate(TIER3_TITLES):
        if kw in title_lower:
            return 40 + (len(TIER3_TITLES) - i)

    # Tier 4: VP/Director level
    for i, kw in enumerate(TIER4_TITLES):
        if kw in title_lower:
            return 20 + (len(TIER4_TITLES) - i)

    return 0


def enrich_contacts(operators):
    """
    Enrich a list of operator records with buyer contact info.
    STRICT: only returns contacts with relevant titles.
    """
    enriched = []
    not_found = []

    for op in operators:
        operator_name = op['operator']

        # Check if we already have a recent contact
        existing = get_contact_by_operator(operator_name)
        if existing and existing.get('contact_email'):
            created = existing.get('created_at', '')
            if created:
                try:
                    created_dt = datetime.strptime(created[:10], '%Y-%m-%d')
                    if (datetime.now() - created_dt).days < 90:
                        enriched.append({**op, **existing})
                        continue
                except ValueError:
                    pass

        # Try enrichment sources in order
        contact = None
        rig_count = op.get('rig_count', 0)

        if Config.APOLLO_API_KEY:
            contact = _search_apollo(operator_name, rig_count)
            if contact:
                time.sleep(0.5)

        if not contact and Config.HUNTER_API_KEY:
            contact = _search_hunter(operator_name, rig_count)

        if not contact:
            contact = _ai_research(operator_name)

        if contact:
            contact['operator'] = operator_name
            contact['rig_count'] = op.get('rig_count')
            contact_id = upsert_contact(contact)
            contact['id'] = contact_id
            enriched.append({**op, **contact})
        else:
            not_found.append(op)

    return {'enriched': enriched, 'not_found': not_found}


def _search_apollo(operator_name, rig_count=0):
    """Search Apollo.io People Search API for the best buyer contact."""
    url = "https://api.apollo.io/v1/mixed_people/search"

    # Build title list based on operator size
    all_titles = TIER1_TITLES + TIER2_TITLES
    if rig_count and rig_count <= 5:
        all_titles += TIER3_TITLES
    all_titles += TIER4_TITLES

    payload = {
        "api_key": Config.APOLLO_API_KEY,
        "q_organization_name": operator_name,
        "person_titles": all_titles,
        "person_locations": ["Texas", "New Mexico", "Midland", "Odessa", "Houston"],
        "contact_email_status": ["verified", "guessed"],
        "per_page": 10,
        "page": 1,
    }

    try:
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        people = data.get('people', [])
        if not people:
            logger.info(f"Apollo: no results for {operator_name}")
            return None

        best = _rank_and_filter(people)
        if not best:
            logger.info(f"Apollo: no relevant titles found for {operator_name}")
            return None

        return {
            'contact_name': best.get('name', ''),
            'contact_title': best.get('title', ''),
            'contact_email': best.get('email', ''),
            'email_confidence': 'verified' if best.get('email_status') == 'verified' else 'guessed',
            'source': 'apollo',
            'linkedin_url': best.get('linkedin_url', ''),
        }
    except requests.RequestException as e:
        logger.error(f"Apollo API error for {operator_name}: {e}")
        return None


def _rank_and_filter(people):
    """Rank contacts by title relevance. ONLY returns people with relevant titles."""
    scored = []
    for person in people:
        title = person.get('title') or ''
        score = _score_title(title)

        if score <= 0:
            continue  # Skip irrelevant or blacklisted titles

        # Boost for verified email
        if person.get('email_status') == 'verified':
            score += 5

        # Must have an email
        if person.get('email'):
            scored.append((score, person))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1] if scored else None


def _search_hunter(operator_name, rig_count=0):
    """Fallback: search Hunter.io — STRICT title filtering."""
    url = "https://api.hunter.io/v2/domain-search"

    params = {
        "company": operator_name,
        "api_key": Config.HUNTER_API_KEY,
        "limit": 20,
    }

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json().get('data', {})

        emails = data.get('emails', [])
        if not emails:
            logger.info(f"Hunter: no results for {operator_name}")
            return None

        # Score every person, only return if they have a relevant title
        scored = []
        for email_rec in emails:
            position = email_rec.get('position') or ''
            score = _score_title(position)

            if score <= 0:
                continue  # Skip irrelevant titles

            if not email_rec.get('value'):
                continue

            name_parts = [email_rec.get('first_name', ''),
                          email_rec.get('last_name', '')]
            name = ' '.join(p for p in name_parts if p)
            if not name:
                continue

            scored.append((score, {
                'contact_name': name,
                'contact_title': position,
                'contact_email': email_rec.get('value', ''),
                'email_confidence': 'verified' if email_rec.get('confidence', 0) >= 90 else 'guessed',
                'source': 'hunter',
                'linkedin_url': email_rec.get('linkedin', ''),
            }))

        scored.sort(key=lambda x: x[0], reverse=True)

        if scored:
            return scored[0][1]

        # NO FALLBACK — don't return random people
        logger.info(f"Hunter: no relevant titles found for {operator_name}")
        return None

    except requests.RequestException as e:
        logger.error(f"Hunter API error for {operator_name}: {e}")
        return None


def _ai_research(operator_name):
    """Last resort: use Claude API with web search to find the right contact."""
    if not Config.ANTHROPIC_API_KEY:
        return None

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)

        prompt = (
            f"I need to find the tubular goods buyer or OCTG procurement contact at "
            f"{operator_name}, an oil and gas operator in the Permian Basin.\n\n"
            f"The ideal contact has one of these titles (in order of preference):\n"
            f"1. Tubular Coordinator / OCTG Coordinator / Pipe Coordinator\n"
            f"2. Procurement Manager / Supply Chain Manager / Materials Manager\n"
            f"3. Drilling Engineer / Completions Engineer (for smaller operators)\n"
            f"4. VP of Drilling / Director of Drilling\n\n"
            f"DO NOT return: Company Man, Tool Pusher, Rig Manager, HSE/Safety, "
            f"Landman, Accounting/Finance, HR, Legal, Geologist, Marketing, IT.\n\n"
            f"Search LinkedIn, company websites, SPE conference papers, and press releases.\n"
            f"Return ONLY a JSON object with: name, title, email (if found), linkedin_url.\n"
            f"If you cannot find someone with a relevant title, return null."
        )

        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
            messages=[{"role": "user", "content": prompt}],
        )

        text = ""
        for block in resp.content:
            if hasattr(block, 'text'):
                text += block.text

        if not text or 'null' in text.lower():
            return None

        import json
        start = text.find('{')
        end = text.rfind('}')
        if start >= 0 and end > start:
            data = json.loads(text[start:end + 1])
            if data.get('name') and data.get('title'):
                # Verify the AI didn't return a skip title
                if _should_skip(data['title']):
                    return None
                if _score_title(data['title']) <= 0:
                    return None
                return {
                    'contact_name': data.get('name', ''),
                    'contact_title': data.get('title', ''),
                    'contact_email': data.get('email', ''),
                    'email_confidence': 'ai_researched',
                    'source': 'ai',
                    'linkedin_url': data.get('linkedin_url', ''),
                }

        return None
    except Exception as e:
        logger.error(f"AI research failed for {operator_name}: {e}")
        return None


def enrich_single_contact(operator_name, rig_count=0):
    """Enrich a single operator — used for manual retry from dashboard."""
    return enrich_contacts([{'operator': operator_name, 'rig_count': rig_count}])
