"""Read-only client for the public CoworkingView API.

Endpoints (https://api.coworkingview.com), and nothing else:

* ``GET /v1/geo/bootstrap``
* ``GET /v1/properties``
* ``GET /v1/properties/{slug}``
* ``GET /v1/market/rates``

Stdlib only. No network call happens at import time, and nothing here
phones home, logs usage, or reads credentials. The catalogue may be
searched and linked; this client requests a single page of at most 50
listings and does not walk cities to copy the catalogue.
"""

from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional, Tuple

__version__ = "1.0.0"

DEFAULT_BASE_URL = "https://api.coworkingview.com"
DEFAULT_TIMEOUT = 20.0
DEFAULT_USER_AGENT = (
    "CoworkingView-sublime/{version} "
    "(+https://github.com/coworkingview/sublime-coworkingview)"
).format(version=__version__)

SITE_URL = "https://coworkingview.com"
LOCALES = ("en", "de", "es")
WORKSPACE_PATH = {"en": "workspace", "es": "oficina", "de": "buero"}
PRODUCTS = (
    "flex_desk",
    "fix_desk",
    "private_office",
    "exclusive_bereich",
    "day_pass",
    "meeting_room",
    "event_space",
    "virtual_office",
    "membership",
)
CURRENCIES = ("EUR", "GBP", "USD", "CHF", "AED", "VES")
ON_SELECT = ("open", "insert", "ask")
INSERT_PRESETS = {
    "markdown": "[{title}]({url})",
    "url": "{url}",
    "plaintext": "{title} ({url})",
}
SORT_BY = "date-desc"

SLUG_RE = re.compile(r"^[a-z0-9-]+$")
_CAMEL = re.compile(r"(?<!^)(?=[A-Z])")

FALLBACK_WAIT = 2.0
MAX_WAIT = 10.0

_STAT_UNITS = {
    "day_pass": "day",
    "meeting_room": "hour",
    "event_space": "hour",
    "virtual_office": "month",
}
_PLACEHOLDERS = (
    "title",
    "url",
    "slug",
    "city",
    "zone",
    "operator",
    "country",
)


class CoworkingViewError(Exception):
    """Base error. Raised directly when the API cannot be reached."""


class APIError(CoworkingViewError):
    """The API answered with a problem document, or with a body that is not JSON."""

    def __init__(self, status, code, title, detail=None):
        # type: (int, str, str, Optional[str]) -> None
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        message = title if not detail else "{0}: {1}".format(title, detail)
        super(APIError, self).__init__(message)


@dataclass(frozen=True)
class City:
    """One city from ``GET /v1/geo/bootstrap``."""

    slug: str
    name: str
    country_code: str
    property_count: Optional[int]


@dataclass(frozen=True)
class Listing:
    """A space the user can open or link to. No description, photos, or logo."""

    slug: str
    title: str
    url: str
    city: Optional[str]
    zone: Optional[str]
    operator: Optional[str]
    country_code: Optional[str]
    from_amount: Optional[float]
    from_currency: Optional[str]
    from_period: Optional[str]
    featured: Optional[bool]


@dataclass(frozen=True)
class SearchResult:
    """One page of :class:`Listing` plus the citation the API asked us to keep."""

    listings: Tuple[Listing, ...]
    total: Optional[int]
    citation: Optional[str]
    notice: Optional[str]


@dataclass(frozen=True)
class RateRow:
    """One Price Index figure: a city crossed with a product."""

    city_slug: str
    city_name: str
    country_code: Optional[str]
    currency: str
    product: str
    unit: Optional[str]
    median: float
    p25: Optional[float]
    p75: Optional[float]
    sample_size: int
    property_count: Optional[int]
    operator_count: Optional[int]
    low_confidence: bool
    as_of: Optional[str]


@dataclass(frozen=True)
class RateReport:
    """``GET /v1/market/rates`` reduced to rows, with the attribution intact."""

    rows: Tuple[RateRow, ...]
    attribution: Optional[str]
    license_url: Optional[str]
    as_of: Optional[str]


def check_locale(value):
    # type: (object) -> str
    """Return ``value`` when it is ``en``, ``de``, or ``es``."""
    if value not in LOCALES:
        raise ValueError('locale must be "en", "de", or "es".')
    return str(value)


