import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from garage_radar.details import parse_detail
from garage_radar.photos import listing_photos, GALLERY_VERSION
from garage_radar.webapp import quick_detail, DETAIL_LOCK, DETAIL_PENDING
from garage_radar.storage import Store
from test_radar import listing


class GalleryTests(unittest.TestCase):
    def test_drom_gallery_has_four_distinct_photos_without_recommendations(self):
        cover='https://s31.auto.drom.ru/photo/v2/first/gen600.jpg'
        item=listing(photo_url=cover, url='https://auto.drom.ru/rostov-na-donu/uaz/3151/123.html')
        gallery=''.join(f'<a href="https://s31.auto.drom.ru/photo/v2/{name}/gen1200.jpg"><img src="https://s31.auto.drom.ru/photo/v2/{name}/gen100.jpg"></a>' for name in ['first','second','third','fourth'])
        document=f'<div data-ftid="bull-page_bull-gallery_thumbnails">{gallery}</div><div data-ftid="bull_description">Описание</div><img src="https://s31.auto.drom.ru/photo/v2/other/gen1200.jpg">'
        detail=parse_detail(document,item)
        self.assertEqual(len(detail['photos']),4)
        self.assertTrue(all('other' not in x for x in detail['photos']))
        self.assertTrue(all('gen1200' in x for x in detail['photos']))
        self.assertEqual(detail['gallery_version'],GALLERY_VERSION)

    def test_schema_image_objects_and_sizes_are_not_four_fake_photos(self):
        photos=[{'@type':'ImageObject','contentUrl':f'https://avatars.mds.yandex.net/get-realty-offers/1/{name}/large'} for name in ['a','b','c','d']]
        photos.append({'url':'https://avatars.mds.yandex.net/get-realty-offers/1/a/small'})
        document='<script type="application/ld+json">'+json.dumps({'@type':'Apartment','image':photos})+'</script>'
        self.assertEqual(len(parse_detail(document,listing())['photos']),4)
        self.assertEqual(len(listing_photos(['https://data.akula.com/files/img/123/270/0.jpg','https://data.akula.com/files/img/123/max/0.jpg'])),1)

    def test_gallery_urls_cannot_inject_external_or_non_https_images(self):
        urls=['https://evil.example/1.jpg','http://10.img.avito.st/a.jpg','https://10.img.avito.st:123/a.jpg','https://10.img.avito.st:bad/a.jpg','https://user@10.img.avito.st/a.jpg','https://10.img.avito.st/a.jpg']
        self.assertEqual(listing_photos(urls),[urls[-1]])

    def test_yandex_lazy_gallery_does_not_collect_recommendation_snippets(self):
        document='<div class="GalleryV2__slidesContainer--abc"><img data-image-src="//avatars.mds.yandex.net/get-realty-offers/1/a/app_large"><img data-image-src="//avatars.mds.yandex.net/get-realty-offers/1/b/app_large"></div><div class="OfferGallerySnippet__container--a"><img src="//avatars.mds.yandex.net/get-realty-offers/1/not-this-listing/app_large"></div><div class="OfferCardTextDescription__text">Текст</div>'
        item=listing(url='https://realty.yandex.ru/offer/123/')
        photos=parse_detail(document,item)['photos']
        self.assertEqual(len(photos),2)
        self.assertTrue(all('not-this-listing' not in x for x in photos))

    def test_public_catalog_photos_are_available_before_slow_detail_finishes(self):
        from garage_radar.sources import parse_public
        from test_extra_catalog import CARD
        extra=''.join(f'<img src="https://data.akula.com/files/img/12345/max/{i}.jpg">' for i in range(1,4))
        item=parse_public(CARD.replace('</div>',extra+'</div>'), 'garage','akula')[0]
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)/'test.sqlite3')
            key=(str(store.path),item.id)
            with DETAIL_LOCK: DETAIL_PENDING.add(key)
            try:
                detail=quick_detail(item,store)
                self.assertTrue(detail['pending'])
                self.assertEqual(len(detail['photos']),4)
            finally:
                with DETAIL_LOCK: DETAIL_PENDING.discard(key)

    def test_failed_detail_returns_available_gallery_and_stops_spinner(self):
        from garage_radar.storage import Store
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)/'test.sqlite3')
            item=listing(photo_url='https://10.img.avito.st/a.jpg')
            store.set_state('detail_retry:'+item.id,json.dumps({'at':time.time(),'error':'Галерея недоступна'}))
            with patch('garage_radar.webapp.read_detail') as read:
                result=quick_detail(item,store)
            read.assert_not_called()
            self.assertFalse(result['pending'])
            self.assertEqual(result['photos'],[item.photo_url])
