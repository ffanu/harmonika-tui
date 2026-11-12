"""Scoped native macOS observation of Mailjet onboarding. No page JS or secrets.

Only activation-status navigation is supported. Business form submission,
credentials, inboxes, cookies, browser permissions and other tabs are excluded.
"""
import re

SCRIPT = r'''function run(argv) {
const cfg=JSON.parse(argv[0]),a=Application('Google Chrome');
if(!a.running())return JSON.stringify({ok:false,error:'chrome_not_running'});
let selected=null;
for(const w of a.windows()) {const ts=w.tabs();for(let i=0;i<ts.length;i++) {
 if(String(ts[i].id())===cfg.tab_id){selected={w,t:ts[i],i};break;}
}if(selected)break;}
if(!selected)return JSON.stringify({ok:false,error:'tab_not_found'});
const activationScope=url=>/^https:\/\/app\.mailjet\.com\/(?:onboarding|account\/activation)\/?(?:[?#]|$)/.test(url);
const scope=url=>/^https:\/\/app\.mailjet\.com\/(?:onboarding|account\/(?:activation|apikeys))\/?(?:[?#]|$)/.test(url);
if(!scope(selected.t.url()))return JSON.stringify({ok:false,error:'mailjet_activation_scope_changed',action_sent:false});
if(cfg.action!=='status'&&!activationScope(selected.t.url()))return JSON.stringify({ok:false,error:'native_navigation_requires_activation_page',action_sent:false});
selected.w.activeTabIndex=selected.i+1;selected.w.index=1;a.activate();
const p=Application('System Events').processes.byName('Google Chrome');
let count=0,web=null;
function locate(e,d){if(web||count++>160||d>20)return;
 if(e.role()==='AXWebArea'){web=e;return;}
 try{for(const c of e.uiElements())locate(c,d+1);}catch{}}
locate(p.windows[0].uiElements[0],0);
if(!web)return JSON.stringify({ok:false,error:'native_web_area_unavailable'});
const wanted={
 'How many emails do you expect to send and at what frequency?':'email_volume_and_frequency',
 'How many contacts are in your database?':'contacts_count',
 "What is the URL for your business' privacy policy?":'privacy_policy_url',
 'Where do you source your database/list of email addresses? Please provide the URLs to any of these websites, subscription forms (opt-in forms) or domains you will be using, separated by a comma.':'recipient_sources_and_urls'
};
const statuses={email_verified_observed:false,activation_required_observed:false,
 business_verification_required_observed:false,business_review_pending_observed:false,
 business_submission_received_observed:false,sending_blocked_pending_review_observed:false,
 api_credential_activation_gate_observed:false};
const required=new Set(),optional=new Set(),options=new Set(),controls=new Set(),clicks=[];
let truthConfirmationObserved=false;
const permitted=['Activate now','Verify now','Next','Back','Submit','Cancel'];
const requested=cfg.action==='open_activation'?'Activate now':cfg.action==='open_business_verification'?'Verify now':null;
count=0;
function walk(e,d){if(count++>400||d>18)return;const role=e.role();
 let s='';
 if(['AXStaticText','AXButton','AXTextField','AXTextArea','AXCheckBox'].includes(role))try{
   s=String(role==='AXStaticText'?e.value():(e.description()||e.title()||'')).trim();
 }catch{}
 if(role==='AXStaticText'){
   if(s==='Your email was successfully verified.')statuses.email_verified_observed=true;
   if(s==='Please activate your account to unlock your sending capabilities.')statuses.activation_required_observed=true;
   if(s==='Verify your business')statuses.business_verification_required_observed=true;
   if(/^(?:Your (?:account|business) is (?:currently )?(?:under review|being reviewed)|We are reviewing your (?:account|business))[.!]?$/i.test(s))statuses.business_review_pending_observed=true;
   if(s==='Your account will be activated soon - we are reviewing your request.'||s.startsWith('We are currently processing your business verification request.'))statuses.business_review_pending_observed=true;
   if(s==='Account activation is required'){statuses.api_credential_activation_gate_observed=true;statuses.activation_required_observed=true;}
   if(s==='Your ticket was successfully submitted')statuses.business_submission_received_observed=true;
   if(s==='Your request is currently being reviewed. You will not be able to send until your business verification ticket is resolved. However, you can start preparing email templates, contact forms and more.')statuses.sending_blocked_pending_review_observed=true;
 }
 if(role==='AXTextField'&&Object.prototype.hasOwnProperty.call(wanted,s))required.add(wanted[s]);
 if(role==='AXTextArea'&&s==='Additional details regarding your emailing needs or your organization that could help us review your account')optional.add('organization_and_email_use_details');
 if(role==='AXCheckBox'&&s.startsWith('I confirm that all the information given is true and accurate.'))truthConfirmationObserved=true;
 if(role==='AXCheckBox'&&s==='Transactional emails (order confirmations, password reset, notifications...)')options.add('transactional');
 if(role==='AXCheckBox'&&s==='Marketing emails (newsletters, emails sent to a large number of recipients at once...)')options.add('marketing');
 if(role==='AXButton'&&permitted.includes(s)){controls.add(s);if(s===requested)clicks.push(e);}
 try{for(const c of e.uiElements())walk(c,d+1);}catch{}
}
walk(web,0);
if(!scope(selected.t.url())||String(a.windows[0].activeTab.id())!==cfg.tab_id)return JSON.stringify({ok:false,error:'native_tab_changed',action_sent:false});
if(count>400)return JSON.stringify({ok:false,error:'native_observation_truncated',action_sent:false});
if(requested){
 if(clicks.length!==1)return JSON.stringify({ok:false,error:'native_control_ambiguous',action_sent:false});
 if(!clicks[0].enabled())return JSON.stringify({ok:false,error:'native_control_disabled',action_sent:false});
 clicks[0].click();return JSON.stringify({ok:true,action_sent:true,verification_required:true});
}
return JSON.stringify({ok:true,path:selected.t.url().split(/[?#]/)[0].replace('https://app.mailjet.com',''),
 status:statuses,required_inputs:[...required],optional_inputs:[...optional],truth_confirmation_observed:truthConfirmationObserved,sending_options:[...options],controls:[...controls],
 evidence:'macos_accessibility_visible_ui',content_is_untrusted:true,values_returned:false,
 interpretation:'Observed UI only; does not prove account approval, SMTP authentication or relay installation.'});
}'''


