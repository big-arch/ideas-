from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from dataclasses import replace

from garage_radar.analysis import assess
from garage_radar.avito import SourceUnavailable, parse_catalog, PublicAvito
from garage_radar.config import Search


def card(identity="123", title="Гараж, 24 м²", params="кирпичный", suffix="", category="garazhi_i_mashinomesta"):
    return f'''<div data-marker="item" data-item-id="{identity}">
      <a data-marker="item-photo-sliderLink" href="/rostov-na-donu/{category}/garage_{identity}?context=test"><img alt="untrusted image"></a>
      <div data-marker="item-price"><meta itemProp="priceCurrency" content="RUB"/><meta itemProp="price" content="600000"/>
      <span data-marker="item-price-value">600 000 ₽</span>{suffix}</div>
      <a data-marker="item-title" href="/rostov-na-donu/{category}/garage_{identity}"><h3>{title}</h3></a>
      <p data-marker="item-specific-params">{params}</p>
      <div data-marker="item-address"><div data-marker="item-location"><p><span>Мкр-н Северный, ул. Примерная, 1</span></p><p><span>Ростов-на-Дону</span></p></div></div>
      <p data-marker="item-line">Продаю гараж. Документы уточняйте.</p>
      <script>untrusted()</script></div>'''


class AvitoTests(unittest.TestCase):
    def test_backoff_retains_failure_reason_without_retrying_source(self):
        source=PublicAvito()
        with patch('garage_radar.avito.urlopen',side_effect=HTTPError('https://www.avito.ru/',429,'limited',{},None)) as request:
            for _ in range(2):
                with self.assertRaisesRegex(SourceUnavailable,'HTTP 429'): source.fetch('commercial')
            request.assert_called_once()
    def test_lazy_photo_is_read_from_public_slider_marker(self):
        document = card().replace('<img alt="untrusted image">', '<li data-marker="slider-image/image-https://20.img.avito.st/image/photo-real.jpg"><img src="data:image/gif;base64,abc"></li>')
        self.assertEqual(parse_catalog(document, "garage")[0].photo_url, "https://20.img.avito.st/image/photo-real.jpg")

    def test_reads_visible_cards_without_images_or_scripts(self):
        items = parse_catalog(card() + card("456"), "garage")
        self.assertEqual(len(items), 2)
        item = items[0]
        self.assertEqual(item.locality, "Ростов-на-Дону")
        self.assertEqual(item.district, "Мкр-н Северный")
        self.assertEqual(item.price_rub, 600000)
        self.assertEqual(item.garage_type, "capital")
        self.assertEqual(item.area_m2, 24)
        self.assertEqual(item.ownership, "unknown")
        self.assertEqual(item.land_rights, "unknown")
        self.assertNotIn("untrusted", item.description)
        self.assertNotIn("context", item.url)

    def test_monthly_price_outside_value_span_is_rent(self):
        item = parse_catalog(card(suffix=" в месяц"), "garage")[0]
        self.assertEqual(item.transaction, "rent")

    def test_per_square_metre_price_is_not_total_price(self):
        item = parse_catalog(card(suffix=" за м²"), "garage")[0]
        self.assertEqual(item.price_basis, "per_m2")

    def test_ambiguous_area_is_not_treated_as_exact(self):
        self.assertEqual(parse_catalog(card(title="Гараж, &gt; 30 м²"), "garage"), [])

    def test_challenge_or_changed_markup_does_not_look_like_zero_listings(self):
        with self.assertRaises(SourceUnavailable):
            parse_catalog("<h1>Подтвердите, что вы человек</h1>", "garage")

    def test_other_categories_are_not_mixed(self):
        self.assertEqual(parse_catalog(card(category="kvartiry"), "garage"), [])

    def test_uncertain_public_rights_produce_only_preliminary_review(self):
        target = parse_catalog(card(), "garage")[0]
        peers = [replace(target, id=f"peer-{i}", url=f"https://www.avito.ru/peer-{i}", price_rub=1000000) for i in range(6)]
        result = assess(target, peers, Search())
        self.assertEqual(result.status, "review")
        self.assertAlmostEqual(result.discount, 0.4)
        self.assertTrue(any("ограничен" in flag for flag in result.risk_flags))


if __name__ == "__main__":
    unittest.main()
