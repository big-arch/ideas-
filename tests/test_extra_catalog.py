import unittest
from datetime import datetime, timezone
from garage_radar.sources import parse_public, source_name, PublicCatalog
from garage_radar.avito import SourceUnavailable
from garage_radar.details import parse_detail
from garage_radar.presentation import recent


CARD = '''<div class="offer-snippets-container__item offer-snippet">
<a href="/prodaja/garage/test-r12345.html"><img data-src="https://data.akula.com/files/img/12345/270/0.jpg"></a>
<span class="offer-snippet-tags__item"><strong>24 м² , Гараж</strong></span>
<span class="offer-snippet-tags__item">20 октября 2026</span>
<div class="offer-snippet__price">600 000 ₽</div><p class="offer-snippet__additional-price">25 000 ₽ за м²</p>
<p class="offer-snippet__description">Продаю капитальный кирпичный гараж.</p>
<span class="offer-snippet__address"><a>Ростовская область</a>, <a>Ростов-на-Дону</a>, ул. Примерная, 1</span>
</div>'''


class ExtraCatalogTests(unittest.TestCase):
    def test_visible_total_price_original_photo_and_date(self):
        item = parse_public(CARD, 'garage', 'akula')[0]
        self.assertEqual(item.price_rub, 600000)
        self.assertEqual(item.area_m2, 24)
        self.assertEqual(item.garage_type, 'capital')
        self.assertEqual(source_name(item), 'Акула')
        self.assertEqual(item.photo_url, 'https://data.akula.com/files/img/12345/270/0.jpg')
        self.assertTrue(recent(item, datetime(2026, 10, 22, tzinfo=timezone.utc), 7))
        detail = parse_detail('<div class="object__text">Полное описание продавца</div><meta property="og:image" content="https://data.akula.com/files/img/12345/max/0.jpg">', item)
        self.assertEqual(detail['description'], 'Полное описание продавца')
        self.assertIn('/max/0.jpg', detail['photos'][-1])

    def test_old_missing_date_and_external_or_wrong_category_are_not_fresh(self):
        now = datetime(2026, 10, 22, tzinfo=timezone.utc)
        for date in ['12 августа 2026', 'дата отсутствует']:
            item = parse_public(CARD.replace('20 октября 2026', date), 'garage', 'akula')[0]
            self.assertFalse(recent(item, now, 7))
        with self.assertRaises(SourceUnavailable): parse_public(CARD, 'apartment', 'akula')
        with self.assertRaises(SourceUnavailable): parse_public(CARD.replace('/prodaja/garage/test-r12345.html', 'https://evil.example/test-r12345.html'), 'garage', 'akula')
        self.assertIsNone(PublicCatalog('akula').fetch('car'))
