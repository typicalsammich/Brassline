const $=id=>document.getElementById(id);
const pages=['Leads','Scraper','Research','Dialer','Follow-ups','Pipeline','Analytics','Scripts','Settings','Team invitations','Shared services','Launch checks','Help'];
const navIcons={'Leads':'◉','Scraper':'⌕','Research':'⌕','Dialer':'☎','Follow-ups':'◷','Pipeline':'▤','Analytics':'▥','Scripts':'≡','Settings':'⚙','Team invitations':'⊕','Shared services':'◎','Launch checks':'✓','Help':'?'};
let scoringConfig={score_bands:{high:80,qualified:60,developing:40,low:0}};
let me,status,leads=[],events=[],page='Leads',active=null,auto=false,index=0,clock,connection,startTime;
async function api(path,data){
 const changing=data!==undefined;let response;
 try{response=await fetch('/api/'+path,changing?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{});}catch{throw Error('Could not reach Brassline. Check your connection and Render service status.'+(changing?' The request may have completed; check the page or history before trying again.':''));}
 const raw=await response.text();let result;
 try{result=JSON.parse(raw);}catch{
  const temporary=[502,503,504].includes(response.status);
  throw Error((temporary?'Brassline is temporarily unavailable (HTTP '+response.status+'). Render may be restarting or deploying.':'The server returned a web page instead of app data (HTTP '+response.status+'). Check the Render service status.')+(changing?' Check whether your action completed before trying again. No automatic retry was made.':' Wait until the service is Live, then refresh.'));
 }
 if(!response.ok){const detail=result?.detail;throw Error(Array.isArray(detail)?detail.map(x=>x.msg).join('; '):(typeof detail==='string'?detail:'Request failed (HTTP '+response.status+').'));}
 return result;
}
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n}
function action(text,fn,cls){const b=el('button',text,cls);b.type='button';b.onclick=async()=>{try{await fn()}catch(e){$('notice').textContent=e.message}};return b}
function showAuth(registering,invitation=''){
 $('shell').hidden=true;$('auth').hidden=false;
 $('email').parentElement.hidden=false;$('email').required=true;const first=!status.initialized;
 $('authTitle').textContent=registering?(first?'Create your administrator account':'Create your account'):'Welcome back';
 $('authHint').textContent=registering?(first?'Your first account manages invitations. Choose a password of at least 12 characters.':'Your private workspace starts with an invitation from your administrator.'):'Sign in to your private calling workspace.';
 $('authButton').textContent=registering?'Create account':'Sign in';
 $('password').autocomplete=registering?'new-password':'current-password';
 $('tokenLabel').hidden=!(registering&&first&&status.online);
 $('invitationLabel').hidden=!(registering&&!first);$('invitation').value=invitation;
 $('authSwitch').textContent=registering?'Already have an account? Sign in':'Create account';
 $('authSwitch').onclick=()=>showAuth(!registering,invitation);$('authError').textContent='';
 $('authForm').onsubmit=async e=>{e.preventDefault();$('authError').textContent='';try{
 let token=first?$('setupToken').value:$('invitation').value.trim();
 if(registering&&!first){if(token.includes('#invite='))token=token.split('#invite=')[1];if(!token)throw Error('Paste an invitation link or code from your administrator.');}
 await api(registering?'register':'login',{email:$('email').value,password:$('password').value,token});history.replaceState(null,'','/');location.reload();
 }catch(error){$('authError').textContent=error.message}};
}
async function boot(){status=await api('status');if(location.hash.startsWith('#verify=')){await api('account/verify-email',{token:location.hash.slice(8)});history.replaceState(null,'','/');}if(location.hash.startsWith('#reset=')){showRecovery(location.hash.slice(7));return;}const invitation=location.hash.startsWith('#invite=')?location.hash.slice(8):'';if(invitation){showAuth(true,invitation);return;}try{me=await api('me')}catch{showAuth(!status.initialized);return;}await dashboard();}
async function dashboard(){$('auth').hidden=true;$('shell').hidden=false;if(typeof updateAccountBadge==='function')updateAccountBadge();else $('account').textContent=me.email;$('mode').textContent=me.practice?'PRACTICE · MADE-UP DATA':'YOUR PRIVATE WORKSPACE';$('backup').hidden=!me.admin;$('logout').hidden=me.practice;$('nav').replaceChildren(...pages.map((p,i)=>{const b=action('',()=>{page=p;render()});b.dataset.page=p;b.classList.toggle('nav-settings-start',p==='Settings');b.append(el('span',navIcons[p],'nav-icon'),el('span',p));return b}));[leads,events,leadBatches,scoringConfig]=await Promise.all([api('leads'),api('events'),api('batches'),api('scoring-settings')]);await loadDialer(true);render();if(!me.onboarding_done&&typeof startWelcomeTour==='function')startWelcomeTour();}
function render(){clearInterval(scraperTimer);if(typeof researchTimer!=="undefined")clearInterval(researchTimer);[...$('nav').children].forEach((b,i)=>b.classList.toggle('active',pages[i]===page));$('title').textContent=page;const content=$('content');content.replaceChildren();if(page==='Leads'){renderLeads(content);return;}if(page==='Scraper'){renderScraper(content);return;}if(page==='Dialer'){renderDialer(content);return;}if(page==='Analytics'){renderAnalytics(content);return;}if(page==='Scripts'){renderScripts(content);return;}if(page==='Settings'){renderSettings(content);return;}if(page==='Team invitations'){renderTeam(content);return;}if(page==='Follow-ups'){renderFollowups(content);return;}if(page==='Pipeline'){renderPipeline(content);return;}if(page==='Launch checks'){renderLaunch(content);return;}if(page==='Research'){renderResearch(content);return;}if(page==='Shared services'){renderSharedServices(content);return;}if(page==='Help'){renderHelp(content);return;}}
$('hangup').onclick=()=>dialHangup().catch(e=>$('notice').textContent=e.message);$('pause').onclick=dialPause;$('mute').onclick=dialMute;
$('logout').onclick=()=>signOut().catch(e=>$('notice').textContent=e.message);$('account').onclick=()=>{page='Settings';render()};$('backup').onclick=()=>location.href='/api/backup';
$('forgotPassword').onclick=()=>openCodeRecovery();
boot().catch(e=>{$('auth').hidden=false;$('authError').textContent=e.message});
