import test from 'node:test';
import assert from 'node:assert/strict';
import {mountOrganizations, organizationPath} from '../../src/kirocrew_custom/saas/web/organizations.mjs';

class Element {
  constructor(tag, document) { this.tag = tag; this.ownerDocument = document; this.children = []; this.value = ''; this.textContent = ''; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  contains(child) { return this === child || this.children.some(node => node.contains(child)); }
  reset() { for (const input of descendants(this, 'input')) input.value = ''; }
}
function descendants(host, tag) { return host.children.flatMap(child => [...(child.tag === tag ? [child] : []), ...descendants(child, tag)]); }
function setup(administrator = true) {
  const document = {createElement: tag => new Element(tag, document)};
  const host = document.createElement('section'), calls = [], pending = [];
  let answer = {agents: [{resource_id: '<script>unsafe</script>', space_id: 'private'}]};
  const request = async (...args) => { calls.push(args); return answer; };
  const run = operation => { const promise = operation(); pending.push(promise); return promise; };
  const company = {company_id: 'company-a', active: true, administrator};
  mountOrganizations(host, company, request, run);
  return {host, calls, company, request, run, pending, setAnswer: value => { answer = value; }};
}
async function submit(context, index) {
  descendants(context.host, 'form')[index].onsubmit({preventDefault() {}});
  await context.pending.at(-1);
}

test('organization URLs cannot traverse or escape company scope', () => {
  assert.equal(organizationPath('company-a', 'hr'), '/companies/company-a/organizations/hr');
  for (const value of ['', '../other', 'hr/agents', 'hr?company=b', 'a#b']) {
    assert.throws(() => organizationPath('company-a', value));
    assert.throws(() => organizationPath(value, 'hr'));
  }
});

test('create nested organization and assign/revoke membership in selected scope', async () => {
  const c = setup(), inputs = descendants(c.host, 'input');
  inputs[1].value = 'recruiting'; inputs[2].value = 'Recruiting'; inputs[3].value = 'hr';
  await submit(c, 1);
  assert.deepEqual(c.calls[0], ['/companies/company-a/organizations/recruiting', {
    method: 'PUT', body: {name: 'Recruiting', parent_id: 'hr'},
  }]);
  assert.equal(inputs[0].value, 'recruiting');
  inputs[4].value = 'verified-subject'; inputs[5].value = 'readers, editors'; inputs[6].checked = false;
  await submit(c, 2);
  assert.deepEqual(c.calls[1], ['/companies/company-a/organizations/recruiting/members', {
    method: 'PUT', body: {subject: 'verified-subject', active: false, groups: ['readers', 'editors'], role: 'member'},
  }]);
});

test('root creation sends a null parent and does not silently grant membership', async () => {
  const c = setup(), inputs = descendants(c.host, 'input');
  inputs[1].value = 'finance'; inputs[2].value = 'Finance';
  await submit(c, 1);
  assert.equal(c.calls.length, 1);
  assert.equal(c.calls[0][1].body.parent_id, null);
});

test('ordinary members get discovery without management forms; customer text is inert', async () => {
  const c = setup(false);
  assert.equal(descendants(c.host, 'form').length, 2);
  assert.equal(descendants(c.host, 'form')[1].hidden, true);
  await submit(c, 0);
  assert.equal(c.calls[0][0], '/companies/company-a/organizations/root/agents');
  assert.equal(descendants(c.host, 'li')[0].textContent, '<script>unsafe</script> · private');
  assert.equal(descendants(c.host, 'script').length, 0);
  descendants(c.host, 'input')[0].oninput();
  assert.equal(descendants(c.host, 'li').length, 0);
});

test('company switches clear old data and stale in-flight results stay detached', async () => {
  const c = setup();
  await submit(c, 0);
  assert.equal(descendants(c.host, 'li').length, 1);
  let finish;
  const deferred = () => new Promise(resolve => { finish = resolve; });
  mountOrganizations(c.host, c.company, deferred, c.run);
  descendants(c.host, 'form')[0].onsubmit({preventDefault() {}});
  mountOrganizations(c.host, {...c.company, company_id: 'company-b'}, c.request, c.run);
  finish({agents: [{resource_id: 'company-a-secret', space_id: 'space'}]});
  await c.pending.at(-1);
  assert.equal(descendants(c.host, 'li').length, 0);
  await submit(c, 0);
  assert.equal(c.calls.at(-1)[0], '/companies/company-b/organizations/root/agents');
  mountOrganizations(c.host, {...c.company, active: false}, c.request, c.run);
  assert.equal(c.host.hidden, true);
  assert.equal(c.host.children.length, 0);
});

test('organization admin permission lookup enables only exact-organization member administration', async () => {
  const c = setup(false);
  c.setAnswer({role: 'organization_admin', manage_members: true, manage_administrators: false});
  const button = descendants(c.host, 'button').find(node => node.textContent === 'Load organization permissions');
  button.onclick(); await c.pending.at(-1);
  const forms = descendants(c.host, 'form');
  assert.equal(forms[1].hidden, false);
  const select = descendants(c.host, 'select')[0];
  assert.equal(select.children[1].disabled, true);
  descendants(c.host, 'input')[0].value = 'sibling';
  descendants(c.host, 'input')[0].oninput();
  assert.equal(forms[1].hidden, true);
  c.setAnswer({role: null, manage_members: false, manage_administrators: false});
  button.onclick(); await c.pending.at(-1);
  assert.equal(forms[1].hidden, true);
  assert.equal(c.calls.at(-1)[0], '/companies/company-a/organizations/sibling/permissions');
});
