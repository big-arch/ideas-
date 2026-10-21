"""Personal listing workflow, saved searches, and transparent price changes."""
import math
from .models import CATEGORIES, normalized

STAGES = {'':'Без статуса','interested':'Интересен','contacted':'Связался','inspection':'Осмотр','rejected':'Отказ'}
VIEWS = {'all':'Все доступные','unseen':'Только непросмотренные','hidden':'Скрытые','drops':'Цена снизилась',**{k:v for k,v in STAGES.items() if k}}


def validate_profile(data: dict) -> dict:
    value = {'name':str(data.get('name','')).strip()[:80], 'category':data.get('category','garage'),
             'locality':str(data.get('locality','')).strip()[:160],
             'include':str(data.get('include','')).strip()[:400], 'exclude':str(data.get('exclude','')).strip()[:400],
             'alerts': data.get('alerts',True)}
    if not value['name'] or value['category'] not in CATEGORIES or type(value['alerts']) is not bool:
        raise ValueError('Укажи название, категорию и настройку уведомлений')
    for field in ('min_price','max_price'):
        raw = data.get(field)
        number = None if raw is None or raw == '' else float(raw)
        if number is not None and (not math.isfinite(number) or not 0 <= number <= 1e12):
            raise ValueError('Бюджет должен быть положительным числом')
        value[field] = number
    if value['min_price'] is not None and value['max_price'] is not None and value['min_price'] > value['max_price']:
        raise ValueError('Минимальная цена больше максимальной')
    return value


def matches_profile(item, profile: dict) -> bool:
    if item.category != profile['category']: return False
    if profile.get('min_price') is not None and item.price_rub < profile['min_price']: return False
    if profile.get('max_price') is not None and item.price_rub > profile['max_price']: return False
    address = normalized(' '.join((item.locality,item.district,item.attributes.get('visible_address',''))))
    if profile.get('locality') and normalized(profile['locality']) not in address: return False
    text = normalized(' '.join((item.title,item.description,item.attributes.get('visible_params',''))))
    required = [normalized(x.strip()) for x in profile.get('include','').split(',') if x.strip()]
    excluded = [normalized(x.strip()) for x in profile.get('exclude','').split(',') if x.strip()]
    return all(word in text for word in required) and not any(word in text for word in excluded)


def active_profile(store, user_id):
    selected = store.get_state(f'active_search:{user_id}')
    return next((p for p in store.profiles(user_id) if p['id']==selected),None)


def watched(meta: dict) -> bool:
    return bool(meta['watch']) if meta['watch'] != -1 else bool(meta['favorite'] or meta['stage'] in {'interested','contacted','inspection'})


def filter_results(store, user_id, results, *, view=None, profile=None):
    view = view if view is not None else store.get_state(f'view:{user_id}','all')
    if view not in VIEWS: raise ValueError('Неизвестный режим просмотра')
    states = store.listing_states(user_id)
    answer = []
    for result in results:
        item = result.listing
        meta = states.get(item.id, {'seen':0,'hidden':0,'stage':''})
        hidden = bool(meta['hidden'] or meta['stage']=='rejected')
        if view=='hidden':
            if not hidden: continue
        elif view!='rejected' and hidden: continue
        if view=='unseen' and meta['seen']: continue
        if view in STAGES and meta['stage'] != view: continue
        if view=='drops' and not store.price_change(item.id): continue
        if profile and not matches_profile(item,profile): continue
        answer.append(result)
    return answer
