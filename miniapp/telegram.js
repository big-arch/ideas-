'use strict';
const telegram = window.Telegram?.WebApp;
const endpoint = 'https://renbakjudvdnkjvjujti.supabase.co/functions/v1/radar-miniapp';
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let cloudRefreshBusy = false;

async function cloudCall(path, options = {}) {
  if (!telegram?.initData) throw Error('Открой приложение кнопкой «Открыть приложение» в Telegram-боте.');
  const r = await fetch(endpoint + path, { ...options, cache: 'no-store', headers: { 'Content-Type': 'application/json', 'X-Telegram-Init-Data': telegram.initData } });
  const value = await r.json();
  if (!r.ok) throw Error(value.error || 'Сервис временно недоступен');
  return value;
}
async function radarCloudApi(path, data) {
  if (path === '/api/connect' || path === '/api/disconnect') throw Error('Подключение ChatGPT управляется в приложении на твоём компьютере. Уже подключённый аккаунт работает и здесь.');
  const job = await cloudCall('/request', { method: 'POST', body: JSON.stringify({ path, ...(data === undefined ? {} : { data }) }) });
  const until = Date.now() + (path === '/api/analyze' ? 285000 : 100000);
  while (Date.now() < until) {
    await pause(1000);
    const result = await cloudCall('/result?id=' + encodeURIComponent(job.id));
    if (result.status === 'done' || result.status === 'failed') {
      const response = result.response || { code: 500, value: {} };
      if (response.code >= 400) throw Error(response.value.error || 'Не удалось завершить запрос');
      if (path.startsWith('/api/list')) syncLabel('Обновлено ' + new Intl.DateTimeFormat('ru-RU', { timeZone: 'Europe/Moscow', hour: '2-digit', minute: '2-digit' }).format(new Date()) + ' МСК · авто');
      return response.value;
    }
  }
  throw Error('Нет ответа сборщика. Проверь, что на компьютере запущен start.cmd. Действие автоматически не повторяется.');
}
function originalPhoto(id, index = 0) {
  const item = currentDetail?.item?.id === id ? currentDetail.item : items.find(x => x.id === id);
  const value = currentDetail?.item?.id === id ? (currentDetail.detail.photos?.[index] || item?.photo_url) : item?.photo_url;
  try { const u = new URL(value); return u.protocol === 'https:' ? u.href : ''; } catch { return ''; }
}
function syncLabel(text) { const node = document.getElementById('syncStatus'); if (node) node.textContent = text; }
function theme() {
  document.documentElement.style.colorScheme = telegram?.colorScheme || 'light';
  document.body.style.paddingTop = (telegram?.contentSafeAreaInset?.top || 0) + 'px';
}
async function refreshCloud() {
  if (cloudRefreshBusy || document.hidden || typeof state === 'undefined' || !state || analysisRunningFor) return;
  cloudRefreshBusy = true;
  const scroll = document.querySelector('.list')?.scrollTop;
  const count = visibleCount;
  try {
    await loadList();
    visibleCount = count; renderRows();
    if (scroll !== undefined) document.querySelector('.list').scrollTop = scroll;
    if (selected && currentDetail) {
      const detail = await api('/api/item?id=' + encodeURIComponent(selected));
      if (selected === detail.item.id) { currentDetail = detail; renderDetail(); }
    }
  } catch { syncLabel('Не удалось обновить · повторим автоматически'); }
  finally { cloudRefreshBusy = false; }
}
if (telegram) {
  telegram.ready(); telegram.expand(); theme();
  telegram.onEvent('themeChanged', theme);
  telegram.onEvent('contentSafeAreaChanged', theme);
  telegram.onEvent('activated', refreshCloud);
  telegram.BackButton.onClick(() => { document.getElementById('layout').classList.remove('detail-open'); telegram.BackButton.hide(); });
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href^="https:"]');
    if (link) { event.preventDefault(); telegram.openLink(link.href); }
    setTimeout(() => {
      if (document.getElementById('layout').classList.contains('detail-open')) telegram.BackButton.show();
      else telegram.BackButton.hide();
    }, 0);
  });
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshCloud(); });
document.addEventListener('DOMContentLoaded', () => {
  if (telegram?.initData) return;
  document.getElementById('connection').textContent = 'Вход через Telegram';
  syncLabel('Данные доступны только владельцу бота');
  document.getElementById('count').textContent = 'Открой Радар в Telegram';
  document.getElementById('rows').innerHTML = '<div class="empty"><h2>Продолжить в Telegram</h2><p>Открой своего бота и нажми «Открыть приложение». Так Радар подтвердит, что это ты.</p><a href="https://t.me/money_ideas_business_bot" class="primary" style="display:inline-block;padding:12px 16px;border-radius:11px;text-decoration:none">Открыть бота</a></div>';
  for (const id of ['sort','viewFilter','savedSearch','favoriteFilter','settingsButton']) document.getElementById(id).disabled = true;
  document.getElementById('searchEditor').hidden = true;
});
setInterval(refreshCloud, 60000);

