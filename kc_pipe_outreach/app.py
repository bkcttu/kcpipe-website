"""
KC Pipe Outreach Platform — Flask application with Monday dashboard.
"""

import csv
import io
import logging
from datetime import datetime

from flask import (
    Flask, render_template, request, jsonify, redirect, url_for, flash
)

from config import Config
from database import (
    init_db, get_pending_outreach, approve_outreach, skip_outreach,
    update_outreach_email, get_approved_outreach, get_sent_history,
    get_monthly_stats, get_no_contact_operators, update_contact_notes,
    create_outreach, get_db, upsert_contact
)
from email_sender import send_all_approved, send_email
from email_generator import generate_email, regenerate_email
from market_intel import get_market_update

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = Config.SECRET_KEY

# Initialize database on startup
init_db()


def _current_week():
    """Get the Monday date for the current week."""
    today = datetime.now()
    monday = today.date() - __import__('datetime').timedelta(days=today.weekday())
    return monday.strftime('%Y-%m-%d')


# ─── Dashboard Routes ────────────────────────────────────────────────────────

@app.route('/')
def dashboard():
    """Main Monday outreach dashboard."""
    week_of = request.args.get('week', _current_week())
    pending = get_pending_outreach(week_of)
    approved = get_approved_outreach()
    no_contact = get_no_contact_operators(week_of)
    stats = get_monthly_stats()
    history = get_sent_history(limit=10)

    return render_template('outreach_dashboard.html',
                           week_of=week_of,
                           pending=pending,
                           approved=approved,
                           no_contact=no_contact,
                           stats=stats,
                           recent_history=history)


@app.route('/history')
def sent_history():
    """Full sent email history."""
    page = int(request.args.get('page', 1))
    per_page = 25
    offset = (page - 1) * per_page
    history = get_sent_history(limit=per_page, offset=offset)
    return render_template('sent_history.html', history=history, page=page)


# ─── Outreach Actions ────────────────────────────────────────────────────────

@app.route('/approve/<int:outreach_id>', methods=['POST'])
def approve(outreach_id):
    """Approve an outreach email for sending."""
    approve_outreach(outreach_id)
    return jsonify({'status': 'approved'})


@app.route('/skip/<int:outreach_id>', methods=['POST'])
def skip(outreach_id):
    """Skip an outreach email."""
    skip_outreach(outreach_id)
    return jsonify({'status': 'skipped'})


@app.route('/edit/<int:outreach_id>', methods=['POST'])
def edit_email(outreach_id):
    """Edit subject/body of an outreach email."""
    data = request.get_json()
    subject = data.get('subject', '')
    body = data.get('body', '')
    if subject and body:
        update_outreach_email(outreach_id, subject, body)
        return jsonify({'status': 'updated'})
    return jsonify({'status': 'error', 'message': 'Subject and body required'}), 400


@app.route('/regenerate/<int:outreach_id>', methods=['POST'])
def regenerate(outreach_id):
    """Regenerate email content with Claude AI."""
    conn = get_db()
    row = conn.execute("""
        SELECT oq.*, c.*
        FROM outreach_queue oq
        JOIN contacts c ON oq.contact_id = c.id
        WHERE oq.id = ?
    """, (outreach_id,)).fetchone()
    conn.close()

    if not row:
        return jsonify({'status': 'error', 'message': 'Not found'}), 404

    contact = dict(row)
    market_update = get_market_update() if contact.get('email_type') == 'follow_up' else None
    result = regenerate_email(contact, contact.get('email_type'), market_update)

    update_outreach_email(outreach_id, result['subject'], result['body'])
    return jsonify({
        'status': 'regenerated',
        'subject': result['subject'],
        'body': result['body'],
    })


@app.route('/send-all', methods=['POST'])
def send_all():
    """Send all approved emails."""
    result = send_all_approved()
    if result['failed'] > 0:
        flash(f"Sent {result['sent']} emails. {result['failed']} failed.", 'warning')
    else:
        flash(f"Successfully sent {result['sent']} emails.", 'success')
    return redirect(url_for('dashboard'))


