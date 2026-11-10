# Overlay packs (preview)

An overlay pack is how a fork's own additions to the onroad screen reach CommaView without waiting for CommaView to
add them: a blue path, a stop marker, a badge, a gauge. You write it for your own comma, and you can share it.

> **Preview.** The format is SDK 1 and may still change. The app installs packs on a comma and draws them only in
> builds that have the overlay renderer; my.commaview.com reviews and signs shared packs before the app offers them.

A pack has two halves that travel together:

| File | Runs on | Does |
| --- | --- | --- |
| `manifest.json` | both | Names the pack and says what it needs. |
| `export.py` | the comma, in the fork's UI process | Reads what the fork's UI already knows and returns a small JSON object, up to 20 times a second. |
| `overlay.json` | the phone | Describes what to draw from that object, with the app's built-in parts (path colour, path chevrons, a line across the path, bands along the road, a badge, text with live values, shapes and outlines, glows, pictures, and groups that can sit on the road at a distance or at the lead car). It's data, not code: the app interprets it. |

```
fork UI process (comma)              CommaView runtime            CommaView app
  export.py → payload() ──► uiStateOnroad.ext["<id>"] ──passes through──► overlay.json ──► drawn
```

The runtime doesn't read the block: anything your `export.py` returns reaches the app as-is, so a pack never needs
a CommaView release. The app draws only what its overlay parts can draw ([overlay-packs.md](overlay-packs.md)); a new
kind of part is an app release, once, for every pack.

A pack that needs nothing from the comma (a clock, a speed readout) can leave out the runtime half: a
[standalone pack](#standalone-packs), drawn by the app alone.

## The example packs

[`examples/`](../examples) has a pack for each thing a pack can be:

| Pack | For | Shows |
| --- | --- | --- |
| [`bluepath`](../examples/bluepath) | TonyBinheWu/sunnypilot's hkg-enhanced | The worked example: the blue path and predicted stop marker, as a pack. |
| [`carrot-hud`](../examples/carrot-hud) | carrotpilot, comma 3X | carrotpilot's own HUD additions: its follow-distance line with its label pinned beside it, its speed limit / speed camera box (blinking for a camera) and the distance box at the lead car. Its [README](../examples/carrot-hud/README.md) says what it leaves out, and why. |
| [`bluepilot`](../examples/bluepilot) | BluePilot (BluePilotDev/bluepilot), the app's comma 3X and comma four designs (level 2) | What BluePilot draws on top of sunnypilot's screen, each where its own setting turns it on. Its [README](../examples/bluepilot/README.md) catalogues every element BluePilot adds and what the pack does with each. |
| [`dragonpilot-hud`](../examples/dragonpilot-hud) | dragonpilot, the app's comma 3X design (level 2) | dragonpilot's own comma 3X additions: its rainbow path, the lead's speed, distance and time to reach it, its radar tracks, the blinker and blind spot bars, its torque bar, and its hide-the-HUD-above-a-speed setting. Its [README](../examples/dragonpilot-hud/README.md) lists every addition. |
| [`commaview-overlay`](../examples/commaview-overlay) | any comma, any fork | CommaView's own onroad design redrawn as a standalone pack and offered as a design of its own. |
| [`datetime`](../examples/datetime) | any comma, any fork | A dashcam-style date and time stamp, bottom left. Standalone: nothing goes on the comma. |
| [`shapes`](../examples/shapes) | any comma, any fork | The shapes, pictures and groups at once: a speed gauge, a blinking dot, a speed bar, a picture turning in a group, a badge pinned to the lead car and dots pinned to the path. Standalone. |
| [`pack-test`](../examples/pack-test) | any comma, any fork | Tests the whole pipeline: see below. |
| [`pack-test-slow`](../examples/pack-test-slow), [`pack-test-broken`](../examples/pack-test-broken) | any comma, any fork | Test the runtime's safety limits: the comma turns each off. |

`bluepilot` and `dragonpilot-hud` are CommaView's own packs (author `CommaView`): the app's catalog lists them as
official, offered to commas running their fork. The others are examples (author `CommaView (example pack)`).

`tools/build-examples.sh` checks every example and writes its `.cvpack` into `dist/extensions/`: send one to your
phone and use the app's Install from a file.

### Testing packs on your comma

The three `pack-test` packs read only what every fork's UI has (openpilot and sunnypilot, on the comma 3X and the
comma four), so they show whether packs work on whatever comma you have, before you write one.

