"""Build only public Mini App assets; no environment or runtime files are copied."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parent / 'garage_radar'
OUT = ROOT / 'dist'
OUT.mkdir(exist_ok=True)
html = (SOURCE / 'ui.html').read_text(encoding='utf-8')
html = html.replace('<head>', '<head><script src="https://telegram.org/js/telegram-web-app.js"></script>')
html = html.replace('Локальное приложение', 'Telegram Mini App')
html = html.replace('ChatGPT и настройки', 'ИИ и настройки')
html = html.replace('<p id="count">', '<p id="syncStatus" class="muted" role="status">Подключаю сборщик…</p><p id="count">')
html = html.replace('<button id="connect" class="primary">Continue with ChatGPT</button>', '<button id="connect" class="primary" disabled>Подключение ChatGPT — на компьютере</button>')
html = html.replace('<button id="disconnect">Отключить ChatGPT</button>', '<button id="disconnect" hidden>Отключить ChatGPT</button>')
html = html.replace('Радар работает на этом компьютере. Для Telegram-приложения на телефоне потребуется отдельное HTTPS-размещение.', 'Mini App работает внутри Telegram. Свежие объявления и новые разборы поступают, пока на компьютере запущен сборщик. Подключение ChatGPT хранится только на компьютере.')
html = html.replace('<script src="/ui-workspace.js"', '<script src="/telegram.js" defer></script><script src="/ui-workspace.js"')
(OUT / 'index.html').write_text(html, encoding='utf-8')
js = (SOURCE / 'ui.js').read_text(encoding='utf-8')
start = js.index('async function api(')
end = js.index('\nfunction toast', start)
js = js[:start] + 'async function api(path,data){return radarCloudApi(path,data)}' + js[end:]
start = js.index('function photo(')
end = js.index('\nfunction plain', start)
js = js[:start] + 'function photo(id,index=0){return originalPhoto(id,index)}' + js[end:]
(OUT / 'ui.js').write_text(js, encoding='utf-8')
for name in ('ui.css', 'ui-workspace.js', 'ui-chat.js'):
    (OUT / name).write_bytes((SOURCE / name).read_bytes())
(OUT / 'telegram.js').write_bytes((ROOT / 'telegram.js').read_bytes())
print('Built Telegram Mini App (6 public assets)')