def check_on_select(value):
    # type: (object) -> str
    """Return ``value`` when it is ``open``, ``insert``, or ``ask``."""
    if value not in ON_SELECT:
        raise ValueError('on_select must be "open", "insert", or "ask".')
    return str(value)


def check_page_size(value):
    # type: (object) -> int
    """Return ``value`` when it is an integer from 1 to 50."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("page_size must be an integer from 1 to 50.")
    if value < 1 or value > 50:
        raise ValueError("page_size must be an integer from 1 to 50.")
    return value


def check_slug(value, label="slug"):
    # type: (object, str) -> str
    """Reject a path segment the API would not accept."""
    if not isinstance(value, str) or len(value) > 200 or not SLUG_RE.match(value):
        raise ValueError(
            "{0} must use lowercase letters, digits, and hyphens "
            "(for example berlin).".format(label)
        )
    return value


def check_product(value):
    # type: (object) -> Optional[str]
    """Return a real ``pricingOptionType``, or ``None`` for no filter."""
    if value is None:
        return None
    if value not in PRODUCTS:
        allowed = ", ".join(PRODUCTS)
        raise ValueError("product must be one of: {0}.".format(allowed))
    return str(value)


def check_currency(value):
    # type: (object) -> Optional[str]
    """Return an ISO currency the API converts, or ``None``."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("currency must be a string.")
    code = value.upper()
    if code not in CURRENCIES:
        allowed = ", ".join(CURRENCIES)
        raise ValueError("currency must be one of: {0}.".format(allowed))
    return code


def listing_url(slug, locale):
    # type: (str, str) -> str
    """Public listing page. The path segment depends on the locale."""
    check_slug(slug)
    check_locale(locale)
    return "{0}/{1}/{2}/{3}".format(SITE_URL, locale, WORKSPACE_PATH[locale], slug)


def format_amount(amount):
    # type: (object) -> Optional[str]
    """Render a JSON number without a trailing ``.0``."""
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        return None
    if isinstance(amount, float) and amount.is_integer():
        return str(int(amount))
    return str(amount)


def format_price(listing):
    # type: (Listing) -> Optional[str]
    """``214 EUR / month`` from the card's ``fromPrice``, or ``None``."""
    amount = format_amount(listing.from_amount)
    if amount is None:
        return None
    text = amount if not listing.from_currency else "{0} {1}".format(
        amount, listing.from_currency
    )
    if listing.from_period:
        text = "{0} / {1}".format(text, listing.from_period)
    return text


def format_insert(listing, insert_format):
    # type: (Listing, object) -> str
    """Render the cursor insertion for ``insert_format``.

    Known presets are ``markdown``, ``url``, and ``plaintext``. Any other
    string that contains ``{`` is a template. Anything else falls back to
    the Markdown link.
    """
    if not isinstance(insert_format, str) or not insert_format.strip():
        raise ValueError("insert_format must be a non-empty string.")
    raw = insert_format.strip()
    if raw in INSERT_PRESETS:
        template = INSERT_PRESETS[raw]
        markdown = raw == "markdown"
    elif "{" in raw:
        template = raw
        markdown = False
    else:
        template = INSERT_PRESETS["markdown"]
        markdown = True
    title = _md_label(listing.title) if markdown else listing.title
    url = _md_url(listing.url) if markdown else listing.url
    values = {
        "title": title,
        "url": url,
        "slug": listing.slug,
        "city": listing.city or "",
        "zone": listing.zone or "",
        "operator": listing.operator or "",
        "country": listing.country_code or "",
    }
    rendered = template
    for key in _PLACEHOLDERS:
        rendered = rendered.replace("{" + key + "}", values[key])
    return rendered


