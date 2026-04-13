"""
Contact Enrichment — finds the actual procurement/tubular buyer at each operator.

Primary: Claude AI with web search (searches LinkedIn profiles)
Fallback: Hunter.io Email Finder (uses name + domain to guess email)

STRICT title filtering — only returns contacts with relevant procurement/drilling
titles. Never returns HR, Finance, Legal, Land, HSE, or other non-buyer roles.
"""

import json
import logging
import re
import time
from datetime import datetime

import requests

from outreach_config import Config
from outreach_db import upsert_contact, get_contact_by_operator

logger = logging.getLogger(__name__)

# ── Title Hierarchy (Byron's exact ranking) ──────────────────────────────────

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

TIER3_TITLES = [
    "drilling engineer",
    "completions engineer",
    "well site manager",
    "drilling superintendent",
    "drilling foreman",
    "senior drilling engineer",
    "completions manager",
]

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
    title_lower = title.lower()
    for skip in SKIP_TITLES:
        if skip in title_lower:
            return True
    return False


def _score_title(title):
    """Score a title based on Byron's hierarchy. Returns 0 if no match, -1 if skip."""
    title_lower = title.lower()

    if _should_skip(title_lower):
        return -1

    for i, kw in enumerate(TIER1_TITLES):
        if kw in title_lower:
            return 100 + (len(TIER1_TITLES) - i)

    for i, kw in enumerate(TIER2_TITLES):
        if kw in title_lower:
            return 70 + (len(TIER2_TITLES) - i)

    for i, kw in enumerate(TIER3_TITLES):
        if kw in title_lower:
            return 40 + (len(TIER3_TITLES) - i)

    for i, kw in enumerate(TIER4_TITLES):
        if kw in title_lower:
            return 20 + (len(TIER4_TITLES) - i)

    return 0


def enrich_contacts(operators):
    """
    Enrich operators with buyer contact info.
    Primary: AI + LinkedIn web search. Fallback: Hunter email finder.
    """
    enriched = []
    not_found = []

    for op in operators:
        operator_name = op['operator']

        # Check cache — skip if recent
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

        rig_count = op.get('rig_count', 0)

        # PRIMARY: Claude AI + web search (finds LinkedIn profiles + email pattern)
        contact = _search_linkedin_via_ai(operator_name, rig_count)

        # FALLBACK 1: If AI found a name but no email, try Hunter Email Finder
        if contact and not contact.get('contact_email') and Config.HUNTER_API_KEY:
            email = _hunter_email_finder(
                contact.get('contact_name', ''),
                operator_name
            )
            if email:
                contact['contact_email'] = email
                contact['email_confidence'] = 'guessed'

        # FALLBACK 2: Guess email from domain + name using common patterns
        if contact and not contact.get('contact_email'):
            domain = contact.get('email_domain') or _guess_domain(operator_name)
            pattern = contact.get('email_pattern') or 'first.last'
            guessed = _build_email_from_pattern(
                contact.get('contact_name', ''),
                domain,
                pattern
            )
            if guessed:
                contact['contact_email'] = guessed
                contact['email_confidence'] = 'guessed'

        if contact and contact.get('contact_name'):
            contact['operator'] = operator_name
            contact['rig_count'] = op.get('rig_count')
            contact_id = upsert_contact(contact)
            contact['id'] = contact_id
            enriched.append({**op, **contact})
            time.sleep(0.5)
        else:
            not_found.append(op)

    return {'enriched': enriched, 'not_found': not_found}


