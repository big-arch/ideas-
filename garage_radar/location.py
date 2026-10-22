"""Links and a lazy map from the source address; never invent coordinates."""
from urllib.parse import urlencode
from .models import normalized


def listing_map(item):
    address = str(item.attributes.get('visible_address', '')).strip()
    parts = [item.region, item.locality]
    if address and normalized(item.locality) in normalized(address): parts = [item.region]
    if address: parts.append(address)
    query = ', '.join(p for p in parts if p)[:500]
    shown_address = address or str(item.locality).strip() or str(item.region).strip()
    params = {'text': query, 'z': 16}
    return {'query': query, 'address': shown_address,
            'url': 'https://yandex.ru/maps/?' + urlencode(params),
            'embed': 'https://yandex.ru/map-widget/v1/?' + urlencode({'mode': 'search', **params}),
            'precision': 'address_search',
            'note': 'Поиск по адресу продавца. Точная точка объекта не подтверждена.' if address else
                    'В объявлении указан только населённый пункт. Точное расположение неизвестно.'}
