export const PKCE_KEY = 'kirocrew.pkce';
export const PKCE_TTL_MS = 10 * 60 * 1000;
const TOKEN_TIMEOUT_MS = 15000;
const base64url = bytes => btoa(String.fromCharCode(...bytes)).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '');

export class LoginSession {
  constructor({storage, crypto, fetch, now = Date.now}) {
    Object.assign(this, {storage, crypto, fetch, now});
    this.token = '';
    this.expiresAt = 0;
  }
  async begin(config) {
    this.clear();
    const verifier = base64url(this.crypto.getRandomValues(new Uint8Array(32)));
    const state = base64url(this.crypto.getRandomValues(new Uint8Array(32)));
    const challenge = base64url(new Uint8Array(await this.crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))));
    this.storage.setItem(PKCE_KEY, JSON.stringify({verifier, state, created: this.now(), client: config.client_id, redirect: config.redirect_uri, endpoint: config.token_endpoint}));
    const url = new URL(config.authorization_endpoint);
    url.search = new URLSearchParams({response_type: 'code', client_id: config.client_id, redirect_uri: config.redirect_uri, scope: config.scopes.join(' '), state, code_challenge: challenge, code_challenge_method: 'S256'});
    return url.href;
  }
  async complete(config, callback, scrub) {
    const url = new URL(callback);
    if (!url.searchParams.has('code') && !url.searchParams.has('error')) return false;
    scrub(url.origin + url.pathname);
    const raw = this.storage.getItem(PKCE_KEY);
    this.storage.removeItem(PKCE_KEY);
    const pending = raw ? JSON.parse(raw) : null;
    if (!pending || pending.state !== url.searchParams.get('state') || url.searchParams.has('error') || this.now() - pending.created > PKCE_TTL_MS || pending.created > this.now() || pending.client !== config.client_id || pending.redirect !== config.redirect_uri || pending.endpoint !== config.token_endpoint || url.origin + url.pathname !== new URL(config.redirect_uri).origin + new URL(config.redirect_uri).pathname) {
      throw new Error('login_failed');
    }
    const code = url.searchParams.get('code');
    if (!code || url.searchParams.getAll('code').length !== 1 || url.searchParams.getAll('state').length !== 1) throw new Error('login_failed');
    const response = await this.fetch(config.token_endpoint, {method: 'POST', credentials: 'omit', cache: 'no-store', signal: AbortSignal.timeout(TOKEN_TIMEOUT_MS), headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: new URLSearchParams({grant_type: 'authorization_code', client_id: config.client_id, redirect_uri: config.redirect_uri, code, code_verifier: pending.verifier})});
    if (!response.ok) throw new Error('login_failed');
    const tokens = await response.json();
    if (typeof tokens.access_token !== 'string' || !tokens.access_token || tokens.token_type?.toLowerCase() !== 'bearer' || !Number.isFinite(tokens.expires_in) || tokens.expires_in <= 0) throw new Error('login_failed');
    this.token = tokens.access_token;
    this.expiresAt = this.now() + tokens.expires_in * 1000;
    return true;
  }
  bearer() {
    if (!this.token || this.now() >= this.expiresAt) { this.clear(); throw new Error('authentication_required'); }
    return this.token;
  }
  clear() { this.token = ''; this.expiresAt = 0; this.storage.removeItem(PKCE_KEY); }
  logout(config) {
    this.clear();
    const url = new URL(config.logout_endpoint);
    url.search = new URLSearchParams({client_id: config.client_id, logout_uri: config.logout_uri});
    return url.href;
  }
}
