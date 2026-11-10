# CommaView overlay packs SDK

[![Buy me a coffee](https://img.shields.io/badge/Buy%20me%20a%20coffee-support-FFDD00?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/commaview)

An overlay pack adds to what [CommaView](https://commaview.com) draws over a comma's onroad view: a fork's blue
path, a stop marker, a badge, a gauge, a dashcam stamp. This SDK is what you need to write one: the pack format,
the checker that tests a pack the way the comma and the app read it, CommaView's example and official packs, and
the docs.

| Folder | Holds |
| --- | --- |
| [`docs/extensions.md`](docs/extensions.md) | Overlay packs: the files, `manifest.json`, the `export.py` API, the limits a pack runs under, bundles, previews, and how to submit one. Start here. |
| [`docs/overlay-packs.md`](docs/overlay-packs.md) | `overlay.json`, the drawing half: every layer, expression, function and name. |
| [`tools/check-pack.py`](tools/check-pack.py) | The checker: checks a pack's manifest, overlay and preview, runs its `export.py` against a stand-in UI, and writes its `.cvpack`. |
| [`tools/commaview_pack_host.py`](tools/commaview_pack_host.py) | The pack host: the code the comma runs packs with (loading, limits, watchdog, the `ctx` a pack sees). The checker runs your pack through it. |
| [`tools/build-examples.sh`](tools/build-examples.sh) | Checks every example and writes its bundle. |
| [`examples/`](examples) | CommaView's example packs, and its official ones (`bluepilot`, `dragonpilot-hud`). |

You need Python 3.11 or newer, nothing else: the tools use only Python's standard library.

## Getting started

**1. Check a pack.** Start from the example nearest what you want (a standalone one such as
[`datetime`](examples/datetime) draws from what the app already has; [`pack-test`](examples/pack-test) runs code on
the comma too):

```sh
cp -r examples/pack-test my-pack     # then set manifest.json's and overlay.json's "id" to "my-pack"
python3 tools/check-pack.py my-pack
```

The checker reads `overlay.json` as the app reads it and lists every problem at once, then loads `export.py`
through the comma's own pack host against a stand-in UI state (engaged, 10 m/s, a lead 32.5 m ahead), runs 120
frames and prints the pack's state, its last payload and the time a frame took. `--attr`, `--service` and
`--module` set what the stand-in UI has ([docs](docs/extensions.md#check-a-pack)).

**2. Preview it.** To see the pack drawn on your own comma, install it from a file (step 3). CommaView's pack studio,
at [my.commaview.com/studio](https://my.commaview.com/studio), can draw a pack and make its `preview.png` (the still the
store shows); like submitting, it's open to CommaView members with pack access for now. The checker checks a
`preview.png` in the pack's folder by the catalog's rules ([docs](docs/extensions.md#the-preview-previewpng)).

**3. Bundle it.** Once it passes, write its bundle, the one file the app installs:

```sh
python3 tools/check-pack.py my-pack --bundle my-pack.cvpack
```

Send the `.cvpack` to your phone and use the app's Install from a file. A pack nobody reviewed runs only while
your comma allows unreviewed packs (the app's Settings › Experimental tools › Overlay packs).

**4. Submit it.** Submit it at [my.commaview.com › Packs](https://my.commaview.com/packs). Pack submission is
currently open to CommaView members with pack access; ask in the
[CommaView Discord](https://discord.gg/UuMhz9K8ft) to get it. Every submission is reviewed by hand by CommaView before
it's listed; official packs are CommaView's own ([docs](docs/extensions.md#sharing-a-pack-the-catalog)).

`tools/build-examples.sh` checks every example and writes its bundle into `dist/extensions/`.

## Community and links

- [CommaView Discord](https://discord.gg/UuMhz9K8ft): questions, pack access, and showing what you've made
- [commaview.com](https://commaview.com)
- [X](https://x.com/CommaView)
- [YouTube](https://www.youtube.com/@CommaView)
- [Instagram](https://www.instagram.com/commaview/)
- [Buy me a coffee](https://buymeacoffee.com/commaview)

## Contributing

Issues and pull requests are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md). This repository is published from
CommaView's own source, so a maintainer ports an accepted pull request there and it comes back with the next sync.

## License

MIT: see [LICENSE](LICENSE). Each pack says its own license in its `manifest.json`.
