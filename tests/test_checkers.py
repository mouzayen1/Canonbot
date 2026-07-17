"""Offline tests for the stock/price detection logic (no network)."""

import os
import sys

from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from canonbot import checkers  # noqa: E402

JSON_LD_IN_STOCK = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Canon G7 X III",
 "offers":{"@type":"Offer","price":"879.99","priceCurrency":"USD",
 "availability":"https://schema.org/InStock"}}
</script></head><body>Add to Cart</body></html>
"""

JSON_LD_OUT_OF_STOCK = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Canon G7 X III",
 "offers":{"@type":"Offer","price":"879.99","priceCurrency":"USD",
 "availability":"http://schema.org/OutOfStock"}}
</script></head><body>Sold Out</body></html>
"""

JSON_LD_AGGREGATE = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"SX740",
 "offers":{"@type":"AggregateOffer","lowPrice":"399.99","priceCurrency":"USD",
 "offers":[{"@type":"Offer","availability":"InStock","price":"399.99"}]}}
</script></head><body></body></html>
"""

TEXT_OUT = "<html><body>This item is currently Out of Stock.</body></html>"
TEXT_IN = "<html><body><button>Add to Cart</button> ready to ship</body></html>"
TEXT_AMBIGUOUS = "<html><body>Canon camera product details and specs.</body></html>"


def parse(html):
    return BeautifulSoup(html, "html.parser")


def test_json_ld_in_stock():
    r = checkers._from_json_ld(parse(JSON_LD_IN_STOCK))
    assert r is not None
    assert r.status == checkers.IN_STOCK
    assert r.price == 879.99
    assert r.currency == "USD"


def test_json_ld_out_of_stock():
    r = checkers._from_json_ld(parse(JSON_LD_OUT_OF_STOCK))
    assert r is not None
    assert r.status == checkers.OUT_OF_STOCK


def test_json_ld_aggregate_offer():
    r = checkers._from_json_ld(parse(JSON_LD_AGGREGATE))
    assert r is not None
    assert r.status == checkers.IN_STOCK
    assert r.price == 399.99


def test_text_out_of_stock():
    r = checkers._from_text(parse(TEXT_OUT))
    assert r.status == checkers.OUT_OF_STOCK


def test_text_in_stock():
    r = checkers._from_text(parse(TEXT_IN))
    assert r.status == checkers.IN_STOCK


def test_text_ambiguous_is_unknown():
    r = checkers._from_text(parse(TEXT_AMBIGUOUS))
    assert r.status == checkers.UNKNOWN


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_bestbuy_api_available(monkeypatch=None):
    import canonbot.checkers as c

    orig = c.requests.get
    c.requests.get = lambda *a, **k: _FakeResp(
        200, {"salePrice": 879.99, "orderable": "Available", "onlineAvailability": True}
    )
    try:
        r = c.check_via_bestbuy_api("6377340", "key", 10)
        assert r.status == c.IN_STOCK
        assert r.price == 879.99
    finally:
        c.requests.get = orig


def test_bestbuy_api_soldout():
    import canonbot.checkers as c

    orig = c.requests.get
    c.requests.get = lambda *a, **k: _FakeResp(
        200, {"regularPrice": 879.99, "orderable": "SoldOut", "onlineAvailability": False}
    )
    try:
        r = c.check_via_bestbuy_api("6377340", "key", 10)
        assert r.status == c.OUT_OF_STOCK
    finally:
        c.requests.get = orig


def test_price_parser():
    assert checkers._parse_price("$1,299.00") == 1299.00
    assert checkers._parse_price("879.99") == 879.99
    assert checkers._parse_price(None) is None
    assert checkers._parse_price("free") is None


if __name__ == "__main__":
    import traceback

    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                passed += 1
                print(f"PASS {name}")
            except Exception:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