def _search_linkedin_via_ai(operator_name, rig_count=0):
    """
    Use Claude AI with web search to find LinkedIn profiles of tubular/procurement
    contacts at the operator. This searches public LinkedIn profiles via Google.
    """
    if not Config.ANTHROPIC_API_KEY:
        return None

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)

        # Build the target title list based on operator size
        if rig_count and rig_count <= 5:
            target_hint = (
                "Best: Tubular Coordinator, OCTG Coordinator, Pipe Coordinator. "
                "Also good: Procurement Manager, Supply Chain Manager, Materials Manager. "
                "For small operators also OK: Drilling Engineer, Completions Engineer, "
                "Drilling Superintendent, Well Site Manager. "
                "Last resort: VP of Drilling, Director of Drilling, Drilling Manager."
            )
        else:
            target_hint = (
                "Best: Tubular Coordinator, OCTG Coordinator, Pipe Coordinator. "
                "Also good: Procurement Manager, Supply Chain Manager, Materials Manager, "
                "Purchasing Manager, Drilling Materials Manager. "
                "Last resort: VP of Drilling, Director of Drilling."
            )

        prompt = (
            f"Search LinkedIn and the web for employees at '{operator_name}' "
            f"(an oil and gas operator in the Permian Basin) who would be "
            f"responsible for buying OCTG steel pipe (casing and tubing).\n\n"
            f"TARGET TITLES (in order of preference):\n{target_hint}\n\n"
            f"SKIP ENTIRELY — do not return these: Company Man, Tool Pusher, "
            f"Rig Manager, HSE/Safety, Landman/Land, Accounting/Finance, HR, "
            f"Legal/Counsel, Geologist/Geophysicist, Marketing, IT, Receptionist.\n\n"
            f"Search queries like:\n"
            f"- site:linkedin.com/in \"{operator_name}\" \"tubular\"\n"
            f"- site:linkedin.com/in \"{operator_name}\" \"procurement\"\n"
            f"- site:linkedin.com/in \"{operator_name}\" \"supply chain\"\n"
            f"- site:linkedin.com/in \"{operator_name}\" \"drilling engineer\"\n\n"
            f"Return ONLY a JSON object with these fields:\n"
            f'{{\n'
            f'  "name": "Full Name",\n'
            f'  "title": "Exact Title",\n'
            f'  "linkedin_url": "https://www.linkedin.com/in/...",\n'
            f'  "email": "email if you found their actual email, or empty",\n'
            f'  "email_domain": "the company email domain like pxd.com or eogresources.com",\n'
            f'  "email_pattern": "first.last OR firstlast OR flast OR first_last — whichever pattern the company uses"\n'
            f'}}\n\n'
            f"Also search for 'email format {operator_name}' or 'email pattern {operator_name}' "
            f"so you can figure out the company's email convention.\n\n"
            f"If you cannot find someone with a relevant title, return: null\n"
            f"Return the BEST match only (one person). No explanation, just JSON."
        )

        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
            messages=[{"role": "user", "content": prompt}],
        )

        text = ""
        for block in resp.content:
            if hasattr(block, 'text'):
                text += block.text

        if not text:
            logger.info(f"AI/LinkedIn: empty response for {operator_name}")
            return None

        # Handle null response
        if re.search(r'\bnull\b', text) and '{' not in text:
            logger.info(f"AI/LinkedIn: no match for {operator_name}")
            return None

        # Extract JSON from response
        start = text.find('{')
        end = text.rfind('}')
        if start < 0 or end <= start:
            logger.info(f"AI/LinkedIn: no JSON in response for {operator_name}")
            return None

        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            logger.warning(f"AI/LinkedIn: JSON parse error for {operator_name}")
            return None

        name = (data.get('name') or '').strip()
        title = (data.get('title') or '').strip()
        linkedin_url = (data.get('linkedin_url') or '').strip()
        email = (data.get('email') or '').strip()
        email_domain = (data.get('email_domain') or '').strip()
        email_pattern = (data.get('email_pattern') or '').strip()

        if not name or not title:
            return None

        # Validate title
        score = _score_title(title)
        if score <= 0:
            logger.info(f"AI/LinkedIn: {operator_name} — title '{title}' rejected")
            return None

        return {
            'contact_name': name,
            'contact_title': title,
            'contact_email': email,
            'email_confidence': 'ai_researched' if email else '',
            'source': 'linkedin',
            'linkedin_url': linkedin_url,
            'email_domain': email_domain,
            'email_pattern': email_pattern,
        }

    except Exception as e:
        logger.error(f"LinkedIn/AI search failed for {operator_name}: {e}")
        return None


