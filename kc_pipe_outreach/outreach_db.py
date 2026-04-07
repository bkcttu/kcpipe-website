"""SQLite database schema and query helpers."""

import sqlite3
from datetime import datetime, timedelta
from outreach_config import Config


def get_db():
    """Get a database connection with row factory."""
    conn = sqlite3.connect(Config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create all tables if they don't exist."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operator TEXT NOT NULL,
            rig_count INTEGER,
            contact_name TEXT,
            contact_title TEXT,
            contact_email TEXT,
            email_confidence TEXT,
            source TEXT,
            linkedin_url TEXT,
            first_contacted DATE,
            last_contacted DATE,
            outreach_count INTEGER DEFAULT 0,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS outreach_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id INTEGER NOT NULL,
            email_type TEXT NOT NULL,
            subject TEXT,
            body TEXT,
            status TEXT DEFAULT 'pending',
            week_of DATE,
            approved_at TIMESTAMP,
            sent_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (contact_id) REFERENCES contacts(id)
        );

        CREATE TABLE IF NOT EXISTS rig_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operator TEXT NOT NULL,
            rig_count INTEGER,
            county TEXT,
            formation TEXT,
            permit_date DATE,
            well_type TEXT,
            report_date DATE,
            raw_data TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sent_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id INTEGER NOT NULL,
            outreach_id INTEGER,
            subject TEXT,
            body TEXT,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            message_id TEXT,
            FOREIGN KEY (contact_id) REFERENCES contacts(id),
            FOREIGN KEY (outreach_id) REFERENCES outreach_queue(id)
        );

        CREATE INDEX IF NOT EXISTS idx_contacts_operator ON contacts(operator);
        CREATE INDEX IF NOT EXISTS idx_contacts_email ON contacts(contact_email);
        CREATE INDEX IF NOT EXISTS idx_outreach_status ON outreach_queue(status);
        CREATE INDEX IF NOT EXISTS idx_outreach_week ON outreach_queue(week_of);
    """)
    conn.commit()
    conn.close()


# --- Contact Helpers ---

def get_contact_by_operator(operator):
    """Find existing contact for an operator."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM contacts WHERE operator = ? ORDER BY updated_at DESC LIMIT 1",
        (operator,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def upsert_contact(data):
    """Insert or update a contact. Returns contact id."""
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM contacts WHERE operator = ? AND contact_email = ?",
        (data['operator'], data.get('contact_email'))
    ).fetchone()

    if existing:
        conn.execute("""
            UPDATE contacts SET
                rig_count = ?, contact_name = ?, contact_title = ?,
                email_confidence = ?, source = ?, linkedin_url = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            data.get('rig_count'), data.get('contact_name'),
            data.get('contact_title'), data.get('email_confidence'),
            data.get('source'), data.get('linkedin_url'),
            existing['id']
        ))
        contact_id = existing['id']
    else:
        cur = conn.execute("""
            INSERT INTO contacts (operator, rig_count, contact_name, contact_title,
                                  contact_email, email_confidence, source, linkedin_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data['operator'], data.get('rig_count'), data.get('contact_name'),
            data.get('contact_title'), data.get('contact_email'),
            data.get('email_confidence'), data.get('source'),
            data.get('linkedin_url')
        ))
        contact_id = cur.lastrowid

    conn.commit()
    conn.close()
    return contact_id


def was_contacted_recently(operator, days=14):
    """Check if operator was contacted within the last N days."""
    conn = get_db()
    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    row = conn.execute(
        "SELECT id FROM contacts WHERE operator = ? AND last_contacted >= ?",
        (operator, cutoff)
    ).fetchone()
    conn.close()
    return row is not None


