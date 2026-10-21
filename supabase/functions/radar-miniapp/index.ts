import { telegramUser, validRequest } from './auth.mjs';

const ORIGIN = 'https://business-ideas-radar.bimmanager981.chatgpt.site';
const base = Deno.env.get('SUPABASE_URL')!;
const service = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!;
const dbHeaders = { apikey: service, Authorization: `Bearer ${service}`, 'Content-Type': 'application/json', Prefer: 'return=representation' };
async function db(table: string, query = '', method = 'GET', body?: unknown) {
  const r = await fetch(`${base}/rest/v1/${table}${query}`, { method, headers: dbHeaders, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!r.ok) throw Error('storage');
  return r.status === 204 ? [] : await r.json();
}
function reply(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Access-Control-Allow-Origin': ORIGIN, 'Access-Control-Allow-Headers': 'Content-Type, X-Telegram-Init-Data, Authorization', 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS', Vary: 'Origin' } });
}
const digest = async (v: string) => [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(v)))].map(x => x.toString(16).padStart(2, '0')).join('');
const uuid = (v: unknown) => typeof v === 'string' && /^[0-9a-f-]{36}$/.test(v);

Deno.serve(async req => {
  if (req.method === 'OPTIONS') return reply({});
  const path = new URL(req.url).pathname.split('/radar-miniapp')[1] || '/';
  try {
    if (Number(req.headers.get('content-length') || 0) > 2500000) return reply({ error: 'Слишком большой запрос' }, 413);
    const [config] = await db('radar_bridge_config', '?id=eq.business-ideas&select=owner_id,bot_id,key_hash');
    if (!config) return reply({ error: 'Сервис не настроен' }, 503);
    if (path.startsWith('/bridge/')) {
      const secret = req.headers.get('Authorization') || '';
      if (!secret.startsWith('Bearer ') || await digest(secret.slice(7)) !== config.key_hash) return reply({ error: 'Доступ запрещён' }, 401);
      if (req.method !== 'POST') return reply({}, 405);
      const body = await req.json();
      if (path === '/bridge/poll') {
        const old = new Date(Date.now() - 86400000).toISOString();
        await db('radar_bridge_jobs', `?created_at=lt.${old}`, 'DELETE');
        const stale = new Date(Date.now() - 300000).toISOString();
        await db('radar_bridge_jobs', `?status=eq.running&claimed_at=lt.${stale}`, 'PATCH', { status: 'failed', response: { code: 504, value: { error: 'Разбор не завершён. Попробуй ещё раз.' } }, finished_at: new Date().toISOString() });
        await db('radar_bridge_jobs', `?status=eq.pending&created_at=lt.${new Date(Date.now() - 90000).toISOString()}`, 'PATCH', { status: 'failed', response: { code: 503, value: { error: 'Компьютер со сборщиком недоступен. Запусти start.cmd.' } }, finished_at: new Date().toISOString() });
        const jobs = await db('radar_bridge_jobs', '?status=eq.pending&order=created_at.asc&limit=6');
        const claimed = [];
        for (const job of jobs) {
          const rows = await db('radar_bridge_jobs', `?id=eq.${job.id}&status=eq.pending`, 'PATCH', { status: 'running', claimed_at: new Date().toISOString() });
          if (rows.length) claimed.push({ id: job.id, request: job.request });
        }
        return reply({ jobs: claimed });
      }
      if (path === '/bridge/result' && uuid(body.id) && body.response && JSON.stringify(body.response).length < 2200000) {
        await db('radar_bridge_jobs', `?id=eq.${body.id}&status=eq.running`, 'PATCH', { status: 'done', response: body.response, finished_at: new Date().toISOString() });
        return reply({ ok: true });
      }
      return reply({}, 400);
    }
    let owner;
    try { owner = await telegramUser(req.headers.get('X-Telegram-Init-Data'), config.bot_id, config.owner_id); }
    catch { return reply({ error: 'Открой приложение кнопкой в своём Telegram-боте. Если оно уже открыто — закрой и открой заново.' }, 401); }
    if (path === '/request' && req.method === 'POST') {
      const text = await req.text();
      if (text.length > 10000) return reply({}, 413);
      const body = JSON.parse(text);
      if (!validRequest(body)) return reply({ error: 'Действие недоступно' }, 400);
      const pending = await db('radar_bridge_jobs', `?owner_id=eq.${owner}&status=in.(pending,running)&select=id&limit=21`);
      if (pending.length >= 20) return reply({ error: 'Дождись завершения текущих запросов' }, 429);
      const [job] = await db('radar_bridge_jobs', '', 'POST', { owner_id: owner, request: body });
      return reply({ id: job.id }, 202);
    }
    if (path === '/result' && req.method === 'GET') {
      const id = new URL(req.url).searchParams.get('id');
      if (!uuid(id)) return reply({}, 400);
      const [job] = await db('radar_bridge_jobs', `?id=eq.${id}&owner_id=eq.${owner}&select=status,response,created_at`);
      if (!job) return reply({}, 404);
      if (job.status === 'pending' && Date.now() - Date.parse(job.created_at) > 90000) return reply({ status: 'failed', response: { code: 503, value: { error: 'Компьютер со сборщиком недоступен. Запусти start.cmd.' } } });
      return reply(job);
    }
    return reply({}, 404);
  } catch { return reply({ error: 'Не удалось завершить запрос. Повтори позже.' }, 500); }
});