def _hunter_email_finder(full_name, company_name):
    """
    Use Hunter.io Email Finder to guess someone's email from their name + company.
    This is different from Domain Search — it finds a specific person.
    """
    if not full_name or not company_name:
        return None

    parts = full_name.strip().split()
    if len(parts) < 2:
        return None

    first_name = parts[0]
    last_name = parts[-1]

    url = "https://api.hunter.io/v2/email-finder"
    params = {
        "company": company_name,
        "first_name": first_name,
        "last_name": last_name,
        "api_key": Config.HUNTER_API_KEY,
    }

    try:
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code != 200:
            return None
        data = resp.json().get('data', {})
        email = data.get('email')
        if email:
            logger.info(f"Hunter found email for {full_name} at {company_name}")
            return email
        return None
    except requests.RequestException as e:
        logger.error(f"Hunter email finder error: {e}")
        return None


def _guess_domain(operator_name):
    """
    Guess a company email domain from the operator name.
    Tries common patterns: acme.com, acmeenergy.com, acme-energy.com
    """
    if not operator_name:
        return ''

    # Clean the name
    name = operator_name.upper()
    # Remove common suffixes
    for suffix in [
        ', LLC', ' LLC', ', L.L.C.', ' L.L.C.',
        ', INC.', ' INC.', ', INC', ' INC',
        ', LP', ' LP', ', L.P.', ' L.P.',
        ', LTD', ' LTD', ', LTD.', ' LTD.',
        ', CO.', ' CO.', ', CO', ' CO',
        ', CORP', ' CORP', ', CORP.', ' CORP.',
        ' COMPANY', ' OPERATING', ' PRODUCTION',
    ]:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    name = name.strip().rstrip(',').rstrip('.')

    # Known operator domain mappings (save on web searches)
    known_domains = {
        'EOG RESOURCES': 'eogresources.com',
        'OXY USA': 'oxy.com',
        'OCCIDENTAL PETROLEUM': 'oxy.com',
        'OCCIDENTAL PERMIAN': 'oxy.com',
        'DIAMONDBACK E&P': 'diamondbackenergy.com',
        'DIAMONDBACK ENERGY': 'diamondbackenergy.com',
        'PIONEER NATURAL RES': 'pxd.com',
        'PIONEER NATURAL RESOURCES': 'pxd.com',
        'PERMIAN RESOURCES': 'permianres.com',
        'APACHE CORPORATION': 'apachecorp.com',
        'APACHE': 'apachecorp.com',
        'APA CORP': 'apachecorp.com',
        'HILCORP': 'hilcorp.com',
        'HILCORP ENERGY': 'hilcorp.com',
        'XTO ENERGY': 'xtoenergy.com',
        'XTO PERMIAN': 'xtoenergy.com',
        'EXXON': 'exxonmobil.com',
        'CIMAREX ENERGY': 'coterra.com',
        'COTERRA ENERGY': 'coterra.com',
        'MEWBOURNE OIL': 'mewbourne.com',
        'CONOCOPHILLIPS': 'conocophillips.com',
        'CONTINENTAL RESOURCES': 'clr.com',
        'DEVON ENERGY': 'dvn.com',
        'OVINTIV': 'ovintiv.com',
        'RING ENERGY': 'ringenergy.com',
        'FASKEN OIL': 'forbanks.com',
        'FASKEN OIL AND RANCH': 'forbanks.com',
        'BLACKBEARD OPERATING': 'blackbeardoperating.com',
        'JETTA OPERATING': 'jettaoperating.com',
        'CRESCENT ENERGY': 'crescentenergyco.com',
        'RILEY PERMIAN OPERATING': 'rileypermian.com',
        'RILEY EXPLORATION PERMIAN': 'rileypermian.com',
        'VTX ENERGY': 'vtxenergy.com',
        'WAGGONER OPERATING': 'waggonerranch.com',
        'BURK ROYALTY': 'burkroyalty.com',
        'BAYSWATER OPERATING': 'bayswaterep.com',
        'GORDY OIL': 'gordyoil.com',
        'LAGUNA TEXAS RESOURCES': 'lagunatx.com',
        'GREENLAKE ENERGY': 'greenlakeenergy.com',
        'U.S. ENERGY DEVELOPMENT': 'usedc.com',
        'RESOLUTE NATURAL RES': 'resoluteenergy.com',
        'SCOUT ENERGY': 'scoutenergymgmt.com',
        'BLUE ARROW OPERATING': 'bluearrowop.com',
        'TRIPLE CROWN RESOURCES': 'triplecrownresources.com',
        'FX ENERGY OPERATING': 'fxenergy.com',
        'ATMOS ENERGY': 'atmosenergy.com',
        'OXYROCK OPERATING': 'oxyrock.com',
        'DEEP BLUE CENTRAL': 'deepbluemidstream.com',
        'PBEX OPERATIONS': 'pbex.com',
        'PBEX-MCM OPERATING': 'pbex.com',
        'AVANT OPERATING': 'avantop.com',
        'SUMMIT PETROLEUM': 'summitpetroleumllc.com',
        'FIREBIRD ENERGY': 'firebirdenergyllc.com',
        'MORIAH OPERATING': 'moriahoperating.com',
        'DE V OPERATING': 'devoperating.com',
        'QUAIL RIDGE OPERATING': 'quailridgeop.com',
        'PERMIAN DEEP ROCK OIL': 'permiandeeprock.com',
        'VETERAN EXPLORATION': 'veteranexploration.com',
        'CORY, KENNETH W': 'kwcory.com',
        'SADDLE RIM ENERGY': 'saddlerimenergy.com',
        'PDEH': 'conocophillips.com',
        'MOUNTAIN CREEK OIL': 'mtncreekoil.com',
        'R2Q OPERATING': 'r2qoperating.com',
        'REATTA ENERGY': 'reattaenergy.com',
        'B.O.L.D. OIL': 'boldoilandgas.com',
        'BLACKBEARD': 'blackbeardoperating.com',
    }

    for key, domain in known_domains.items():
        if name.startswith(key) or key in name:
            return domain

    # Fallback: construct domain from first word(s)
    words = re.findall(r'[A-Z]+', name)
    if not words:
        return ''
    # Use first 1-2 meaningful words
    skip = {'THE', 'OF', 'AND', 'OIL', 'GAS'}
    meaningful = [w for w in words if w not in skip]
    if meaningful:
        return meaningful[0].lower() + '.com'
    return ''