def get_contacts_needing_followup(min_days=7, max_days=21):
    """Get contacts that were contacted between min_days and max_days ago."""
    conn = get_db()
    min_date = (datetime.now() - timedelta(days=max_days)).strftime('%Y-%m-%d')
    max_date = (datetime.now() - timedelta(days=min_days)).strftime('%Y-%m-%d')
    rows = conn.execute("""
        SELECT * FROM contacts
        WHERE last_contacted BETWEEN ? AND ?
          AND outreach_count >= 1
        ORDER BY last_contacted ASC
    """, (min_date, max_date)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_contacts_for_reengagement(min_days=30):
    """Get contacts not contacted in 30+ days with 2+ prior outreach."""
    conn = get_db()
    cutoff = (datetime.now() - timedelta(days=min_days)).strftime('%Y-%m-%d')
    rows = conn.execute("""
        SELECT * FROM contacts
        WHERE last_contacted < ?
          AND outreach_count >= 2
        ORDER BY last_contacted ASC
    """, (cutoff,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_contact_sent(contact_id):
    """Update contact after email is sent."""
    conn = get_db()
    conn.execute("""
        UPDATE contacts SET
            last_contacted = DATE('now'),
            outreach_count = outreach_count + 1,
            first_contacted = COALESCE(first_contacted, DATE('now'))
        WHERE id = ?
    """, (contact_id,))
    conn.commit()
    conn.close()


def update_contact_notes(contact_id, notes):
    """Update the notes field for a contact."""
    conn = get_db()
    conn.execute("UPDATE contacts SET notes = ? WHERE id = ?", (notes, contact_id))
    conn.commit()
    conn.close()


# --- Outreach Queue Helpers ---

def create_outreach(contact_id, email_type, subject, body, week_of):
    """Create a new outreach queue entry."""
    conn = get_db()
    cur = conn.execute("""
        INSERT INTO outreach_queue (contact_id, email_type, subject, body, week_of)
        VALUES (?, ?, ?, ?, ?)
    """, (contact_id, email_type, subject, body, week_of))
    outreach_id = cur.lastrowid
    conn.commit()
    conn.close()
    return outreach_id


def get_pending_outreach(week_of=None):
    """Get all pending outreach items, optionally filtered by week."""
    conn = get_db()
    if week_of:
        rows = conn.execute("""
            SELECT oq.*, c.operator, c.contact_name, c.contact_title,
                   c.contact_email, c.email_confidence, c.rig_count,
                   c.outreach_count, c.last_contacted, c.notes, c.source
            FROM outreach_queue oq
            JOIN contacts c ON oq.contact_id = c.id
            WHERE oq.status = 'pending' AND oq.week_of = ?
            ORDER BY c.rig_count ASC
        """, (week_of,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT oq.*, c.operator, c.contact_name, c.contact_title,
                   c.contact_email, c.email_confidence, c.rig_count,
                   c.outreach_count, c.last_contacted, c.notes, c.source
            FROM outreach_queue oq
            JOIN contacts c ON oq.contact_id = c.id
            WHERE oq.status = 'pending'
            ORDER BY oq.created_at DESC
        """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def approve_outreach(outreach_id):
    """Mark an outreach item as approved."""
    conn = get_db()
    conn.execute(
        "UPDATE outreach_queue SET status = 'approved', approved_at = CURRENT_TIMESTAMP WHERE id = ?",
        (outreach_id,)
    )
    conn.commit()
    conn.close()


def skip_outreach(outreach_id):
    """Mark an outreach item as skipped."""
    conn = get_db()
    conn.execute("UPDATE outreach_queue SET status = 'skipped' WHERE id = ?", (outreach_id,))
    conn.commit()
    conn.close()


def mark_outreach_sent(outreach_id):
    """Mark an outreach item as sent."""
    conn = get_db()
    conn.execute(
        "UPDATE outreach_queue SET status = 'sent', sent_at = CURRENT_TIMESTAMP WHERE id = ?",
        (outreach_id,)
    )
    conn.commit()
    conn.close()


def update_outreach_email(outreach_id, subject, body):
    """Update subject/body for an outreach item (edit before send)."""
    conn = get_db()
    conn.execute(
        "UPDATE outreach_queue SET subject = ?, body = ? WHERE id = ?",
        (subject, body, outreach_id)
    )
    conn.commit()
    conn.close()


def get_approved_outreach():
    """Get all approved outreach items ready to send."""
    conn = get_db()
    rows = conn.execute("""
        SELECT oq.*, c.operator, c.contact_name, c.contact_title,
               c.contact_email, c.email_confidence, c.rig_count, c.source
        FROM outreach_queue oq
        JOIN contacts c ON oq.contact_id = c.id
        WHERE oq.status = 'approved'
        ORDER BY oq.approved_at ASC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Sent History ---

def log_sent_email(contact_id, outreach_id, subject, body, message_id=None):
    """Log a sent email to history."""
    conn = get_db()
    conn.execute("""
        INSERT INTO sent_history (contact_id, outreach_id, subject, body, message_id)
        VALUES (?, ?, ?, ?, ?)
    """, (contact_id, outreach_id, subject, body, message_id))
    conn.commit()
    conn.close()


def get_sent_history(limit=50, offset=0):
    """Get sent email history."""
    conn = get_db()
    rows = conn.execute("""
        SELECT sh.*, c.operator, c.contact_name, c.contact_email
        FROM sent_history sh
        JOIN contacts c ON sh.contact_id = c.id
        ORDER BY sh.sent_at DESC
        LIMIT ? OFFSET ?
    """, (limit, offset)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_monthly_stats():
    """Get outreach stats for the current month."""
    conn = get_db()
    first_of_month = datetime.now().replace(day=1).strftime('%Y-%m-%d')
    row = conn.execute("""
        SELECT
            COUNT(*) as sent_count,
            COUNT(DISTINCT contact_id) as unique_contacts
        FROM sent_history
        WHERE sent_at >= ?
    """, (first_of_month,)).fetchone()
    conn.close()
    return dict(row) if row else {'sent_count': 0, 'unique_contacts': 0}


def get_no_contact_operators(week_of):
    """Get operators from this week's rig report with no contact found."""
    conn = get_db()
    rows = conn.execute("""
        SELECT DISTINCT rr.operator, rr.rig_count, rr.county
        FROM rig_reports rr
        WHERE rr.report_date = ?
          AND NOT EXISTS (
              SELECT 1 FROM contacts c
              WHERE c.operator = rr.operator AND c.contact_email IS NOT NULL
          )
        ORDER BY rr.rig_count ASC
    """, (week_of,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Rig Report Helpers ---

def save_rig_report(records, report_date):
    """Save rig report data."""
    conn = get_db()
    for rec in records:
        conn.execute("""
            INSERT INTO rig_reports (operator, rig_count, county, formation,
                                     permit_date, well_type, report_date, raw_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            rec['operator'], rec.get('rig_count'), rec.get('county'),
            rec.get('formation'), rec.get('permit_date'),
            rec.get('well_type'), report_date, str(rec)
        ))
    conn.commit()
    conn.close()
