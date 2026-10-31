# Package Control channel entry

Add this object to [`repository/c.json`](https://github.com/sublimehq/package_control_channel/blob/master/repository/c.json) in the [Package Control channel](https://github.com/sublimehq/package_control_channel). Do not reformat the rest of the file. The channel file is indented with tabs; the block below uses tabs as well.

## Alphabetical position

Case-insensitive order in `repository/c.json` places **CoworkingView** after **CoverMe** and before **Cowsay**. Those two entries are adjacent. Insert the object between them. The trailing comma stays, because `Cowsay` follows.

## JSON

```json
		{
			"name": "CoworkingView",
			"details": "https://github.com/coworkingview/sublime-coworkingview",
			"homepage": "https://coworkingview.com",
			"labels": ["coworking", "search"],
			"releases": [
				{
					"sublime_text": ">=4000",
					"tags": true
				}
			]
		},
```

`tags: true` means Package Control installs from semver git tags. The first release tag is `1.0.0`. `sublime_text` is `>=4000` because the package runs on the Sublime Text 4 Python 3.8 plugin host (`.python-version` contains `3.8`).

The package name is `CoworkingView`. It does not contain "Sublime", it is not already in the channel, and the repository root is the package root.
