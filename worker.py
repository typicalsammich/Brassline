"""Outbound-only worker. Run on your computer, never on the web host."""
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import urllib.error
from urllib.parse import urlsplit

ROOT=Path(__file__).parent
DATA=ROOT/'data';DATA.mkdir(exist_ok=True)
PROFILE=DATA/'google-scraper-profile'

def chrome_path():
    candidates=[Path(os.getenv('PROGRAMFILES','C:/Program Files'))/'Google/Chrome/Application/chrome.exe',Path(os.getenv('PROGRAMFILES(X86)','C:/Program Files (x86)'))/'Google/Chrome/Application/chrome.exe',Path(os.getenv('LOCALAPPDATA',''))/'Google/Chrome/Application/chrome.exe']
    return next((str(path) for path in candidates if path.is_file()),None)

def login():
    chrome=chrome_path()
    if not chrome:raise RuntimeError('Install Google Chrome, then open the profile again.')
    PROFILE.mkdir(exist_ok=True)
    subprocess.Popen([chrome,'--user-data-dir='+str(PROFILE),'--no-first-run','--new-window','https://www.google.com/maps?hl=en'])
    print('Sign into your spare Google account in this dedicated Chrome window. Close ALL windows for this profile before starting the worker. Never use your main Google profile.')

def configuration():
    path=DATA/'worker.json'
    if not path.exists():
        try:
            import tkinter as tk
            from tkinter import filedialog
            root=tk.Tk();root.withdraw()
            selected=filedialog.askopenfilename(title='Choose the worker.json file downloaded from Brassline',filetypes=[('Worker configuration','*.json')]);root.destroy()
            if not selected:raise RuntimeError('No configuration selected. Download it from the Scraper page first.')
            contents=Path(selected).read_text(encoding='utf-8')
            config=json.loads(contents)
            validate_config(config);path.write_text(contents,encoding='utf-8')
        except ImportError:raise RuntimeError('Save worker.json in the dashboard data folder, then start the worker again.')
    config=json.loads(path.read_text(encoding='utf-8'));validate_config(config);return config

def validate_config(config):
    parsed=urlsplit(config.get('dashboard_url',''))
    if not parsed.hostname or parsed.username or parsed.password or parsed.scheme not in ('http','https'):raise RuntimeError('Invalid dashboard URL in worker configuration')
    if parsed.scheme!='https' and parsed.hostname not in ('127.0.0.1','localhost','::1'):raise RuntimeError('Online dashboard connections require HTTPS')
    if not isinstance(config.get('worker_token'),str) or len(config['worker_token'])<24:raise RuntimeError('Invalid worker connection file. Download a fresh one from Brassline.')

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args):return None

class API:
    def __init__(self,config):self.config=config;self.opener=urllib.request.build_opener(NoRedirect)
    def post(self,path,body):
        url=self.config['dashboard_url'].rstrip('/')+'/api/worker/'+path
        request=urllib.request.Request(url,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+self.config['worker_token']},method='POST')
        last=None
        for attempt in range(3):
            try:
                with self.opener.open(request,timeout=30) as response:return json.loads(response.read(1000000))
            except urllib.error.HTTPError as error:
                if error.code in (401,403):raise RuntimeError('Worker access was revoked or the file is invalid. Download a new configuration from Brassline.')
                if error.code==409:raise RuntimeError('This run is no longer assigned to the worker. It may have been stopped or requeued.')
                if error.code<500:raise RuntimeError('Dashboard rejected a worker update (HTTP '+str(error.code)+'). Check the run status.')
                last=error
            except (urllib.error.URLError,TimeoutError) as error:last=error
            time.sleep(2*(attempt+1))
        raise RuntimeError('Could not reach the dashboard. The worker will reconnect; checked businesses stay saved.') from last

def run_job(api,job):
    from maps_scraper import MapsBrowser,BrowserAttention,same_industry
    config=job['config'];base={'run_id':job['id'],'lease_token':job['lease_token']};cancel=False;opened=0
    def heartbeat(message):
        nonlocal cancel
        response=api.post('progress',{**base,'message':message});cancel=cancel or response['cancel'];return cancel
    try:
        with MapsBrowser(PROFILE,heartbeat) as browser:
            for tile in config['tiles']:
                if heartbeat('Searching '+tile['wording']+' · '+config['location']['label']):break
                completed=False
                for cards,finished in browser.result_cards(tile,config['location']):
                    if finished:completed=True;break
                    for card in cards:
                        if cancel:break
                        cheap='Permanently closed' if card['closed'] else 'Different industry' if same_industry(card['category'],config['industry']) is False else ''
                        saved=api.post('reserve',{**base,**{key:card[key] for key in ['name','phone','place_id','source_url','category']},'cheap_reason':cheap})
                        cancel=cancel or saved['cancel']
                        if cancel:break
                        if not saved['should_open']:continue
                        try:details=browser.business(card,config['industry'],config['location'])
                        except BrowserAttention:
                            api.post('result',{**base,'lead_id':saved['lead_id'],'details':{'review_facts':{'scrape_status':'needs_browser_attention','warnings':['Browser attention interrupted this check. Facts remain unknown.']}}});raise
                        except Exception:
                            details={'review_facts':{'scrape_status':'check_failed','warnings':['Could not read this business. Facts remain unknown; it will not be reopened automatically.']}}
                        api.post('result',{**base,'lead_id':saved['lead_id'],'details':details});opened+=1
                        if opened>=config['max_leads']:
                            api.post('progress',{**base,'final_state':'completed','message':'Business limit reached. Unfinished tiles remain available for the next run.'});return
                        if heartbeat('Saved '+card['name']):break
                        time.sleep(config['scraper']['pause_seconds'])
                    if cancel:break
                if completed:api.post('progress',{**base,'tile_key':tile['key'],'message':'Search tile complete'})
                if cancel:break
        api.post('progress',{**base,'final_state':'cancelled' if cancel else 'completed','message':'Stopped after current business' if cancel else 'Run complete · saved facts and source links'})
    except Exception as error:
        logging.warning('Run stopped: %s',str(error))
        try:api.post('progress',{**base,'final_state':'failed','message':str(error)[:500]})
        except Exception:pass

def lock_worker():
    handle=open(DATA/'worker.lock','a+b');handle.seek(0);handle.write(b'1');handle.flush();handle.seek(0)
    try:
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:handle.close();raise RuntimeError('A Brassline worker is already running on this computer.')
    return handle

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--login',action='store_true');args=parser.parse_args()
    if args.login:login();return
    handler=RotatingFileHandler(DATA/'worker.log',maxBytes=1000000,backupCount=2,encoding='utf-8')
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s',handlers=[handler,logging.StreamHandler(sys.stdout)] if sys.stdout else [handler])
    lock=lock_worker();config=configuration();api=API(config)
    logging.info('Worker ready. Waiting for jobs. No incoming connection is opened.')
    try:
        while True:
            try:
                response=api.post('claim',{'worker_name':config.get('worker_name','My computer')})
                if response['job']:run_job(api,response['job'])
            except Exception as error:logging.warning('%s',str(error))
            time.sleep(10)
    finally:lock.close()

if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:pass
    except Exception as error:print(str(error));sys.exit(1)