def format_market_report(report):
    # type: (RateReport) -> str
    """Plain-text Price Index for an output panel, attribution included."""
    lines = ["CoworkingView Price Index", SITE_URL]  # type: List[str]
    if report.as_of:
        lines.append("as of {0}".format(report.as_of))
    lines.append("")
    if not report.rows:
        lines.append("No published figures.")
    current = None  # type: Optional[Tuple[str, str, Optional[str], str]]
    for row in report.rows:
        key = (row.city_slug, row.city_name, row.country_code, row.currency)
        if key != current:
            if current is not None:
                lines.append("")
            current = key
            bits = [row.city_name, row.city_slug]
            if row.country_code:
                bits.append(row.country_code)
            bits.append(row.currency)
            lines.append(" · ".join(bits))
        lines.append(_format_rate_line(row))
    lines.append("")
    if report.attribution:
        lines.append(report.attribution)
    if report.license_url and report.license_url not in (report.attribution or ""):
        lines.append(report.license_url)
    lines.append("")
    return "\n".join(lines)


def retry_wait(headers, now=None):
    # type: (Mapping[str, str], Optional[datetime]) -> float
    """Seconds to wait before the single retry, capped at 10.

    Uses ``Retry-After``, else ``RateLimit-Reset``, else 2 seconds.
    """
    wait = _seconds(_header(headers, "Retry-After"), now=now, allow_date=True)
    if wait is None:
        wait = _seconds(_header(headers, "RateLimit-Reset"), now=now, allow_date=False)
    if wait is None:
        wait = FALLBACK_WAIT
    return min(wait, MAX_WAIT)


class Client:
    """JSON ``GET`` client. One retry on ``429`` or ``5xx``. No key."""

    def __init__(self, base_url=None, timeout=DEFAULT_TIMEOUT, user_agent=None,
                 opener=None, sleep=None):
        # type: (Optional[str], float, Optional[str], Any, Any) -> None
        resolved = base_url or os.environ.get("COWORKINGVIEW_API_URL")
        if not resolved:
            resolved = DEFAULT_BASE_URL
        bad_timeout = isinstance(timeout, bool) or not isinstance(timeout, (int, float))
        if bad_timeout or timeout <= 0:
            raise ValueError("timeout must be a positive number of seconds.")
        self.base_url = resolved.rstrip("/")
        self.timeout = float(timeout)
        self.user_agent = user_agent or DEFAULT_USER_AGENT
        self._opener = opener or urllib.request.build_opener()
        self._sleep = sleep or time.sleep

    def geo_bootstrap(self, locale):
        # type: (str) -> Tuple[City, ...]
        """Countries are ignored; the command palette needs cities."""
        locale = check_locale(locale)
        payload = self._get("/v1/geo/bootstrap", {"locale": locale})
        data = _data_dict(payload, "GET /v1/geo/bootstrap")
        cities = data.get("cities")
        if not isinstance(cities, list):
            raise _bad("GET /v1/geo/bootstrap")
        parsed = []  # type: List[City]
        for item in cities:
            city = _parse_bootstrap_city(item)
            if city is not None:
                parsed.append(city)
        parsed.sort(key=lambda city: (city.name.casefold(), city.slug))
        return tuple(parsed)

    def search_properties(self, locale, city=None, product=None, page_size=20,
                          sort_by=SORT_BY):
        # type: (str, Optional[str], Optional[str], int, str) -> SearchResult
        """One page of ``GET /v1/properties``. ``page_size`` is at most 50."""
        locale = check_locale(locale)
        page_size = check_page_size(page_size)
        product = check_product(product)
        if city is not None:
            city = check_slug(city, "city")
        if sort_by not in ("random", "date-desc", "date-asc", "price-asc", "distance"):
            raise ValueError("sort_by is not a sort the API accepts.")
        params = {
            "locale": locale,
            "city": city,
            "pricingOptionType": product,
            "page": 1,
            "pageSize": page_size,
            "sortBy": sort_by,
        }  # type: Dict[str, Any]
        payload = self._get("/v1/properties", params)
        data = _data_list(payload, "GET /v1/properties")
        listings = tuple(parse_listing(card, locale) for card in data)
        citation, total, notice = _meta_bits(payload)
        return SearchResult(listings, total, citation, notice)

    def property_detail(self, slug, locale):
        # type: (str, str) -> Listing
        """``GET /v1/properties/{slug}``. ``webUrl`` wins when it is on the site."""
        locale = check_locale(locale)
        slug = check_slug(slug)
        payload = self._get("/v1/properties/" + slug, {"locale": locale})
        data = _data_dict(payload, "GET /v1/properties/{slug}")
        listing = parse_listing(data, locale)
        web_url = data.get("webUrl")
        if isinstance(web_url, str) and web_url.startswith(SITE_URL + "/"):
            listing = replace(listing, url=web_url)
        return listing

    def market_rates(self, locale, city=None, currency=None):
        # type: (str, Optional[str], Optional[str]) -> RateReport
        """``GET /v1/market/rates`` as one row per published city and product."""
        locale = check_locale(locale)
        currency = check_currency(currency)
        if city is not None:
            city = check_slug(city, "city")
        payload = self._get(
            "/v1/market/rates",
            {"locale": locale, "city": city, "currency": currency},
        )
        data = _data_dict(payload, "GET /v1/market/rates")
        rows = tuple(_rate_rows(data))
        as_of = data.get("asOf") if isinstance(data.get("asOf"), str) else None
        attribution = data.get("attribution")
        if not isinstance(attribution, str):
            attribution = None
        license_url = data.get("licenseUrl")
        if not isinstance(license_url, str):
            license_url = None
        return RateReport(rows, attribution, license_url, as_of)

    def _get(self, path, params):
        # type: (str, Mapping[str, Any]) -> Dict[str, Any]
        url = self._url(path, params)
        for attempt in (1, 2):
            status, reason, headers, raw = self._fetch(url)
            if 200 <= status <= 299:
                return _decode_object(raw, status, reason, path)
            if attempt == 1 and (status == 429 or 500 <= status <= 599):
                self._sleep(retry_wait(headers))
                continue
            raise _problem(status, reason, raw)
        raise AssertionError("unreachable")  # pragma: no cover

    def _url(self, path, params):
        # type: (str, Mapping[str, Any]) -> str
        query = {key: value for key, value in params.items() if value is not None}
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        return url

    def _fetch(self, url):
        # type: (str) -> Tuple[int, str, Any, bytes]
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": self.user_agent},
            method="GET",
        )
        try:
            response = self._opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as err:
            try:
                raw = err.read()
            finally:
                err.close()
            return err.code, str(err.reason or ""), err.headers, raw
        except (
            urllib.error.URLError,
            socket.timeout,
            TimeoutError,
            ConnectionError,
        ) as err:
            reason = getattr(err, "reason", err)
            raise CoworkingViewError(
                "Could not reach {0}: {1}".format(self.base_url, reason)
            ) from err
        with response:
            status = getattr(response, "status", None) or response.getcode()
            reason = getattr(response, "reason", "") or ""
            return status, reason, response.headers, response.read()