@app.route('/notes/<int:contact_id>', methods=['POST'])
def save_notes(contact_id):
    """Save notes for a contact."""
    data = request.get_json()
    update_contact_notes(contact_id, data.get('notes', ''))
    return jsonify({'status': 'saved'})


# ─── Run Report (Manual Trigger) ─────────────────────────────────────────────

@app.route('/run-report', methods=['POST'])
def run_report():
    """Manually trigger the full outreach pipeline."""
    try:
        from enverus_puller import pull_enverus_report, filter_targets
        from contact_enricher import enrich_contacts
        from database import save_rig_report, was_contacted_recently

        week_of = _current_week()

        # Step 1: Pull Enverus data
        flash('Pulling Enverus report...', 'info')
        records = pull_enverus_report()

        if not records:
            flash('No records from Enverus. Try uploading a CSV manually.', 'warning')
            return redirect(url_for('dashboard'))

        # Save raw report
        save_rig_report(records, week_of)

        # Step 2: Filter targets
        targets = filter_targets(records)
        all_targets = targets['all_unique']

        # Exclude recently contacted
        new_targets = [t for t in all_targets if not was_contacted_recently(t['operator'])]

        # Step 3: Enrich contacts
        result = enrich_contacts(new_targets)

        # Step 4: Generate emails
        market_update = get_market_update()
        email_count = 0

        for contact in result['enriched']:
            email = generate_email(contact, market_update)
            contact_id = contact.get('id')
            if contact_id:
                create_outreach(contact_id, email['email_type'],
                                email['subject'], email['body'], week_of)
                email_count += 1

        # Also generate follow-up emails for existing contacts
        from database import get_contacts_needing_followup, get_contacts_for_reengagement

        followups = get_contacts_needing_followup()
        for contact in followups:
            email = generate_email(contact, market_update)
            create_outreach(contact['id'], email['email_type'],
                            email['subject'], email['body'], week_of)
            email_count += 1

        reengagements = get_contacts_for_reengagement()
        for contact in reengagements:
            email = generate_email(contact)
            create_outreach(contact['id'], email['email_type'],
                            email['subject'], email['body'], week_of)
            email_count += 1

        flash(
            f"Report complete: {len(records)} operators found, "
            f"{len(result['enriched'])} contacts enriched, "
            f"{len(result['not_found'])} need manual research, "
            f"{email_count} emails generated.",
            'success'
        )

    except Exception as e:
        logger.exception("Run report failed")
        flash(f"Report failed: {str(e)}", 'error')

    return redirect(url_for('dashboard'))


# ─── CSV Upload (Manual Fallback) ────────────────────────────────────────────

@app.route('/upload-csv', methods=['POST'])
def upload_csv():
    """Upload a CSV file manually when Enverus auto-pull fails."""
    if 'csv_file' not in request.files:
        flash('No file uploaded.', 'error')
        return redirect(url_for('dashboard'))

    file = request.files['csv_file']
    if not file.filename.endswith('.csv'):
        flash('Please upload a CSV file.', 'error')
        return redirect(url_for('dashboard'))

    try:
        from enverus_puller import parse_csv, filter_targets
        from contact_enricher import enrich_contacts
        from database import save_rig_report, was_contacted_recently

        csv_content = file.read().decode('utf-8')
        records = parse_csv(csv_content)

        if not records:
            flash('No valid records found in CSV.', 'warning')
            return redirect(url_for('dashboard'))

        week_of = _current_week()
        save_rig_report(records, week_of)

        targets = filter_targets(records)
        new_targets = [t for t in targets['all_unique']
                       if not was_contacted_recently(t['operator'])]

        result = enrich_contacts(new_targets)
        market_update = get_market_update()
        email_count = 0

        for contact in result['enriched']:
            email = generate_email(contact, market_update)
            contact_id = contact.get('id')
            if contact_id:
                create_outreach(contact_id, email['email_type'],
                                email['subject'], email['body'], week_of)
                email_count += 1

        flash(
            f"CSV imported: {len(records)} operators, "
            f"{len(result['enriched'])} enriched, "
            f"{email_count} emails generated.",
            'success'
        )

    except Exception as e:
        logger.exception("CSV upload failed")
        flash(f"CSV import failed: {str(e)}", 'error')

    return redirect(url_for('dashboard'))


