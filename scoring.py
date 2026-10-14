"""Evidence-based, reproducible scoring. Unknown facts earn no claimed points."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

def settings():
    return json.loads((Path(__file__).parent / 'settings.json').read_text(encoding='utf-8'))

def public_url(value):
    try:
        u=urlsplit(str(value))
        return u.scheme in ('https','http') and bool(u.hostname) and not u.username and not u.password
    except ValueError:
        return False

def phone_key(value):
    raw=re.split(r'(?:ext\.?|extension|\bx\s*|#)',str(value or ''),flags=re.I)[0]
    digits=re.sub(r'\D','',raw)
    if len(digits)==11 and digits.startswith('1'): digits=digits[1:]
    return digits if len(digits)==10 else ''

def category_key(value, config):
    key=' '.join(str(value or '').lower().strip().split())
    return config['category_aliases'].get(key,key)

def age_days(value, now):
    try:
        date=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if date.tzinfo is None:date=date.replace(tzinfo=timezone.utc)
        age=(now-date).total_seconds()/86400
        return age if age>=0 else None
    except (TypeError,ValueError):return None

def score(lead, config=None, now=None):
    config=config or settings();now=now or datetime.now(timezone.utc)
    facts=lead.get('facts') or {}
    weights=config['weights'];parts={key:0 for key in weights};unknown=[];reasons=[];sources=[]
    def evidence(label,url):
        if public_url(url):sources.append({'label':label,'url':url})
    category=category_key(lead.get('category'),config)
    allowed=category in config['category_allowlist'] or facts.get('category_approved') is True
    exclusion=''
    if not str(lead.get('name') or '').strip():exclusion='Missing business name'
    elif facts.get('permanently_closed') is True:exclusion='Confirmed permanently closed'
    elif facts.get('illegitimate') is True:exclusion='Confirmed personal-only or illegitimate listing'
    elif facts.get('central_website') is True:exclusion='Website purchases are controlled centrally'
    elif facts.get('wrong_industry') is True:exclusion='Different industry from the requested search'
    elif facts.get('contact_checked') is True and not (phone_key(lead.get('phone')) or lead.get('email') or public_url(lead.get('social_url'))):exclusion='No usable public contact found after checking'
    elif category and not allowed:exclusion='Category needs approval: '+category
    need=facts.get('website_need','unknown');need_source=facts.get('website_source','');need_verified=public_url(need_source) and bool(facts.get('website_checked_at'))
    if need=='strong' and need_verified:exclusion=exclusion or 'Verified strong website; no specific improvement opportunity'
    if need in config['website_need'] and need_verified:
        parts['website_need']=round(weights['website_need']*config['website_need'][need])
        labels={'no_website':'No dedicated website found after checking reasonable sources','broken':'Verified website availability problem','usability':'Verified mobile or contact usability problem','content':'Verified missing service or project information','dated':'Older appearance only — weaker opportunity','strong':'No identified website improvement opportunity'}
        reasons.append(facts.get('website_note') or labels[need]);evidence('Website evidence',need_source)
    else:unknown.append('Website opportunity has not been verified')
    activity=[]
    for item in facts.get('activity',[]):
        age=age_days(item.get('date'),now)
        if age is not None and public_url(item.get('source')):activity.append((age,item))
    current=[(age,item) for age,item in activity if age<config['activity_window_days']]
    if activity:
        age,item=min(activity,key=lambda pair:pair[0])
        recency=max(0,1-age/config['activity_window_days'])
        volume=min(1,len(current)/config['activity_volume_target'])
        share=config['activity_recency_share']
        parts['activity']=round(weights['activity']*(recency*share+volume*(1-share)))
        reasons.append('Recent '+str(item.get('kind','business activity')).lower() if current else 'Saved activity evidence is older than the scoring window')
        evidence('Business activity',item['source'])
    else:unknown.append('Recent business activity is unknown')
    if phone_key(lead.get('phone')):parts['contactability']=round(weights['contactability']*config['contact_phone_share']);reasons.append('Public business phone listed; working status unverified')
    elif lead.get('email'):parts['contactability']=round(weights['contactability']*config['contact_email_share']);reasons.append('Public business email listed')
    elif public_url(lead.get('social_url')):parts['contactability']=round(weights['contactability']*config['contact_social_share']);reasons.append('Public business social account listed')
    else:unknown.append('Public contact channel is unknown')
    evidence('Business listing',lead.get('source_url',''))
    if facts.get('owner_name') and public_url(facts.get('owner_source')) and facts.get('owner_access') in ('identified','authority'):
        share=config['decision_authority_share'] if facts['owner_access']=='authority' else config['decision_identified_share']
        parts['decision_maker']=round(weights['decision_maker']*share);reasons.append('Decision-maker identified: '+facts['owner_name']);evidence('Decision-maker evidence',facts['owner_source'])
    else:unknown.append('Decision-maker access is unknown')
    if allowed:parts['commercial_fit']+=round(weights['commercial_fit']*config['fit_category_share'])
    else:unknown.append('Business category is unknown')
    independence_verified=facts.get('independent') is True and public_url(facts.get('independence_source'))
    if independence_verified:
        parts['commercial_fit']+=round(weights['commercial_fit']*config['fit_independence_share']);reasons.append('Independent business confirmed');evidence('Independence evidence',facts.get('independence_source'))
    elif not independence_verified:unknown.append('Independent ownership is unknown')
    total=min(100,sum(parts.values()))
    band=next((g for g,minimum in sorted(config['score_bands'].items(),key=lambda pair:pair[1],reverse=True) if total>=minimum),'low')
    if exclusion:state='skipped';reasons.insert(0,exclusion)
    elif not allowed or not need_verified or not current or not independence_verified:state='waiting'
    elif total>=config['ready_score'] and phone_key(lead.get('phone')) and parts['website_need']>0:state='ready'
    else:state='low_priority';reasons.insert(0,'Below calling threshold or no public business phone')
    return {'score':total,'grade':total,'score_band':band,'state':state,'breakdown':parts,'unknown':unknown,'reasons':reasons,'sources':sources,'provisional':bool(unknown),'priority_market':str(lead.get('city','')).lower() in config['priority_markets']}
