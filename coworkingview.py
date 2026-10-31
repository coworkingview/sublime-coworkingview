"""Sublime Text commands for the public CoworkingView API.

Importing this module does not touch the network. Each command does its
HTTP work on a worker thread and only then updates the quick panel, the
output panel, or the buffer. Nothing is sent except the request the
command needs, and only to the public API.
"""

import threading

import sublime
import sublime_plugin

import coworkingview_api as api

# (pricingOptionType or None, quick-panel label). Values are the API enum.
_PRODUCTS = (
    (None, "Any type"),
    ("flex_desk", "Flex desk"),
    ("fix_desk", "Fixed desk"),
    ("private_office", "Private office"),
    ("exclusive_bereich", "Exclusive area"),
    ("day_pass", "Day pass"),
    ("meeting_room", "Meeting room"),
    ("event_space", "Event space"),
    ("virtual_office", "Virtual office"),
    ("membership", "Membership"),
)


class Prefs(object):
    """The settings that change a command. Read once, on the UI thread."""

    def __init__(self, locale, insert_format, on_select, page_size):
        self.locale = locale
        self.insert_format = insert_format
        self.on_select = on_select
        self.page_size = page_size


def load_prefs():
    """Read and validate CoworkingView.sublime-settings."""
    settings = sublime.load_settings("CoworkingView.sublime-settings")
    locale = api.check_locale(settings.get("locale", "en"))
    insert_format = settings.get("insert_format", "markdown")
    if not isinstance(insert_format, str) or not insert_format.strip():
        raise ValueError("insert_format must be a non-empty string.")
    on_select = api.check_on_select(settings.get("on_select", "open"))
    page_size = api.check_page_size(settings.get("page_size", 20))
    return Prefs(locale, insert_format.strip(), on_select, page_size)


def _spawn(work, done):
    """Run ``work`` off the UI thread, then call ``done(result, error)``."""

    def target():
        result = None
        error = None
        try:
            result = work()
        except Exception as exc:  # reported locally; never forwarded anywhere
            error = exc

        sublime.set_timeout(lambda: done(result, error), 0)

    threading.Thread(target=target, name="coworkingview", daemon=True).start()


def fail(window, exc):
    """Show ``exc`` in the status bar and in a dialog. No other reporting."""
    message = _error_text(exc)
    window.status_message("CoworkingView: {0}".format(message))
    sublime.error_message("CoworkingView\n\n{0}".format(message))


def _error_text(exc):
    if isinstance(exc, api.APIError):
        return "{0} ({1} {2})".format(exc, exc.status, exc.code)
    return str(exc)


def quick_panel(window, items, on_select, placeholder):
    """``show_quick_panel``, with a placeholder on builds that support it."""
    try:
        modern = int(sublime.version()) >= 4096
    except (TypeError, ValueError):
        modern = False
    if modern:
        window.show_quick_panel(items, on_select, 0, 0, None, placeholder)
    else:
        window.show_quick_panel(items, on_select)


def selected_slug(window):
    """Return the selection when it is a single API slug, else ``None``."""
    view = window.active_view()
    if view is None or len(view.sel()) != 1:
        return None
    region = view.sel()[0]
    if region.empty():
        return None
    text = view.substr(region).strip().lower()
    if len(text) < 3 or len(text) > 200 or not api.SLUG_RE.match(text):
        return None
    return text


def _city_item(city):
    bits = []
    if city.country_code:
        bits.append(city.country_code)
    if city.property_count is not None:
        bits.append("{0} spaces".format(city.property_count))
    bits.append(city.slug)
    return [city.name, " · ".join(bits)]


def _listing_item(listing):
    bits = []
    if listing.zone:
        bits.append(listing.zone)
    if listing.city:
        bits.append(listing.city)
    if listing.operator:
        bits.append(listing.operator)
    price = api.format_price(listing)
    if price:
        bits.append("from {0}".format(price))
    if listing.featured:
        bits.append("featured")
    return [listing.title, " · ".join(bits) if bits else listing.slug]


