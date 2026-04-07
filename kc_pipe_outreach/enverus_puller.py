"""
Enverus Data Puller — pulls weekly Permian Basin rig activity and permit data.

Supports three modes:
1. Enverus REST API (preferred)
2. Playwright browser automation (fallback)
3. Manual CSV upload (last resort)
"""

import csv
import io
import json
import logging
from datetime import datetime

import requests

from outreach_config import Config

logger = logging.getLogger(__name__)

# Permian Basin = Texas RRC Districts 7C, 8, 8A
PERMIAN_DISTRICTS = ['7C', '8', '8A']


def pull_enverus_report():
    """Main entry — try API first, then browser, then return empty for manual upload."""
    try:
        records = _pull_via_api()
        if records:
            logger.info(f"Enverus API returned {len(records)} records")
            return records
    except Exception as e:
        logger.warning(f"Enverus API failed: {e}")

    try:
        records = _pull_via_browser()
        if records:
            logger.info(f"Enverus browser pull returned {len(records)} records")
            return records
    except Exception as e:
        logger.warning(f"Enverus browser pull failed: {e}")

    logger.info("Both Enverus methods failed — use manual CSV upload")
    return []


def _pull_via_api():
    """Attempt to pull data via Enverus REST API."""
    if not Config.ENVERUS_USERNAME or not Config.ENVERUS_PASSWORD:
        raise ValueError("Enverus credentials not configured")

    # Authenticate
    auth_url = "https://api.enverus.com/v3/direct-access/tokens"
    auth_resp = requests.post(auth_url, json={
        "username": Config.ENVERUS_USERNAME,
        "password": Config.ENVERUS_PASSWORD
    }, timeout=30)
    auth_resp.raise_for_status()
    token = auth_resp.json().get("token")

    headers = {"Authorization": f"Bearer {token}"}

    # Pull rig activity for Permian Basin
    rigs_url = "https://api.enverus.com/v3/direct-access/rigs"
    params = {
        "stateProvince": "TEXAS",
        "county": "",  # Will filter by district below
        "pageSize": 500,
        "status": "Active"
    }

    resp = requests.get(rigs_url, headers=headers, params=params, timeout=60)
    resp.raise_for_status()
    raw_data = resp.json()

    # Pull permits
    permits_url = "https://api.enverus.com/v3/direct-access/permits"
    permits_params = {
        "stateProvince": "TEXAS",
        "pageSize": 500,
        "approvedDateMin": datetime.now().strftime('%Y-%m-01')
    }
    permits_resp = requests.get(permits_url, headers=headers, params=permits_params, timeout=60)
    permits_data = permits_resp.json() if permits_resp.ok else []

    return _normalize_api_data(raw_data, permits_data)


def _normalize_api_data(rigs_data, permits_data):
    """Normalize API responses into standard record format."""
    operators = {}

    for rig in rigs_data:
        op = rig.get('operatorName', '').strip()
        if not op:
            continue
        if op not in operators:
            operators[op] = {
                'operator': op,
                'rig_count': 0,
                'county': rig.get('county', ''),
                'formation': rig.get('formation', ''),
                'permit_date': None,
                'well_type': rig.get('wellType', ''),
            }
        operators[op]['rig_count'] += 1

    # Enrich with permit data
    for permit in permits_data:
        op = permit.get('operatorName', '').strip()
        if op in operators and permit.get('approvedDate'):
            operators[op]['permit_date'] = permit['approvedDate']
            if permit.get('county'):
                operators[op]['county'] = permit['county']
            if permit.get('formation'):
                operators[op]['formation'] = permit['formation']

    return list(operators.values())


