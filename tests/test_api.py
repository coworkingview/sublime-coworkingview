"""API-layer tests. Mocked HTTP only; safe to run with plain python3.

    python3 tests/test_api.py
    python3 -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
import urllib.error
from datetime import datetime, timezone
from email.message import EmailMessage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import coworkingview_api as api  # noqa: E402


def _headers(pairs):
    message = EmailMessage()
    for key, value in pairs:
        message[key] = value
    return message


class Body(object):
    """A successful urllib response."""

    def __init__(self, payload, reason="OK"):
        self.status = 200
        self.reason = reason
        self.headers = EmailMessage()
        if isinstance(payload, bytes):
            self._raw = payload
        elif isinstance(payload, str):
            self._raw = payload.encode("utf-8")
        else:
            self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class Opener(object):
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []
        self.timeouts = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        self.timeouts.append(timeout)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def http_error(status, payload, header_pairs=None, reason="Error"):
    if isinstance(payload, bytes):
        raw = payload
    elif isinstance(payload, str):
        raw = payload.encode("utf-8")
    else:
        raw = json.dumps(payload).encode("utf-8")
    return urllib.error.HTTPError(
        "https://api.coworkingview.com/",
        status,
        reason,
        _headers(header_pairs or []),
        io.BytesIO(raw),
    )


def client(opener, sleep=None):
    return api.Client(opener=opener, sleep=sleep, base_url=api.DEFAULT_BASE_URL)


CARD = {
    "slug": "sankt-oberholz-rosenthaler-berlin",
    "title": "St. Oberholz [Mitte]",
    "countryCode": "DE",
    "city": {"slug": "berlin", "name": "Berlin"},
    "zone": {"slug": "mitte", "name": "Mitte"},
    "operator": {
        "slug": "sankt-oberholz",
        "name": "St. Oberholz",
        "logo": {"url": "https://example.com/logo.png"},
    },
    "fromPrice": {"amount": 209, "currency": "EUR"},
    "fromBillingPeriod": "month",
    "featured": True,
    "excerpt": "Example description used as a test fixture.",
    "images": [{"url": "https://example.com/image.webp"}],
    "amenities": ["wifi"],
}


def _rates_payload():
    return {
        "data": {
            "asOf": "2026-10-01T00:00:00Z",
            "attribution": "Source: CoworkingView Price Index.",
            "licenseUrl": "https://creativecommons.org/licenses/by/4.0/",
            "cities": [
                {
                    "city": {"slug": "berlin", "name": "Berlin"},
                    "countryCode": "DE",
                    "currency": "EUR",
                    "meetingRoom": {
                        "median": {"amount": 32, "currency": "EUR"},
                        "sampleSize": 79,
                    },
                },
                {
                    "city": {"slug": "madrid", "name": "Madrid"},
                    "countryCode": "ES",
                    "currency": "EUR",
                    "figures": [
                        {
                            "optionType": "private_office",
                            "median": {"amount": 400, "currency": "EUR"},
                            "p25": {"amount": 300, "currency": "EUR"},
                            "p75": {"amount": 500, "currency": "EUR"},
                            "sampleSize": 12,
                            "propertyCount": 8,
                            "operatorCount": 4,
                        },
                        {
                            "optionType": "flex_desk",
                            "median": {"amount": 200, "currency": "EUR"},
                            "p25": {"amount": 150, "currency": "EUR"},
                            "p75": {"amount": 250, "currency": "EUR"},
                            "sampleSize": 9,
                            "propertyCount": 7,
                            "operatorCount": 3,
                        },
                    ],
                    "byType": [
                        {
                            "optionType": "fix_desk",
                            "median": {"amount": 1, "currency": "EUR"},
                            "sampleSize": 99,
                        }
                    ],
                    "dayPass": {
                        "median": {"amount": 25, "currency": "EUR"},
                        "sampleSize": 4,
                    },
                    "entryMedian": {"amount": 180, "currency": "EUR"},
                },
            ],
            "unpublished": [
                {
                    "city": {"slug": "tiny", "name": "Tiny"},
                    "insufficientData": True,
                }
            ],
        },
        "meta": {"contractVersion": "1.0.0"},
    }


class ClientTests(unittest.TestCase):
    def test_constructing_a_client_does_not_call_the_network(self):
        opener = Opener([])
        api.Client(opener=opener)
        self.assertEqual(opener.requests, [])

    def test_invalid_locale_is_rejected_before_a_request(self):
        opener = Opener([])
        with self.assertRaises(ValueError):
            client(opener).geo_bootstrap("fr")
        self.assertEqual(opener.requests, [])

    def test_geo_bootstrap_requests_the_real_path_and_sorts_cities(self):
        payload = {
            "data": {
                "countries": [],
                "cities": [
                    {
                        "slug": "madrid",
                        "name": "Madrid",
                        "countryCode": "ES",
                        "propertyCount": 10,
                        "center": {"lat": 40.4, "lng": -3.7},
                        "zones": [],
                    },
                    {
                        "slug": "berlin",
                        "name": "Berlin",
                        "countryCode": "DE",
                        "propertyCount": 85,
                        "center": {"lat": 52.5, "lng": 13.4},
                        "zones": [],
                    },
                    {"slug": "SKIP", "name": "Not a slug"},
                ],
                "currencies": {},
            },
            "meta": {"contractVersion": "1.0.0"},
        }
        opener = Opener([Body(payload)])
        cities = client(opener).geo_bootstrap("de")
        self.assertEqual(
            [city.slug for city in cities], ["berlin", "madrid"]
        )
        self.assertEqual(cities[0].property_count, 85)
        request = opener.requests[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertIn("CoworkingView-sublime/", request.get_header("User-agent"))
        self.assertTrue(
            request.full_url.endswith("/v1/geo/bootstrap?locale=de")
        )
        self.assertEqual(opener.timeouts, [api.DEFAULT_TIMEOUT])

    def test_search_builds_the_listing_url_and_drops_operator_media(self):
        payload = {
            "data": [CARD],
            "meta": {
                "contractVersion": "1.0.0",
                "pagination": {
                    "page": 1,
                    "pageSize": 20,
                    "total": 110,
                    "pageCount": 6,
                },
                "citation": {"text": "Source: CoworkingView."},
                "catalogueNotice": "You may link to listing pages.",
            },
        }
        opener = Opener([Body(payload)])
        result = client(opener).search_properties(
            "en", city="berlin", product="flex_desk", page_size=20
        )
        self.assertEqual(result.total, 110)
        self.assertEqual(result.citation, "Source: CoworkingView.")
        listing = result.listings[0]
        self.assertEqual(
            listing.url,
            "https://coworkingview.com/en/workspace/"
            "sankt-oberholz-rosenthaler-berlin",
        )
        self.assertEqual(listing.city, "Berlin")
        self.assertEqual(listing.zone, "Mitte")
        self.assertEqual(listing.operator, "St. Oberholz")
        self.assertEqual(api.format_price(listing), "209 EUR / month")
        self.assertTrue(listing.featured)
        self.assertNotIn("excerpt", listing.__dict__)
        self.assertNotIn("example.com", listing.url)
        query = opener.requests[0].full_url.split("?", 1)[1]
        self.assertIn("locale=en", query)
        self.assertIn("city=berlin", query)
        self.assertIn("pricingOptionType=flex_desk", query)
        self.assertIn("page=1", query)
        self.assertIn("pageSize=20", query)
        self.assertIn("sortBy=date-desc", query)
        self.assertNotIn("/v1/properties/", opener.requests[0].full_url)

    def test_search_omits_filters_that_were_not_set(self):
        opener = Opener([Body({"data": [], "meta": {"pagination": {"total": 0}}})])
        result = client(opener).search_properties("es")
        self.assertEqual(result.listings, ())
        self.assertEqual(result.total, 0)
        url = opener.requests[0].full_url
        self.assertIn("/v1/properties?", url)
        self.assertNotIn("city=", url)
        self.assertNotIn("pricingOptionType=", url)
        self.assertIn("locale=es", url)

    def test_spanish_and_german_listing_paths(self):
        slug = "sankt-oberholz-rosenthaler-berlin"
        self.assertEqual(
            api.listing_url(slug, "es"),
            "https://coworkingview.com/es/oficina/" + slug,
        )
        self.assertEqual(
            api.listing_url(slug, "de"),
            "https://coworkingview.com/de/buero/" + slug,
        )

    def test_detail_prefers_an_on_site_web_url(self):
        detail = dict(CARD)
        detail["webUrl"] = (
            "https://coworkingview.com/en/workspace/"
            "sankt-oberholz-rosenthaler-berlin"
        )
        detail["description"] = "Operator prose that must not be copied."
        opener = Opener([Body({"data": detail, "meta": {}})])
        listing = client(opener).property_detail(detail["slug"], "en")
        self.assertEqual(listing.url, detail["webUrl"])
        self.assertNotIn("description", listing.__dict__)
        self.assertIn(
            "/v1/properties/sankt-oberholz-rosenthaler-berlin?locale=en",
            opener.requests[0].full_url,
        )

    def test_detail_ignores_a_web_url_on_another_host(self):
        detail = dict(CARD)
        detail["webUrl"] = "https://evil.example/phish"
        opener = Opener([Body({"data": detail, "meta": {}})])
        listing = client(opener).property_detail(detail["slug"], "en")
        self.assertTrue(listing.url.startswith("https://coworkingview.com/"))

    def test_invalid_slug_is_rejected_before_a_request(self):
        opener = Opener([])
        with self.assertRaises(ValueError):
            client(opener).property_detail("../admin", "en")
        with self.assertRaises(ValueError):
            client(opener).property_detail("Berlin Mitte", "en")
        self.assertEqual(opener.requests, [])

    def test_market_rates_follow_the_published_row_rules(self):
        opener = Opener([Body(_rates_payload())])
        report = client(opener).market_rates("en", currency="eur")
        products = [(row.city_slug, row.product, row.unit) for row in report.rows]
        self.assertEqual(
            products,
            [
                ("berlin", "meeting_room", "hour"),
                ("madrid", "flex_desk", "month"),
                ("madrid", "private_office", "month"),
                ("madrid", "day_pass", "day"),
            ],
        )
        flex = report.rows[1]
        self.assertTrue(flex.low_confidence)
        self.assertEqual(flex.sample_size, 9)
        self.assertEqual(flex.p25, 150)
        self.assertFalse(report.rows[2].low_confidence)
        self.assertTrue(report.rows[3].low_confidence)
        self.assertEqual(report.attribution, "Source: CoworkingView Price Index.")
        self.assertEqual(
            report.license_url, "https://creativecommons.org/licenses/by/4.0/"
        )
        self.assertEqual(report.as_of, "2026-10-01T00:00:00Z")
        text = api.format_market_report(report)
        self.assertIn("Source: CoworkingView Price Index.", text)
        self.assertIn("https://creativecommons.org/licenses/by/4.0/", text)
        self.assertIn("low confidence", text)
        self.assertNotIn("tiny", text)
        self.assertNotIn("fix_desk", text)
        self.assertIn("currency=EUR", opener.requests[0].full_url)
        self.assertNotIn("city=", opener.requests[0].full_url)

    def test_by_type_is_used_only_when_figures_are_absent(self):
        payload = {
            "data": {
                "asOf": "2026-10-01T00:00:00Z",
                "attribution": "Source: CoworkingView Price Index.",
                "cities": [
                    {
                        "city": {"slug": "berlin", "name": "Berlin"},
                        "currency": "EUR",
                        "byType": [
                            {
                                "optionType": "flex_desk",
                                "median": {"amount": 214, "currency": "EUR"},
                                "sampleSize": 72,
                            }
                        ],
                    }
                ],
            }
        }
        report = client(Opener([Body(payload)])).market_rates("en", city="berlin")
        self.assertEqual(len(report.rows), 1)
        self.assertEqual(report.rows[0].product, "flex_desk")
        self.assertEqual(report.rows[0].unit, "month")
        self.assertIsNone(report.rows[0].p25)

    def test_unpublished_cities_do_not_become_rows(self):
        payload = {
            "data": {
                "attribution": "Source: CoworkingView Price Index.",
                "licenseUrl": "https://creativecommons.org/licenses/by/4.0/",
                "cities": [],
                "unpublished": [
                    {"city": {"slug": "tiny", "name": "Tiny"}, "insufficientData": True}
                ],
            }
        }
        report = client(Opener([Body(payload)])).market_rates("en")
        self.assertEqual(report.rows, ())
        text = api.format_market_report(report)
        self.assertIn("No published figures.", text)
        self.assertIn("Source: CoworkingView Price Index.", text)

    def test_problem_json_is_raised_and_not_retried(self):
        slept = []
        opener = Opener([
            http_error(
                400,
                {
                    "title": "Bad Request",
                    "code": "VALIDATION_FAILED",
                    "detail": "city is invalid",
                },
                reason="Bad Request",
            )
        ])
        with self.assertRaises(api.APIError) as caught:
            client(opener, sleep=slept.append).geo_bootstrap("en")
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(caught.exception.code, "VALIDATION_FAILED")
        self.assertIn("city is invalid", str(caught.exception))
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(slept, [])

    def test_error_without_a_code_uses_the_http_status(self):
        opener = Opener([http_error(404, b"<html>", reason="Not Found")])
        with self.assertRaises(api.APIError) as caught:
            client(opener).geo_bootstrap("en")
        self.assertEqual(caught.exception.code, "HTTP_404")
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(len(opener.requests), 1)

    def test_non_json_success_is_a_bad_response(self):
        opener = Opener([Body(b"not-json")])
        with self.assertRaises(api.APIError) as caught:
            client(opener).geo_bootstrap("en")
        self.assertEqual(caught.exception.code, "BAD_RESPONSE")

    def test_json_that_is_not_an_object_is_a_bad_response(self):
        opener = Opener([Body([1, 2])])
        with self.assertRaises(api.APIError) as caught:
            client(opener).geo_bootstrap("en")
        self.assertEqual(caught.exception.code, "BAD_RESPONSE")

    def test_retryable_status_is_retried_once_then_returned(self):
        slept = []
        opener = Opener([
            http_error(
                429,
                {"title": "Too Many Requests", "code": "RATE_LIMITED"},
                [("Retry-After", "99")],
                reason="Too Many Requests",
            ),
            Body({"data": {"cities": []}, "meta": {}}),
        ])
        cities = client(opener, sleep=slept.append).geo_bootstrap("en")
        self.assertEqual(cities, ())
        self.assertEqual(slept, [10.0])
        self.assertEqual(len(opener.requests), 2)

    def test_a_second_server_error_is_raised(self):
        slept = []
        opener = Opener([
            http_error(500, {"title": "Oops", "code": "INTERNAL_ERROR"}),
            http_error(500, {"title": "Oops", "code": "INTERNAL_ERROR"}),
        ])
        with self.assertRaises(api.APIError) as caught:
            client(opener, sleep=slept.append).geo_bootstrap("en")
        self.assertEqual(caught.exception.status, 500)
        self.assertEqual(caught.exception.code, "INTERNAL_ERROR")
        self.assertEqual(len(opener.requests), 2)
        self.assertEqual(slept, [2.0])

    def test_connection_failure_is_not_an_api_error(self):
        opener = Opener([urllib.error.URLError("name resolution failed")])
        with self.assertRaises(api.CoworkingViewError) as caught:
            client(opener).geo_bootstrap("en")
        self.assertNotIsInstance(caught.exception, api.APIError)
        self.assertIn("name resolution failed", str(caught.exception))

    def test_retry_wait_uses_the_documented_headers(self):
        self.assertEqual(api.retry_wait({"Retry-After": "99"}), 10)
        self.assertEqual(api.retry_wait({"RateLimit-Reset": "3"}), 3)
        self.assertEqual(api.retry_wait({}), 2)
        now = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
        waited = api.retry_wait(
            {"Retry-After": "Sat, 31 Oct 2026 12:00:04 GMT"}, now=now
        )
        self.assertEqual(waited, 4)
        capped = api.retry_wait(
            {"Retry-After": "Sat, 31 Oct 2026 12:00:30 GMT"}, now=now
        )
        self.assertEqual(capped, 10)

    def test_validation_helpers(self):
        self.assertEqual(api.check_page_size(1), 1)
        self.assertEqual(api.check_page_size(50), 50)
        with self.assertRaises(ValueError):
            api.check_page_size(51)
        with self.assertRaises(ValueError):
            api.check_page_size(True)
        with self.assertRaises(ValueError):
            api.check_product("office")
        self.assertIsNone(api.check_product(None))
        self.assertEqual(api.check_currency("usd"), "USD")
        with self.assertRaises(ValueError):
            api.check_currency("XXX")
        with self.assertRaises(ValueError):
            api.check_on_select("browser")
        opener = Opener([])
        with self.assertRaises(ValueError):
            client(opener).search_properties("en", page_size=0)
        self.assertEqual(opener.requests, [])

    def test_insert_formats(self):
        listing = api.parse_listing(CARD, "en")
        markdown = api.format_insert(listing, "markdown")
        self.assertEqual(
            markdown,
            "[St. Oberholz \\[Mitte\\]](https://coworkingview.com/en/workspace/"
            "sankt-oberholz-rosenthaler-berlin)",
        )
        self.assertTrue(api.format_insert(listing, "url").startswith("https://"))
        self.assertIn(
            "(https://coworkingview.com/", api.format_insert(listing, "plaintext")
        )
        custom = api.format_insert(listing, "{title} — {city} — {operator}")
        self.assertEqual(custom, "St. Oberholz [Mitte] — Berlin — St. Oberholz")
        self.assertIn("](https://", api.format_insert(listing, "markdwon"))
        with self.assertRaises(ValueError):
            api.format_insert(listing, "  ")

    def test_env_base_url_is_prefixed_to_the_real_path(self):
        key = "COWORKINGVIEW_API_URL"
        previous = os.environ.get(key)
        os.environ[key] = "https://example.test/root/"
        try:
            opener = Opener([Body({"data": {"cities": []}})])
            api.Client(opener=opener).geo_bootstrap("en")
        finally:
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous
        self.assertTrue(
            opener.requests[0].full_url.startswith(
                "https://example.test/root/v1/geo/bootstrap?"
            )
        )


class RequestUrlTests(unittest.TestCase):
    def test_city_filter_is_sent_on_the_rates_endpoint(self):
        payload = {
            "data": {
                "cities": [],
                "attribution": "Source: CoworkingView Price Index.",
            }
        }
        opener = Opener([Body(payload)])
        client(opener).market_rates("de", city="berlin")
        self.assertIn("/v1/market/rates?", opener.requests[0].full_url)
        self.assertIn("locale=de", opener.requests[0].full_url)
        self.assertIn("city=berlin", opener.requests[0].full_url)


class ByTypeUrlTests(unittest.TestCase):
    def test_by_type_request_includes_the_city(self):
        payload = {
            "data": {
                "cities": [
                    {
                        "city": {"slug": "berlin", "name": "Berlin"},
                        "currency": "EUR",
                        "byType": [
                            {
                                "optionType": "flex_desk",
                                "median": {"amount": 214, "currency": "EUR"},
                                "sampleSize": 72,
                            }
                        ],
                    }
                ]
            }
        }
        opener = Opener([Body(payload)])
        report = client(opener).market_rates("en", city="berlin")
        self.assertEqual(report.rows[0].median, 214)
        self.assertIn("/v1/market/rates?", opener.requests[0].full_url)
        self.assertIn("city=berlin", opener.requests[0].full_url)


if __name__ == "__main__":
    unittest.main()
