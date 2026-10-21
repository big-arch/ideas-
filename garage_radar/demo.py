from __future__ import annotations

from .models import Listing, utc_now


def demo_listings() -> list[Listing]:
    """Deliberately invented data: never imported into the production database."""
    base = {
        "description": "Вымышленное объявление для проверки программы.",
        "region": "Ростовская область", "locality": "Ростов-на-Дону", "district": "Советский",
        "area_m2": 24.0, "garage_type": "capital", "ownership": "owned",
        "land_rights": "owned", "condition": "normal", "observed_at": utc_now().isoformat(), "demo": True,
    }
    results = []
    for index, price in enumerate([950000, 980000, 1000000, 1020000, 1050000, 1100000, 550000, 400000]):
        data = {**base, "id": f"demo-{index}", "url": f"https://example.org/demo/garage-{index}",
                "title": "Гараж 24 м² (демо)", "price_rub": price}
        if index == 6:
            data["description"] = "Вымышленный пример. Срочная продажа в связи с переездом."
        if index == 7:
            data["description"] = "Вымышленный пример. Есть трещины, требуется осмотр."
        results.append(Listing.from_dict(data))
    return results
