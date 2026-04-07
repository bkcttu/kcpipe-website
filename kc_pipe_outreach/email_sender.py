"""
Email Sender — sends approved emails via Microsoft Graph API.
Reuses auth pattern from existing KC Pipe Quote System.
"""

import logging

import msal
import requests

from outreach_config import Config
from outreach_db import (
    get_approved_outreach, mark_outreach_sent, mark_contact_sent,
    log_sent_email
)

logger = logging.getLogger(__name__)


def _get_access_token():
    """Acquire Microsoft Graph access token using client credentials flow."""
    authority = f"https://login.microsoftonline.com/{Config.MICROSOFT_TENANT_ID}"
    app = msal.ConfidentialClientApplication(
        Config.MICROSOFT_CLIENT_ID,
        authority=authority,
        client_credential=Config.MICROSOFT_CLIENT_SECRET,
    )

    result = app.acquire_token_for_client(
        scopes=["https://graph.microsoft.com/.default"]
    )

    if "access_token" not in result:
        error = result.get("error_description", "Unknown error")
        raise RuntimeError(f"Failed to acquire token: {error}")

    return result["access_token"]


def send_email(to_email, subject, body, save_to_sent=True):
    """
    Send a single email via Microsoft Graph API.

    Args:
        to_email: recipient email address
        subject: email subject line
        body: email body (plain text, will be sent as HTML with line breaks)
        save_to_sent: whether to save a copy to Sent Items

    Returns:
        dict with 'success' bool and 'message_id' or 'error'
    """
    if not all([Config.MICROSOFT_CLIENT_ID, Config.MICROSOFT_CLIENT_SECRET,
                Config.MICROSOFT_TENANT_ID, Config.SENDER_EMAIL]):
        return {'success': False, 'error': 'Microsoft Graph credentials not configured'}

    try:
        token = _get_access_token()
    except RuntimeError as e:
        return {'success': False, 'error': str(e)}

    # Convert plain text body to HTML with Byron's signature
    # Strip the plain text signature (email_generator adds it) before adding HTML version
    sig_marker = "Byron Courts\nKC Pipe LP\n"
    if sig_marker in body:
        body_text = body[:body.index(sig_marker)].rstrip()
    else:
        body_text = body

    html_body = body_text.replace('\n', '<br>\n')
    html_body += '''<br><br>
<table cellpadding="0" cellspacing="0" border="0">
<tr>
<td style="padding-right: 15px; vertical-align: top;">
<img src="https://i.imgur.com/4OJUP1r.png" alt="KC Pipe" width="80">
</td>
<td style="vertical-align: top; font-family: Arial, sans-serif; font-size: 14px;">
<strong style="font-size: 15px;">BYRON COURTS</strong><br>
<span style="color: #666;">c: (432) 230-5579 &nbsp;|&nbsp; o: (432) 563-0500</span><br>
<a href="mailto:bcourts@kcpipe.com" style="color: #c00;">bcourts@kcpipe.com</a><br>
<a href="http://www.kcpipe.com" style="color: #c00;">www.kcpipe.com</a>
</td>
</tr>
</table>'''

    url = f"https://graph.microsoft.com/v1.0/users/{Config.SENDER_EMAIL}/sendMail"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    payload = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "HTML",
                "content": html_body,
            },
            "toRecipients": [
                {"emailAddress": {"address": to_email}}
            ],
        },
        "saveToSentItems": save_to_sent,
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        if resp.status_code == 202:
            # Graph API returns 202 Accepted for sendMail
            return {'success': True, 'message_id': resp.headers.get('request-id', '')}
        else:
            error_detail = resp.json().get('error', {}).get('message', resp.text)
            return {'success': False, 'error': f"HTTP {resp.status_code}: {error_detail}"}
    except requests.RequestException as e:
        return {'success': False, 'error': str(e)}


def send_all_approved():
    """
    Send all approved outreach emails.

    Returns:
        dict with 'sent', 'failed', and 'results' lists
    """
    approved = get_approved_outreach()
    results = []
    sent_count = 0
    failed_count = 0

    for item in approved:
        to_email = item.get('contact_email')
        if not to_email:
            results.append({
                'outreach_id': item['id'],
                'operator': item['operator'],
                'success': False,
                'error': 'No email address',
            })
            failed_count += 1
            continue

        result = send_email(to_email, item['subject'], item['body'])

        if result['success']:
            mark_outreach_sent(item['id'])
            mark_contact_sent(item['contact_id'])
            log_sent_email(
                contact_id=item['contact_id'],
                outreach_id=item['id'],
                subject=item['subject'],
                body=item['body'],
                message_id=result.get('message_id'),
            )
            sent_count += 1
        else:
            failed_count += 1

        results.append({
            'outreach_id': item['id'],
            'operator': item['operator'],
            'contact': item.get('contact_name', ''),
            **result,
        })

    return {
        'sent': sent_count,
        'failed': failed_count,
        'results': results,
    }