def parse_listing(card, locale):
    # type: (Mapping[str, Any], str) -> Listing
    """Map a property card or detail object onto a :class:`Listing`.

    ``excerpt``, ``description``, ``images``, and operator logos are not copied.
    """
    if not isinstance(card, dict):
        raise _bad("GET /v1/properties")
    slug = card.get("slug")
    title = card.get("title")
    if not isinstance(slug, str) or not isinstance(title, str):
        raise APIError(
            200,
            "BAD_RESPONSE",
            "A listing is missing slug or title",
            "GET /v1/properties",
        )
    check_slug(slug)
    check_locale(locale)
    price = card.get("fromPrice") if isinstance(card.get("fromPrice"), dict) else {}
    amount = price.get("amount")
    currency = price.get("currency")
    period = card.get("fromBillingPeriod")
    featured = card.get("featured")
    return Listing(
        slug=slug,
        title=title,
        url=listing_url(slug, locale),
        city=_nested_name(card.get("city")),
        zone=_nested_name(card.get("zone")),
        operator=_nested_name(card.get("operator")),
        country_code=_text(card.get("countryCode")),
        from_amount=amount if _is_number(amount) else None,
        from_currency=currency if isinstance(currency, str) else None,
        from_period=period if isinstance(period, str) else None,
        featured=featured if isinstance(featured, bool) else None,
    )


def _parse_bootstrap_city(item):
    # type: (object) -> Optional[City]
    if not isinstance(item, dict):
        return None
    slug = item.get("slug")
    name = item.get("name")
    country = item.get("countryCode")
    if not isinstance(slug, str) or not isinstance(name, str):
        return None
    if not SLUG_RE.match(slug):
        return None
    count = item.get("propertyCount")
    if isinstance(count, bool) or not isinstance(count, int):
        count = None
    return City(
        slug=slug,
        name=name,
        country_code=country if isinstance(country, str) else "",
        property_count=count,
    )


