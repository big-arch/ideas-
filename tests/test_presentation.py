import unittest
from dataclasses import replace
from unittest.mock import patch

from garage_radar.analysis import assess
from garage_radar.bot import Telegram
from garage_radar.config import Search
from garage_radar.presentation import caption, ranked_feed
from garage_radar.network import RemoteError
from garage_radar.models import utc_now
from test_radar import listing, peers


class PresentationTests(unittest.TestCase):
    def test_placeholder_commercial_price_not_ranked_as_a_bargain(self):
        placeholder=listing(category='commercial',price=10,attributes={'source':'avito_public_html','site_date_at':utc_now().isoformat()})
        self.assertEqual(ranked_feed([placeholder],Search(category='commercial'),recent_days=7),[])
        result=assess(placeholder,[placeholder],Search(category='commercial'))
        self.assertEqual(result.status,'excluded')
        self.assertIn('не подтверждена',result.reason)
    def test_city_outside_rostov_has_visible_warning_in_telegram(self):
        target = listing()
        target = replace(target, locality="Волгодонск")
        text = caption(assess(target, [target], Search()))
        self.assertIn("🔶 <b>Волгодонск · за пределами Ростова-на-Дону</b>", text)
        self.assertNotIn("за пределами", caption(assess(listing(), peers(), Search())))

    def test_exact_percentage_is_calculated_not_rounded_to_a_tier(self):
        for price, expected in ((900000, "10%"), (800000, "20%"), (730000, "27%")):
            result = assess(listing(price=price), peers(), Search())
            text = caption(result)
            self.assertIn("Дешевле похожих на " + expected, text)
            self.assertIn("<b>Гараж 24 м²</b>", text)

    def test_unreliable_spread_does_not_appear_as_a_discount(self):
        result = replace(assess(listing(), peers(), Search()), status="insufficient")
        self.assertNotIn("Дешевле", caption(result))

    def test_html_is_escaped_and_address_is_visible(self):
        result = assess(listing(title="Гараж <важно>", attributes={"visible_address": "Ростов-на-Дону, ул. Тестовая, 1"}), peers(), Search())
        text = caption(result)
        self.assertIn("&lt;важно&gt;", text)
        self.assertIn("ул. Тестовая, 1", text)
        self.assertNotIn("ID:", text)

    def test_all_feed_shows_unrated_listings_instead_of_empty_result(self):
        self.assertEqual(len(ranked_feed([listing()], Search())), 1)
        self.assertEqual(ranked_feed([listing()], Search(), 0.1), [])

    def test_city_level_comparison_is_explicitly_labelled(self):
        attributes = {"source": "avito_public_html"}
        target = listing(attributes=attributes, district="")
        pool = peers(attributes=attributes, district="Другой район")
        result = assess(target, pool, Search())
        self.assertEqual(result.comparison_scope, "city")
        self.assertAlmostEqual(result.discount, 0.4)
        self.assertIn("без поправки на район", caption(result))

    def test_photo_delivery_uses_sendphoto_with_caption_and_buttons(self):
        result = assess(listing(photo_url="https://10.img.avito.st/image/real.jpg"), peers(), Search())
        api = Telegram("123:example")
        with patch.object(api, "call", return_value={"photo": [{"file_id": "cached-photo"}]}) as call:
            api.send_card(123, result, {"inline_keyboard": []})
        self.assertEqual(call.call_args.args[0], "sendPhoto")
        self.assertIn("caption", call.call_args.args[1])
        self.assertEqual(call.call_args.args[1]["parse_mode"], "HTML")

    def test_rejected_photo_falls_back_to_same_useful_text_card(self):
        result = assess(listing(photo_url="https://10.img.avito.st/image/real.jpg"), peers(), Search())
        api = Telegram("123:example")
        with patch.object(api, "call", side_effect=[RemoteError(400), {}]) as call, patch("garage_radar.bot.photo_bytes", side_effect=RemoteError(400)):
            api.send_card(123, result, {"inline_keyboard": []})
        self.assertEqual(call.call_args.args[0], "sendMessage")
        self.assertIn("Гараж", call.call_args.args[1]["text"])


if __name__ == "__main__":
    unittest.main()
