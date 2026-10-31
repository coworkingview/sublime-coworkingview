# CoworkingView

Search [CoworkingView](https://coworkingview.com) flexible workspaces from the Sublime Text command palette, insert a listing link at the cursor, and read city price-index figures in an output panel.

The package talks only to the public read-only API at `https://api.coworkingview.com`. It does not need an account or an API key. Maintained by [CoworkingView](https://coworkingview.com). This package was built with AI assistance.

## Install

Package Control installs from the semver tag `1.0.0` once this repository is listed in the default channel (the entry to add is in [CHANNEL_ENTRY.md](CHANNEL_ENTRY.md)).

1. Command Palette → **Package Control: Install Package**
2. Select **CoworkingView**

Until that listing is merged, add the repository yourself:

1. Command Palette → **Package Control: Add Repository**
2. Paste `https://github.com/coworkingview/sublime-coworkingview`
3. Command Palette → **Package Control: Install Package** → **CoworkingView**

To install from a clone, the folder Sublime loads must be named `CoworkingView` (the package name), not `sublime-coworkingview`:

```bash
cd "${SUBLIME_PACKAGES}"
git clone https://github.com/coworkingview/sublime-coworkingview.git CoworkingView
```

`${SUBLIME_PACKAGES}` is the Packages directory: **Preferences → Browse Packages…**. On Linux that is `~/.config/sublime-text/Packages`, on macOS `~/Library/Application Support/Sublime Text/Packages`, and on Windows `%AppData%\Sublime Text\Packages`.

Requires Sublime Text 4 (build 4000 or newer). The plugin host is Python 3.8, declared in `.python-version`. The package uses only the Python standard library.

## Commands

Open the Command Palette and type `CoworkingView`. The same commands are under **Tools → CoworkingView**.

| Command | What it does |
|---|---|
| **CoworkingView: Search Workspaces** | Quick panel of cities (from `GET /v1/geo/bootstrap`), then an optional product filter, then one page of listings (`GET /v1/properties`). Choosing a row opens the listing on coworkingview.com, or inserts a link, depending on `on_select`. |
| **CoworkingView: Insert Listing Link** | The same search. Choosing a row always inserts a link at the cursor. |
| **CoworkingView: Market Rates** | Pick a city, then write the Price Index (`GET /v1/market/rates`) into the **CoworkingView** output panel, including the API's attribution line. |
| **Preferences: CoworkingView Settings** | Opens default and user settings side by side. |

City names in the quick panel are the names the API returns for your `locale`. Type to filter. **All cities** skips the city filter. **Enter a city slug...** asks for a slug such as `berlin`. If the selection in the buffer is a single slug (`sankt-oberholz-rosenthaler-berlin`), the first row fetches that listing with `GET /v1/properties/{slug}`.

The product filter is optional. **Any type** is the first row. The values sent as `pricingOptionType` are the API's: `flex_desk`, `fix_desk`, `private_office`, `exclusive_bereich`, `day_pass`, `meeting_room`, `event_space`, `virtual_office`, `membership`.

Each search requests a single page (`page=1`, `pageSize` from settings, `sortBy=date-desc`). The package does not page through the catalogue.

Listing links point at `https://coworkingview.com/{locale}/{workspace|oficina|buero}/{slug}`. A detail response's `webUrl` is used when it is an `https://coworkingview.com/` URL.

## Settings

**Preferences → Package Settings → CoworkingView → Settings**.

| Setting | Default | Values |
|---|---|---|
| `locale` | `"en"` | `"en"`, `"de"`, `"es"`. Language of names, and the path on coworkingview.com (`workspace`, `buero`, `oficina`). |
| `insert_format` | `"markdown"` | `"markdown"` → `[Title](url)`. `"url"` → the URL. `"plaintext"` → `Title (url)`. Any other string containing `{` is a template. |
| `on_select` | `"open"` | `"open"` opens the listing in the browser. `"insert"` inserts `insert_format` at the cursor. `"ask"` shows both choices. **Insert Listing Link** always inserts. |
| `page_size` | `20` | Integer from 1 to 50. How many listings one search requests. |

Template placeholders: `{title}`, `{url}`, `{slug}`, `{city}`, `{zone}`, `{operator}`, `{country}`.

```json
{
	"locale": "de",
	"insert_format": "{title} — {url}",
	"on_select": "ask",
	"page_size": 20
}
```

`COWORKINGVIEW_API_URL`, when set in the environment Sublime was started from, replaces `https://api.coworkingview.com`. The path (`/v1/...`) does not change.

## Key bindings

This package does not ship key bindings, so it cannot override another package. Add your own under **Preferences → Key Bindings**, for example:

```json
[
	{
		"keys": ["ctrl+alt+w"],
		"command": "coworkingview_search_workspaces"
	},
	{
		"keys": ["ctrl+alt+l"],
		"command": "coworkingview_insert_listing_link"
	},
	{
		"keys": ["ctrl+alt+m"],
		"command": "coworkingview_market_rates"
	}
]
```

On macOS, use `super+alt+w` (and the same for the other two) if you prefer the Command key.

## Data, attribution, and limits

- **Price Index** figures (`Market Rates`) are open data under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The output panel prints the `attribution` string and `licenseUrl` returned by `GET /v1/market/rates`. Keep that credit if you copy the figures out. A row marked `low confidence` has a sample size under 10.
- **Catalogue** names and prices belong to the operators. The API allows linking to a listing. It does not allow systematic copying of the catalogue. A list response may include a description and images; this package does not copy those into the quick panel or the inserted link, and it does not follow pagination.
- The API allows 120 requests per minute per IP address. A command makes one or two requests. On HTTP 429 or a 5xx response the client waits (`Retry-After`, otherwise `RateLimit-Reset`, otherwise 2 seconds, never more than 10) and retries once.

## Privacy

There is no telemetry. The package does not send a machine id, the contents of your buffer, or analytics. The only network calls are the GETs you trigger, to `https://api.coworkingview.com` (or `COWORKINGVIEW_API_URL`), with `Accept: application/json` and a `User-Agent` of `CoworkingView-sublime/<version> (+https://github.com/coworkingview/sublime-coworkingview)`.

## Errors

A failed request is shown in a dialog and in the status bar. Validation problems (a bad `locale`, a slug the API would reject) are raised before any request. An empty search sets the status bar to "No workspaces matched." and does not open a dialog.

## Development

Tests mock HTTP and run on the standard library. pytest can collect them, and is not required.

```bash
python3 tests/test_api.py
python3 -m unittest discover -s tests -t . -v
python3 -m pip install "flake8==7.1.1" && python3 -m flake8
```

GitHub Actions runs flake8 and the unit tests on Python 3.8 and 3.12.

The endpoints and fields this package reads are the public ones documented for the [CoworkingView API](https://api.coworkingview.com) and implemented by the clients in [flex-office-data](https://github.com/coworkingview/flex-office-data): `/v1/geo/bootstrap`, `/v1/properties`, `/v1/properties/{slug}`, and `/v1/market/rates`.

## License

Code is MIT. See [LICENSE](LICENSE). Price Index data is CC BY 4.0, as returned by the API. Catalogue content remains with the operators.