def _rate_rows(data):
    # type: (Mapping[str, Any]) -> List[RateRow]
    """SPEC section 3: figures, else byType, plus top-level stat objects."""
    as_of = data.get("asOf") if isinstance(data.get("asOf"), str) else None
    rows = []  # type: List[RateRow]
    cities = data.get("cities") or []
    if not isinstance(cities, list):
        raise _bad("GET /v1/market/rates")
    for city in cities:
        if not isinstance(city, dict):
            raise _bad("GET /v1/market/rates")
        place = city.get("city") if isinstance(city.get("city"), dict) else None
        if place is None or not isinstance(place.get("slug"), str):
            raise _bad("GET /v1/market/rates")
        name_ok = isinstance(place.get("name"), str)
        currency_ok = isinstance(city.get("currency"), str)
        if not name_ok or not currency_ok:
            raise _bad("GET /v1/market/rates")
        base_slug = place["slug"]
        base_name = place["name"]
        country = _text(city.get("countryCode"))
        currency = city["currency"]
        series = city.get("figures") or city.get("byType") or []
        if not isinstance(series, list):
            raise _bad("GET /v1/market/rates")
        for item in series:
            rows.append(
                _monthly_row(
                    item, base_slug, base_name, country, currency, as_of
                )
            )
        for key, value in city.items():
            if not _is_stat(value):
                continue
            rows.append(
                _stat_row(key, value, base_slug, base_name, country, currency, as_of)
            )
    rows.sort(key=lambda row: (row.city_slug, _product_key(row.product)))
    return rows


def _monthly_row(item, city_slug, city_name, country, currency, as_of):
    # type: (object, str, str, Optional[str], str, Optional[str]) -> RateRow
    if not isinstance(item, dict) or not isinstance(item.get("optionType"), str):
        raise _bad("GET /v1/market/rates")
    median = _amount(item.get("median"))
    sample = item.get("sampleSize")
    if median is None or not isinstance(sample, int) or isinstance(sample, bool):
        raise _bad("GET /v1/market/rates")
    return RateRow(
        city_slug=city_slug,
        city_name=city_name,
        country_code=country,
        currency=currency,
        product=item["optionType"],
        unit="month",
        median=float(median),
        p25=_amount(item.get("p25")),
        p75=_amount(item.get("p75")),
        sample_size=sample,
        property_count=_count(item.get("propertyCount")),
        operator_count=_count(item.get("operatorCount")),
        low_confidence=sample < 10,
        as_of=as_of,
    )


def _stat_row(key, value, city_slug, city_name, country, currency, as_of):
    """One row from a top-level stat object such as ``dayPass``."""
    product = _CAMEL.sub("_", key).lower()
    median = _amount(value.get("median"))
    sample = value.get("sampleSize")
    if median is None or not isinstance(sample, int) or isinstance(sample, bool):
        raise _bad("GET /v1/market/rates")
    return RateRow(
        city_slug=city_slug,
        city_name=city_name,
        country_code=country,
        currency=currency,
        product=product,
        unit=_STAT_UNITS.get(product),
        median=float(median),
        p25=_amount(value.get("p25")),
        p75=_amount(value.get("p75")),
        sample_size=sample,
        property_count=_count(value.get("propertyCount")),
        operator_count=_count(value.get("operatorCount")),
        low_confidence=sample < 10,
        as_of=as_of,
    )


def _format_rate_line(row):
    # type: (RateRow) -> str
    median = format_amount(row.median) or str(row.median)
    parts = [
        "  {0}".format(row.product.ljust(18)),
        (row.unit or "-").ljust(6),
        "median {0}".format(median),
    ]
    if row.p25 is not None:
        parts.append("p25 {0}".format(format_amount(row.p25)))
    if row.p75 is not None:
        parts.append("p75 {0}".format(format_amount(row.p75)))
    parts.append("n={0}".format(row.sample_size))
    if row.property_count is not None:
        parts.append("properties={0}".format(row.property_count))
    if row.low_confidence:
        parts.append("low confidence")
    return "  ".join(parts)


