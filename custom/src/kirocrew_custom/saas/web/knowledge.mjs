import {t} from './i18n.mjs';
import {organizationPath} from './organizations.mjs';

export function documentPath(company, id) {
  if (![company, id].every(value => /^[A-Za-z0-9_-]{1,128}$/.test(value))) throw new Error('invalid_request');
  return '/companies/' + company + '/knowledge/documents/' + id;
}
export async function filePayload(file) {
  if (!file || file.size < 1 || file.size > 1048576) throw new Error('knowledgeFileError');
  const extension = file.name.split('.').at(-1).toLowerCase();
  if (!['txt', 'md', 'pdf'].includes(extension)) throw new Error('knowledgeFileError');
  const bytes = new Uint8Array(await file.arrayBuffer());
  if (bytes.length !== file.size) throw new Error('knowledgeFileError');
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return {extension, data: btoa(binary)};
}

export function mountKnowledge(host, company, enabled, request, run, document = host.ownerDocument) {
  host.replaceChildren(); host.hidden = !company?.active || !enabled;
  if (host.hidden) return;
  const node = (tag, key) => { const result = document.createElement(tag); if (key) result.textContent = t(key); return result; };
  const field = (form, key, type = 'text') => {
    const label = node('label'), input = node('input'); input.type = type;
    input.required = !['checkbox', 'file'].includes(type); input.maxLength = 2000;
    label.append(node('span', key), input); form.append(label); return input;
  };
  const button = (form, key, type = 'submit') => { const result = node('button', key); result.type = type; form.append(result); return result; };
  host.append(node('h2', 'knowledge'), node('p', 'knowledgeBoundary'));
  const upload = node('form'); upload.append(node('h3', 'knowledgeUpload'), node('p', 'knowledgeManagers'));
  const id = field(upload, 'knowledgeId'), title = field(upload, 'knowledgeTitle');
  const org = field(upload, 'organizationId'); org.value = 'root';
  const companyWide = field(upload, 'knowledgeCompanyWide', 'checkbox');
  const children = field(upload, 'knowledgeChildren'); children.required = false;
  upload.append(node('p', 'knowledgeSubtree'));
  companyWide.onchange = () => { org.disabled = companyWide.checked; org.required = !companyWide.checked; children.disabled = companyWide.checked; };
  const file = field(upload, 'knowledgeFile', 'file'); file.accept = '.txt,.md,.pdf'; file.required = true;
  button(upload, 'knowledgeUpload'); host.append(upload);
  const manage = node('form'); manage.append(node('h3', 'knowledgeManage'));
  const existing = field(manage, 'knowledgeId');
  const load = button(manage, 'knowledgeLoad', 'button');
  const state = node('p'); manage.append(state);
  const shared = field(manage, 'knowledgeChildren'); shared.required = false;
  const active = field(manage, 'knowledgeActive', 'checkbox'); active.checked = true;
  button(manage, 'knowledgeSave'); host.append(manage);
  let loaded = null;
  const childIds = input => input.value.trim() ? input.value.split(',').map(value => value.trim()) : [];
  const display = result => {
    loaded = result; existing.value = result.document_id;
    shared.value = result.shared_children.join(', '); shared.disabled = result.organization_id === null;
    active.checked = result.active;
    state.textContent = result.title + ' · ' + (result.organization_id ?? t('knowledgeCompanyWide')) + ' · ' + t('knowledgeIndex') + ': ' + (result.index_status ?? t('knowledgeCheckStatus'));
  };
  existing.oninput = () => { loaded = null; state.textContent = ''; shared.value = ''; };
  upload.onsubmit = event => {
    event.preventDefault();
    const path = documentPath(company.company_id, id.value), selectedFile = file.files?.[0];
    const body = {organization_id: companyWide.checked ? null : org.value, title: title.value, shared_children: companyWide.checked ? [] : childIds(children)};
    run(async () => {
      const payload = await filePayload(selectedFile);
      if (!host.contains(upload)) return;
      const result = await request(path, {method: 'PUT', body: {...body, ...payload}});
      if (host.contains(upload)) display(result);
    }, 'knowledgeSubmitted');
  };
  load.onclick = () => {
    const selected = existing.value; loaded = null; state.textContent = '';
    run(async () => {
      const result = await request(documentPath(company.company_id, selected));
      if (host.contains(manage) && existing.value === selected) display(result);
    });
  };
  manage.onsubmit = event => {
    event.preventDefault();
    run(async () => {
      if (!loaded || loaded.document_id !== existing.value) throw new Error('knowledgeLoadFirst');
      const selected = existing.value;
      const result = await request(documentPath(company.company_id, selected) + '/sharing', {method:'PUT', revision:loaded.revision, body:{shared_children: loaded.organization_id === null ? [] : childIds(shared), active:active.checked}});
      if (host.contains(manage) && existing.value === selected) display(result);
    });
  };
  const preview = node('form'); preview.append(node('h3', 'knowledgePreview'));
  const previewOrg = field(preview, 'organizationId'); previewOrg.value = 'root';
  const query = field(preview, 'knowledgeQuery'); button(preview, 'knowledgeSearch');
  const sources = node('ul'); host.append(preview, sources);
  previewOrg.oninput = query.oninput = () => sources.replaceChildren();
  preview.onsubmit = event => {
    event.preventDefault(); sources.replaceChildren();
    const selected = previewOrg.value, text = query.value;
    run(async () => {
      const result = await request(organizationPath(company.company_id, selected) + '/knowledge/query', {method:'POST', body:{query:text}});
      if (!host.contains(preview) || previewOrg.value !== selected || query.value !== text) return;
      sources.replaceChildren();
      for (const source of result.sources) {
        const item = node('li'), heading = node('strong'), excerpt = node('p');
        heading.textContent = source.title + ' · ' + source.document_id;
        excerpt.textContent = source.excerpt; item.append(heading, excerpt); sources.append(item);
      }
      if (!result.sources.length) sources.append(node('li', 'knowledgeEmpty'));
    }, 'knowledgeResults');
  };
}
