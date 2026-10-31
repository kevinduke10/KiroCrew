import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {LoginSession, PKCE_KEY, PKCE_TTL_MS} from '../../src/kirocrew_custom/saas/web/oauth.mjs';

const config = {client_id:'app-client', redirect_uri:'https://app.example.test/', logout_uri:'https://app.example.test/', authorization_endpoint:'https://login.example.test/oauth2/authorize', token_endpoint:'https://login.example.test/oauth2/token', logout_endpoint:'https://login.example.test/logout', scopes:['openid','aws.cognito.signin.user.admin']};
function fixture() {
  const values = new Map(), requests = [], clean = [];
  let time = 1000, sequence = 0;
  const storage = {getItem:key=>values.get(key) ?? null, setItem:(key,value)=>values.set(key,value), removeItem:key=>values.delete(key)};
  const session = new LoginSession({storage, crypto:{subtle:webcrypto.subtle, getRandomValues:bytes=>bytes.fill(++sequence)}, now:()=>time, fetch:async (url, options)=>{
    requests.push({url,options});
    return {ok:true,json:async()=>({access_token:'opaque-token',token_type:'Bearer',expires_in:300,refresh_token:'must-not-persist'})};
  }});
  return {session,storage,requests,clean,values,setTime:value=>{time=value;},scrub:url=>clean.push(url)};
}
async function callback(f) {
  const authorize = new URL(await f.session.begin(config));
  return config.redirect_uri + '?code=one-use-code&state=' + authorize.searchParams.get('state');
}

test('uses S256 PKCE, state, public code exchange and memory-only token storage', async()=>{
  const f=fixture();
  const authorize = new URL(await f.session.begin(config));
  const pending = JSON.parse(f.storage.getItem(PKCE_KEY));
  const expected = Buffer.from(await webcrypto.subtle.digest('SHA-256',new TextEncoder().encode(pending.verifier))).toString('base64url');
  assert.equal(authorize.searchParams.get('code_challenge'),expected);
  assert.equal(authorize.searchParams.get('code_challenge_method'),'S256');
  assert.equal(authorize.searchParams.get('response_type'),'code');
  assert.notEqual(pending.state,pending.verifier);
  assert.equal(await f.session.complete(config,config.redirect_uri+'?code=one-use-code&state='+pending.state,f.scrub),true);
  assert.equal(f.session.bearer(),'opaque-token');
  assert.equal(f.values.size,0);
  assert.deepEqual(f.clean,[config.redirect_uri]);
  assert.equal(f.requests[0].options.credentials,'omit');
  assert.equal(f.requests[0].options.body.get('code_verifier'),pending.verifier);
  assert.equal(f.requests[0].options.body.has('client_secret'),false);
});

test('rejects state mismatch before token exchange and consumes the pending verifier',async()=>{
  const f=fixture(); await callback(f);
  await assert.rejects(()=>f.session.complete(config,config.redirect_uri+'?code=code&state=wrong',f.scrub));
  assert.equal(f.requests.length,0); assert.equal(f.values.size,0); assert.equal(f.clean.length,1);
});

test('rejects expired state, changed client config and duplicate callback values',async()=>{
  for(const mode of ['expired','client','duplicate']) {
    const f=fixture();let url=await callback(f);let current=config;
    if(mode==='expired') f.setTime(1000+PKCE_TTL_MS+1);
    if(mode==='client') current={...config,client_id:'other-client'};
    if(mode==='duplicate') url+='&code=second';
    await assert.rejects(()=>f.session.complete(current,url,f.scrub));
    assert.equal(f.requests.length,0);
  }
});

test('callback cannot be replayed; expired memory token and logout clear state',async()=>{
  const f=fixture(), url=await callback(f);
  await f.session.complete(config,url,f.scrub);
  await assert.rejects(()=>f.session.complete(config,url,f.scrub));
  f.setTime(302000); assert.throws(()=>f.session.bearer());
  const logout = new URL(f.session.logout(config));
  assert.equal(logout.searchParams.get('logout_uri'),config.logout_uri);
  assert.equal(f.values.size,0); assert.equal(f.session.token,'');
});

test('OAuth error is scrubbed without exposing its text or making a token request',async()=>{
  const f=fixture();const url=new URL(await callback(f));url.searchParams.set('error','private-provider-detail');
  await assert.rejects(()=>f.session.complete(config,url.href,f.scrub),/login_failed/);
  assert.equal(f.clean[0],config.redirect_uri);assert.equal(f.requests.length,0);
});