def _product_key(product):
    # type: (str) -> Tuple[int, int, str]
    if product in PRODUCTS:
        return (0, PRODUCTS.index(product), "")
    return (1, 0, product)


def _is_stat(value):
    # type: (object) -> bool
    return isinstance(value, dict) and "median" in value and "sampleSize" in value


def _amount(money):
    # type: (object) -> Optional[float]
    if not isinstance(money, dict) or "amount" not in money:
        return None
    amount = money.get("amount")
    if not _is_number(amount):
        return None
    return float(amount)


def _count(value):
    # type: (object) -> Optional[int]
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _is_number(value):
    # type: (object) -> bool
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _text(value):
    # type: (object) -> Optional[str]
    return value if isinstance(value, str) else None


def _nested_name(value):
    # type: (object) -> Optional[str]
    if isinstance(value, dict) and isinstance(value.get("name"), str):
        return value["name"]
    return None


def _data_dict(payload, what):
    # type: (Mapping[str, Any], str) -> Dict[str, Any]
    data = _data(payload, what)
    if not isinstance(data, dict):
        raise _bad(what)
    return data


def _data_list(payload, what):
    # type: (Mapping[str, Any], str) -> List[Any]
    data = _data(payload, what)
    if not isinstance(data, list):
        raise _bad(what)
    return data


def _data(payload, what):
    # type: (Mapping[str, Any], str) -> Any
    if not isinstance(payload, dict) or "data" not in payload:
        raise APIError(200, "BAD_RESPONSE", "The API response has no data member", what)
    return payload["data"]


def _meta_bits(payload):
    # type: (Mapping[str, Any]) -> Tuple[Optional[str], Optional[int], Optional[str]]
    meta = payload.get("meta") if isinstance(payload, dict) else None
    if not isinstance(meta, dict):
        return None, None, None
    citation = meta.get("citation")
    text = citation.get("text") if isinstance(citation, dict) else None
    if not isinstance(text, str):
        text = None
    notice = meta.get("catalogueNotice")
    if not isinstance(notice, str):
        notice = None
    total = None
    pagination = meta.get("pagination")
    if isinstance(pagination, dict) and _count(pagination.get("total")) is not None:
        total = pagination["total"]
    return text, total, notice


def _bad(what):
    # type: (str) -> APIError
    return APIError(200, "BAD_RESPONSE", "Unexpected API payload", what)


def _decode_object(raw, status, reason, path):
    # type: (bytes, int, str, str) -> Dict[str, Any]
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise APIError(
            status,
            "BAD_RESPONSE",
            "The API returned a body that is not valid JSON",
            "GET {0}".format(path),
        ) from None
    if not isinstance(payload, dict):
        raise APIError(
            status,
            "BAD_RESPONSE",
            "The API returned JSON that is not an object",
            "GET {0}".format(path),
        )
    return payload


def _problem(status, reason, raw):
    # type: (int, str, bytes) -> APIError
    body = None  # type: Any
    try:
        body = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, ValueError):
        body = None
    if not isinstance(body, dict):
        body = {}
    title = body.get("title")
    detail = body.get("detail")
    code = body.get("code")
    if not isinstance(title, str) or not title:
        title = reason or "HTTP {0}".format(status)
    if not isinstance(code, str) or not code:
        code = "HTTP_{0}".format(status)
    return APIError(
        status=status,
        code=code,
        title=title,
        detail=detail if isinstance(detail, str) else None,
    )


def _header(headers, name):
    # type: (Any, str) -> Optional[str]
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    value = getter(name)
    if value is not None:
        return str(value)
    if isinstance(headers, dict):
        for key, item in headers.items():
            if str(key).lower() == name.lower():
                return str(item)
    return None


def _seconds(value, now, allow_date):
    # type: (Optional[str], Optional[datetime], bool) -> Optional[float]
    if value is None:
        return None
    text = value.strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    if not allow_date:
        return None
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return max(0.0, (when - current).total_seconds())


def _md_label(title):
    # type: (str) -> str
    return title.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _md_url(url):
    # type: (str) -> str
    return url.replace(" ", "%20").replace(")", "%29")
