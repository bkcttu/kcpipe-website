"""
APScheduler — runs the outreach pipeline every Monday at 7:00 AM Central.
"""

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def monday_outreach_run():
    """Full Monday morning pipeline — pull, enrich, generate. Does NOT auto-send."""
    from enverus_puller import pull_enverus_report, filter_targets
    from contact_enricher import enrich_contacts
    from email_generator import generate_email
    from market_intel import get_market_update
    from database import (
        save_rig_report, was_contacted_recently, create_outreach,
        get_contacts_needing_followup, get_contacts_for_reengagement
    )

    logger.info("=== Monday Outreach Run Starting ===")
    today = datetime.now()
    week_of = (today.date() - __import__('datetime').timedelta(days=today.weekday())).strftime('%Y-%m-%d')

    try:
        # Step 1: Pull Enverus
        records = pull_enverus_report()
        logger.info(f"Enverus: {len(records)} operators found")

        if records:
            save_rig_report(records, week_of)

            # Step 2: Filter and enrich
            targets = filter_targets(records)
            new_targets = [t for t in targets['all_unique']
                           if not was_contacted_recently(t['operator'])]

            result = enrich_contacts(new_targets)
            logger.info(f"Enriched: {len(result['enriched'])}, Not found: {len(result['not_found'])}")

            # Step 3: Generate emails
            market_update = get_market_update()

            for contact in result['enriched']:
                email = generate_email(contact, market_update)
                if contact.get('id'):
                    create_outreach(contact['id'], email['email_type'],
                                    email['subject'], email['body'], week_of)

        # Step 4: Follow-ups and re-engagements
        market_update = get_market_update()

        followups = get_contacts_needing_followup()
        for contact in followups:
            email = generate_email(contact, market_update)
            create_outreach(contact['id'], email['email_type'],
                            email['subject'], email['body'], week_of)

        reengagements = get_contacts_for_reengagement()
        for contact in reengagements:
            email = generate_email(contact)
            create_outreach(contact['id'], email['email_type'],
                            email['subject'], email['body'], week_of)

        logger.info("=== Monday Outreach Run Complete — Dashboard ready for review ===")

    except Exception as e:
        logger.exception(f"Monday outreach run failed: {e}")


def start_scheduler():
    """Start the background scheduler."""
    central = pytz.timezone('US/Central')
    trigger = CronTrigger(day_of_week='mon', hour=7, minute=0, timezone=central)

    scheduler.add_job(
        monday_outreach_run,
        trigger=trigger,
        id='monday_outreach',
        name='Monday Outreach Run',
        replace_existing=True,
    )

    scheduler.start()
    logger.info("Scheduler started — next run: Monday 7:00 AM CT")


def stop_scheduler():
    """Stop the scheduler."""
    if scheduler.running:
        scheduler.shutdown()