def _pull_via_browser():
    """Fallback: use Playwright to log in and export CSV from Enverus web app."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("Playwright not installed — run: pip install playwright && playwright install chromium")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Login
        page.goto("https://app.enverus.com/login", timeout=30000)
        page.fill('input[name="email"], input[type="email"]', Config.ENVERUS_USERNAME)
        page.fill('input[name="password"], input[type="password"]', Config.ENVERUS_PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_load_state('networkidle', timeout=30000)

        # Navigate to rig activity report
        page.goto("https://app.enverus.com/rig-analytics", timeout=30000)
        page.wait_for_load_state('networkidle', timeout=30000)

        # Apply Permian Basin filter — look for basin/area filter
        try:
            page.click('text=Filters', timeout=5000)
            page.click('text=Basin', timeout=5000)
            page.fill('input[placeholder*="search" i]', 'Permian')
            page.click('text=Permian Basin', timeout=5000)
            page.click('text=Apply', timeout=5000)
            page.wait_for_load_state('networkidle', timeout=15000)
        except Exception as e:
            logger.warning(f"Could not apply Permian filter: {e}")

        # Export CSV
        try:
            with page.expect_download(timeout=30000) as download_info:
                page.click('text=Export', timeout=5000)
                page.click('text=CSV', timeout=5000)
            download = download_info.value
            csv_path = download.path()
            with open(csv_path, 'r') as f:
                csv_content = f.read()
        except Exception as e:
            logger.error(f"CSV download failed: {e}")
            browser.close()
            return []

        browser.close()
        return parse_csv(csv_content)


def parse_csv(csv_content):
    """Parse an Enverus rig report CSV (exported or manually uploaded)."""
    records = []
    reader = csv.DictReader(io.StringIO(csv_content))

    # Normalize column names (Enverus exports vary)
    for row in reader:
        # Try common column name variants
        operator = (row.get('Operator') or row.get('operator') or
                    row.get('OperatorName') or row.get('operator_name') or '').strip()
        if not operator:
            continue

        rig_count = _safe_int(row.get('Rig Count') or row.get('rig_count') or
                              row.get('RigCount') or row.get('Active Rigs') or '1')
        county = (row.get('County') or row.get('county') or '').strip()
        formation = (row.get('Formation') or row.get('formation') or
                     row.get('Target Formation') or '').strip()
        permit_date = (row.get('Permit Date') or row.get('permit_date') or
                       row.get('PermitDate') or '').strip()
        well_type = (row.get('Well Type') or row.get('well_type') or
                     row.get('WellType') or '').strip()

        records.append({
            'operator': operator,
            'rig_count': rig_count,
            'county': county,
            'formation': formation,
            'permit_date': permit_date or None,
            'well_type': well_type,
        })

    # Aggregate by operator (CSV may have one row per rig)
    return _aggregate_by_operator(records)


def _aggregate_by_operator(records):
    """Combine multiple rows per operator into single records with rig counts."""
    operators = {}
    for rec in records:
        op = rec['operator']
        if op not in operators:
            operators[op] = rec.copy()
            operators[op]['rig_count'] = 0
        operators[op]['rig_count'] += 1
        # Keep the most recent county/formation
        if rec.get('county'):
            operators[op]['county'] = rec['county']
        if rec.get('formation'):
            operators[op]['formation'] = rec['formation']

    return list(operators.values())


def filter_targets(records):
    """
    Split records into casing targets and tubing targets.
    Casing: 1-5 rigs (KC Pipe sweet spot)
    Tubing: ALL operators
    """
    casing_targets = [r for r in records if 1 <= (r.get('rig_count') or 0) <= 5]
    tubing_targets = list(records)  # All operators are tubing targets

    return {
        'casing': casing_targets,
        'tubing': tubing_targets,
        'all_unique': _deduplicate(casing_targets + tubing_targets)
    }


def _deduplicate(records):
    """Remove duplicate operators, keeping the first occurrence."""
    seen = set()
    unique = []
    for rec in records:
        if rec['operator'] not in seen:
            seen.add(rec['operator'])
            unique.append(rec)
    return unique


def parse_enverus_text(text):
    """
    Parse Enverus 'Permit Activity by Operators' text report.

    This is the format you get when you copy-paste from the Enverus web report
    or when the Excel export is pasted as text.
    """
    import re

    records = []
    current_county = ''
    current_operator = ''
    current_formation = ''
    current_permit_date = ''
    current_well_type = ''

    lines = text.split('\n')

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Match county headers like "EDDY CountyCount: 40 Total" or "MIDLAND CountyCount: 16 Total"
        county_match = re.match(r'^([A-Z]+)\s+County\s*Count:\s*(\d+)\s*Total', line)
        if county_match:
            current_county = county_match.group(1).title()
            continue

        # Match operator lines like "Operator: EOG RESOURCES INCKRISTINA AGEE432-686-6996"
        # or "Operator: PERMIAN RESOURCES OPERATING, LLCBRANDON MARTIN432-695-4222"
        op_match = re.match(r'Operator:\s*(.+?)(?:[A-Z][a-z]+\s+[A-Z][a-z]+\d|\s*$)', line)
        if not op_match:
            # Try alternate pattern — operator name followed by contact name and phone
            op_match = re.match(r'Operator:\s*(.+?)(?:[A-Z]{2,}[a-z])', line)
        if not op_match:
            # Simpler: just grab everything after "Operator: " up to a phone-number-like pattern
            op_match = re.match(r'Operator:\s*(.+?)(?:\d{3}[-.]?\d{3}[-.]?\d{4})', line)
        if not op_match:
            op_match = re.match(r'Operator:\s*(.+)', line)

        if op_match:
            raw_op = op_match.group(1).strip()
            # Clean up: remove trailing contact name (usually ALLCAPS first + mixed case last)
            # Pattern: operator name ends, then contact name starts
            # e.g., "EOG RESOURCES INCKRISTINA AGEE" -> "EOG RESOURCES INC"
            # Look for where uppercase company name transitions to a personal name
            clean = re.match(
                r'^(.*?(?:INC\.?|LLC\.?|LP\.?|CORP\.?|CO\.?|LTD\.?|COMPANY|OPERATING|RESOURCES|ENERGY|USA)[\s.,]*)',
                raw_op, re.IGNORECASE
            )
            if clean:
                current_operator = clean.group(1).strip().rstrip(',').rstrip('.')
            else:
                # Fallback: just use as-is but try to trim contact name
                current_operator = re.sub(r'[A-Z][a-z]+\s+[A-Z][a-z]+\s*$', '', raw_op).strip()
                if not current_operator:
                    current_operator = raw_op

            # Normalize common suffixes
            current_operator = current_operator.rstrip(',').rstrip('.').strip()
            continue

        # Match formation line
        form_match = re.search(r'Formation:\s*([^\t]+)', line)
        if form_match:
            current_formation = form_match.group(1).strip()

        # Match approved date
        date_match = re.search(r'Approved Date:\s*([\d-]+)', line)
        if date_match:
            current_permit_date = date_match.group(1).strip()

        # Match well type
        wt_match = re.search(r'Well Type:\s*([^\t\s]+(?:\s*&\s*[^\t\s]+)?)', line)
        if wt_match:
            current_well_type = wt_match.group(1).strip()

            # When we hit well type, we have a complete record
            if current_operator:
                records.append({
                    'operator': current_operator,
                    'rig_count': 1,  # Will aggregate later
                    'county': current_county,
                    'formation': current_formation,
                    'permit_date': current_permit_date or None,
                    'well_type': current_well_type,
                })

    # Aggregate by operator (count permits as proxy for activity level)
    return _aggregate_by_operator(records)


def _safe_int(val):
    """Safely convert to int, default 1."""
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return 1
