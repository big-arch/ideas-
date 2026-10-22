// Telegram third-party validation: no bot token is deployed to the cloud.
export const TELEGRAM_KEY = 'e7bf03a2fa4602af4580703d88dda5bb59f32ed8b02a56c187fe7d34caed242d';
export async function telegramUser(raw, botId, ownerId, now = Date.now(), publicKey = TELEGRAM_KEY) {
  if (typeof raw !== 'string' || !raw || raw.length > 16000) throw Error('auth');
  const params = new URLSearchParams(raw), seen = new Set();
  for (const [key] of params) { if (seen.has(key)) throw Error('auth'); seen.add(key); }
  const stamp = Number(params.get('auth_date'));
  if (!Number.isInteger(stamp) || stamp > now / 1000 + 30 || now / 1000 - stamp > 3600) throw Error('expired');
  const signature = params.get('signature') || '';
  if (!/^[A-Za-z0-9_-]{86}={0,2}$/.test(signature)) throw Error('auth');
  const pairs = [...params].filter(([key]) => !['signature', 'hash'].includes(key)).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0);
  const message = `${botId}:WebAppData\n` + pairs.map(([k, v]) => `${k}=${v}`).join('\n');
  const key = await crypto.subtle.importKey('raw', Uint8Array.from(publicKey.match(/../g).map(x => parseInt(x, 16))), 'Ed25519', false, ['verify']);
  const bytes = Uint8Array.from(atob(signature.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - signature.length % 4) % 4)), c => c.charCodeAt(0));
  if (!await crypto.subtle.verify('Ed25519', key, bytes, new TextEncoder().encode(message))) throw Error('auth');
  const user = JSON.parse(params.get('user') || '{}');
  if (!Number.isSafeInteger(user.id) || String(user.id) !== String(ownerId)) throw Error('owner');
  return String(user.id);
}

const READ = new Set(['/api/state', '/api/models', '/api/list', '/api/item']);
const WRITE = new Set(['/api/model', '/api/favorite', '/api/meta', '/api/preferences', '/api/search/save', '/api/search/activate', '/api/search/delete', '/api/analyze', '/api/chat']);
export function validRequest(value) {
  if (!value || typeof value.path !== 'string' || value.path.length > 2400 || /[\r\n\\#]/.test(value.path)) return false;
  const url = new URL(value.path, 'https://local.invalid');
  if (url.origin !== 'https://local.invalid' || !value.path.startsWith('/api/')) return false;
  if (value.data === undefined) return READ.has(url.pathname);
  return WRITE.has(value.path) && value.data && typeof value.data === 'object' && !Array.isArray(value.data) && JSON.stringify(value.data).length < 7500;
}
