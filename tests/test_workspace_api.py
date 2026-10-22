import http.client
import json
import unittest
from unittest.mock import patch

from garage_radar.config import Config, Search
from garage_radar.models import utc_now
from test_radar import listing
import test_webapp_signin as signin_support


class WorkspaceApiTests(unittest.TestCase):
    tearDown = signin_support.LoopbackSignInTests.tearDown

    def setUp(self):
        signin_support.LoopbackSignInTests.setUp(self)
        self.server.config = Config(Search(),self.server.store.path,None,60)
        self.server.store.set_state('owner_chat_id','123')
        self.server.store.upsert([listing(price=600000,attributes={'site_date_at':utc_now().isoformat()}),
                                  listing('cheap',price=400000,attributes={'site_date_at':utc_now().isoformat()})])
        account=patch('garage_radar.webapp.chatgpt.status',return_value={'connected':False})
        account.start();self.addCleanup(account.stop)
        self.cookie='';self.csrf=''
        self.request('GET','/')
        self.csrf=self.request('GET','/api/state')[1]['csrf']

    def request(self, method, path, data=None, csrf=True):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
        headers={'Host':'127.0.0.1:8766','Cookie':self.cookie}
        body=None
        if data is not None:
            body=json.dumps(data).encode()
            headers.update({'Content-Type':'application/json','Origin':'http://127.0.0.1:8766'})
            if csrf:headers['X-CSRF-Token']=self.csrf
        connection.request(method,path,body=body,headers=headers)
        response=connection.getresponse()
        if response.getheader('Set-Cookie'):self.cookie=response.getheader('Set-Cookie').split(';')[0]
        raw=response.read();code=response.status;connection.close()
        return code, json.loads(raw) if raw.startswith(b'{') else {}

    def test_price_order_saved_search_and_private_status_end_to_end(self):
        code,result=self.request('GET','/api/list?category=garage')
        self.assertEqual(code,200)
        self.assertEqual([x['id'] for x in result['items']],['cheap','target'])
        code,saved=self.request('POST','/api/search/save',{'name':'До 450 тысяч','category':'garage','max_price':450000,'alerts':True})
        self.assertEqual(code,200)
        self.assertEqual([x['id'] for x in self.request('GET','/api/list?category=garage')[1]['items']],['cheap'])
        self.assertEqual(self.server.store.profiles(456),[])
        code,state=self.request('POST','/api/meta',{'id':'cheap','changes':{'stage':'contacted','seen':True}})
        self.assertEqual(code,200);self.assertTrue(state['meta']['watching'])
        self.assertEqual(self.server.store.listing_meta(456,'cheap')['stage'],'')
        self.request('POST','/api/search/delete',{'id':saved['selected']})
        self.assertEqual(self.request('GET','/api/state')[1]['profiles'],[])

    def test_hidden_restoration_and_unseen_filter(self):
        self.request('POST','/api/meta',{'id':'cheap','changes':{'hidden':True,'seen':True}})
        self.assertEqual([x['id'] for x in self.request('GET','/api/list?category=garage')[1]['items']],['target'])
        self.assertEqual([x['id'] for x in self.request('GET','/api/list?category=garage&view=hidden')[1]['items']],['cheap'])
        self.request('POST','/api/meta',{'id':'cheap','changes':{'hidden':False}})
        self.assertEqual([x['id'] for x in self.request('GET','/api/list?category=garage&view=unseen')[1]['items']],['target'])

    def test_csrf_and_invalid_budget_prevent_mutation(self):
        self.assertEqual(self.request('POST','/api/meta',{'id':'cheap','changes':{'hidden':True}},csrf=False)[0],403)
        self.assertEqual(self.server.store.listing_meta(123,'cheap')['hidden'],0)
        self.assertEqual(self.request('POST','/api/search/save',{'name':'bad','max_price':-5})[0],400)
        self.assertEqual(self.server.store.profiles(123),[])

    def test_chat_boundary_owner_map_and_static_asset(self):
        with patch('garage_radar.webapp.conversation_turn',return_value={'answer':'Уточним задачу.','category':'garage','results':[]}) as turn:
            self.assertEqual(self.request('POST','/api/chat',{'text':'Привет'},csrf=False)[0],403)
            turn.assert_not_called()
            self.assertEqual(self.request('POST','/api/chat',{'text':'Привет','user_id':456})[0],200)
            self.assertEqual(str(turn.call_args.args[2]),'123')
        with patch('garage_radar.webapp.conversation_turn') as turn:
            self.assertEqual(self.request('POST','/api/chat',{'text':'x','selected_id':['wrong']})[0],400)
            turn.assert_not_called()
        item=self.request('GET','/api/item?id=cheap')[1]['item']
        self.assertEqual(item['map']['precision'],'address_search')
        self.assertEqual(item['map']['address'],'Ростов-на-Дону')
        self.assertTrue(item['map']['embed'].startswith('https://yandex.ru/map-widget/'))
        self.assertEqual(self.request('GET','/ui-chat.js')[0],200)
