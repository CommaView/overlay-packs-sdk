# overlay.json: the overlay format

`overlay.json` is a pack's drawing half: data the app reads and draws every frame over its road view, never code. The
checker ([`tools/check-pack.py`](../tools/check-pack.py)) reads it as the app does and refuses what the app would
refuse. This page is the reference; [extensions.md](extensions.md) is the rest of a pack.

## The file

```json
{
  "format": 1,
  "id": "my-pack",
  "level": 1,
  "standalone": false,
  "images": {"arrow": "data:image/png;base64,..."},
  "design": {"name": "My design"},
  "layers": [
    {"type": "badge", "when": "ext.active", "text": "Lead {lead.distance|0} m", "x": 0.5, "y": 0.1,
     "width": 0.2, "height": 0.06, "fill": "#101D2DE1", "textColor": "#FFFFFF"}
  ]
}
```

| Member | Is |
| --- | --- |
| `format` | `1`. |
| `id` | The manifest's `id`. |
| `level` | The overlay level the pack needs: `1` (the default) or `2` ([Level 2](#level-2)). An app that reads a lower level refuses the pack whole. |
| `standalone` | `true` for a pack drawn by the app alone, with no runtime half ([extensions.md](extensions.md#standalone-packs)). |
| `images` | The pack's pictures, by name ([Pictures](#pictures)). |
| `design` | Offers the pack as a design of its own ([below](#layers)). |
| `layers` | What's drawn, bottom first. |

The app refuses a pack with anything wrong, whole: a layer it doesn't draw, a field out of range or that the layer
doesn't take, an expression it can't read. The checker lists every such problem at once.

## Layers

In short: at most 24 layers at the top (and in each group), 128 in all (groups' included), 192 KiB of UTF-8. Every layer
can have `when`, an expression: drawn only while it's true; every layer but the path's can have `opacity`, 0–1 or an
expression (a group's fades its layers as one). Colours are `"#RRGGBB"` or `"#RRGGBBAA"` (alpha last, the `#` needed),
or `"="` and an expression giving one; text lengths are in characters as a person counts them.

| Layer | Draws | Fields |
| --- | --- | --- |
| `pathFill` | The driving path filled with a vertical gradient. | `gradient`: 2–8 `[position 0–1, colour]` pairs in order. |
| `pathChevrons` | Chevrons in the path that drift with `acceleration`. | `color`, `acceleration` (expression, m/s²). |
| `pathLine` | A line across the path `distance` metres ahead. | `distance` (expression), `halfWidthMeters` (0.05–3), `thickness` (0.001–0.05), `color`, `glow`. |
| `ribbon` | A band on the road along the path, a lane line or a road edge. | `along`, `index`, `from`, `to`, `start`, `end`, `color` or `gradient`, `fade`, `dash`; see Ribbons below. |
| `badge` | A rounded box with one line of text (smaller where it's wider than the box). | `text` (a template), `x`, `y`, `width` (0.02–0.8), `height` (0.02–0.15), `fill`, `textColor`. |
| `text` | A line of text, or a few wrapped. | `text` (a template), `x`, `y`, `anchor`, `size` (0.015–0.12), `color`, `outline`, `background` (as wide as the words), `font`, and the text options below. |
| `rect` | A box, its corners `radius` round. | `x`, `y`, `width`, `height`, `radius` (default 0), a paint. |
| `circle` | A circle. | `cx`, `cy`, `r`, a paint. |
| `line` | A line, round-ended. | `x1`, `y1`, `x2`, `y2`, `stroke` (a colour or a gradient), `strokeWidth` (default 0.006). |
| `polygon` | A closed outline. | `points`: 3–64 `[x, y]` pairs; a paint. |
| `path` | Any outline: curves, holes, a glyph. | `d` (SVG path data in fractions of its box), a paint, `evenOdd`. |
| `arc` | Part of a circle: stroked a gauge, filled a wedge. | `cx`, `cy`, `r`, `start` (degrees, 0 to the right, clockwise; default 0), `sweep` (degrees), a paint. |
| `image` | One of the pack's pictures, stretched into its box. | `image` (a name in `images`), `x`, `y`, `width`, `height`, `tint`. |
| `edgeGlow` | A glow inward from its box's edges. | `color`, `reach` (0.01–0.5 of the box's shorter side), `sides` (`"all"`, `"sides"`, `"ends"`, or a list of `left`, `right`, `top`, `bottom`). |
| `group` | Layers in a box of their own, turned and placed together, or sitting on the road. | `x`, `y`, `width`, `height` (default its whole parent), `rotation` (degrees clockwise), `layers` (1–24), `aspect`, `pin`, `anchor`. |

The road layers (the path's three and `ribbon`) can't be in a group. `badge`, `text` and the path layers take plain
numbers; the shapes', images', groups' and edge glows' numbers, a ribbon's `from`, `to`, `start` and `end`, and every
layer's `opacity`, may also be expressions in a string, worked out every frame. Every colour, the road layers'
gradient stops included, may be `"="` and an expression giving one (a shape's gradient stops excepted).

`"design": {"name": "<1–32 characters>"}` at the top of overlay.json also offers the pack as a design of its own:
Display lists it beside the app's designs, and there it draws on a road view with nothing else on it (`design` is true).

### Shapes

`rect`, `circle`, `polygon`, `arc` and `path` take a paint: `fill`, `stroke` (either or both) and
`strokeWidth` (default 0.006); a fill or stroke may be a gradient across the shape's own bounds, `{"linear": [x1, y1,
x2, y2], "stops": [[0, "#102B30B4"], [1, "#070C12E7"]]}` or `{"radial": [cx, cy, r], "stops": ...}` (2–8 stops).
Positions are fractions of the layer's box (the road view, or its group's): −1 to 2, so a shape may run off an edge.
`width`, `height` and `r` are 0–2; `radius` and `strokeWidth` 0–1, both fractions of the box's height, so a circle
stays round; angles −720 to 720. A number is a number in that range or an expression in a
string (its value kept to the range); a colour is `"#RRGGBB[AA]"` or `"="` and an expression giving one. A layer
whose value is missing this frame isn't drawn. A speed gauge, its arc growing with the speed and red over 100:

```json
{"type": "arc", "cx": 0.5, "cy": 0.55, "r": 0.35, "start": 135, "sweep": "270 * clamp(speed / 120, 0, 1)",
 "stroke": "=speed > 100 ? '#FF5050' : '#37AAFF'", "strokeWidth": 0.06}
```

A warning that blinks, on for half of every second while the pack's block says so:

```json
{"type": "circle", "when": "ext.warning && clock % 1000 < 500", "cx": 0.05, "cy": 0.1, "r": 0.025, "fill": "#FF3030"}
```

A `path`'s `d` is SVG path data, its numbers fractions of its box (x of its width, y of its height): `M L H V C S Q T A
Z`, upper case absolute and lower case relative, at most 2048 characters and 256 commands; `"evenOdd": true` makes a
shape inside another a hole. Every shape may take a `glow`, a blurred copy under it: `{"color": "#5FE0D2", "radius":
0.04, "dx": 0, "dy": 0.01}` (`radius` 0–0.5, offsets −0.5 to 0.5, fractions of the box's height).

### Ribbons

A `ribbon` is a band on the road, drawn with the path layers: `along` `"path"`, `"laneLine"` (`index`
0–3, left to right) or `"roadEdge"` (`index` 0 left, 1 right); `from` and `to` its sides in metres right of the line's
centre (−10 to 10), `start` and `end` metres ahead (0–250, default the line's own end), a `color` or a `gradient`
along its length (one, not both), `fade` (0–1, the far part it fades over) and `dash` (`[on, off]` metres, each
0.1–50). `"opacity": "lane.1.prob"` draws a lane line as surely as the model sees it.

### Text options

A `text` layer may also take `weight` (`"medium"`, `"semibold"` or `"bold"`; `condensed` is semibold
only), `tracking` (letter spacing in ems, −0.1 to 0.5), `maxWidth` (a fraction of its box's width it's drawn smaller to
fit, 0.01–2), `wrap` (`{"width": 0.01–2, "lines": 1–6}`), and its background's `padding` (`[x, y]` ems, 0–2 each) and
`radius` (ems, 0–2).

### Pictures

`"images": {"arrow": "data:image/png;base64,iVBORw0..."}` at the top of overlay.json holds the
pack's pictures by name (1–32 of `a-z A-Z 0-9 - _`), which `image` layers draw: PNGs (a PNG's signature and its
`IHDR` first) of at most 1024 px a side, at most 8, 96 KiB of PNG together. `opacity` fades one and `tint` (a colour or
`"="` and an expression) draws every pixel in that colour, keeping its alpha: draw icons white and tint them.
`base64 -w0 icon.png` makes the text after the comma.

### Groups

A group's layers are placed in fractions of its box, so moving or turning the group moves everything in
it (a gauge, a sign, an icon and its label). In a group, a `text`'s `size` and a `badge`'s `width` and `height` may
go up to 1 (the box's height), and they're placed as shapes are (−1 to 2). Groups go at most 4 deep. `"aspect": <width
over height>` keeps a group's shape in any window: its box is the largest of that shape inside `x`, `y`, `width` and
`height`, placed by its `anchor`.

### Pins

A top-level group can sit on the road instead of the screen. `"pin": {"path": "<distance>", "offset":
<metres>}` is the driving path `distance` metres ahead (an expression), `offset` metres right of its centre (−10 to
10, left when negative, default 0); `"pin": {"lead": true}` is the lead car. The group's `anchor` (default `bottom`)
sits on that point, moved by `x` and `y` (−1 to 1 of the road view), and its `width` and `height` are needed, in
fractions of the road view, unless the pin gives `"size": [width, height]` in metres at that point (0.05–50): then it's
drawn as big as that would be there, growing as it comes closer (`{"lead": true, "size": [2.5, 1.9]}`). While the point isn't on the road ahead in view (past the model's path, no lead) the
group isn't drawn. The lead's distance, above the lead car:

```json
{"type": "group", "pin": {"lead": true}, "y": -0.02, "width": 0.12, "height": 0.06, "layers": [
  {"type": "badge", "text": "{lead.distance|0} m", "x": 0, "y": 0, "width": 1, "height": 1,
   "fill": "#101D2DE1", "textColor": "#FFCF6E"}]}
```

### Text with live values

A badge's or text layer's `text` may hold `{expr}` or `{expr|format}`: the formats `0`,
`0.0`, `0.00`, `int`, `upper`, `lower` and `date:<pattern>` (`yyyy MM dd HH hh mm ss a EEE`, `'text'` literal). At
most 16 values and 512 characters; a value that isn't there prints `--`, and a text whose values are all missing isn't
drawn. The format starts at the first `|`, so `||` belongs in the layer's `when`. `{{` and `}}` are literal braces.

### Expressions

**Expressions** (`when`, `distance`, `acceleration`, a pin's `path`, and any number or colour given as one):
numbers, `'text'`, `true`, `false`, `null`, names, `! - * / % + -` (`%` is never negative; `+` joins text when either
side is text: `'Lead ' + lead.distance`), `== != < <= > >=`, `&& ||`, `cond ? a : b` and parentheses, and these
functions:

| Function | Gives |
| --- | --- |
| `min(a, b, ...)`, `max(a, b, ...)` | The least or greatest of 2–8 numbers. |
| `abs(x)`, `floor(x)`, `ceil(x)`, `sqrt(x)` | As in maths; `sqrt` of a negative number is `null`. |
| `clamp(x, low, high)` | `x` kept from `low` to `high`. |
| `round(x)`, `round(x, places)` | Rounded, halves up, to 0–6 places. |
| `sin(deg)`, `cos(deg)` | Of an angle in degrees: a needle's end is `cx + r * cos(angle)`. |
| `rgba(r, g, b)`, `rgba(r, g, b, a)` | A colour, `"#rrggbbaa"`: channels 0–255, alpha 0–1. For a colour field: `"=rgba(255, 0, 0, clamp(ext.risk, 0, 1))"`. |
| `pick(value, key1, result1, ..., default)` | The result whose key equals `value` (3–33 arguments; the last, unpaired, is the default). |
| `mix(a, b, t)`, `mixColor(c1, c2, t)` | `a` to `b`, or one colour to another, as `t` goes 0 to 1. |
| `smoothstep(e0, e1, x)`, `ease(t)`, `easeIn(t)`, `easeInOut(t)` | Easing curves, 0 to 1. |
| `since(x)`, `previous(x)`, `hold(x, ms)`, `smooth(x, ms)` | They remember from frame to frame: milliseconds since `x` changed, its value before, true while `x` is and for `ms` after, `x` eased with a time constant of `ms`. |

A function takes only its number of arguments (the checker says which); no loops, no assignment, no functions of
a pack's own. A name that isn't there, and anything that can't be worked out (`1 / 0`, `min(null, 1)`), is `null`;
`null`, `false`, `0` and `''` are false. The names: `ext.<field>` (the pack's own block), `engaged`, `status`,
`car.vEgo`, `car.aEgo` (m/s, m/s²), `car.standstill`, `metric`, `speed` and `speedUnit` (the comma's units),
`lead.present`, `lead.distance`, `lead.speed`, `lead.vRel`, `lead.yRel`, `lead.gap` (null without a lead), `clock`
(epoch milliseconds, the comma's clock as the frame was built), and what CommaView's own HUD reads: `setSpeed`,
`speedLimit`, `speedLimitOver`, `steer.torque` (−1 to 1), `alert`, `alert.text1`, `alert.text2`, `alert.severity`,
`alert.size`, `blinker.left`/`right`, `blindspot.left`/`right`, `laneChange`, `experimental`, `longControl`,
`dm.awareness`, `dm.level` and `dm.rhd`; the second lead car's `lead2.present`, `lead2.distance`, `lead2.speed`,
`lead2.vRel`, `lead2.yRel` and `lead2.gap`; `prompt`, `signStyle`, the model's `lane.0.prob` … `lane.3.prob`,
`edge.0.std`, `edge.1.std`, `path.length` and `model.accel`; `view.aspect`, `reduceMotion`, and `design` (true while the
pack is drawn as its own design). A pack that needs one of these doesn't need an export.py for it.

## Level 2

A pack that says `"level": 2` (an app that reads level 1 refuses it whole, and the catalog keeps serving
such an app the pack's last level-1 version) may also use: `hide` (the
design's own parts it turns off) and `inset` (`{"left", "right", "top", "bottom"}`, each 0–0.4, numbers only, which moves
the design's HUD inward while the pack draws, and with a `when` only while that's true; each side takes the largest of
the packs whose inset applies); a group's `"pin": {"hud": "<part>"}`, `{"car": [ahead, right]}` and `{"lead": 1|2}`;
the `backdrop` layer; gradients of 2–16 stops whose positions, a shape's colours, and a shape's `linear` and `radial`
coordinates may be expressions (a coordinate with no value leaves the gradient undrawn); a ribbon's `space`
(`"length"` or `"view"`); a text's `arc` and `inside`; a group's `"repeat": {"over": "ext.<array>", "max": 1-64}` (its
layers count `max` times toward the 128); `count()` and `integrate()`; and the names `screen`, `device`, `fork`,
`odometer`, `car.steeringAngle`, `hud.*`, `item`, `index`, `dm.face`, `dm.pitch` and `dm.yaw`. A ribbon along a lane
line or a road edge (`"along": "laneLine"`, `index` 0–3, or `"roadEdge"`, `index` 0–1) is level 1, and follows that
line's own points as far as the design draws it.

The design's parts a level 2 pack can name (the checker's `HUD_PARTS` and `HIDE_PARTS`):

- `hide` takes up to 16 of `path`, `laneLines`, `roadEdges`, `leads` (the road's), `speed`, `setSpeed`, `speedLimit`,
  `wheel`, `driverMonitor`, `confidenceBall`, `torqueBar`, `border`, `turnSignals`, `turnSignalLeft`,
  `turnSignalRight` (the HUD's), and `hud` (every HUD part). The app's alerts and connection notices are never among
  them.
- A group's `"pin": {"hud": "<part>"}` sits on one of the HUD's parts, and `hud.<part>.x`, `.y`, `.width` and
  `.height` (with `hud.borderWidth`) say where each is, in fractions of the road view.
- `backdrop` takes `fill` (a colour, a gradient, or `"="` and an expression) and `opacity`: it fills the whole road
  view, over the camera and under every road layer (a black one hides the camera). A pack has at most one, and never
  inside a group.
- A repeated group (`"repeat": {"over": "ext.<array>", "max": 1-64}`) is drawn once for each item of the pack's
  array, up to `max`; inside it `item` is that item and `index` its position, from 0.