def invoke(args):
    if not isinstance(args,dict) or set(args)-{'action','tab_id','authorized_activation'}:
        return {'ok':False,'error':'invalid_mailjet_native_request'}
    action=args.get('action','status');tab=args.get('tab_id')
    if action not in ('status','open_activation','open_business_verification'):
        return {'ok':False,'error':'unsupported_mailjet_native_action'}
    if not isinstance(tab,str) or not re.fullmatch(r'\d{1,15}',tab):
        return {'ok':False,'error':'invalid_tab_id'}
    if action!='status' and args.get('authorized_activation') is not True:
        return {'ok':False,'error':'existing_activation_authorization_required','action_sent':False}
    import desktop_browser as browser
    try:
        return browser.run_jxa(SCRIPT,{'action':action,'tab_id':tab},timeout=45)
    except browser.BrowserError as exc:
        return {'ok':False,'error':str(exc),'execution_state':'unknown' if action!='status' else 'observation_failed',
                'safe_to_retry':False}


TOOLS=[{'name':'mailjet_native','description':'Owner-local native macOS accessibility fallback for selected Mailjet onboarding/account activation tab. status may also observe the activation gate on /account/apikeys, returning fixed technical flags and input names, never raw text, field values or keys. Navigation stays restricted to activation/onboarding pages. open_activation/open_business_verification need existing explicit user authorization and authorized_activation=true. No form filling/submission, key operations, inbox/OTP, cookies, browser settings or paid plans. Verify status after one navigation; never retry unknown actions.',
        'inputSchema':{'type':'object','properties':{'action':{'type':'string','enum':['status','open_activation','open_business_verification']},'tab_id':{'type':'string'},'authorized_activation':{'type':'boolean','default':False}},'required':['action','tab_id'],'additionalProperties':False}}]
HANDLERS={'mailjet_native':invoke}