def _insert(window, listing, insert_format):
    view = window.active_view()
    if view is None:
        sublime.error_message(
            "CoworkingView\n\n"
            "Open a file first. The link is inserted at the cursor."
        )
        return
    try:
        text = api.format_insert(listing, insert_format)
    except ValueError as exc:
        fail(window, exc)
        return
    view.run_command("coworkingview_insert_text", {"text": text})
    window.status_message(
        "CoworkingView: Inserted a link to {0}".format(listing.title)
    )


def _show_output(window, text):
    panel = window.create_output_panel("coworkingview")
    panel.set_name("CoworkingView")
    panel.settings().set("word_wrap", False)
    panel.set_read_only(False)
    panel.run_command("coworkingview_replace_view", {"text": text})
    panel.set_read_only(True)
    window.run_command("show_panel", {"panel": "output.coworkingview"})


class _SearchSession(object):
    """City, optional product, then one page of listings."""

    def __init__(self, window, action):
        self.window = window
        # ``None`` means "use the on_select setting". "insert" forces a link.
        self.action = action
        self.prefs = None
        self.choices = []
        self.city_slug = None
        self.product = None
        self.result = None

    def start(self):
        try:
            self.prefs = load_prefs()
        except ValueError as exc:
            fail(self.window, exc)
            return
        if self.action is None:
            self.action = self.prefs.on_select
        self.window.status_message("CoworkingView: Loading cities...")
        locale = self.prefs.locale
        _spawn(lambda: api.Client().geo_bootstrap(locale), self._cities_ready)

    def _cities_ready(self, cities, error):
        if error is not None:
            fail(self.window, error)
            return
        self.choices = []
        items = []
        slug = selected_slug(self.window)
        if slug:
            self.choices.append(("slug", slug))
            items.append([
                "Selected slug: {0}".format(slug),
                "Fetch this listing",
            ])
        for city in cities:
            self.choices.append(("city", city.slug))
            items.append(_city_item(city))
        self.choices.append(("city", None))
        items.append(["All cities", "Do not filter by city"])
        self.choices.append(("typed", None))
        items.append([
            "Enter a city slug...",
            "Lowercase letters, digits, and hyphens",
        ])
        quick_panel(self.window, items, self._on_city, "City")

    def _on_city(self, index):
        if index < 0 or index >= len(self.choices):
            return
        kind, value = self.choices[index]
        if kind == "slug":
            self._load_detail(value)
            return
        if kind == "typed":
            self.window.show_input_panel(
                "City slug", "", self._on_typed_city, None, None
            )
            return
        self.city_slug = value
        self._ask_type()

    def _on_typed_city(self, text):
        try:
            self.city_slug = api.check_slug(text.strip().lower(), "city")
        except ValueError as exc:
            fail(self.window, exc)
            return
        self._ask_type()

    def _ask_type(self):
        items = []
        for product, label in _PRODUCTS:
            detail = "No product filter" if product is None else product
            items.append([label, detail])
        quick_panel(
            self.window, items, self._on_type, "Workspace type (optional)"
        )

    def _on_type(self, index):
        if index < 0 or index >= len(_PRODUCTS):
            return
        self.product = _PRODUCTS[index][0]
        self.window.status_message("CoworkingView: Searching...")
        _spawn(self._search, self._results_ready)

    def _search(self):
        return api.Client().search_properties(
            self.prefs.locale,
            city=self.city_slug,
            product=self.product,
            page_size=self.prefs.page_size,
        )

    def _results_ready(self, result, error):
        if error is not None:
            fail(self.window, error)
            return
        self.result = result
        if not result.listings:
            self.window.status_message("CoworkingView: No workspaces matched.")
            return
        shown = len(result.listings)
        if result.total is None:
            status = "CoworkingView: {0} workspaces.".format(shown)
        else:
            status = "CoworkingView: {0} of {1} workspaces.".format(
                shown, result.total
            )
        if result.citation:
            status = "{0} {1}".format(status, result.citation)
        self.window.status_message(status)
        items = [_listing_item(item) for item in result.listings]
        quick_panel(self.window, items, self._on_listing, "Workspaces")

    def _load_detail(self, slug):
        self.window.status_message("CoworkingView: Loading listing...")
        locale = self.prefs.locale
        _spawn(
            lambda: api.Client().property_detail(slug, locale),
            self._detail_ready,
        )

    def _detail_ready(self, listing, error):
        if error is not None:
            fail(self.window, error)
            return
        self._apply(listing)

    def _on_listing(self, index):
        if self.result is None or index < 0 or index >= len(self.result.listings):
            return
        self._apply(self.result.listings[index])

    def _apply(self, listing):
        if self.action == "ask":
            preview = api.format_insert(listing, self.prefs.insert_format)
            quick_panel(
                self.window,
                [
                    ["Open in browser", listing.url],
                    ["Insert link at cursor", preview],
                ],
                lambda index: self._finish_ask(listing, index),
                "Use this listing",
            )
            return
        self._finish(listing, self.action)

    def _finish_ask(self, listing, index):
        if index == 0:
            self._finish(listing, "open")
        elif index == 1:
            self._finish(listing, "insert")

    def _finish(self, listing, action):
        if action == "insert":
            _insert(self.window, listing, self.prefs.insert_format)
            return
        sublime.run_command("open_url", {"url": listing.url})
        self.window.status_message(
            "CoworkingView: Opened {0}".format(listing.title)
        )