# ─── Manual Contact Add ──────────────────────────────────────────────────────

@app.route('/add-contact', methods=['POST'])
def add_contact():
    """Manually add a contact for an operator."""
    data = request.get_json()
    operator = data.get('operator', '').strip()
    if not operator:
        return jsonify({'status': 'error', 'message': 'Operator required'}), 400

    contact_id = upsert_contact({
        'operator': operator,
        'rig_count': data.get('rig_count'),
        'contact_name': data.get('contact_name', ''),
        'contact_title': data.get('contact_title', ''),
        'contact_email': data.get('contact_email', ''),
        'email_confidence': 'manual',
        'source': 'manual',
        'linkedin_url': data.get('linkedin_url', ''),
    })

    # Generate email for this contact
    contact = {
        'id': contact_id,
        'operator': operator,
        'contact_name': data.get('contact_name', ''),
        'contact_title': data.get('contact_title', ''),
        'rig_count': data.get('rig_count'),
        'county': data.get('county', ''),
        'formation': data.get('formation', ''),
        'outreach_count': 0,
    }

    email = generate_email(contact)
    week_of = _current_week()
    create_outreach(contact_id, email['email_type'],
                    email['subject'], email['body'], week_of)

    return jsonify({'status': 'created', 'contact_id': contact_id})


# ─── Retry Enrichment ────────────────────────────────────────────────────────

@app.route('/retry-enrich', methods=['POST'])
def retry_enrich():
    """Retry contact enrichment for a specific operator."""
    data = request.get_json()
    operator = data.get('operator', '')
    rig_count = data.get('rig_count', 0)

    if not operator:
        return jsonify({'status': 'error', 'message': 'Operator required'}), 400

    from contact_enricher import enrich_single_contact
    result = enrich_single_contact(operator, rig_count)

    if result['enriched']:
        contact = result['enriched'][0]
        email = generate_email(contact)
        week_of = _current_week()
        create_outreach(contact['id'], email['email_type'],
                        email['subject'], email['body'], week_of)
        return jsonify({'status': 'found', 'contact': {
            'name': contact.get('contact_name'),
            'title': contact.get('contact_title'),
            'email': contact.get('contact_email'),
        }})

    return jsonify({'status': 'not_found'})


# ─── Export ───────────────────────────────────────────────────────────────────

@app.route('/export-csv')
def export_csv():
    """Export current outreach queue as CSV."""
    week_of = request.args.get('week', _current_week())
    pending = get_pending_outreach(week_of)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        'operator', 'contact_name', 'contact_title', 'contact_email',
        'email_confidence', 'rig_count', 'email_type', 'subject', 'status'
    ])
    writer.writeheader()
    for item in pending:
        writer.writerow({
            'operator': item.get('operator', ''),
            'contact_name': item.get('contact_name', ''),
            'contact_title': item.get('contact_title', ''),
            'contact_email': item.get('contact_email', ''),
            'email_confidence': item.get('email_confidence', ''),
            'rig_count': item.get('rig_count', ''),
            'email_type': item.get('email_type', ''),
            'subject': item.get('subject', ''),
            'status': item.get('status', ''),
        })

    from flask import Response
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=kc_pipe_outreach_{week_of}.csv'}
    )


if __name__ == '__main__':
    app.run(debug=True, port=5000)
