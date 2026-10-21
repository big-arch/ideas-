"""Paginated Telegram card sections; every section edits the original post."""
from __future__ import annotations

from html import escape

def pages(text: str, budget: int) -> list[str]:
    """Count UTF-16 units as Telegram does, including astral characters."""
    result, part, used = [], [], 0
    for char in text:
        width = len(char.encode('utf-16-le')) // 2
        if used + width > budget:
            result.append(''.join(part))
            part, used = [], 0
        part.append(char)
        used += width
    if part or not result:
        result.append(''.join(part))
    return result


def section_card(item, text: str, section: str, page: int, *, photo: bool, favorite: bool):
    from .bot import card_keyboard
    titles = {'a': 'Анализ ИИ', 'd': 'Объект и описание', 'p': 'Похожие объявления', 'c': 'Расчёт цены','h':'История цены (МСК)'}
    parts = pages(text, 650 if photo else 2900)
    page = min(max(0, page), len(parts)-1)
    heading = f'<b>{escape(item.title[:70])}</b>\n<b>{item.price_rub:,.0f} ₽</b>\n'
    heading += f'{escape(titles[section])} · {page+1}/{len(parts)}\n\n'
    body = heading + escape(parts[page])
    keyboard = card_keyboard(item)
    for row in keyboard['inline_keyboard']:
        for button in row:
            if button.get('callback_data', '').startswith('favorite:'):
                button['text'] = '★ Сохранено' if favorite else '☆ Сохранить'
    navigation = []
    if page:
        navigation.append({'text': '← Назад', 'callback_data': f'v:{section}:{page-1}:{item.id}'})
    if page+1 < len(parts):
        navigation.append({'text': 'Далее →', 'callback_data': f'v:{section}:{page+1}:{item.id}'})
    if navigation:
        keyboard['inline_keyboard'].append(navigation)
    keyboard['inline_keyboard'].append([{'text': 'Краткая карточка', 'callback_data': 'card:' + item.id}])
    return body, keyboard