class _RatesSession(object):
    """Pick a city, then write the Price Index into an output panel."""

    def __init__(self, window):
        self.window = window
        self.prefs = None
        self.slugs = []

    def start(self):
        try:
            self.prefs = load_prefs()
        except ValueError as exc:
            fail(self.window, exc)
            return
        self.window.status_message("CoworkingView: Loading cities...")
        locale = self.prefs.locale
        _spawn(lambda: api.Client().geo_bootstrap(locale), self._cities_ready)

    def _cities_ready(self, cities, error):
        if error is not None:
            fail(self.window, error)
            return
        self.slugs = [None]
        items = [["All published cities", "Every city in the Price Index"]]
        for city in cities:
            self.slugs.append(city.slug)
            items.append(_city_item(city))
        quick_panel(self.window, items, self._on_city, "City")

    def _on_city(self, index):
        if index < 0 or index >= len(self.slugs):
            return
        city = self.slugs[index]
        self.window.status_message("CoworkingView: Loading market rates...")
        locale = self.prefs.locale
        _spawn(
            lambda: api.Client().market_rates(locale, city=city),
            self._ready,
        )

    def _ready(self, report, error):
        if error is not None:
            fail(self.window, error)
            return
        _show_output(self.window, api.format_market_report(report))


class CoworkingviewSearchWorkspacesCommand(sublime_plugin.WindowCommand):
    """CoworkingView: Search Workspaces."""

    def run(self):
        _SearchSession(self.window, None).start()


class CoworkingviewInsertListingLinkCommand(sublime_plugin.WindowCommand):
    """CoworkingView: Insert Listing Link."""

    def run(self):
        _SearchSession(self.window, "insert").start()


class CoworkingviewMarketRatesCommand(sublime_plugin.WindowCommand):
    """CoworkingView: Market Rates."""

    def run(self):
        _RatesSession(self.window).start()


class CoworkingviewInsertTextCommand(sublime_plugin.TextCommand):
    """Insert text at every cursor. Not listed in the Command Palette."""

    def run(self, edit, text):
        for region in self.view.sel():
            self.view.replace(edit, region, text)


class CoworkingviewReplaceViewCommand(sublime_plugin.TextCommand):
    """Replace an output panel's contents. Not listed in the Command Palette."""

    def run(self, edit, text):
        self.view.replace(edit, sublime.Region(0, self.view.size()), text)
