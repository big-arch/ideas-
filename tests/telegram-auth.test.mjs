import { test } from 'node:test';
import assert from 'node:assert/strict';
import { telegramUser, validRequest } from '../supabase/functions/radar-miniapp/auth.mjs';

const keys = await crypto.subtle.generateKey('Ed25519', true, ['sign','verify']);
const key = Buffer.from(await crypto.subtle.exportKey('raw', keys.publicKey)).toString('hex');
const now = Date.now();
async function signed({ stamp = Math.floor(now/1000), user = 123, bot = '8897925046' } = {}) {
  const params = new URLSearchParams({ auth_date:String(stamp), user:JSON.stringify({id:user}), query_id:'test' });
  const message = bot + ':WebAppData\n' + [...params].sort(([a],[b])=>a.localeCompare(b)).map(([k,v])=>`${k}=${v}`).join('\n');
  const signature = await crypto.subtle.sign('Ed25519', keys.privateKey, new TextEncoder().encode(message));
  params.set('signature', Buffer.from(signature).toString('base64url'));
  return params.toString();
}
test('valid Telegram signature grants only the configured owner',async()=>{
  assert.equal(await telegramUser(await signed(),'8897925046','123',now,key),'123');
  await assert.rejects(()=>telegramUser(signed(),'8897925046','123',now,key));
  await assert.rejects(()=>telegramUser('', '8897925046','123',now,key));
  const other = await signed({user:124});
  await assert.rejects(()=>telegramUser(other,'8897925046','123',now,key));
});
test('rejects tampering, wrong bot, duplicate fields, stale and future launch',async()=>{
  const raw = await signed();
  await assert.rejects(()=>telegramUser(raw.replace('123','124'),'8897925046','123',now,key));
  await assert.rejects(()=>telegramUser(raw,'wrong-bot','123',now,key));
  await assert.rejects(()=>telegramUser(raw+'&auth_date=123','8897925046','123',now,key));
  for(const stamp of [Math.floor(now/1000)-3601,Math.floor(now/1000)+60]){
    const raw = await signed({stamp});
    await assert.rejects(()=>telegramUser(raw,'8897925046','123',now,key));
  }
});
test('production key cannot be replaced with test signing material',async()=>{
  const raw=await signed();
  await assert.rejects(()=>telegramUser(raw,'8897925046','123',now));
});
test('bridge only permits business API paths',()=>{
  assert.equal(validRequest({path:'/api/list?category=garage'}),true);
  for(const path of ['/api/connect','/api/disconnect','//evil.example/api/state','/api/meta','/api/../api/connect','/.env'])assert.equal(validRequest({path}),false,path);
  assert.equal(validRequest({path:'/api/meta',data:{id:'ad',changes:{favorite:true}}}),true);
  assert.equal(validRequest({path:'/api/chat',data:{text:'Lexus IS 200'}}),true);
  assert.equal(validRequest({path:'/api/chat'}),false);
  assert.equal(validRequest({path:'/api/chat',data:{text:'x'.repeat(8000)}}),false);
});
