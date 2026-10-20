"""HTTP-only cron entry point. Never opens the web service's SQLite database."""
import os
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None


def main():
    base=os.environ.get('DISCOVERY_APP_URL','').rstrip('/')
    token=os.environ.get('DISCOVERY_TRIGGER_TOKEN','')
    url=urlsplit(base)
    if url.scheme!='https' or not url.hostname or url.username or url.password or url.path or url.query or url.fragment or len(token)<32:
        raise SystemExit('Configure an HTTPS app origin and a scheduler token of at least 32 characters.')
    request=Request(base+'/api/discovery/wake',data=b'{}',method='POST',headers={
        'Content-Type':'application/json','Authorization':'Bearer '+token})
    try:
        with build_opener(NoRedirect()).open(request,timeout=55) as response:
            if response.status!=200:
                raise ValueError('Wake rejected')
    except Exception:
        raise SystemExit('Discovery wake failed. Check the app URL, scheduler secret and web-service health. No secret was logged.')
    print('Discovery scheduler notified; durable processing runs in the web service.')


if __name__=='__main__':
    main()
