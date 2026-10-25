import os,unittest,tempfile,time,hashlib
os.environ.update(APP_DATABASE='/tmp/generated-security.sqlite',APP_AUTH_SECRET='SYNTHETIC_ONLY_SECRET_'+'a'*40,APP_PUBLIC_BASE_URL='https://app.test',APP_OAUTH_GOOGLE_CLIENT_ID='synthetic-client',APP_OAUTH_GOOGLE_CLIENT_SECRET='synthetic-secret',APP_MIDTRANS_SERVER_KEY='SYNTHETIC_KEY_'+'b'*20,APP_PAYMENT_TRUST_WEBHOOK='true')
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import auth_api,auth_jwt,oauth_login,oauth_providers,payment_core,payments_api
app=FastAPI();app.include_router(auth_api.router);app.include_router(oauth_login.router);app.include_router(payments_api.router)
class Security(unittest.TestCase):
 def test_real_pyjwt_http_lifecycle(self):
  with TestClient(app,base_url='https://app.test') as c:
   self.assertEqual(c.post('/api/auth/register',json={'email':'demo@example.test','password':'Synthetic!23456'}).status_code,201)
   login=c.post('/api/auth/login',json={'email':'demo@example.test','password':'Synthetic!23456'});self.assertEqual(login.status_code,200);self.assertEqual(login.headers['cache-control'],'no-store');token=login.json();self.assertEqual(auth_jwt.decode_access(token['access_token'])['typ'],'access')
   self.assertEqual(c.post('/api/auth/refresh',json={'refresh_token':token['refresh_token']}).status_code,200);self.assertEqual(c.post('/api/auth/refresh',json={'refresh_token':token['refresh_token']}).status_code,401)
 def test_oauth_signed_cookies_and_future_timestamp(self):
  with TestClient(app,base_url='https://app.test') as c:
   res=c.get('/auth/google/start');self.assertEqual(res.status_code,200);self.assertEqual(len(res.headers.get_list('set-cookie')),2);self.assertIn('__Host-hms_oauth_state',c.cookies);self.assertIn('__Host-hms_oauth_verifier',c.cookies)
   state=c.cookies.get('__Host-hms_oauth_state').split('.')[0]
   c.cookies.set('__Host-hms_oauth_state',state+'.'+str(int(time.time()))+'.forged');self.assertEqual(c.get('/auth/google/callback',params={'state':state,'code':'synthetic'}).status_code,403)
   signed='google:demo.'+str(int(time.time())+1000);self.assertFalse(oauth_providers.verify_state(signed,oauth_providers.sign_state(signed,os.environ['APP_AUTH_SECRET']),os.environ['APP_AUTH_SECRET'],time.time()+1000,time.time()))
 def test_pending_webhook_never_marks_paid_and_settlement_not_dropped(self):
  payment_core.create_intent('demo-order',150000,'idr')
  with TestClient(app,base_url='https://app.test') as c:
   for status in ('pending','settlement'):
    p={'order_id':'demo-order','status_code':'200','gross_amount':'150000.00','transaction_status':status};p['signature_key']=hashlib.sha512(('demo-order200150000.00'+os.environ['APP_MIDTRANS_SERVER_KEY']).encode()).hexdigest();res=c.post('/api/payments/webhook/midtrans',json=p);self.assertEqual(res.status_code,200);self.assertTrue(res.json()['recorded']);self.assertFalse(res.json()['paid'])
   self.assertEqual(c.post('/api/payments/webhook/midtrans',json=[]).status_code,400)
  with payment_core.connect() as db:self.assertEqual(db.execute('SELECT status FROM payment_intents WHERE id=?',('demo-order',)).fetchone()[0],'pending')
if __name__=='__main__':unittest.main()
