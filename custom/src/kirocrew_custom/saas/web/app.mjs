import {mountKnowledge} from './knowledge.mjs';
import {LoginSession} from './oauth.mjs';
import {t} from './i18n.mjs';
import {mountOrganizations} from './organizations.mjs';
const byId = id => document.getElementById(id);
const organizationHost = document.createElement('section');
organizationHost.className = 'organization-panel'; organizationHost.hidden = true;
byId('workspace').append(organizationHost);
const knowledgeHost = document.createElement('section');
knowledgeHost.className = 'organization-panel'; knowledgeHost.hidden = true;
byId('workspace').append(knowledgeHost);
function clearOrganizations() { knowledgeHost.replaceChildren(); knowledgeHost.hidden = true; organizationHost.replaceChildren(); organizationHost.hidden = true; }
document.querySelectorAll('[data-i18n]').forEach(element => { element.textContent = t(element.dataset.i18n); });
document.title = t('title');
const session = new LoginSession({storage: sessionStorage, crypto, fetch: window.fetch.bind(window)});
let config, companies = [], selected = null, superman = false, after = null;
const pending = new Map();
function status(key) { byId('status').textContent = t(key); }
function reset() { clearOrganizations(); companies = []; selected = null; after = null; superman = false; byId('workspace').hidden = true; byId('logout').hidden = true; byId('login').hidden = false; }
async function request(path, {method = 'GET', body, revision} = {}) {
  const headers = {Authorization: 'Bearer ' + session.bearer()};
  if (revision) headers['If-Match'] = '"' + revision + '"';
  const key = method + path + JSON.stringify(body);
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    if (!pending.has(key)) pending.set(key, crypto.randomUUID());
    headers['Idempotency-Key'] = pending.get(key);
  }
  const response = await fetch(path, {method, headers, body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store', credentials: 'omit', signal: AbortSignal.timeout(30000)});
  const payload = await response.json();
  if (!response.ok) {
    if (response.status === 409 && method === 'PUT') pending.delete(key);
    if (response.status === 401) { session.clear(); reset(); }
    throw new Error(payload.error || 'error');
  }
  pending.delete(key);
  return payload;
}
function select(company) {
  mountKnowledge(knowledgeHost, company, config?.knowledge_enabled, request, run);
  mountOrganizations(organizationHost, company, request, run);
  selected = company;
  byId('choose').hidden = true;
  byId('selected').textContent = company.name;
  const owner = superman || company.role === 'company_owner';
  byId('settings').hidden = !owner;
  byId('settings').elements.active.disabled = !superman;
  byId('company-role').hidden = !company.administrator;
  for (const option of byId('company-role').elements.role.options) option.disabled = option.value !== 'member' && !owner;
  for (const option of byId('invite').elements.role.options) option.disabled = option.value === 'company_admin' && !owner;
  byId('company-role').elements.role.value = 'member';
  byId('invite').elements.role.value = 'member';
  byId('role').textContent = superman ? t('superman') : t(company.role === 'member' ? 'member_role' : company.role);
  byId('settings').elements.name.value = company.name;
  byId('settings').elements.active.checked = company.active;
  byId('invite').hidden = !company.administrator || !company.active;
}
async function load(append = false) {
  const page = await request('/me' + (append && after ? '?after=' + encodeURIComponent(after) : ''));
  if (!append || page.superman !== superman) { clearOrganizations(); companies = []; selected = null; byId('settings').hidden = true; byId('invite').hidden = true; byId('company-role').hidden = true; byId('choose').hidden = false; }
  superman = page.superman;
  companies.push(...page.companies);
  after = page.next;
  byId('role').textContent = t(superman ? 'superman' : 'member');
  byId('create').hidden = !superman;
  byId('more').hidden = !after;
  byId('companies').replaceChildren();
  for (const company of companies) {
    const item = document.createElement('li'), button = document.createElement('button');
    button.textContent = company.name + (company.active ? '' : ' · ' + t('suspended'));
    button.onclick = () => select(company);
    item.append(button); byId('companies').append(item);
  }
  if (!companies.length) status('empty');
  byId('workspace').hidden = false; byId('logout').hidden = false; byId('login').hidden = true;
}
async function run(operation, key = 'saved') {
  status('working');
  document.querySelectorAll('button').forEach(button => { button.disabled = true; });
  try { await operation(); status(key); }
  catch (error) { status(error.message); }
  finally { document.querySelectorAll('button').forEach(button => { button.disabled = false; }); }
}
byId('login').onclick = () => run(async () => { location.assign(await session.begin(config)); });
byId('logout').onclick = () => { const url = session.logout(config); reset(); location.assign(url); };
byId('reload').onclick = () => run(() => load());
byId('more').onclick = () => run(() => load(true));
byId('create').onsubmit = event => { event.preventDefault(); run(async () => {
  const result = await request('/platform/companies', {method:'POST', body:{name:byId('create').elements.name.value}});
  byId('create').reset(); await load(); select(companies.find(company => company.company_id === result.company_id) ?? {...result, administrator:true, role:superman ? 'superman' : 'company_owner'});
}); };
byId('settings').onsubmit = event => { event.preventDefault(); run(async () => {
  const result = await request('/companies/' + selected.company_id, {method:'PUT', revision:selected.revision, body:{name:byId('settings').elements.name.value, active:byId('settings').elements.active.checked}});
  await load(); select(companies.find(company => company.company_id === result.company_id) ?? {...result, administrator:true, role:superman ? 'superman' : 'company_owner'});
}); };
byId('invite').onsubmit = event => { event.preventDefault(); run(async () => {
  const invitation = await request('/companies/' + selected.company_id + '/invitations', {method:'POST', body:{email:byId('invite').elements.email.value, role:byId('invite').elements.role.value}});
  byId('company-role').elements.subject.value = invitation.subject; byId('invite').reset();
}, 'invited'); };
byId('company-role').onsubmit = event => { event.preventDefault(); run(async () => {
  const form = byId('company-role');
  await request('/companies/' + selected.company_id + '/members/' + encodeURIComponent(form.elements.subject.value), {method:'PUT', revision:selected.revision, body:{role:form.elements.role.value, active:form.elements.active.checked}});
  await load();
}); };
await run(async () => {
  const response = await fetch('/auth/config', {cache:'no-store', credentials:'omit', signal:AbortSignal.timeout(15000)});
  if (!response.ok) throw new Error('service_unavailable');
  config = await response.json();
  if (await session.complete(config, location.href, clean => history.replaceState(null, '', clean))) await load();
}, 'choose');
