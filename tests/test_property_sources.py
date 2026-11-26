import json
import unittest
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

from garage_radar import ayax, restate
from garage_radar.avito import SourceUnavailable
from garage_radar.details import ListingWithdrawn, parse_detail
from garage_radar.models import utc_now
from garage_radar.photos import listing_photos
from garage_radar.presentation import recent
from garage_radar.sources import parse_public, PublicCatalog


class PropertySourceTests(unittest.TestCase):
    url = "https://batajsk.ayax.ru/flat/785/"
    garage = "https://rostov-na-donu.restate.ru/obj/prodazha-garazha-ulica-588/"

    def ayax_node(self, **changes):
        return dict(dict(code="785", url=self.url, estate_type={"key":"flat"}, target_type_id=1,
                    address={"sub_region":{"region":{"name":"Ростовская область"}}, "locality":{"name":"Батайск"},
                             "street":{"name":"Тестовая"}, "house_number":"1"},
                    price="3700000.0000", properties={"area_total":"40.00"}, title="Квартира 40&nbsp;м²",
                    description="<p>Описание квартиры</p>", created_at=(utc_now()-timedelta(days=1)).isoformat(),
                    image=[{"active":True,"image":"images/data/realty/785/test.jpg"},
                           {"active":True,"image":"images/data/realty/999/other.jpg"}]), **changes)

    def ayax_html(self, roots):
        table = []
        def encode(value):
            index = len(table)
            table.append(None)
            if isinstance(value, dict):
                value = {k:encode(v) for k,v in value.items()}
            elif isinstance(value, list):
                value = [encode(v) for v in value]
            table[index] = value
            return index
        encode(roots)
        return '<a href="/flat/785/">Квартира</a><script id="__NUXT_DATA__" type="application/json">' + json.dumps(table) + '</script>'

    def test_only_visible_sales_from_the_requested_region_category_and_result_set(self):
        listing = self.ayax_node()
        rental = self.ayax_node(target_type_id=2,price=1)
        foreign = self.ayax_node(address={"sub_region":{"region":{"name":"Московская область"}}},price=2)
        rec = self.ayax_node(code="123",url="https://batajsk.ayax.ru/flat/123/")
        page = self.ayax_html([{"data":[listing,rental,foreign,rec], "count":"4"}, rec])
        item, = ayax.parse_catalog(page, "apartment")
        self.assertEqual((item.locality,item.price_rub,item.area_m2),("Батайск",3700000,40))
        self.assertEqual(item.attributes["photo_urls"], ["https://www.ayax.ru/images/data/realty/785/test.jpg"])
        self.assertTrue(recent(item,utc_now()))
        self.assertEqual(ayax.parse_catalog(self.ayax_html([{"data":[], "count":"0"}, listing]), "garage"), [])
        with self.assertRaises(SourceUnavailable):
            ayax.parse_catalog(page, "garage")

    def test_ayax_original_price_status_and_first_publication(self):
        node = self.ayax_node(created_at=(utc_now()-timedelta(days=10)).isoformat(),updated_at=utc_now().isoformat())
        item, = ayax.parse_catalog(self.ayax_html([{"data":[node],"count":1}]),"apartment")
        self.assertFalse(recent(item,utc_now()))
        record = dict(node,active="active",realty_type="realtyResale",target_type={"key":"buy"})
        detail = parse_detail(self.ayax_html([record]),item)
        self.assertEqual((detail["source_price"],detail["availability"]), (item.price_rub,"active"))
        with self.assertRaises(ListingWithdrawn):
            parse_detail(self.ayax_html([dict(record,active="sold")]),item)
        with self.assertRaises(SourceUnavailable):
            parse_detail(self.ayax_html([record]),replace(item,url="https://batajsk.ayax.ru/flat/888"))

    def restate_card(self, description="Отдельный гараж", label="26.11"):
        return (f'<div class="sri" data-id="588"><a class="sri__slider" href="{self.garage}"></a>'
                '<p class="sri__header">Гараж, Тестовая улица, 15 м²</p><p class="sri__price">350 000 ₽</p>'
                '<div class="sri__area">Ростовская область, Ростов-на-Дону, Ленинский р-н</div>'
                f'<div class="sri__descr-text">{description}</div><p class="sri__date">{label}</p></div>')

    def test_restate_checks_first_publication_and_excludes_auctions(self):
        today = utc_now().astimezone(restate.MSK)
        label = today.strftime("%d.%m")
        old = (today-timedelta(days=10)).strftime("%d.%m.%Y")
        card = self.restate_card(label=label)
        item, = parse_public(card,"garage","restate")
        self.assertFalse(recent(item,utc_now()))
        original = (f'<link rel="canonical" href="{self.garage}"><a data-popup-target="popup19--send-msg" data-id="588"></a>'
                    '<span itemprop="priceCurrency" content="RUB"></span><span itemprop="price" content="350000"></span>'
                    '<p class="base__address">Ростовская область, Ростов-на-Дону, Тестовая улица</p>'
                    f'<p class="ad-params__param">с {old}, обновлён {today:%d.%m.%Y}</p>'
                    '<div class="gallery-top"><img src="/uploads/26/11/20/o_123.jpg"></div>')
        read = Mock(side_effect=lambda i:parse_detail(original,i))
        adapter = PublicCatalog("restate", detail_reader=read)
        row, = adapter.restate_sales(card,"garage")
        self.assertFalse(recent(row,utc_now()))
        adapter.restate_sales(card,"garage")
        self.assertEqual(adapter.restate_sales(self.restate_card("Продажа на торгах",label),"garage"), [])
        read.assert_called_once()
        self.assertTrue(recent(replace(item, attributes={**item.attributes,"site_date_at":
            parse_detail(original.replace(f"с {old}", f"с {today:%d.%m.%Y}"),item)["published_at"]}),utc_now()))
        with self.assertRaises(SourceUnavailable):
            parse_detail(original.replace('data-id="588"', 'data-id="777"'),item)
        self.assertEqual(listing_photos(["https://www.ayax.ru/private/a.jpg",
                                        "https://cdn.restate.ru/other.jpg", "https://restate.ru.example/uploads/26/11/20/o_123.jpg"]), [])