def _build_email_from_pattern(full_name, domain, pattern='first.last'):
    """
    Construct an email address from a name + domain + pattern.

    Patterns supported:
      first.last   → john.smith@domain
      firstlast    → johnsmith@domain
      flast        → jsmith@domain
      first_last   → john_smith@domain
      first        → john@domain
      last         → smith@domain
      f.last       → j.smith@domain
    """
    if not full_name or not domain:
        return ''

    parts = full_name.strip().split()
    if len(parts) < 2:
        return ''

    first = re.sub(r'[^a-z]', '', parts[0].lower())
    last = re.sub(r'[^a-z]', '', parts[-1].lower())

    if not first or not last:
        return ''

    # Clean up domain
    domain = domain.lower().strip()
    if domain.startswith('@'):
        domain = domain[1:]
    if domain.startswith('http'):
        domain = re.sub(r'https?://', '', domain)
    domain = domain.strip('/')

    pattern = (pattern or 'first.last').lower().strip()

    local = ''
    if pattern == 'first.last':
        local = f'{first}.{last}'
    elif pattern == 'firstlast':
        local = f'{first}{last}'
    elif pattern == 'flast':
        local = f'{first[0]}{last}'
    elif pattern == 'first_last':
        local = f'{first}_{last}'
    elif pattern == 'first':
        local = first
    elif pattern == 'last':
        local = last
    elif pattern == 'f.last':
        local = f'{first[0]}.{last}'
    elif pattern == 'firstl':
        local = f'{first}{last[0]}'
    else:
        local = f'{first}.{last}'  # default

    return f'{local}@{domain}'


def enrich_single_contact(operator_name, rig_count=0):
    """Enrich a single operator — used for manual retry from dashboard."""
    return enrich_contacts([{'operator': operator_name, 'rig_count': rig_count}])
