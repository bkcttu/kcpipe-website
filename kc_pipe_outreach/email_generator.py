"""
Email Copywriting — Claude AI generates personalized outreach emails in Byron's voice.

Three email types:
A) First Contact (outreach_count == 0)
B) Follow-Up with market intel (7-21 days since last contact)
C) Re-engagement (30+ days, 2+ prior outreach)
"""

import logging
from datetime import datetime

from config import Config

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_BASE = """You are Byron Courts, owner of KC Pipe LP — a one-person OCTG steel pipe distributor \
based in Odessa, Texas, serving the Permian Basin for over a decade. You are writing \
a sales outreach email to a tubular goods buyer or drilling procurement contact.

Your competitive advantages:
- Speed: one decision-maker, no corporate chain — you answer the phone
- Relationships: direct mill relationships (domestic and foreign) for competitive pricing
- Casing: specialty for 1-5 rig operators who get ignored by the big distributors
- Tubing: highly competitive on tubing for all operators
- Local: Permian Basin based, you understand their operations

Voice: direct, confident, Permian Basin authentic. Short paragraphs. No corporate speak.
Do NOT say "I hope this email finds you well."
Do NOT use the phrase "synergies" or "value-added solutions."
Do NOT start with "Dear" — use the first name only.
Maximum 150 words in the email body.
Subject line should be specific and intriguing, not generic.

Generate ONLY the subject line and email body. No preamble or explanation.
Format:
SUBJECT: [subject line]
BODY:
[email body]"""

SYSTEM_PROMPT_FOLLOWUP = SYSTEM_PROMPT_BASE + """

This is a follow-up to a previous email. Do NOT reference the previous email awkwardly. \
Instead, lead with a brief, useful Permian Basin market update — current OCTG market \
conditions, mill lead times, pricing trends, or supply observations — then pivot to \
a soft ask. Make the market intel the value, not the sales pitch."""

SYSTEM_PROMPT_REENGAGEMENT = SYSTEM_PROMPT_BASE + """

This is a re-engagement email — the contact hasn't responded to previous outreach. \
Keep it SHORT (under 80 words). Punchy. Something with "still in the mix if you \
ever need a second quote" energy. Light and confident, not desperate. No market update needed."""


def generate_email(contact, market_update=None):
    """
    Generate an email for a contact based on their outreach history.

    Args:
        contact: dict with operator, contact_name, contact_title, rig_count, etc.
        market_update: optional string for follow-up emails

    Returns:
        dict with 'subject', 'body', 'email_type'
    """
    outreach_count = contact.get('outreach_count', 0)
    last_contacted = contact.get('last_contacted')

    # Determine email type
    if outreach_count == 0:
        return _generate_first_contact(contact)

    days_since = _days_since(last_contacted) if last_contacted else 999

    if days_since >= 30 and outreach_count >= 2:
        return _generate_reengagement(contact)
    elif 7 <= days_since <= 21:
        return _generate_followup(contact, market_update)
    else:
        return _generate_first_contact(contact)


def _generate_first_contact(contact):
    """Type A: First contact email."""
    rig_count = contact.get('rig_count', 0)
    target_product = 'casing and tubing' if rig_count and rig_count <= 5 else 'tubing'

    user_prompt = f"""Write a first-contact outreach email to:
- Name: {contact.get('contact_name', 'the procurement team')}
- Title: {contact.get('contact_title', '')}
- Company: {contact['operator']}
- They have {rig_count} active rigs in the Permian Basin
- Recent activity: {contact.get('county', 'Permian Basin')} county, {contact.get('formation', '')} formation
- Target product: {target_product}

Make it feel like you looked them up specifically, not a mass blast."""

    result = _call_claude(SYSTEM_PROMPT_BASE, user_prompt)
    result['email_type'] = 'first_contact'
    return result


def _generate_followup(contact, market_update=None):
    """Type B: Follow-up with market intel."""
    days_since = _days_since(contact.get('last_contacted', ''))
    rig_count = contact.get('rig_count', 0)
    target_product = 'casing and tubing' if rig_count and rig_count <= 5 else 'tubing'

    market_context = market_update or "Current OCTG market conditions in the Permian Basin."

    user_prompt = f"""Write a follow-up email to {contact.get('contact_name', 'the procurement team')} at {contact['operator']}.
Byron contacted them {days_since} days ago. No response yet.
Include a brief (2-3 sentence) Permian Basin tubular market update using this context:
{market_context}
Then soft-pitch KC Pipe's availability for their {target_product} needs."""

    result = _call_claude(SYSTEM_PROMPT_FOLLOWUP, user_prompt)
    result['email_type'] = 'follow_up'
    return result


def _generate_reengagement(contact):
    """Type C: Re-engagement after long gap."""
    days_since = _days_since(contact.get('last_contacted', ''))

    user_prompt = f"""Write a re-engagement email to {contact.get('contact_name', 'the procurement team')} at {contact['operator']}.
Last contacted {days_since} days ago. {contact.get('outreach_count', 0)} previous outreach attempts.
They have {contact.get('rig_count', 'several')} rigs in the Permian.
Keep it under 80 words. Light, punchy, confident."""

    result = _call_claude(SYSTEM_PROMPT_REENGAGEMENT, user_prompt)
    result['email_type'] = 're_engagement'
    return result


def _call_claude(system_prompt, user_prompt):
    """Call Claude API to generate email content."""
    if not Config.ANTHROPIC_API_KEY:
        return _placeholder_email(user_prompt)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)

        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        text = resp.content[0].text
        return _parse_email_response(text)

    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return _placeholder_email(user_prompt)


def _parse_email_response(text):
    """Parse Claude's response into subject and body."""
    subject = ""
    body = ""

    lines = text.strip().split('\n')
    in_body = False

    for line in lines:
        if line.upper().startswith('SUBJECT:'):
            subject = line[len('SUBJECT:'):].strip()
        elif line.upper().startswith('BODY:'):
            in_body = True
            # Check if there's content on the same line
            rest = line[len('BODY:'):].strip()
            if rest:
                body = rest + '\n'
        elif in_body:
            body += line + '\n'
        elif not subject and not in_body:
            # Sometimes Claude skips the SUBJECT: prefix
            subject = line.strip()

    body = body.strip()

    # Append CAN-SPAM footer
    body += f"\n\n---\n{Config.BUSINESS_ADDRESS}"

    return {'subject': subject, 'body': body}


def _placeholder_email(user_prompt):
    """Generate placeholder when API key is not set (for testing)."""
    return {
        'subject': '[DRAFT] KC Pipe Outreach — API key needed to generate',
        'body': f'[Email will be generated by Claude AI when ANTHROPIC_API_KEY is configured]\n\n'
                f'Prompt context:\n{user_prompt[:200]}...\n\n---\n{Config.BUSINESS_ADDRESS}',
        'email_type': 'placeholder',
    }


def _days_since(date_str):
    """Calculate days since a date string."""
    if not date_str:
        return 999
    try:
        dt = datetime.strptime(str(date_str)[:10], '%Y-%m-%d')
        return (datetime.now() - dt).days
    except (ValueError, TypeError):
        return 999


def regenerate_email(contact, email_type=None, market_update=None):
    """Regenerate an email — used when Byron wants a different version."""
    if email_type == 'first_contact':
        return _generate_first_contact(contact)
    elif email_type == 'follow_up':
        return _generate_followup(contact, market_update)
    elif email_type == 're_engagement':
        return _generate_reengagement(contact)
    else:
        return generate_email(contact, market_update)
