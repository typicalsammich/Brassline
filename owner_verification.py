"""Evidence classification, separate from marketing score and outreach permission."""
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
from scoring import age_days, phone_key, public_url

OWNER_ROLES = {'owner', 'co-owner', 'co owner', 'managing member', 'sole proprietor'}
AUTHORITY_ROLES = {'president', 'ceo', 'chief executive officer', 'principal'}
PHONE_LABELS = ('Verified owner-specific business phone', 'Verified business phone associated with an identified owner',
                'General business phone', 'Unverified phone', 'No phone available')


def us_phone(value):
    digits = phone_key(value)
    if not digits or digits[0] not in '23456789' or digits[3] not in '23456789':
        return ''
    # NANP fictional range and obvious repeated/placeholder numbers are unusable.
    if digits[3:6] == '555' and 100 <= int(digits[6:]) <= 199:
        return ''
    if len(set(digits)) <= 2 or digits in ('1234567890', '9876543210', '8005551212'):
        return ''
    return '+1' + digits


def person(item, now=None):
    now = now or datetime.now(timezone.utc)
    role = str(item.get('role', '')).strip().casefold()
    age = age_days(item.get('retrieved_at'), now)
    stale = age is None or age > 180
    # A registry snapshot/AI/text guess alone does not verify a person's present authority.
    reliable = (item.get('basis') in ('matched_company_publication', 'reviewed_public_evidence')
                and item.get('identity_confirmed') is True and public_url(item.get('source_url'))
                and bool(item.get('evidence', '').strip()) and not stale)
    status, confidence = 'unknown', 0
    if role in OWNER_ROLES:
        status, confidence = ('verified_owner', 90) if reliable else ('probable_owner', 45)
    elif role in AUTHORITY_ROLES:
        status, confidence = ('verified_decision_maker', 85) if reliable else ('unknown', 20)
    elif role == 'founder':
        status, confidence = 'probable_owner', 40  # Historic founding does not prove present control.
    return {**item, 'status': status, 'confidence': confidence, 'possibly_outdated': stale,
            'role': str(item.get('role', ''))[:120], 'name': str(item.get('name', ''))[:160]}


def phone_record(value, evidence, people):
    number = us_phone(value)
    result = {'number': number or str(value or ''), 'classification': 'unverified' if value else 'none',
              'source_url': evidence.get('source_url', ''), 'retrieved_at': evidence.get('retrieved_at'),
              'evidence': evidence.get('evidence', ''), 'line_connected': 'not_tested', 'owner_direct': False}
    if not number:
        return result
    published = (evidence.get('published_business') is True and public_url(result['source_url'])
                 and bool(result['evidence']) and (age_days(result['retrieved_at'], datetime.now(timezone.utc)) or 0) <= 180
                 and age_days(result['retrieved_at'], datetime.now(timezone.utc)) is not None)
    verified = [p for p in people if p['status'] in ('verified_owner', 'verified_decision_maker') and not p['possibly_outdated']]
    if published:
        result['classification'] = 'owner_associated_business' if verified else 'general_business'
        if verified and evidence.get('person_name') and any(p['name'].casefold() == evidence['person_name'].casefold() for p in verified) and evidence.get('explicit_business_direct') is True:
            result.update(classification='owner_specific_business', owner_direct=True)
    return result


def website_result(report, identity_confirmed=False):
    result = {'classification': 'unknown', 'findings': [], 'checked_at': report.get('checked_at'), 'source_url': ''}
    if not identity_confirmed or report.get('error'):
        return result
    for finding in report.get('findings', []):
        # Missing viewport is directly reproducible, but requires manual assessment of impact.
        if finding.get('kind') == 'mobile_hint':
            result.update(classification='usability_review', source_url=finding.get('source', ''))
            result['findings'].append(finding)
        elif finding.get('kind') == 'availability':
            result.update(classification='availability_review', source_url=finding.get('source', ''))
            result['findings'].append(finding)
    return result


def summarize(profile, minimum=80, require_phone=True):
    people = [person(p) for p in profile.get('people', [])]
    best = max(people, key=lambda p: (p['confidence'],p.get('retrieved_at','')), default=None)
    phone = phone_record(profile.get('phone', ''), profile.get('phone_evidence', {}), people)
    verified = bool(best and best['status'] in ('verified_owner', 'verified_decision_maker'))
    associated = phone['classification'] in ('owner_specific_business', 'owner_associated_business')
    confidence = min(100, (best['confidence'] * 65 // 100 if best else 0) + (30 if associated else 10 if phone['classification'] == 'general_business' else 0) + (5 if profile.get('business_validated') else 0))
    website = profile.get('website', {'classification': 'unknown'})
    opportunity = website.get('classification') in ('no_website_verified', 'broken_verified', 'usability_verified', 'contact_verified', 'content_verified')
    now = datetime.now(timezone.utc)
    activity_age = age_days(profile.get('activity_date'), now)
    website_age = age_days(website.get('checked_at'), now)
    current_activity = profile.get('activity_verified') and activity_age is not None and activity_age <= 180
    current_site = website_age is not None and website_age <= 180
    qualified = bool(verified and confidence >= minimum and (associated or not require_phone) and opportunity and current_site and profile.get('business_validated') and profile.get('independent') and current_activity)
    return {**profile, 'people': people, 'best_person': best, 'phone_detail': phone, 'owner_contact_confidence': confidence,
            'qualified': qualified, 'research_status': 'ready_for_review' if qualified else 'research_required',
            'calling_eligibility': 'Existing calling rules and suppression checks still apply; qualification does not authorize outreach.',
            'suggested_opening': ('Hi, is ' + best['name'] + ' available? I’m calling about ' + profile.get('name', 'your business') + '. Is now a good time?') if verified else 'Hi, may I speak with the person who handles your company website?'}