- **`pack-test`** sends the UI's status, carState's speed and acceleration, radarState's lead and the number of UI
  frames it has seen. Its overlay fills the path **magenta**, draws white chevrons that drift with the car's
  acceleration, a red line across the path at the lead (green, 20 m ahead, without one), a "Pack test running"
  badge, and two lines of live text along the bottom. Install it, go offroad and back onroad: if the path turns
  magenta and the frame count climbs, the comma runs packs and the app draws them. Live View's Details should say
  it's running.
- **`pack-test-slow`** spends 3 ms of every UI frame, over the 2 ms budget: half a second after it starts, the
  comma turns it off ("over 2 ms a frame for 30 frames") and its yellow badge goes. Until then it slows the car's
  screen a little, so don't leave it installed.
- **`pack-test-broken`** raises in every `payload()`: after 10 in a row the comma turns it off ("payload() raised
  10 times in a row: RuntimeError: …"). Its red badge never draws.

The fork's UI and the rest of CommaView carry on through both. Remove the test packs from the app once you've seen
them work.

## manifest.json

```json
{
  "sdk": 1,
  "id": "bluepath",
  "name": "Blue path and predicted stop",
  "version": "0.1.0",
  "author": "you",
  "description": "What it adds, and for which forks.",
  "license": "MIT",
  "runtime": {
    "entry": "export.py",
    "services": ["modelV2", "carState"],
    "requiresModules": ["openpilot.selfdrive.ui.sunnypilot.onroad.blue_path"]
  },
  "overlay": "overlay.json"
}
```

| Field | Meaning |
| --- | --- |
| `sdk` | `1`. A runtime with another SDK reports the pack `disabled` and doesn't run it. |
| `id` | 1–32 characters of `a-z`, `0-9` and `-`, not starting with `-`. The pack's folder's name, and the key in `uiStateOnroad.ext`. |
| `name` | 1–64 characters. |
| `version` | 1–32 of `0-9 A-Z a-z . + -`. |
| `forks` | Optional: the forks the pack is for, at most 16 fork ids (`a-z`, `0-9`, `-`), none twice. The catalog offers the pack to commas running them. The forks CommaView lists are in [`tools/listed-forks.json`](../tools/listed-forks.json); a fork it doesn't list is warned of, not refused. An official pack names at least one, each a listed fork. |
| `runtime` | The runtime half. Left out only by a [standalone pack](#standalone-packs). |
| `runtime.entry` | The Python file, inside the pack's folder. Default `export.py`. |
| `runtime.services` | Services your pack reads (up to 8): a new message of one makes the comma send `uiStateOnroad` again. Only services the fork's UI already subscribes to have messages; a pack can't subscribe to more. |
| `runtime.requiresModules` | Fork UI modules that must be loaded for the pack to run. Without one, the pack is `inactive` and its code is never imported, so a pack costs nothing on forks it isn't for. |

## export.py (SDK 1)

```python
class Extension:
  def applies(self, ctx) -> bool:   # optional: asked each frame until it's True once; False makes the pack inactive
    return hasattr(ctx.ui_state, "rainbow_mode_style")

  def observe(self, ctx) -> None:   # optional: every UI frame (60 Hz), to keep state between frames
    ...

  def payload(self, ctx) -> dict:   # up to 20 times a second: what the app gets as uiStateOnroad.ext["<id>"]
    return {"enabled": bool(getattr(ctx.ui_state, "rainbow_path", False))}
```

`ctx` (each pack has its own: what one pack sets on it never reaches another; it's `_ExtensionContext` in
[`tools/commaview_pack_host.py`](../tools/commaview_pack_host.py)):

| Name | Is |
| --- | --- |
| `ctx.ui_state` | The fork UI's `UIState` (`ui_state.sm` is its SubMaster). Read it; never change it. |
| `ctx.status` | The fork UI's own status, in uiStateOnroad's names: `"engaged"`, `"latOnly"` (steering only), `"longOnly"` (speed only), `"disengaged"`, `"override"`, or `"unknown"`. overlay.json's `status` names two of them `lat_only` and `long_only`. |
| `ctx.engaged` | `status` is engaged or steering only. |
| `ctx.now` | `time.monotonic()` for this frame. |
| `ctx.module(name)` | The fork UI's module if it's loaded, else `None`. Never imports. |
| `ctx.service(name)` | The latest message of a service received this drive, else `None`. |
| `ctx.fresh(*services, timeout=0.5)` | Every service named is valid, alive, received this drive and newer than `timeout` s. |
| `ctx.param(key, kind="str", default=None, attr=None)` | A param as `"bool"`, `"int"`, `"float"` or `"str"`: the UI's own `attr` when it has one, else the file, read at most every 3 s (each value 3–3.6 s, at random, so a pack's params aren't all read in one frame). |
| `ctx.device` | The comma's hardware: `"mici"` (comma four), `"tizi"` (comma 3X) or `"tici"` (comma three); `None` when unknown. |

### The rules the runtime enforces

The pack runs inside the fork's UI, which draws the car's screen, so the comma holds it to a budget and turns it off
for the rest of the UI's run (state `disabled`, with the reason) when it breaks one. The rest of CommaView, and the
fork's UI, carry on. The limits are the constants at the top of the pack host's overlay pack section
(`COMMAVIEW_EXTENSION_*` in [`tools/commaview_pack_host.py`](../tools/commaview_pack_host.py)), and the checker holds
your pack to the same ones.

| Rule | Limit |
| --- | --- |
| Time | `observe` and `payload` together: 2 ms a frame. 30 frames over it in a row, or one frame over 20 ms, turns the pack off. A frame `payload` didn't run in (the payload is built up to 20 times a second, the UI draws at 60) counts when it's over the budget, and doesn't end a run of slow frames when it isn't. |
| Loading | The import and `Extension()` together: 250 ms. Loading happens only once the car is known to be off; a pack never loads during a drive. |
| Hangs | One call (`applies`, `observe` or `payload`) running over 1 s, or the import and `Extension()` over 5 s, is interrupted by a watchdog (again each second while it goes on, so a bare `except:` doesn't stop it). One such run turns the pack off for that run alone: it runs again at the next start. The third run it hangs in turns it off until it's installed again. A call stuck in C (not running Python, holding the GIL) can't be interrupted, and the UI may freeze until it returns; nor can a finalizer (`__del__`). Don't loop or block in either. |
| Errors | 10 exceptions in a row from one of its calls (`applies`, `observe` or `payload`), of any kind (a `BaseException` too): one that works doesn't hide another that keeps raising. |
| `payload` | An `Extension` without `payload(ctx)` is turned off when it loads. The payload is checked as the runtime sends it (string keys, integers within 64 bits) and a copy is sent, so changing the dict afterwards changes nothing. |
| Payload | A `dict` that is JSON (no NaN or infinity, no objects), at most 4 KiB encoded. |
| All payloads | 16 KiB together in one frame: over it, the largest block is left out of that frame, then the next largest, until the rest fit (the pack isn't turned off). |
| For the run | A pack turned off for one of these stays off through a reload another pack's change causes, until the UI restarts or it's installed again. |
| Packs | At most 8 are read. |
| Sizes | `manifest.json` at most 16 KiB, `export.py` at most 256 KiB, `overlay.json` at most 192 KiB, the bundle at most 512 KiB. |

Write it so it can't hurt the screen:

- Read, never write: no params, files, sockets or processes, and no changes to `ui_state`.
- Don't import heavy modules or build big objects every frame. The UI runs with Python's garbage collector off.
- Prefer the fork's own functions (`ctx.module(...)`), as the blue path pack calls `predicted_stop`: the pack then
  says what the fork's screen says, and you don't copy its logic.
- Send values, not drawing: distances, flags, colours, text. The app decides where and how.

## overlay.json

What the app draws, and how: see [overlay-packs.md](overlay-packs.md). In short, at most 24 layers at the top and 128
in all, 192 KiB of UTF-8; every layer can be shown only while an expression is true, and its numbers and colours can
be expressions of the pack's block (`ext.<field>`) and of what the app already knows (the speed, the lead, the
status, the clock).

## Standalone packs

A pack the app can draw from what it already has (the time, the car's speed, the lead, the status) needs no runtime
half. Its `overlay.json` says `"standalone": true`, its manifest has no `runtime`, and it has no `export.py`; its
`.cvpack` has no `export`. The app draws it whether or not the comma sends a block for it, and never sends it to a
comma. [`datetime`](../examples/datetime) is one:

```json
{
  "format": 1,
  "id": "datetime",
  "standalone": true,
  "layers": [
    {
      "type": "text",
      "text": "{clock|date:yyyy-MM-dd}  {clock|date:HH:mm:ss}",
      "x": 0.02, "y": 0.97, "anchor": "bottomLeft", "size": 0.04,
      "color": "#FFFFFF", "outline": "#000000C0", "font": "mono"
    }
  ]
}
```

Its `clock` is the comma's own clock as it sent it with each frame, so a recording is stamped with when the drive
happened, not when you watch it. A pack with a runtime half can use `clock` too.

## Check a pack

```sh
python3 tools/check-pack.py examples/bluepath \
  --module openpilot.selfdrive.ui.sunnypilot.onroad.blue_path \
  --attr rainbow_path=true --attr rainbow_mode_style=1
```

It checks `overlay.json` as the app reads it: every layer one the app draws, every field in range, every expression
and text template one the app can read (its functions with their number of arguments), every outline's path data,
every picture a PNG the app can draw, groups no deeper than 4 and pins only on top-level groups. The app refuses a
pack with anything wrong, whole; the checker lists every problem at once (`FAIL overlay.json: layers[2].size must be
a number from 0.015 to 0.12`). A field a layer doesn't take (a typo, another layer's) is one of them, as in the app,
and so is a `level` newer than the checker reads, or a field of a higher level than the pack says. It warns of what
the app would read as null: a name that isn't one it gives (`car.speed`), an `ext.` field the pack's payload doesn't
send, `item` or `index` outside a repeated group's copies.

Then it loads the pack through the comma's own pack host ([`tools/commaview_pack_host.py`](../tools/commaview_pack_host.py),
the same code the comma runs) against a stand-in UI state (engaged, 10 m/s, a radar lead 32.5 m ahead), runs 120
frames, and prints the pack's state, its last payload and the time a frame took:

| Option | Does |
| --- | --- |
| `--attr NAME=VALUE` | Sets a UI state attribute (a JSON value): `--attr rainbow_path=true`. |
| `--service NAME=JSON` | A service's latest message: `--service carrotMan='{"nRoadLimitSpeed": 60}'`. |
| `--module NAME` | A fork module the UI has loaded (imported if it can be, else an empty one). |
| `--frames N` | How many frames to run (120). |
| `--expect STATE` | The state the pack should end in: `active` (the default), `inactive`, or `disabled` for a pack that tests the limits. |
| `--bundle OUT.cvpack` | Writes the [bundle](#the-bundle-cvpack) once every check passes. |

It can't run your fork's functions unless you point `PYTHONPATH` at your fork's checkout; the comma is the real
test. A standalone pack is checked for its manifest and overlay (and preview) alone.

## The bundle (.cvpack)

A pack travels as one file, a `.cvpack`: UTF-8 JSON of at most 512 KiB that holds the three files, and the
[preview](#the-preview-previewpng) when the pack has one.

```json
{
  "cvpack": 1,
  "manifest": { "sdk": 1, "id": "bluepath", "name": "...", "version": "0.1.0", "...": "..." },
  "export": "<export.py's source, at most 256 KiB>",
  "overlay": { "id": "bluepath", "...": "..." },
  "preview": "data:image/png;base64,<preview.png, optional>"
}
```

- `overlay.id` is `manifest.id`.
- `manifest.runtime.entry`, if given, is `export.py`: the bundle carries that one file. A standalone pack's manifest
  has no `runtime`, and its bundle no `export`.
- No member appears twice in an object, and there's no NaN or Infinity.
- Its **digest** is the lowercase hex SHA-256 of the file's exact bytes. The app sends it with the bundle, and
  my.commaview.com signs the id, version and digest of a pack it approved.

`check-pack.py --bundle` writes the bundle only after every check passes, the folder's `preview.png` in it when there
is one, and prints its size and SHA-256. The checker also takes a `.cvpack` in place of a folder: it checks the
bundle's rules, unpacks it into a folder as the comma would and runs it.

### The preview (preview.png)

A pack may carry one still of Live View with it drawn, `preview.png` in its folder: the store shows it on the pack's
card, its row and its detail. Any PNG that keeps these rules will do (a screenshot of Live View, cropped to 16:9):

- a PNG, 8-bit RGB or RGBA (no palette, greyscale or 16-bit), one frame (no APNG: no `acTL`, `fcTL` or `fdAT`);
- 16:9: its height its width × 9 / 16, rounded (1280 × 720, 960 × 540, 640 × 360), from 320 × 180 to 1280 × 720;
- at most 256 KiB as a file;
- one that decodes: every chunk's CRC right, `IHDR` first, the `IDAT`s together, `IEND` last, and its image data
  inflating to exactly the rows its `IHDR` says. Text and other ancillary chunks are allowed and ignored.

In the bundle it's the root member `"preview"`, the PNG as `data:image/png;base64,…` (that prefix exactly, standard
padded base64), so the bundle's digest and CommaView's signature cover it. The bundle stays at most 512 KiB with it;
when it doesn't fit, `check-pack.py` says whether a smaller preview would do. The checker and my.commaview.com refuse
a pack whose preview breaks a rule, with the same messages. The app installs and draws a pack whatever its preview,
and the comma never reads it.

## Install a pack on your comma

From the app (Settings › Overlay packs): install a reviewed pack from the catalog, or a `.cvpack` file. The app shows
a file's `export.py` before it installs one nobody reviewed. It can update, turn off and remove a pack the same way.

A pack nobody reviewed (your own, while you write it) runs only while your comma allows unreviewed packs: the app's
Settings › Experimental tools › Overlay packs, per comma. It's off until you turn it on; turning it off stops the
unreviewed packs installed.

On the comma, each pack is a folder named for its `id` in `/data/commaview/extensions`. A pack loads only while the
car is off: after the app installs, updates, turns off or removes one, the comma reads its packs again the next time
it's offroad, without restarting the UI; a change made during a drive waits until the car is off. To turn every
pack off by hand, create `/data/commaview/config/extensions-disabled`.

See how a pack is doing in Live View's Details: `active`, or why it isn't (`inactive`: not for this UI;
`disabled`: turned off, and the reason).

## Sharing a pack: the catalog

The app's overlay pack catalog lists packs anyone can install. Submit yours at
[my.commaview.com › Packs](https://my.commaview.com/packs). Pack submission is currently open to CommaView members with
pack access; ask in the [CommaView Discord](https://discord.gg/UuMhz9K8ft) to get it. The pack studio
([my.commaview.com/studio](https://my.commaview.com/studio)), open to the same members, can draw a pack and make its
preview.

- **Community packs are reviewed by hand.** CommaView reviews every submission by hand before it's listed: its
  `export.py` (short, only reading, within the rules above), its overlay, and what it says it's for. A pack that
  passes is signed by the catalog, and the app checks that signature before it installs a pack from the catalog. A
  new version is reviewed again.
- **Official packs are CommaView's.** Only CommaView's own pipeline publishes official packs; nobody else can.
  Packs whose author is `CommaView`, such as [`bluepilot`](../examples/bluepilot) and
  [`dragonpilot-hud`](../examples/dragonpilot-hud), are CommaView's own, published here in `examples/` too, and listed
  as official, offered to commas running their fork.

Run the checker before you submit: the catalog runs the same checks, the preview's included.

## Security

A pack's `export.py` is Python running with the fork UI's permissions on the comma. Install packs only from people
you trust, and read `export.py` first: it should be short, and only read. Packs the app offers from its catalog were
reviewed, and the comma itself checks the catalog's signature before it runs one, unless its owner allows unreviewed
packs. `overlay.json` can only describe drawing, and the app keeps packs away from its alerts.
