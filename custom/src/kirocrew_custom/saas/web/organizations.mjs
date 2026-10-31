import {t} from './i18n.mjs';

const identifier = /^[A-Za-z0-9_-]{1,128}$/;
export function organizationPath(company, organization) {
  if (!identifier.test(company) || !identifier.test(organization)) throw new Error('invalid_request');
  return '/companies/' + company + '/organizations/' + organization;
}

export function mountOrganizations(host, company, request, run, document = host.ownerDocument) {
  host.replaceChildren();
  host.hidden = !company?.active;
  if (!company?.active) return;
  const element = (tag, key) => {
    const node = document.createElement(tag);
    if (key) node.textContent = t(key);
    return node;
  };
  const field = (form, key, {required = true, value = '', id = false} = {}) => {
    const label = element('label'), input = element('input');
    label.append(element('span', key));
    input.required = required; input.value = value; input.maxLength = 128;
    if (id) input.pattern = '[A-Za-z0-9_-]{1,128}';
    label.append(input); form.append(label);
    return input;
  };
  const button = (form, key) => { const node = element('button', key); form.append(node); };
  const current = element('form'), list = element('ul');
  host.append(element('h2', 'organizations'), element('p', 'organizationBoundary'));
  const organization = field(current, 'organizationId', {value: 'root', id: true});
  button(current, 'loadAgents');
  current.onsubmit = event => {
    event.preventDefault(); list.replaceChildren();
    const selected = organization.value;
    run(async () => {
      const result = await request(organizationPath(company.company_id, selected) + '/agents');
      if (!host.contains(current) || organization.value !== selected) return;
      list.replaceChildren();
      for (const agent of result.agents) {
        const item = element('li');
        item.textContent = agent.resource_id + ' · ' + agent.space_id;
        list.append(item);
      }
      if (!result.agents.length) list.append(element('li', 'noAgents'));
    }, 'agentsLoaded');
  };
  organization.oninput = () => list.replaceChildren();
  host.append(current, list);

  const create = element('form');
  create.append(element('h3', 'createOrganization'));
  const id = field(create, 'newOrganizationId', {id: true});
  const name = field(create, 'organizationName');
  const parent = field(create, 'parentOrganization', {required: false, id: true});
  create.append(element('p', 'parentOrganizationHint'));
  button(create, 'createOrganization');
  create.onsubmit = event => {
    event.preventDefault();
    const newId = id.value;
    run(async () => {
      await request(organizationPath(company.company_id, newId), {
        method: 'PUT', body: {name: name.value, parent_id: parent.value || null},
      });
      if (!host.contains(create)) return;
      organization.value = newId; list.replaceChildren(); create.reset();
    }, 'organizationCreated');
  };
  const currentRole = element('p'); host.append(currentRole);
  const membership = element('form');
  membership.hidden = !company.administrator;
  membership.append(element('h3', 'organizationMembership'));
  const subject = field(membership, 'memberSubject');
  const groups = field(membership, 'organizationGroups', {required: false});
  const activeLabel = element('label'), active = element('input');
  active.type = 'checkbox'; active.checked = true;
  activeLabel.append(active, element('span', 'membershipActive')); membership.append(activeLabel);
  const roleLabel = element('label'), role = element('select');
  roleLabel.append(element('span', 'role'));
  for (const value of ['member', 'organization_admin']) {
    const option = element('option', value === 'member' ? 'member_role' : value);
    option.value = value; option.disabled = value === 'organization_admin' && !company.administrator;
    role.append(option);
  }
  role.value = 'member'; roleLabel.append(role); membership.append(roleLabel);
  const access = element('button', 'loadPermissions'); access.type = 'button'; current.append(access);
  access.onclick = () => {
    const selected = organization.value;
    run(async () => {
      const permissions = await request(organizationPath(company.company_id, selected) + '/permissions');
      if (!host.contains(membership) || organization.value !== selected) return;
      currentRole.textContent = permissions.role ? t(permissions.role === 'member' ? 'member_role' : permissions.role) : (company.administrator ? t(company.role ?? 'company_admin') : t('noOrganizationMembership'));
      membership.hidden = !permissions.manage_members;
      role.children[1].disabled = !permissions.manage_administrators;
      role.value = 'member';
    });
  };
  const clear = organization.oninput;
  organization.oninput = () => { clear(); currentRole.textContent = ''; if (!company.administrator) membership.hidden = true; role.value = 'member'; };
  button(membership, 'saveMembership');
  membership.onsubmit = event => {
    event.preventDefault();
    const selected = organization.value;
    run(async () => {
      await request(organizationPath(company.company_id, selected) + '/members', {
        method: 'PUT', body: {subject: subject.value, active: active.checked,
          groups: groups.value.trim() ? groups.value.split(',').map(value => value.trim()) : [], role: role.value},
      });
      if (host.contains(membership)) list.replaceChildren();
    }, 'membershipSaved');
  };
  if (company.administrator) host.append(create);
  host.append(membership);
}
