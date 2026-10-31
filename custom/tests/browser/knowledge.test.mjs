import test from 'node:test';
import assert from 'node:assert/strict';
import {mountKnowledge, documentPath, filePayload} from '../../src/kirocrew_custom/saas/web/knowledge.mjs';
class Element {
  constructor(tag, document) { this.tag = tag; this.ownerDocument = document; this.children = []; this.value = ''; this.textContent = ''; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  contains(child) { return this === child || this.children.some(node => node.contains(child)); }
}
function all(host, tag) { return host.children.flatMap(child => [...(child.tag === tag ? [child] : []), ...all(child, tag)]); }
const record = {document_id:'handbook', title:'Handbook', organization_id:'hr', shared_children:['recruiting'], active:true, revision:'r1', index_status:'INDEXED'};
function setup() {
  const document = {createElement: tag => new Element(tag, document)}, host = document.createElement('section');
  const company = {company_id:'acme', active:true}, calls = [], pending = [];
  let result = record;
  const request = async (...args) => { calls.push(args); return result; };
  const run = action => { const task = action(); pending.push(task); return task; };
  mountKnowledge(host, company, true, request, run);
  return {host, company, calls, pending, request, run, result: value => { result = value; }};
}
async function submit(c, index) { all(c.host, 'form')[index].onsubmit({preventDefault(){}}); await c.pending.at(-1); }
const file = {name:'policy.md', size:6, arrayBuffer:async () => new TextEncoder().encode('Policy').buffer};
test('file payload preserves UTF8 bytes and rejects unsupported sizes/extensions', async () => {
  assert.deepEqual(await filePayload(file), {extension:'md', data:'UG9saWN5'});
  for (const candidate of [null, {...file,size:0}, {...file,size:1048577}, {...file,name:'a.exe'}]) await assert.rejects(filePayload(candidate));
  for (const id of ['../bad', 'a/b', 'a?b']) assert.throws(() => documentPath('acme', id));
});
test('upload defaults local, selected child shares are explicit, companywide clears shares', async () => {
  const c = setup(), inputs = all(c.host, 'input');
  inputs[0].value='handbook'; inputs[1].value='Handbook'; inputs[2].value='hr'; inputs[4].value='recruiting'; inputs[5].files=[file];
  await submit(c, 0);
  assert.equal(c.calls[0][0], '/companies/acme/knowledge/documents/handbook');
  assert.deepEqual(c.calls[0][1].body, {organization_id:'hr', title:'Handbook', shared_children:['recruiting'],extension:'md', data:'UG9saWN5'});
  inputs[3].checked=true; inputs[3].onchange(); await submit(c,0);
  assert.equal(c.calls[1][1].body.organization_id, null); assert.deepEqual(c.calls[1][1].body.shared_children, []);
  assert.equal(inputs[2].disabled, true);
});
test('sharing requires loaded revision and preserves explicit disable action', async () => {
  const c=setup(), inputs=all(c.host,'input'); inputs[6].value='handbook';
  await assert.rejects(submit(c,1), /knowledgeLoadFirst/);
  all(c.host,'button').find(b => b.textContent==='Load document status').onclick(); await c.pending.at(-1);
  inputs[7].value=''; inputs[8].checked=false; await submit(c,1);
  assert.deepEqual(c.calls.at(-1), ['/companies/acme/knowledge/documents/handbook/sharing', {method:'PUT', revision:'r1', body:{shared_children:[],active:false}}]);
  inputs[6].value='another'; inputs[6].oninput(); await assert.rejects(submit(c,1), /knowledgeLoadFirst/);
});
test('preview scopes organization and renders retrieved content as inert text', async () => {
  const c=setup(), inputs=all(c.host,'input'); inputs[9].value='interns'; inputs[10].value='Policy';
  c.result({sources:[{title:'<script>bad</script>', document_id:'handbook', excerpt:'<img src=x onerror=bad()> '}]});
  await submit(c,2);
  assert.deepEqual(c.calls[0], ['/companies/acme/organizations/interns/knowledge/query', {method:'POST', body:{query:'Policy'}}]);
  assert.equal(all(c.host,'strong')[0].textContent,'<script>bad</script> · handbook');
  assert.equal(all(c.host,'script').length,0);
  inputs[9].oninput(); assert.equal(all(c.host,'li').length,0);
});
test('company switches discard pending upload and preview responses; disabled feature stays hidden', async () => {
  const c=setup(); let resolve;
  mountKnowledge(c.host,c.company,true,() => new Promise(done => {resolve=done;}),c.run);
  const inputs=all(c.host,'input'); inputs[10].value='policy';
  all(c.host,'form')[2].onsubmit({preventDefault(){}});
  mountKnowledge(c.host,{company_id:'other',active:true},true,c.request,c.run);
  resolve({sources:[{title:'secret',document_id:'d',excerpt:'private'}]}); await c.pending.at(-1);
  assert.equal(all(c.host,'li').length,0);
  let readFile;
  const fresh=all(c.host,'input'); fresh[0].value='handbook'; fresh[1].value='title'; fresh[5].files=[{...file,arrayBuffer:() => new Promise(done => {readFile=done;})}];
  all(c.host,'form')[0].onsubmit({preventDefault(){}});
  mountKnowledge(c.host,c.company,false,c.request,c.run);
  readFile(new TextEncoder().encode('Policy').buffer); await c.pending.at(-1);
  assert.equal(c.calls.length,0); assert.equal(c.host.hidden,true); assert.equal(c.host.children.length,0);
});
