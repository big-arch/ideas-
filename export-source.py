"""Prepare an explicit, secret-checked source manifest for the user's repository."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
paths = [p for p in ROOT.iterdir() if p.is_file() and (p.suffix in {'.py','.ps1','.cmd','.md','.toml'} or p.name in {'.gitignore','.env.example'})]
for name in ('garage_radar','tests','supabase','.github'):
    paths.extend(p for p in (ROOT/name).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix in {'.py','.js','.mjs','.ts','.css','.html','.sql','.yml'})
paths.extend(ROOT/name for name in ('miniapp/build.py','miniapp/telegram.js','miniapp/.openai/hosting.json'))
secrets = []
for line in (ROOT/'.env').read_text(encoding='utf-8-sig').splitlines():
    key, _, value = line.partition('=')
    if key in {'TELEGRAM_BOT_TOKEN','TELEGRAM_PAIRING_CODE'} and len(value.strip()) >= 8: secrets.append(value.strip())
secrets.append(json.loads((ROOT/'runtime/cloud-bridge.json').read_text())['key'])
tree = []
for path in sorted(set(paths)):
    content = path.read_text(encoding='utf-8-sig')
    if any(secret in content for secret in secrets): raise RuntimeError('Secret found in source export: '+path.name)
    tree.append({'path':path.relative_to(ROOT).as_posix(),'mode':'100644','type':'blob','content':content})
(ROOT/'runtime/export-source.json').write_text(json.dumps(tree,ensure_ascii=False),encoding='utf-8')
print('Prepared',len(tree),'source files; credential check passed')
