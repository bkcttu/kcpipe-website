"""Configuration loader — reads credentials from .env file."""

import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))


class Config:
    # Enverus
    ENVERUS_USERNAME = os.getenv('ENVERUS_USERNAME', '')
    ENVERUS_PASSWORD = os.getenv('ENVERUS_PASSWORD', '')

    # Apollo.io
    APOLLO_API_KEY = os.getenv('APOLLO_API_KEY', '')

    # Hunter.io
    HUNTER_API_KEY = os.getenv('HUNTER_API_KEY', '')

    # Anthropic
    ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')

    # Microsoft Graph (existing from Quote System)
    MICROSOFT_CLIENT_ID = os.getenv('MICROSOFT_CLIENT_ID', '')
    MICROSOFT_CLIENT_SECRET = os.getenv('MICROSOFT_CLIENT_SECRET', '')
    MICROSOFT_TENANT_ID = os.getenv('MICROSOFT_TENANT_ID', '')
    SENDER_EMAIL = os.getenv('SENDER_EMAIL', '')

    # Database
    DATABASE_PATH = os.getenv('DATABASE_PATH',
                              os.path.join(os.path.dirname(__file__), 'outreach.db'))

    # CAN-SPAM compliance
    BUSINESS_ADDRESS = os.getenv('BUSINESS_ADDRESS',
                                 'KC Pipe LP | Odessa, TX')

    # Flask
    SECRET_KEY = os.getenv('SECRET_KEY', 'kc-pipe-outreach-dev-key')
