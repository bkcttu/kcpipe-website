"""
Market Intel — pulls OCTG market updates for follow-up emails.
Uses Claude API with web search tool.
"""

import logging
from datetime import datetime

from config import Config

logger = logging.getLogger(__name__)

_cached_update = None
_cache_date = None


def get_market_update(force_refresh=False):
    """
    Get a 2-3 sentence OCTG market update for the Permian Basin.
    Caches result for the day to avoid redundant API calls.
    """
    global _cached_update, _cache_date

    today = datetime.now().strftime('%Y-%m-%d')
    if not force_refresh and _cached_update and _cache_date == today:
        return _cached_update

    if not Config.ANTHROPIC_API_KEY:
        return _fallback_update()

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)

        current_month = datetime.now().strftime('%B %Y')

        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
            messages=[{
                "role": "user",
                "content": (
                    f"Give me a 2-3 sentence factual summary of current OCTG/tubular "
                    f"market conditions in the Permian Basin as of {current_month}. "
                    f"Focus on: mill lead times, pricing direction, supply/demand balance. "
                    f"Be specific with numbers if available. No hedging — state what's happening."
                )
            }],
        )

        text = ""
        for block in resp.content:
            if hasattr(block, 'text'):
                text += block.text

        if text:
            _cached_update = text.strip()
            _cache_date = today
            return _cached_update

    except Exception as e:
        logger.error(f"Market intel fetch failed: {e}")

    return _fallback_update()


def _fallback_update():
    """Static fallback when API is unavailable."""
    return (
        "Permian Basin OCTG market remains active with steady demand. "
        "Mill lead times are running typical ranges — reach out for current availability."
    )
