"""
Contact Enrichment — finds the actual procurement/tubular buyer at each operator.

Priority: Apollo.io → Hunter.io → AI Web Research
"""

import logging
import time
from datetime import datetime, timedelta

import requests

from config import Config
from database import get_db, upsert_contact, get_contact_by_operator

logger = logging.getLogger(__name__)

# Title hierarchy — ordered by relevance to KC Pipe's buyers
TITLE_KEYWORDS = [
    "tubular coordinator",
    "OCTG buyer",
    "supply chain",
    "procurement",
    "materials manager",
    "drilling engineer",
    "well site manager",
    "completions engineer",
    "tubular goods",
    "pipe coordinator",
]

# Broader titles for small operators where one person wears many hats
FALLBACK_TITLES = [
    "VP drilling",
    "director drilling",
    "operations manager",
    "drilling manager",
    "purchasing",
]


def enrich_contacts(operators):
    """
    Enrich a list of operator records with buyer contact info.

    Args:
        operators: list of dicts with at least 'operator' key

    Returns:
        dict with 'enriched' and 'not_found' lists
    """
    enriched = []
    not_found = []

    for op in operators:
        operator_name = op['operator']

        # Check if we already have a recent contact
        existing = get_contact_by_operator(operator_name)
        if existing and existing.get('contact_email'):
            # Re-enrich only if contact is 90+ days old
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

        if Config.APOLLO_API_KEY:
            contact = _search_apollo(operator_name, op.get('rig_count', 0))
            if contact:
                time.sleep(0.5)  # Rate limit

        if not contact and Config.HUNTER_API_KEY:
            contact = _search_hunter(operator_name)

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

    # Use broader titles for small operators
    titles = list(TITLE_KEYWORDS)
    if rig_count and rig_count <= 3:
        titles.extend(FALLBACK_TITLES)

    payload = {
        "api_key": Config.APOLLO_API_KEY,
        "q_organization_name": operator_name,
        "person_titles": titles,
        "person_locations": ["Texas", "Midland", "Odessa", "Houston"],
        "contact_email_status": ["verified", "guessed"],
        "per_page": 5,
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

        # Rank by title relevance
        best = _rank_contacts(people)
        if not best:
            return None

        return {
            'contact_name': best.get('name', ''),
            'contact_title': best.get('title', ''),
            'contact_email': best.get('email', ''),
            'email_confidence': _apollo_confidence(best),
            'source': 'apollo',
            'linkedin_url': best.get('linkedin_url', ''),
        }
    except requests.RequestException as e:
        logger.error(f"Apollo API error for {operator_name}: {e}")
        return None


def _rank_contacts(people):
    """Rank Apollo results by title relevance."""
    scored = []
    for person in people:
        title = (person.get('title') or '').lower()
        score = 0

        # Score based on title keyword match position (earlier = better)
        for i, keyword in enumerate(TITLE_KEYWORDS):
            if keyword.lower() in title:
                score = len(TITLE_KEYWORDS) - i + 10
                break

        # Fallback titles get lower scores
        if score == 0:
            for i, keyword in enumerate(FALLBACK_TITLES):
                if keyword.lower() in title:
                    score = len(FALLBACK_TITLES) - i
                    break

        # Boost for verified email
        if person.get('email_status') == 'verified':
            score += 5

        if score > 0 or person.get('email'):
            scored.append((score, person))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1] if scored else None


def _apollo_confidence(person):
    """Map Apollo email status to our confidence levels."""
    status = person.get('email_status', '')
    if status == 'verified':
        return 'verified'
    elif status in ('guessed', 'likely'):
        return 'guessed'
    return 'guessed'


def _search_hunter(operator_name):
    """Fallback: search Hunter.io Domain Search."""
    url = "https://api.hunter.io/v2/domain-search"

    params = {
        "company": operator_name,
        "api_key": Config.HUNTER_API_KEY,
        "limit": 10,
    }

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json().get('data', {})

        emails = data.get('emails', [])
        if not emails:
            logger.info(f"Hunter: no results for {operator_name}")
            return None

        # Filter by title relevance
        all_keywords = TITLE_KEYWORDS + FALLBACK_TITLES
        for email_rec in emails:
            position = (email_rec.get('position') or '').lower()
            for kw in all_keywords:
                if kw.lower() in position:
                    name_parts = [email_rec.get('first_name', ''),
                                  email_rec.get('last_name', '')]
                    return {
                        'contact_name': ' '.join(p for p in name_parts if p),
                        'contact_title': email_rec.get('position', ''),
                        'contact_email': email_rec.get('value', ''),
                        'email_confidence': _hunter_confidence(email_rec),
                        'source': 'hunter',
                        'linkedin_url': email_rec.get('linkedin', ''),
                    }

        # If no title match, return the first result with a name
        for email_rec in emails:
            if email_rec.get('first_name') and email_rec.get('value'):
                name_parts = [email_rec.get('first_name', ''),
                              email_rec.get('last_name', '')]
                return {
                    'contact_name': ' '.join(p for p in name_parts if p),
                    'contact_title': email_rec.get('position', ''),
                    'contact_email': email_rec.get('value', ''),
                    'email_confidence': 'guessed',
                    'source': 'hunter',
                    'linkedin_url': email_rec.get('linkedin', ''),
                }

        return None
    except requests.RequestException as e:
        logger.error(f"Hunter API error for {operator_name}: {e}")
        return None


def _hunter_confidence(email_rec):
    """Map Hunter confidence score to our levels."""
    confidence = email_rec.get('confidence', 0)
    if confidence >= 90:
        return 'verified'
    return 'guessed'


def _ai_research(operator_name):
    """Last resort: use Claude API with web search to find contacts."""
    if not Config.ANTHROPIC_API_KEY:
        return None

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)

        prompt = (
            f"Search for the tubular goods buyer or OCTG procurement contact at "
            f"{operator_name}, an oil and gas operator in the Permian Basin. "
            f"Look for LinkedIn profiles, company website staff pages, conference "
            f"speaker lists, SPE papers, and press releases. "
            f"Return the result as JSON with these fields: "
            f"name, title, email (if found), linkedin_url (if found), source_url. "
            f"If you cannot find a specific contact, return null."
        )

        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
            messages=[{"role": "user", "content": prompt}],
        )

        # Extract text from response
        text = ""
        for block in resp.content:
            if hasattr(block, 'text'):
                text += block.text

        if not text or 'null' in text.lower():
            return None

        # Try to parse JSON from response
        import json
        # Find JSON in the response
        start = text.find('{')
        end = text.rfind('}')
        if start >= 0 and end > start:
            data = json.loads(text[start:end + 1])
            if data.get('name'):
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
