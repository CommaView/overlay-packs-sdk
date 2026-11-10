#!/usr/bin/env python3
"""
Checks an overlay pack on this machine before it goes on a comma or a phone (docs/extensions.md), and makes the
one-file bundle (.cvpack) the app installs and my.commaview.com takes.

  tools/check-pack.py PACK_DIR|PACK.cvpack [--attr name=value ...] [--module dotted.name ...]
                                 [--service name=JSON ...] [--frames N] [--expect STATE] [--bundle OUT.cvpack]

overlay.json first, as the app reads it (CommaView's docs/overlay-packs.md): every layer one the app draws (pathFill,
pathChevrons, pathLine, ribbon, badge, text, rect, circle, line, polygon, arc, path, edgeGlow, image, group,
backdrop), every field in range, every expression and text template one the app can read, every outline's SVG path
data, every picture a PNG it can draw, every group's pin one it can place, a design's name, and no field a layer
doesn't take, at the pack's level ("level", default 1: CommaView's docs/overlay-packs.md, Levels). The app refuses a pack with any
of that wrong, whole; here you hear of every problem at once. Warnings (a name the app doesn't know, a date pattern
letter that prints as is) don't fail the check.

Then the runtime half: it loads the pack through the overlay pack host (commaview_pack_host.py), the very code the
comma runs packs with, the way the comma's UI would, against a stand-in UI state: engaged, 10 m/s, a radar lead 32.5 m
ahead, with the attributes you set (--attr rainbow_path=true), the services' messages you give (--service
carrotMan='{"nRoadLimitSpeed": 60}') and the fork modules you say the UI has loaded (--module ..., empty). It runs N
frames and prints the pack's state, its last payload and the time each frame took, and exits 1 if the pack wasn't
--expect's state (active: running and sending something). It can't stand in for the fork's own code: a pack that calls
the fork's functions is only really tested on a comma (or against the fork's checkout, with PYTHONPATH pointing there
and --module naming what to import).

A standalone pack (overlay.json "standalone": true, a manifest without "runtime") is the app's alone: nothing runs
on the comma, so only its manifest and overlay are checked, and its bundle has no "export".

A pack may carry a preview, preview.png in its folder: one still of Live View with it drawn, which the store shows
(the store's "Preview image" rules, docs/extensions.md, The preview). It's checked by the catalog's rules, with its
messages: a PNG, 8-bit RGB or RGBA, one frame (no APNG), 16:9, 320 x 180 to 1280 x 720, at most 256 KiB, and one that
decodes. In a bundle it's the root member "preview": "data:image/png;base64,...".

manifest.forks, the forks a pack is for, is optional; when it's there it's held to the catalog's rule (at most 16
lowercase fork ids, none twice). An official pack, one of CommaView's own in examples/ with the author
"CommaView" exactly, must name at least one, each a fork CommaView lists.

A .cvpack is checked against the bundle's rules (the same the comma's control API holds it to, and its preview the
catalog's) and then like a folder. --bundle writes the pack as a .cvpack once every check passes, the folder's
preview.png in it when there is one, and prints its SHA-256: the digest the app sends with it. A bundle over 512 KiB
with its preview is refused saying whether a smaller preview, or none, would bring it under.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import importlib
import importlib.util
import io
import json
import math
import re
import shutil
import struct
import sys
import tempfile
import time
import types
import zlib
from functools import cache
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
# The overlay pack host: the comma runs packs with this module (its exporter carries it word for word).
PACK_HOST = REPO_ROOT / "tools" / "commaview_pack_host.py"

# The bundle's rules (the comma's control API's, and my.commaview.com's).
MAX_BUNDLE_BYTES = 512 * 1024
MAX_EXPORT_BYTES = 256 * 1024
MAX_MANIFEST_BYTES = 16 * 1024
# The comma's JSON reader (its control API's): containers nested at most this deep.
COMMA_MAX_JSON_DEPTH = 64
ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
VERSION_RE = re.compile(r"[0-9A-Za-z.+-]{1,32}")
# manifest.forks, the forks a pack is for (my.commaview.com's catalog rule): at most 16 lowercase fork ids, none twice.
FORK_ID_RE = re.compile(r"[a-z0-9-]{1,40}")
MAX_FORKS = 16
# An official pack: one of CommaView's own, in examples/ with exactly this author. Only CommaView publishes it,
# and the catalog offers it to commas on its forks.
OFFICIAL_AUTHOR = "CommaView"
EXAMPLES = REPO_ROOT / "examples"
# The forks CommaView lists, by their flavor: CommaView writes its table of them into LISTED_FORKS with each
# sync of the SDK.
LISTED_FORKS = REPO_ROOT / "tools" / "listed-forks.json"
TRANSFORMER: Path | None = None


def _no_duplicates(pairs):
  result = {}
  for key, value in pairs:
    if key in result:
      raise ValueError(f"{key!r} appears twice in one object")
    result[key] = value
  return result


def _strict_json(data: bytes):
  # As the comma reads it: UTF-8, no member twice, no NaN or Infinity.
  def constant(name):
    raise ValueError(f"{name} isn't JSON")

  try:
    return json.loads(data.decode("utf-8"), object_pairs_hook=_no_duplicates, parse_constant=constant)
  except RecursionError:
    raise ValueError("is nested too deeply to read") from None


def _is_one(value) -> bool:
  # An integer 1, as the app reads cvpack, sdk and format: it doesn't take 1.0.
  return type(value) is int and value == 1


# ---- overlay.json, as the app reads it (its overlay pack reader; docs/overlay-packs.md): the road layers (the
# path's, and ribbons), badges and text with live values, shapes (outlines from SVG path data, glows), edge glows,
# pictures, groups and road pins, standalone packs and packs that are designs of their own. The app's conformance
# cases, which CommaView runs against this checker, keep the two alike.

OVERLAY_FORMAT = 1
# The highest overlay level check-pack reads, the app's OverlayPack.LEVEL (docs/overlay-packs.md, Levels): a pack says the
# level it needs ("level", default 1). A field a later round adds goes in FIELD_LEVELS, a function in FUNCTION_LEVELS
# and a name in NAME_LEVELS.
OVERLAY_LEVEL = 2
OVERLAY_MAX_BYTES = 192 * 1024
# Layers at the top, and in each group.
OVERLAY_MAX_LAYERS = 24
# Layers in all, groups' included; groups inside groups.
OVERLAY_MAX_NODES = 128
OVERLAY_MAX_DEPTH = 4
POLYGON_MAX_POINTS = 64
# overlay.json's pictures: how many, their PNG bytes together, and their pixels a side.
IMAGES_MAX = 8
IMAGES_MAX_BYTES = 96 * 1024
IMAGE_MAX_SIDE = 1024
IMAGE_PREFIX = "data:image/png;base64,"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
BADGE_MAX_TEXT = 48
# Level 2: hide's entries, a gradient's stops (at level 1, and from level 2), a repeated group's copies, and what's drawn
# of a text along an arc.
HIDE_MAX = 16
STOPS_MAX_LEVEL_1 = 8
STOPS_MAX = 16
REPEAT_MAX = 64
ARC_MAX_TEXT = 32
# The design's parts (level 2): its HUD's, which a pack can hide and a group can sit on, and its road's, which a pack
# can hide too, as can "hud" (every HUD part). The app's alerts and connection notices are never among them.
HUD_PARTS = (
  "speed",
  "setSpeed",
  "speedLimit",
  "wheel",
  "driverMonitor",
  "confidenceBall",
  "torqueBar",
  "border",
  "turnSignals",
  "turnSignalLeft",
  "turnSignalRight",
)
ROAD_PARTS = ("path", "laneLines", "roadEdges", "leads")
HIDE_PARTS = ROAD_PARTS + HUD_PARTS + ("hud",)
INSET_SIDES = ("left", "right", "top", "bottom")
INSET = (0, 0.4)
# A pack that's a design of its own: its name, in characters.
DESIGN_MAX_NAME = 32
# A text with live values (PackTemplate): its source, its placeholders, and what's drawn of it once filled (longer
# is cut).
TEMPLATE_MAX_SOURCE = 512
TEMPLATE_MAX_PLACEHOLDERS = 16
TEMPLATE_MAX_DRAWN = 64
EXPRESSION_MAX_LENGTH = 512
EXPRESSION_MAX_TOKENS = 256
EXPRESSION_MAX_DEPTH = 32
# A path layer's SVG path data (PackPathData): its length, and its drawing commands (each repeat of an implicit one
# counted).
PATH_DATA_MAX_LENGTH = 2048
PATH_DATA_MAX_COMMANDS = 256
PATH_DATA_COMMANDS = "MmLlHhVvCcSsQqTtAaZz"
# What each command reads: n a number, f an arc's flag (0 or 1).
_PATH_DATA_ARGUMENTS = {"M": "nn", "L": "nn", "H": "n", "V": "n", "C": "nnnnnn", "S": "nnnn", "Q": "nnnn", "T": "nn"}
_PATH_DATA_ARGUMENTS.update({"A": "nnnffnn", "Z": ""})
_OPERATORS = ("&&", "||", "==", "!=", "<=", ">=", "<", ">", "!", "+", "-", "*", "/", "%", "(", ")", "?", ":", ",")
# What an expression can call, and how many arguments each takes (PackExpression's FUNCTIONS, then those that
# remember from frame to frame, its STATEFUL, then count: in its order).
FUNCTIONS = {
  "min": (2, 8),
  "max": (2, 8),
  "abs": (1, 1),
  "clamp": (3, 3),
  "floor": (1, 1),
  "ceil": (1, 1),
  "round": (1, 2),
  "sqrt": (1, 1),
  "sin": (1, 1),
  "cos": (1, 1),
  "rgba": (3, 4),
  "pick": (3, 33),
  "mix": (3, 3),
  "mixColor": (3, 3),
  "smoothstep": (3, 3),
  "ease": (1, 1),
  "easeIn": (1, 1),
  "easeInOut": (1, 1),
  "since": (1, 1),
  "previous": (1, 1),
  "hold": (2, 2),
  "smooth": (2, 2),
  "integrate": (1, 2),
  "count": (1, 1),
}
# The level each function came with where it's above 1 (PackExpression.FUNCTION_LEVELS): a pack of a lower level that
# calls one is refused, told the level to give.
FUNCTION_LEVELS = {"count": 2, "integrate": 2}
# The names the app gives an expression (OverlayPackFrames.scope); ext.<anything> is the pack's own block.
KNOWN_NAMES = frozenset(
  {
    "engaged",
    "status",
    "car.vEgo",
    "car.aEgo",
    "car.standstill",
    "metric",
    "clock",
    "speed",
    "speedUnit",
    "lead.distance",
    "lead.speed",
    "lead.vRel",
    "lead.yRel",
    "lead.gap",
    "lead.present",
    "lead2.present",
    "lead2.distance",
    "lead2.speed",
    "lead2.vRel",
    "lead2.yRel",
    "lead2.gap",
    "setSpeed",
    "speedLimit",
    "speedLimitOver",
    "steer.torque",
    "experimental",
    "longControl",
    "laneChange",
    "prompt",
    "signStyle",
    "alert",
    "alert.text1",
    "alert.text2",
    "alert.severity",
    "alert.size",
    "blinker.left",
    "blinker.right",
    "blindspot.left",
    "blindspot.right",
    "dm.awareness",
    "dm.level",
    "dm.rhd",
    "lane.0.prob",
    "lane.1.prob",
    "lane.2.prob",
    "lane.3.prob",
    "edge.0.std",
    "edge.1.std",
    "path.length",
    "model.accel",
    "view.aspect",
    "reduceMotion",
    "design",
  }
)
# The names level 2 added; item.<field> is a repeated group's element, as ext.<field> is the block. The app doesn't
# check names (one it doesn't give reads as null), so a pack of a lower level that reads one is warned, not refused.
LEVEL_2_NAMES = frozenset(
  {
    "screen",
    "device",
    "fork",
    "odometer",
    "car.steeringAngle",
    "dm.face",
    "dm.pitch",
    "dm.yaw",
    "hud.borderWidth",
    "item",
    "index",
  }
  | {f"hud.{part}.{side}" for part in HUD_PARTS for side in ("x", "y", "width", "height")}
)
KNOWN_NAMES = KNOWN_NAMES | LEVEL_2_NAMES
NAME_LEVELS = dict.fromkeys(LEVEL_2_NAMES, 2)
NUMBER_FORMATS = frozenset({"0", "0.0", "0.00", "int"})
TEXT_FORMATS = frozenset({"upper", "lower"})
DATE_LETTERS = frozenset({"yyyy", "yy", "MM", "M", "dd", "d", "HH", "H", "hh", "h", "mm", "m", "ss", "s", "a", "EEE"})
DATE_PATTERN_LETTERS = "yMdHhmsaE"
ANCHORS = frozenset({"topLeft", "topRight", "bottomLeft", "bottomRight", "top", "bottom", "center"})
FONTS = frozenset({"sans", "mono", "condensed"})
WEIGHTS = ("medium", "semibold", "bold")
# What a ribbon runs along, and its highest index there (none along the path).
RIBBON_ALONG = {"path": None, "laneLine": 3, "roadEdge": 1}
EDGE_SIDES = ("left", "right", "top", "bottom")
OVERLAY_FIELDS = ("format", "id", "level", "layers", "standalone", "images", "design", "hide", "inset")
# The level a field came with, by (layer type, field) or ("overlay", field), where it's above 1. A layer of a later
# level has every field of it, its type first, at that level.
FIELD_LEVELS: dict[tuple[str, str], int] = {
  ("overlay", "hide"): 2,
  ("overlay", "inset"): 2,
  ("ribbon", "space"): 2,
  ("text", "arc"): 2,
  ("text", "inside"): 2,
  ("group", "repeat"): 2,
  **{("backdrop", field): 2 for field in ("type", "when", "fill", "opacity")},
}
PAINT_FIELDS = {"fill", "stroke", "strokeWidth"}
# Every layer the app draws (in the app's order) and its fields. Every layer but the path's may have an opacity, and
# every shape a glow.
LAYER_FIELDS = {
  "pathFill": {"gradient"},
  "pathChevrons": {"color", "acceleration"},
  "pathLine": {"distance", "halfWidthMeters", "thickness", "color", "glow"},
  "ribbon": {"along", "index", "from", "to", "start", "end", "color", "gradient", "fade", "dash", "opacity", "space"},
  "badge": {"text", "x", "y", "width", "height", "fill", "textColor", "opacity"},
  "text": {"text", "x", "y", "anchor", "size", "color", "outline", "background", "font", "opacity"}
  | {"weight", "tracking", "maxWidth", "wrap", "padding", "radius", "arc", "inside"},
  "rect": {"x", "y", "width", "height", "radius", "opacity", "glow"} | PAINT_FIELDS,
  "circle": {"cx", "cy", "r", "opacity", "glow"} | PAINT_FIELDS,
  "line": {"x1", "y1", "x2", "y2", "stroke", "strokeWidth", "opacity", "glow"},
  "polygon": {"points", "opacity", "glow"} | PAINT_FIELDS,
  "arc": {"cx", "cy", "r", "start", "sweep", "opacity", "glow"} | PAINT_FIELDS,
  "path": {"d", "evenOdd", "opacity", "glow"} | PAINT_FIELDS,
  "edgeGlow": {"color", "reach", "sides", "opacity"},
  "image": {"image", "x", "y", "width", "height", "opacity", "tint"},
  "group": {"x", "y", "width", "height", "rotation", "anchor", "pin", "layers", "opacity", "aspect", "repeat"},
  "backdrop": {"fill", "opacity"},
}
# A shape's gradient in place of a colour: {"linear": [x1, y1, x2, y2] | "radial": [cx, cy, r], "stops": [...]}.
GRADIENT_KEYS = frozenset({"linear", "radial", "stops"})
GLOW_KEYS = ("color", "radius", "dx", "dy")
UNIT = (0, 1)
OPACITY = (0, 1)
# A group's box kept to this width over height.
ASPECT = (0.1, 10)
# The layers drawn on the path, which take their colours' alpha rather than an opacity; and those drawn on the road,
# which can't be inside a group.
PATH_TYPES = frozenset({"pathFill", "pathChevrons", "pathLine"})
ROAD_TYPES = PATH_TYPES | {"ribbon"}
# A shape's numbers (a number in range, or an expression in a string). Where things go: their box is 0-1, a little
# past it is allowed (a shape that runs off an edge).
POSITION = (-1, 2)
SIZE = (0, 2)
# Radii, stroke widths: fractions of the box's height.
LENGTH = (0, 1)
ANGLE = (-720, 720)
# Metres right of the path's (or a line's) centre: a path pin's offset, a ribbon's sides; and metres ahead.
OFFSET = (-10, 10)
AHEAD = (0, 250)
# A pinned group's nudge in fractions of the road view, and its size in metres.
PIN_NUDGE = (-1, 1)
PIN_METRES = (0.05, 50)
# A ribbon's dashes, on and off, in metres.
DASH = (0.1, 50)
# An edge glow's reach, of its box's shorter side; a shape's glow's radius and offsets, of its box's height.
REACH = (0.01, 0.5)
GLOW_RADIUS = (0, 0.5)
GLOW_OFFSET = (-0.5, 0.5)
# A text's letter spacing (ems), its widths (of its box's width: maxWidth, wrap's), its background's padding and
# corners (ems), and the lines it wraps to.
TRACKING = (-0.1, 0.5)
WRAP_WIDTH = (0.01, 2)
EMS = (0, 2)
WRAP_MAX_LINES = 6
# A text's arc (level 2), and what a text along one can't take.
ARC_KEYS = ("cx", "cy", "r", "at")
NOT_WITH_ARC = ("x", "y", "anchor", "wrap", "background", "padding", "radius")
# A car pin's point (level 2): metres ahead of the front axle, and to its right.
CAR_AHEAD = (-5, 250)
CAR_RIGHT = (-20, 20)


def _is_space(c: str) -> bool:
  # As the app's Char.isWhitespace(): Python's isspace() but for U+0085.
  return c.isspace() and c != "\x85"


def _blank(text: str) -> bool:
  return all(_is_space(c) for c in text)


def _trim(text: str) -> str:
  start, end = 0, len(text)
  while start < end and _is_space(text[start]):
    start += 1
  while end > start and _is_space(text[end - 1]):
    end -= 1
  return text[start:end]


def _utf16_length(text: str) -> int:
  # A text's length as the app's String.length counts it, in UTF-16 units: a character past U+FFFF is two.
  return len(text.encode("utf-16-le", "surrogatepass")) // 2


def _code_points(text: str) -> int:
  # Characters as a person counts them, as the app does (OverlayPack.codePoints): an emoji is one.
  # Python joins an escaped pair into one character already; a pair left apart counts once, an unpaired half once.
  return len(text) - sum(
    1 for a, b in zip(text, text[1:], strict=False) if "\ud800" <= a <= "\udbff" and "\udc00" <= b <= "\udfff"
  )


def _shown(value) -> str:
  # A value as an error shows it, as the app's do: text as it is, anything else as JSON.
  if isinstance(value, str):
    return value
  try:
    return json.dumps(value)
  except (ValueError, RecursionError):
    return "(a value too big to show)"


def _f32(value: float) -> float:
  # [value] as a Float holds it (the app reads every number into one), infinite past a Float's range.
  try:
    return struct.unpack("<f", struct.pack("<f", value))[0]
  except OverflowError:
    return math.copysign(math.inf, value)


def _float(value) -> float | None:
  """A JSON number as the app reads it, into a Float; None for anything else, and for one past a Float's range."""
  if type(value) not in (int, float):
    return None
  try:
    number = _f32(float(value))  # float(): an integer past a double's range (10**400) is no number
  except OverflowError:
    return None
  return number if math.isfinite(number) else None


def _in(number: float | None, bounds: tuple) -> bool:
  # Whether [number] (a _float) is in [bounds], their ends Floats as the app's are.
  return number is not None and _f32(bounds[0]) <= number <= _f32(bounds[1])


class ExpressionError(ValueError):
  pass


def _expression_tokens(source: str) -> list[str]:
  tokens: list[str] = []
  i = 0
  while i < len(source):
    c = source[i]
    if _is_space(c):
      i += 1
    elif c in "'\"":
      end = source.find(c, i + 1)
      if end < 0:
        raise ExpressionError("unclosed string")
      tokens.append("'" + source[i + 1 : end])
      i = end + 1
    elif c.isdecimal() or (c == "." and source[i + 1 : i + 2].isdecimal()):
      start = i
      while i < len(source) and (source[i].isdecimal() or source[i] == "."):
        i += 1
      tokens.append(source[start:i])
    elif c.isalpha() or c == "_":
      start = i
      while i < len(source) and (source[i].isalpha() or source[i].isdecimal() or source[i] in "_."):
        i += 1
      name = source[start:i]
      if name.endswith(".") or ".." in name:
        raise ExpressionError(f"bad name '{name}'")
      tokens.append(name)
    else:
      op = next((op for op in _OPERATORS if source.startswith(op, i)), None)
      if op is None:
        raise ExpressionError(f"unexpected '{c}'")
      tokens.append(op)
      i += len(op)
    if len(tokens) > EXPRESSION_MAX_TOKENS:
      raise ExpressionError(f"has more than {EXPRESSION_MAX_TOKENS} parts")
  return tokens


def expression_names(source: str, level: int = OVERLAY_LEVEL) -> list[str]:
  """The names [source] reads, if the app can parse it as an expression (PackExpression's grammar: literals, names,
  ! - * / % + -, comparisons, && ||, cond ? a : b, parentheses and FUNCTIONS' calls, those of a pack's [level]); else
  raises ExpressionError."""
  if _blank(source):
    raise ExpressionError("is empty")
  if _utf16_length(source) > EXPRESSION_MAX_LENGTH:
    raise ExpressionError(f"is longer than {EXPRESSION_MAX_LENGTH} characters")
  tokens = _expression_tokens(source)
  names: list[str] = []
  at = 0
  depth = 0

  def peek():
    return tokens[at] if at < len(tokens) else None

  def take(*ops):
    nonlocal at
    if peek() in ops:
      at += 1
      return tokens[at - 1]
    return None

  def nested(fn):
    nonlocal depth
    depth += 1
    if depth > EXPRESSION_MAX_DEPTH:
      raise ExpressionError("nested too deeply")
    fn()
    depth -= 1

  def conditional():
    def body():
      or_()
      if not take("?"):
        return
      conditional()
      if not take(":"):
        raise ExpressionError("'?' without its ':'")
      conditional()

    nested(body)

  def or_():
    and_()
    while take("||"):
      and_()

  def and_():
    comparison()
    while take("&&"):
      comparison()

  def comparison():
    sum_()
    if take("==", "!=", "<", "<=", ">", ">="):
      sum_()

  def sum_():
    product()
    while take("+", "-"):
      product()

  def product():
    unary()
    while take("*", "/", "%"):
      unary()

  def unary():
    def body():
      if take("!", "-"):
        unary()
      else:
        primary()

    nested(body)

  def primary():
    nonlocal at
    token = peek()
    if token is None:
      raise ExpressionError("ends too soon")
    at += 1
    if token == "(":
      conditional()
      if not take(")"):
        raise ExpressionError("missing ')'")
    elif token in ("true", "false", "null") or token.startswith("'"):
      pass
    elif token[0].isdecimal() or token[0] == ".":
      # The app reads a number's digits as ASCII alone.
      try:
        value = float(token) if token.isascii() else math.nan
      except ValueError:
        value = math.nan
      if not math.isfinite(value):
        raise ExpressionError(f"bad number '{token}'")
    elif (token[0].isalpha() or token[0] == "_") and peek() == "(":
      call(token)
    elif token[0].isalpha() or token[0] == "_":
      names.append(token)
    else:
      raise ExpressionError(f"unexpected '{token}'")

  def call(name):
    if name not in FUNCTIONS:
      raise ExpressionError(f"has no function '{name}' ({', '.join(FUNCTIONS)})")
    since = FUNCTION_LEVELS.get(name, 1)
    if since > level:
      raise ExpressionError(f"has function '{name}', which came with level {since}: give the pack \"level\": {since}")
    take("(")
    count = 0
    if not take(")"):
      while True:
        conditional()
        count += 1
        if not take(","):
          break
      if not take(")"):
        raise ExpressionError(f"missing ')' after {name}'s arguments")
    low, high = FUNCTIONS[name]
    if not low <= count <= high:
      wanted = f"{low}" if low == high else f"{low}-{high}"
      raise ExpressionError(f"{name} takes {wanted} arguments, not {count}")

  conditional()
  if at != len(tokens):
    raise ExpressionError(f"unexpected '{tokens[at]}'")
  return names


def _date_pattern_problems(pattern: str) -> tuple[list[str], list[str]]:
  """([errors], [warnings]) of a date:<pattern>, as the app reads it: runs of y M d H h m s a E are fields,
  '...' is literal text ('' a quote), any other letter is a mistake (quote it: 'T'), anything else prints as is."""
  errors: list[str] = []
  warnings: list[str] = []
  i = 0
  while i < len(pattern):
    c = pattern[i]
    if c == "'":
      end = pattern.find("'", i + 1)
      if end < 0:
        errors.append("date pattern has an unclosed quote")
        break
      i = end + 1
    elif c in DATE_PATTERN_LETTERS:
      j = i
      while j < len(pattern) and pattern[j] == c:
        j += 1
      if pattern[i:j] not in DATE_LETTERS:
        warnings.append(
          f"date pattern field '{pattern[i:j]}' isn't one of {' '.join(sorted(DATE_LETTERS))}: "
          "the app pads the value to its length"
        )
      i = j
    elif c.isalpha():
      errors.append(f"date pattern letter '{c}' isn't one of yyyy MM dd HH hh mm ss a EEE (quote literal text: 'T')")
      i += 1
    else:
      i += 1
  return errors, warnings


def template_problems(text: str, level: int = OVERLAY_LEVEL) -> tuple[list[str], list[str], list[str], bool]:
  """([errors], [warnings], [names], whether it has a placeholder) of a badge's or text layer's text with {expr}
  and {expr|format} placeholders, read as the app's PackTemplate reads it at a pack's [level]."""
  errors: list[str] = []
  warnings: list[str] = []
  names: list[str] = []
  if _utf16_length(text) > TEMPLATE_MAX_SOURCE:
    return [f"is longer than {TEMPLATE_MAX_SOURCE} characters"], [], [], False
  placeholders = 0
  i = 0
  while i < len(text):
    c = text[i]
    if c in "{}" and text[i + 1 : i + 2] == c:
      i += 2
    elif c == "}":
      errors.append("has a '}' without its '{' (write '}}' for one)")
      break
    elif c == "{":
      end = text.find("}", i + 1)
      if end < 0:
        errors.append("has a '{' without its '}'")
        break
      placeholders += 1
      inside = text[i + 1 : end]
      # The format follows the placeholder's first '|' (so an expression in a placeholder can't use '||').
      source, bar, fmt = inside.partition("|")
      if "||" in inside:
        warnings.append(
          f"placeholder {{{inside}}}: the format starts at the first '|', so '||' can't be used in a "
          "placeholder (use the layer's when)"
        )
      try:
        names.extend(expression_names(source, level))
      except ExpressionError as e:
        errors.append(f"placeholder {{{inside}}}: the expression {e}")
      fmt = _trim(fmt)
      if bar and fmt.startswith("date:"):
        date_errors, date_warnings = _date_pattern_problems(fmt[5:])
        errors.extend(f"placeholder {{{inside}}}: {error}" for error in date_errors)
        warnings.extend(f"placeholder {{{inside}}}: {warning}" for warning in date_warnings)
      elif bar and fmt not in NUMBER_FORMATS | TEXT_FORMATS:
        errors.append(
          f"placeholder {{{inside}}}: format '{fmt}' isn't one of 0, 0.0, 0.00, int, upper, lower, date:<pattern>"
        )
      i = end + 1
    else:
      i += 1
  if placeholders > TEMPLATE_MAX_PLACEHOLDERS:
    errors.append(f"has more than {TEMPLATE_MAX_PLACEHOLDERS} values")
  return errors, warnings, names, placeholders > 0


class PathDataError(ValueError):
  pass


def path_data_problem(data: str) -> str | None:
  """What's wrong with a path layer's `d` as the app's PackPathData reads it, None when nothing is: SVG path data
  that starts with a move, its commands M L H V C S Q T A Z (upper case absolute, lower case relative), numbers as SVG
  writes them ("-.5", "1e-3"), an arc's flags 0 or 1, at most PATH_DATA_MAX_LENGTH characters and
  PATH_DATA_MAX_COMMANDS commands."""
  try:
    _read_path_data(data)
  except PathDataError as e:
    return str(e)
  return None


def _read_path_data(data: str) -> None:
  if _blank(data):
    raise PathDataError("is empty")
  if _utf16_length(data) > PATH_DATA_MAX_LENGTH:
    raise PathDataError(f"is longer than {PATH_DATA_MAX_LENGTH} characters")
  at = 0

  def where(index: int) -> int:
    # Where the app says it is: from 1, in UTF-16 units.
    return _utf16_length(data[:index]) + 1

  def skip_separators() -> None:
    nonlocal at
    while at < len(data) and (_is_space(data[at]) or data[at] == ","):
      at += 1

  def digits() -> int:
    nonlocal at
    start = at
    while at < len(data) and data[at].isdecimal():
      at += 1
    return at - start

  def number() -> None:
    nonlocal at
    skip_separators()
    start = at
    if at < len(data) and data[at] in "+-":
      at += 1
    count = digits()
    if at < len(data) and data[at] == ".":
      at += 1
      count += digits()
    if count > 0 and at < len(data) and data[at] in "eE":
      mark = at
      at += 1
      if at < len(data) and data[at] in "+-":
        at += 1
      if digits() == 0:
        at = mark
    if count == 0:
      found = f"'{data[start]}' at {where(start)}" if start < len(data) else "the end"
      raise PathDataError(f"has {found} where a number was wanted")
    # Read into a Float, its digits ASCII alone.
    text = data[start:at]
    if not text.isascii() or not math.isfinite(_f32(float(text))):
      raise PathDataError(f"has a number out of range at {where(start)}")

  def flag() -> None:
    nonlocal at
    skip_separators()
    if at >= len(data):
      raise PathDataError("ends where an arc's flag (0 or 1) was wanted")
    if data[at] not in "01":
      raise PathDataError(f"has '{data[at]}' at {where(at)} where an arc's flag (0 or 1) was wanted")
    at += 1

  skip_separators()
  if at < len(data) and data[at] not in "Mm":
    raise PathDataError("must start with M or m")
  command = " "
  commands = 0
  while True:
    skip_separators()
    if at >= len(data):
      break
    c = data[at]
    if c.isalpha():
      if c not in PATH_DATA_COMMANDS:
        raise PathDataError(f"has no command '{c}' at {where(at)} ({' '.join(PATH_DATA_COMMANDS)})")
      command = c
      at += 1
    elif command in " Zz":
      raise PathDataError(f"has '{c}' at {where(at)} where a command was wanted")
    commands += 1
    if commands > PATH_DATA_MAX_COMMANDS:
      raise PathDataError(f"has more than {PATH_DATA_MAX_COMMANDS} commands")
    for kind in _PATH_DATA_ARGUMENTS[command.upper()]:
      if kind == "n":
        number()
      else:
        flag()
    # A move's further pairs are lines.
    command = {"M": "L", "m": "l"}.get(command, command)


def _color_ok(value) -> bool:
  return isinstance(value, str) and re.fullmatch(r"#([0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})", value) is not None


def png_data_url_size(url):
  """(width, height, PNG bytes) of a "data:image/png;base64,..." URL, as the app's PackImage reads it: strict
  base64 (padded, nothing but its alphabet), a PNG's signature and its IHDR first, at most 1024 px a side. Else
  None."""
  if not isinstance(url, str) or not url.startswith(IMAGE_PREFIX):
    return None
  text = url[len(IMAGE_PREFIX) :]
  try:
    png = base64.b64decode(text, validate=True)
  except (binascii.Error, ValueError):
    return None
  # Kotlin's decoder wants the padding and zero pad bits: only base64 that encodes back the same.
  if base64.b64encode(png).decode("ascii") != text:
    return None
  if len(png) < 24 or png[:8] != PNG_SIGNATURE or png[12:16] != b"IHDR":
    return None
  width, height = struct.unpack(">ii", png[16:24])
  if not (1 <= width <= IMAGE_MAX_SIDE and 1 <= height <= IMAGE_MAX_SIDE):
    return None
  return width, height, len(png)


def _image_problems(overlay: dict, errors: list) -> set:
  """The names of overlay.json's pictures (its `images`), problems added to [errors]."""
  if "images" not in overlay:
    return set()
  images = overlay["images"]
  if not isinstance(images, dict):
    errors.append("images must be an object of names to PNG data URLs")
    return set()
  if len(images) > IMAGES_MAX:
    errors.append(f"at most {IMAGES_MAX} images")
  total = 0
  names = set()
  for name, url in list(images.items())[:IMAGES_MAX]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", name):
      errors.append(f"images.{name}: a name is 1-32 of a-z, A-Z, 0-9, - and _")
    size = png_data_url_size(url)
    if size is None:
      errors.append(f'images.{name} must be a PNG of at most {IMAGE_MAX_SIDE} px a side, as "{IMAGE_PREFIX}..."')
      continue
    total += size[2]
    names.add(name)
  if total > IMAGES_MAX_BYTES:
    errors.append(f"images are over {IMAGES_MAX_BYTES // 1024} KiB of PNG together")
  return names


def overlay_problems(overlay, manifest_id=None) -> tuple[list[str], list[str], list[str]]:
  """([errors], [warnings], [names its expressions read]) of an overlay.json (parsed), as the app reads it."""
  if not isinstance(overlay, dict):
    return ["overlay.json isn't a JSON object"], [], []
  errors: list[str] = []
  warnings: list[str] = []
  names = _Names()
  level = overlay.get("level", 1)
  if not (type(level) is int and level >= 1):
    errors.append("level must be a whole number, 1 or more")
    level = 1
  if level > OVERLAY_LEVEL:
    # A newer pack than check-pack reads: what its fields mean isn't this reader's to guess.
    return [f"level {level}: this pack needs a newer reader (check-pack reads up to level {OVERLAY_LEVEL})"], [], []
  if not (type(overlay.get("format")) is int and overlay.get("format") == OVERLAY_FORMAT):
    errors.append(f"format must be {OVERLAY_FORMAT}")
  ext_id = overlay.get("id")
  if not isinstance(ext_id, str) or not ID_RE.fullmatch(ext_id):
    errors.append("id must be 1-32 of a-z, 0-9 and -, not starting with -")
  elif manifest_id is not None and ext_id != manifest_id:
    errors.append("overlay.id must be manifest.id")
  if "standalone" in overlay and not isinstance(overlay["standalone"], bool):
    errors.append("standalone must be true or false")
  for key in overlay:
    if key not in OVERLAY_FIELDS:
      errors.append(f"{_shown(key)} isn't a field of overlay.json ({', '.join(OVERLAY_FIELDS)})")
    elif FIELD_LEVELS.get(("overlay", key), 1) > level:
      since = FIELD_LEVELS[("overlay", key)]
      errors.append(f'{key} came with level {since}: give the pack "level": {since}')
  layers = overlay.get("layers")
  if not isinstance(layers, list) or not layers:
    errors.append("layers must be a non-empty list")
    layers = []
  if len(layers) > OVERLAY_MAX_LAYERS:
    errors.append(f"at most {OVERLAY_MAX_LAYERS} layers")
  images = _image_problems(overlay, errors)
  # Layers read so far, groups' included (a repeated group's counted as many times as it may draw), and backdrops.
  nodes = [0, 0]
  for index, layer in enumerate(layers[:OVERLAY_MAX_LAYERS]):
    _LayerReader(f"layers[{index}]", layer, errors, warnings, names, images, 0, nodes, level).read()
  if "hide" in overlay:
    _hide_problems(overlay["hide"], level, errors, names)
  if "inset" in overlay:
    _inset_problems(overlay["inset"], level, errors, names)
  if nodes[0] > OVERLAY_MAX_NODES:
    errors.append(f"at most {OVERLAY_MAX_NODES} layers in all, groups' included (this has {nodes[0]})")
  if "design" in overlay:
    # A design of its own, offered under its name (docs/overlay-packs.md, A pack as a design).
    design = overlay["design"]
    name = design.get("name") if isinstance(design, dict) else None
    if (
      not isinstance(name, str)
      or _blank(name)
      or _code_points(name) > DESIGN_MAX_NAME
      or any(key != "name" for key in design)
    ):
      errors.append(f'design must be {{"name": "<1-{DESIGN_MAX_NAME} characters>"}}')
  return errors, warnings, names


def _hide_problems(hide, level: int, errors: list, names: list) -> None:
  """overlay.json's hide (level 2): 1-16 entries, each a part's name (HIDE_PARTS) or {"part": ..., "when": ...}. Any
  other name ("alerts", "notices") is a mistake, told the names."""
  if not isinstance(hide, list) or not 1 <= len(hide) <= HIDE_MAX:
    errors.append(
      f'hide must be a list of 1-{HIDE_MAX} parts, each a part\'s name or {{"part": "<name>", "when": "<expression>"}}'
    )
  if not isinstance(hide, list):
    return

  def part(name, at: str) -> None:
    if not (isinstance(name, str) and name in HIDE_PARTS):
      errors.append(
        f"{at} '{name if isinstance(name, str) else ''}' isn't a part a pack can hide ({', '.join(HIDE_PARTS)})"
      )

  for index, entry in enumerate(hide[:HIDE_MAX]):
    at = f"hide[{index}]"
    if isinstance(entry, str):
      part(entry, at)
    elif isinstance(entry, dict):
      part(entry.get("part"), f"{at}.part")
      when = entry.get("when")
      if not isinstance(when, str):
        errors.append(f"{at}.when must be an expression in a string")
      else:
        try:
          names.extend(expression_names(when, level))
        except ExpressionError as e:
          errors.append(f"{at}.when {e}")
      if any(key not in ("part", "when") for key in entry):
        errors.append(f"{at} takes part and when")
    else:
      errors.append(f'{at} must be a part\'s name or {{"part": "<name>", "when": "<expression>"}}')


def _inset_problems(inset, level: int, errors: list, names: list) -> None:
  """overlay.json's inset (level 2): {"left", "right", "top", "bottom"}, each a number 0-0.4, never an expression, and
  optionally "when", an expression: the inset applies only while it's true."""
  if not isinstance(inset, dict):
    errors.append(
      f'inset must be {{"left": ..., "right": ..., "top": ..., "bottom": ..., "when": ...}}, each side 0-{INSET[1]}'
    )
    return
  if any(key not in INSET_SIDES and key != "when" for key in inset):
    errors.append("inset takes left, right, top, bottom and when")
  for side in INSET_SIDES:
    if side in inset and not _in(_float(inset[side]), INSET):
      errors.append(f"inset.{side} must be a number from {INSET[0]} to {INSET[1]}")
  if "when" in inset:
    when = inset["when"]
    if not isinstance(when, str):
      errors.append("inset.when must be an expression in a string")
    else:
      try:
        names.extend(expression_names(when, level))
      except ExpressionError as e:
        errors.append(f"inset.when {e}")


class _Names(list):
  """The names an overlay's expressions read, in order; [outside] those read outside a repeated group's copies, where
  `item` and `index` are always null. A repeated group's own fields are read in its copies, its repeat.over isn't."""

  def __init__(self):
    super().__init__()
    self.outside: list[str] = []

  def extend(self, found) -> None:
    found = list(found)
    super().extend(found)
    self.outside.extend(found)

  def copies(self) -> _CopyNames:
    """Where a repeated group's copies add their names: to this list, not to its outside."""
    return _CopyNames(self)


class _CopyNames:
  def __init__(self, names: _Names):
    self.names = names

  def extend(self, found) -> None:
    list.extend(self.names, found)


class _LayerReader:
  """One layer of overlay.json read as the app's OverlayPackReader.LayerReader reads it, [depth] groups down; [nodes]
  is the layers read so far and the backdrops, [in_repeat] whether it's inside a repeated group."""

  def __init__(self, where, layer, errors, warnings, names, images, depth, nodes, level, in_repeat=False):
    self.where, self.layer, self.errors, self.warnings, self.names = where, layer, errors, warnings, names
    self.images, self.depth, self.nodes, self.level, self.in_repeat = images, depth, nodes, level, in_repeat

  def sub(self, where: str, obj: dict) -> _LayerReader:
    """A reader of a part of this layer (a pin, a glow) whose problems are this layer's."""
    return _LayerReader(
      where, obj, self.errors, self.warnings, self.names, self.images, self.depth, self.nodes, self.level
    )

  def read(self) -> None:
    where, layer, errors = self.where, self.layer, self.errors
    if not isinstance(layer, dict):
      errors.append(f"{where} isn't an object")
      return
    self.nodes[0] += 1
    # A repeated group's own fields, its when first, are read in each copy; its repeat.over isn't (_group).
    self.outer_names = self.names
    if layer.get("type") == "group" and "repeat" in layer and isinstance(self.names, _Names):
      self.names = self.names.copies()
    self.expression("when", required=False)
    kind = layer.get("type")
    name = kind if isinstance(kind, str) else ""
    if self.depth > 0 and name in ROAD_TYPES:
      errors.append(f"{where}.type '{name}' draws on the road, so it can't be inside a group")
      return
    if self.depth > 0 and name == "backdrop":
      errors.append(f"{where}.type 'backdrop' fills the road view, so it can't be inside a group")
      return
    # Every layer but the path's may fade (an opacity on the path's is a field it doesn't take, below).
    if name not in PATH_TYPES and "opacity" in layer:
      self.value("opacity", OPACITY)
    if name not in LAYER_FIELDS:
      errors.append(f"{where}.type '{_shown(kind)}' isn't a layer the app draws ({', '.join(LAYER_FIELDS)})")
      return
    # At the pack's level every field is one the layer takes: anything else is a mistake (a typo, another layer's).
    fields = LAYER_FIELDS[name] | {"type", "when"}
    for key in layer:
      if key not in fields:
        article = "an" if name[0].lower() in "aeiou" else "a"
        errors.append(f"{where}.{_shown(key)} isn't a field of {article} {name} ({', '.join(sorted(fields))})")
      elif FIELD_LEVELS.get((name, key), 1) > self.level:
        since = FIELD_LEVELS[(name, key)]
        errors.append(f'{where}.{key} came with level {since}: give the pack "level": {since}')
    getattr(self, "_" + name)()

  # ---- the fields

  def expression(self, key, required) -> bool:
    if key not in self.layer and not required:
      return False
    value = self.layer.get(key)
    if not isinstance(value, str):
      self.errors.append(f"{self.where}.{key} must be an expression in a string")
      return False
    try:
      self.names.extend(expression_names(value, self.level))
    except ExpressionError as e:
      self.errors.append(f"{self.where}.{key} {e}")
      return False
    return True

  def number(self, key, bounds, default=None):
    """A field that's a number alone (the road layers', badges' and text's): one in [bounds]."""
    if key not in self.layer and default is not None:
      return default
    value = _float(self.layer.get(key))
    if not _in(value, bounds):
      self.errors.append(f"{self.where}.{key} must be a number from {bounds[0]} to {bounds[1]}")
      return default if default is not None else bounds[0]
    return value

  def value(self, key, bounds, default=None) -> None:
    """A shape's number: one in [bounds], or an expression in a string (its value kept to [bounds] as drawn); without
    a [default], it must be there."""
    if key not in self.layer and default is not None:
      return
    self._value(self.layer.get(key), key, bounds)

  def _value(self, element, key, bounds) -> None:
    if isinstance(element, str):
      try:
        self.names.extend(expression_names(element, self.level))
      except ExpressionError as e:
        self.errors.append(f"{self.where}.{key} {e}")
      return
    if not _in(_float(element), bounds):
      self.errors.append(
        f"{self.where}.{key} must be a number from {bounds[0]} to {bounds[1]}, or an expression in a string"
      )

  def numbers(self, key, count, bounds) -> bool:
    """Whether the field is a list of [count] numbers (each a number alone) in [bounds]."""
    value = self.layer.get(key)
    numbers = [_float(n) for n in value] if isinstance(value, list) else []
    return len(numbers) == count and all(_in(n, bounds) for n in numbers)

  def color_text(self, text) -> bool:
    """Whether a gradient stop's colour is one: "#RRGGBB[AA]", or "=" and an expression (whose error is added)."""
    if isinstance(text, str) and text.startswith("="):
      try:
        self.names.extend(expression_names(text[1:], self.level))
        return True
      except ExpressionError as e:
        self.errors.append(f"{self.where}: '{text}' {e}")
        return False
    return _color_ok(text)

  def stops(self, key) -> None:
    """A road layer's gradient: 2-16 [position 0-1, colour] pairs in order (2-8 at level 1), each colour "#RRGGBB[AA]"
    or "=" and an expression giving one."""
    self.read_stops(self.layer.get(key), key, '"#RRGGBBAA" or "=expression"', expression_colors=True)

  def read_stops(self, stops, key: str, colors: str, expression_colors: bool) -> bool:
    """Whether a gradient's stops at [key] are right: 2-16 of them (2-8 at level 1), each [position, colour], the
    positions 0-1 and in order. At level 2 a position may be an expression in a string (put in order as drawn), and so
    may a colour where [expression_colors] (a shape's only from level 2); at level 1 either is told the level it came
    with. Each problem is added."""
    most = STOPS_MAX if self.level >= 2 else STOPS_MAX_LEVEL_1
    shaped = isinstance(stops, list) and 2 <= len(stops) <= most
    gated = False
    last = -math.inf
    for index, stop in enumerate(stops if isinstance(stops, list) else []):
      pair = stop if isinstance(stop, list) else []
      position = pair[0] if pair else None
      color = pair[1] if len(pair) > 1 else None
      at = None
      position_ok = False
      if isinstance(position, str):
        position_ok = True
        if self.level < 2:
          gated = True
          self.errors.append(
            f'{self.where}.{key}[{index}]: a position as an expression came with level 2: give the pack "level": 2'
          )
        else:
          try:
            self.names.extend(expression_names(position, self.level))
          except ExpressionError as e:
            gated = True
            self.errors.append(f"{self.where}.{key}[{index}] {e}")
      else:
        at = _float(position)
        position_ok = _in(at, UNIT)
      color_ok = False
      if isinstance(color, str) and color.startswith("="):
        color_ok = True
        if not expression_colors:
          gated = True
          self.errors.append(
            f'{self.where}.{key}[{index}]: a colour as an expression came with level 2: give the pack "level": 2'
          )
        elif not self.color_text(color):
          # Its expression doesn't parse, and has said why: the stops' shape is right.
          gated = True
      else:
        color_ok = _color_ok(color)
      # Positions given as numbers are in order; one an expression gives is put in order as it's drawn.
      if position_ok and at is not None:
        if at < last:
          shaped = False
        else:
          last = at
      if len(pair) != 2 or not position_ok or not color_ok:
        shaped = False
    if not shaped:
      positions = "position 0-1 or an expression" if self.level >= 2 else "position 0-1"
      self.errors.append(f"{self.where}.{key} must be 2-{most} [{positions}, {colors}] pairs in order")
    return shaped and not gated

  def paint_gradient(self, key, value: dict) -> bool:
    """A shape's gradient across its own bounds (PackGradient): linear or radial, 2-16 stops in order (2-8 at level 1),
    their colours and positions, and its coordinates, may be expressions from level 2."""
    where = f"{self.where}.{key}"
    radial = "radial" in value
    ok = True
    if ("radial" in value) == ("linear" in value) or any(k not in GRADIENT_KEYS for k in value):
      self.errors.append(
        f'{where}: a gradient is {{"linear": [x1, y1, x2, y2], "stops": [...]}} or '
        '{"radial": [cx, cy, r], "stops": [...]}'
      )
      ok = False
    else:
      kind = "radial" if radial else "linear"
      geometry = value[kind]
      wanted = 3 if radial else 4
      items = geometry if isinstance(geometry, list) else []
      shaped = len(items) == wanted
      # At level 2 a coordinate may be an expression in a string (its value kept to the range as drawn).
      for index, item in enumerate(items):
        bounds = SIZE if radial and index == 2 else POSITION
        if isinstance(item, str):
          if self.level < 2:
            ok = False
            self.errors.append(
              f'{where}.{kind}[{index}]: a coordinate as an expression came with level 2: give the pack "level": 2'
            )
            continue
          try:
            self.names.extend(expression_names(item, self.level))
          except ExpressionError as e:
            ok = False
            self.errors.append(f"{where}.{kind}[{index}] {e}")
        elif not _in(_float(item), bounds):
          shaped = False
      if not shaped:
        self.errors.append(
          f"{where}.{kind} must be {wanted} numbers from {POSITION[0]} to {POSITION[1]}"
          + (f" (r from {SIZE[0]} to {SIZE[1]})" if radial else "")
          + (", or expressions in strings" if self.level >= 2 else "")
        )
        ok = False
    colors = '"#RRGGBBAA" or "=expression"' if self.level >= 2 else '"#RRGGBBAA"'
    stops_ok = self.read_stops(value.get("stops"), f"{key}.stops", colors, expression_colors=self.level >= 2)
    return ok and stops_ok

  def color_value(self, key, gradient=False) -> bool:
    """A colour: "#RRGGBB[AA]", or "=" and an expression giving one, or (where [gradient]) a gradient. False when
    it's missing or wrong."""
    if key not in self.layer:
      return False
    value = self.layer[key]
    if gradient and isinstance(value, dict):
      return self.paint_gradient(key, value)
    if isinstance(value, str) and value.startswith("="):
      try:
        self.names.extend(expression_names(value[1:], self.level))
        return True
      except ExpressionError as e:
        self.errors.append(f"{self.where}.{key} {e}")
        return False
    if _color_ok(value):
      return True
    self.errors.append(
      f'{self.where}.{key} must be a colour, "#RRGGBB" or "#RRGGBBAA", or "=" and an expression giving one'
    )
    return False

  def required_color(self, key) -> None:
    """A colour that must be there: as color_value's (a colour or "=" and an expression)."""
    if key not in self.layer:
      self.errors.append(
        f'{self.where}.{key} must be a colour, "#RRGGBB" or "#RRGGBBAA", or "=" and an expression giving one'
      )
    else:
      self.color_value(key)

  def paint(self) -> None:
    self.color_value("fill", gradient=True)
    self.color_value("stroke", gradient=True)
    if "fill" not in self.layer and "stroke" not in self.layer:
      self.errors.append(f"{self.where} needs a fill, a stroke or both")
    self.value("strokeWidth", LENGTH, default=0.006)

  def glow(self) -> None:
    """A shape's glow: {"color": ..., "radius": 0-0.5, "dx": -0.5-0.5, "dy": -0.5-0.5}, a blurred copy under it."""
    if "glow" not in self.layer:
      return
    glow = self.layer["glow"]
    if not isinstance(glow, dict):
      self.errors.append(f'{self.where}.glow must be {{"color": ..., "radius": ...}}')
      return
    sub = self.sub(f"{self.where}.glow", glow)
    sub.required_color("color")
    sub.value("radius", GLOW_RADIUS)
    sub.value("dx", GLOW_OFFSET, default=0)
    sub.value("dy", GLOW_OFFSET, default=0)
    if any(key not in GLOW_KEYS for key in glow):
      self.errors.append(f"{self.where}.glow takes {', '.join(GLOW_KEYS[:-1])} and {GLOW_KEYS[-1]}")

  def flag(self, key) -> None:
    """A true-or-false field, false without one."""
    if key in self.layer and not isinstance(self.layer[key], bool):
      self.errors.append(f"{self.where}.{key} must be true or false")

  def anchor(self) -> None:
    if "anchor" in self.layer and (not isinstance(self.layer["anchor"], str) or self.layer["anchor"] not in ANCHORS):
      self.errors.append(f"{self.where}.anchor must be one of {', '.join(sorted(ANCHORS))}")

  def template(self, kind) -> bool | None:
    """Reads the layer's text; whether it has placeholders (None when it isn't text)."""
    text = self.layer.get("text")
    where = self.where
    if not isinstance(text, str) or not text:
      self.errors.append(f"{where}.text must be 1-{BADGE_MAX_TEXT} characters of text")
      return None
    found_errors, found_warnings, found_names, dynamic = template_problems(text, self.level)
    self.errors.extend(f"{where}.text {error}" for error in found_errors)
    self.warnings.extend(f"{where}.text: {warning}" for warning in found_warnings)
    self.names.extend(found_names)
    if not dynamic and _code_points(text) > BADGE_MAX_TEXT:
      self.errors.append(f"{where}.text must be 1-{BADGE_MAX_TEXT} characters")
    elif dynamic and kind == "badge" and _code_points(text) > BADGE_MAX_TEXT:
      self.warnings.append(
        f"{where}.text: a badge fits about {BADGE_MAX_TEXT} characters; the app draws at most "
        f"{TEMPLATE_MAX_DRAWN} filled"
      )
    return dynamic

  # ---- the layers

  def _pathFill(self) -> None:
    self.stops("gradient")

  def _pathChevrons(self) -> None:
    self.required_color("color")
    self.expression("acceleration", required=False)

  def _pathLine(self) -> None:
    self.expression("distance", required=True)
    self.number("halfWidthMeters", (0.05, 3), default=0.85)
    self.number("thickness", (0.001, 0.05), default=0.007)
    self.required_color("color")
    self.color_value("glow")

  def _ribbon(self) -> None:
    """A band along the model's path, a lane line or a road edge, drawn on the road."""
    layer, where, errors = self.layer, self.where, self.errors
    along = layer.get("along") if isinstance(layer.get("along"), str) else ""
    if along not in RIBBON_ALONG:
      errors.append(f"{where}.along '{along}' isn't one of {', '.join(RIBBON_ALONG)}")
      along = "path"
    if "index" in layer:
      most = RIBBON_ALONG[along]
      index = layer["index"]
      if most is None:
        errors.append(f"{where}.index is for a laneLine or roadEdge")
      elif type(index) is not int or not 0 <= index <= most:
        errors.append(f"{where}.index must be 0-{most}")
    has_color, has_gradient = "color" in layer, "gradient" in layer
    if has_color == has_gradient:
      errors.append(f"{where} needs a color or a gradient, not both")
    if "dash" in layer and not self.numbers("dash", 2, DASH):
      errors.append(f"{where}.dash must be [on, off] metres, each {DASH[0]} to {DASH[1]}")
    self.value("from", OFFSET, default=0)
    self.value("to", OFFSET, default=0)
    self.value("start", AHEAD, default=0)
    self.value("end", AHEAD, default=0)
    if has_color:
      self.required_color("color")
    elif has_gradient:
      self.stops("gradient")
    self.number("fade", UNIT, default=0)
    # What its gradient runs along (level 2): its length, or down the road view.
    if "space" in layer:
      space = layer["space"]
      if space not in ("length", "view"):
        errors.append(f"{where}.space '{space if isinstance(space, str) else ''}' isn't one of length, view")
      if not has_gradient:
        errors.append(f"{where}.space is for a ribbon's gradient")

  def _badge(self) -> None:
    self.template("badge")
    # In a group, placed as its shapes are (a little past its box); on the road view, inside it.
    bounds = POSITION if self.depth else UNIT
    x, y = self.number("x", bounds), self.number("y", bounds)
    # In a group a badge can fill its box; on the road view it's a label's size.
    width = self.number("width", (0.02, 1 if self.depth else 0.8))
    height = self.number("height", (0.02, 1 if self.depth else 0.15))
    self.required_color("fill")
    self.required_color("textColor")
    # As the app adds them, in Floats.
    if not self.depth and (_f32(x + width) > 1 or _f32(y + height) > 1):
      self.errors.append(f"{self.where} must fit inside the road view")

  def _text(self) -> None:
    layer, where, errors = self.layer, self.where, self.errors
    font = layer.get("font", "sans")
    if not isinstance(font, str) or font not in FONTS:
      errors.append(f"{where}.font must be one of {', '.join(sorted(FONTS))}")
      font = "sans"
    # Absent: the font's own (condensed's semibold, the others' medium). Given, one of the names ("" or a number isn't).
    weight = layer.get("weight")
    if "weight" in layer and weight not in WEIGHTS:
      errors.append(f"{where}.weight '{_shown(weight)}' isn't one of {', '.join(WEIGHTS)}")
      weight = "medium"
    if font == "condensed" and weight not in (None, "semibold"):
      errors.append(f"{where}.weight: condensed is semibold only")
    if "wrap" in layer:
      wrap = layer["wrap"]
      width = _float(wrap.get("width")) if isinstance(wrap, dict) else None
      lines = wrap.get("lines") if isinstance(wrap, dict) else None
      if (
        not isinstance(wrap, dict)
        or any(key not in ("width", "lines") for key in wrap)
        or not _in(width, WRAP_WIDTH)
        or type(lines) is not int
        or not 1 <= lines <= WRAP_MAX_LINES
      ):
        errors.append(f'{where}.wrap must be {{"width": {WRAP_WIDTH[0]}-{WRAP_WIDTH[1]}, "lines": 1-{WRAP_MAX_LINES}}}')
    if "padding" in layer and not self.numbers("padding", 2, EMS):
      errors.append(f"{where}.padding must be [x, y] ems, each {EMS[0]} to {EMS[1]}")
    # Along a circle (level 2): in place of x, y and anchor, and without a box's wrap, background, padding or radius.
    on_arc = "arc" in layer
    if on_arc:
      arc = layer["arc"]
      if not isinstance(arc, dict):
        errors.append(f'{where}.arc must be {{"cx": ..., "cy": ..., "r": ..., "at": degrees}}')
      else:
        sub = self.sub(f"{where}.arc", arc)
        if any(key not in ARC_KEYS for key in arc):
          errors.append(f"{where}.arc takes cx, cy, r and at")
        sub.value("cx", POSITION)
        sub.value("cy", POSITION)
        sub.value("r", SIZE)
        sub.value("at", ANGLE, default=0)
      for key in NOT_WITH_ARC:
        if key in layer:
          errors.append(f"{where}.{key} can't be used with arc")
    self.flag("inside")
    if "inside" in layer and not on_arc:
      errors.append(f"{where}.inside is for text along an arc")
    dynamic = self.template("text")
    text = layer.get("text")
    if on_arc and dynamic is False:
      if _code_points(text) > ARC_MAX_TEXT:
        errors.append(f"{where}.text must be 1-{ARC_MAX_TEXT} characters along an arc")
    bounds = POSITION if self.depth else UNIT
    if not on_arc:
      self.number("x", bounds)
      self.number("y", bounds)
      self.anchor()
    # Of its box's height: a group's box can be small.
    self.number("size", (0.015, 1 if self.depth else 0.12))
    self.required_color("color")
    self.color_value("outline")
    self.color_value("background")
    self.number("tracking", TRACKING, default=0)
    if "maxWidth" in layer:
      self.number("maxWidth", WRAP_WIDTH)
    self.number("radius", EMS, default=0)

  def _rect(self) -> None:
    for key, bounds in (("x", POSITION), ("y", POSITION), ("width", SIZE), ("height", SIZE)):
      self.value(key, bounds)
    self.value("radius", LENGTH, default=0)
    self.paint()
    self.glow()

  def _circle(self) -> None:
    self.value("cx", POSITION)
    self.value("cy", POSITION)
    self.value("r", SIZE)
    self.paint()
    self.glow()

  def _line(self) -> None:
    for key in ("x1", "y1", "x2", "y2"):
      self.value(key, POSITION)
    if not self.color_value("stroke", gradient=True) and "stroke" not in self.layer:
      self.errors.append(f"{self.where}.stroke must be a colour or a gradient")
    self.value("strokeWidth", LENGTH, default=0.006)
    self.glow()

  def _polygon(self) -> None:
    points = self.layer.get("points")
    if not isinstance(points, list) or not 3 <= len(points) <= POLYGON_MAX_POINTS:
      self.errors.append(f"{self.where}.points must be 3-{POLYGON_MAX_POINTS} [x, y] pairs")
    else:
      for index, point in enumerate(points):
        if not isinstance(point, list) or len(point) != 2:
          self.errors.append(f"{self.where}.points[{index}] must be [x, y]")
          continue
        self._value(point[0], f"points[{index}][0]", POSITION)
        self._value(point[1], f"points[{index}][1]", POSITION)
    self.paint()
    self.glow()

  def _arc(self) -> None:
    self.value("cx", POSITION)
    self.value("cy", POSITION)
    self.value("r", SIZE)
    self.value("start", ANGLE, default=0)
    self.value("sweep", ANGLE)
    self.paint()
    self.glow()

  def _path(self) -> None:
    """Any outline, from SVG path data in fractions of its box."""
    data = self.layer.get("d")
    if not isinstance(data, str):
      self.errors.append(f"{self.where}.d must be SVG path data in a string")
    elif (problem := path_data_problem(data)) is not None:
      self.errors.append(f"{self.where}.d {problem}")
    self.paint()
    self.flag("evenOdd")
    self.glow()

  def _edgeGlow(self) -> None:
    """A glow inward from its box's sides."""
    self.required_color("color")
    self.value("reach", REACH)
    if "sides" not in self.layer:
      return
    sides = self.layer["sides"]
    if isinstance(sides, str) and sides in ("all", "sides", "ends"):
      return
    if (
      isinstance(sides, list) and sides and all(side in EDGE_SIDES for side in sides) and len(set(sides)) == len(sides)
    ):
      return
    self.errors.append(f'{self.where}.sides must be "all", "sides", "ends" or a list of {", ".join(EDGE_SIDES)}')

  def _image(self) -> None:
    name = self.layer.get("image")
    name = name if isinstance(name, str) else ""
    if name not in self.images:
      self.errors.append(f"{self.where}.image '{name}' isn't one of the pack's images")
    for key, bounds in (("x", POSITION), ("y", POSITION), ("width", SIZE), ("height", SIZE)):
      self.value(key, bounds)
    self.color_value("tint")

  def _group(self) -> None:
    where, layer, errors = self.where, self.layer, self.errors
    if self.depth >= OVERLAY_MAX_DEPTH:
      errors.append(f"{where}: groups go at most {OVERLAY_MAX_DEPTH} deep")
      return
    pinned = False
    on_part = False
    pin = layer.get("pin")
    if "pin" in layer:
      if not isinstance(pin, dict):
        errors.append(f"{where}.pin must be an object")
      elif "lead" in pin:
        pinned = True
        self.lead(pin)
      elif "path" in pin:
        sub = self.sub(f"{where}.pin", pin)
        pinned = sub.expression("path", required=True)
        sub.value("offset", OFFSET, default=0)
        if any(key not in ("path", "offset", "size") for key in pin):
          errors.append(f"{where}.pin takes path, offset and size")
      # Level 2: a point by the car, and a part of the design.
      elif "car" in pin:
        if self.level < 2:
          errors.append(f'{where}.pin.car came with level 2: give the pack "level": 2')
        if any(key not in ("car", "size") for key in pin):
          errors.append(f"{where}.pin takes car and size")
        point = pin["car"]
        if not isinstance(point, list) or len(point) != 2:
          errors.append(
            f"{where}.pin.car must be [ahead, right] in metres, ahead {CAR_AHEAD[0]} to {CAR_AHEAD[1]} "
            f"and right {CAR_RIGHT[0]} to {CAR_RIGHT[1]}, numbers or expressions"
          )
        else:
          pinned = True
          self._value(point[0], "pin.car[0]", CAR_AHEAD)
          self._value(point[1], "pin.car[1]", CAR_RIGHT)
      elif "hud" in pin:
        if self.level < 2:
          errors.append(f'{where}.pin.hud came with level 2: give the pack "level": 2')
        if any(key != "hud" for key in pin):
          errors.append(f"{where}.pin takes only hud")
        part = pin["hud"]
        if not (isinstance(part, str) and part in HUD_PARTS):
          errors.append(
            f"{where}.pin.hud '{part if isinstance(part, str) else ''}' isn't a part a group can sit on "
            f"({', '.join(HUD_PARTS)})"
          )
        else:
          pinned = on_part = True
      elif self.level < 2:
        errors.append(f'{where}.pin must be {{"path": "distance", "offset": metres}} or {{"lead": true}}')
      else:
        errors.append(
          f'{where}.pin must be {{"path": "distance", "offset": metres}}, {{"lead": true|1|2}}, '
          '{"car": [ahead, right]} or {"hud": "<part>"}'
        )
    if pinned and self.depth > 0:
      errors.append(f"{where}.pin: only a top-level group {'sits on a part' if on_part else 'sits on the road'}")
    # A road pin: the box sits on its point, nudged in fractions of the road view. On a part, it's placed in the part's
    # rectangle, as a group is in its parent's box.
    on_road = pinned and not on_part
    # A pinned group's size in metres at its point (it grows as the point comes closer), in place of width and height.
    sized = False
    if isinstance(pin, dict) and "size" in pin:
      sized = self.sub(f"{where}.pin", pin).numbers("size", 2, PIN_METRES)
      if not sized:
        errors.append(f"{where}.pin.size must be [width, height] in metres, each {PIN_METRES[0]} to {PIN_METRES[1]}")
    # Drawn for each element of an array (level 2); its layers count as many times as it may draw.
    repeat_max = None
    if "repeat" in layer:
      repeat = layer["repeat"]
      over_reader = self.sub(f"{where}.repeat", repeat) if isinstance(repeat, dict) else None
      if over_reader is not None:
        over_reader.names = self.outer_names
      over = over_reader is not None and over_reader.expression("over", required=True)
      most = repeat.get("max") if isinstance(repeat, dict) else None
      if (
        not isinstance(repeat, dict)
        or not over
        or type(most) is not int
        or not 1 <= most <= REPEAT_MAX
        or any(key not in ("over", "max") for key in repeat)
      ):
        errors.append(f'{where}.repeat must be {{"over": "<expression>", "max": 1-{REPEAT_MAX}}}')
      else:
        repeat_max = most
      if self.in_repeat:
        errors.append(f"{where}.repeat: repeats don't nest")
    children = layer.get("layers")
    if not isinstance(children, list) or not 1 <= len(children) <= OVERLAY_MAX_LAYERS:
      errors.append(f"{where}.layers must be a list of 1-{OVERLAY_MAX_LAYERS} layers")
    before = self.nodes[0]
    for index, child in enumerate(children[:OVERLAY_MAX_LAYERS] if isinstance(children, list) else []):
      _LayerReader(
        f"{where}.layers[{index}]",
        child,
        errors,
        self.warnings,
        self.names,
        self.images,
        self.depth + 1,
        self.nodes,
        self.level,
        self.in_repeat or "repeat" in layer,
      ).read()
    if repeat_max is not None:
      self.nodes[0] += (self.nodes[0] - before) * (repeat_max - 1)
    self.value("x", PIN_NUDGE if on_road else POSITION, default=0)
    self.value("y", PIN_NUDGE if on_road else POSITION, default=0)
    self.value("width", SIZE, default=None if on_road and not sized else 1)
    self.value("height", SIZE, default=None if on_road and not sized else 1)
    self.value("rotation", ANGLE, default=0)
    if "aspect" in layer:
      self.value("aspect", ASPECT)
    self.anchor()

  def lead(self, pin: dict) -> None:
    """A group's {"lead": true} (the lead car), or at level 2 {"lead": 1} or {"lead": 2} (the second)."""
    lead = pin["lead"]
    which = None
    if lead is True or lead == "true":
      which = 1
    elif type(lead) is int and lead in (1, 2):
      which = lead
      if self.level < 2:
        self.errors.append(f'{self.where}.pin.lead: {lead} came with level 2: give the pack "level": 2')
    if which is None or any(key not in ("lead", "size") for key in pin):
      allowed = "true" if self.level < 2 else "true, 1 or 2"
      self.errors.append(f"{self.where}.pin.lead must be {allowed}, with only a size beside it")

  def _backdrop(self) -> None:
    """Over the camera and under every road layer (level 2): a fill (a colour, a gradient or "=" and an expression)
    across the whole road view, at most one per pack."""
    self.nodes[1] += 1
    if self.nodes[1] > 1:
      self.errors.append(f"{self.where}: a pack has at most one backdrop")
    if "fill" not in self.layer:
      self.errors.append(f'{self.where}.fill must be a colour, a gradient, or "=" and an expression giving one')
    else:
      self.color_value("fill", gradient=True)


# A pack's preview (the store's "Preview image" rules, docs/extensions.md, The preview): preview.png in its folder, a
# root member "preview": "data:image/png;base64,..." in its bundle. Read as the catalog reads it at submission
# (my.commaview.com's preview check), with the same messages: keep the two in step. The app never refuses a pack for
# its preview; the catalog and this checker do.
PREVIEW_FILE = "preview.png"
PREVIEW_PREFIX = IMAGE_PREFIX
PREVIEW_MAX_BYTES = 256 * 1024
PREVIEW_MIN = (320, 180)
PREVIEW_MAX = (1280, 720)
# Standard base64, padded, as a data URI writes it.
_PREVIEW_BASE64 = re.compile(r"[A-Za-z0-9+/]*={0,2}")
_CHUNK_TYPE = re.compile(rb"[A-Za-z]{4}")
# The chunks of an animated PNG: an APNG's first frame alone would be a still, but it isn't one frame.
_ANIMATION_CHUNKS = frozenset({b"acTL", b"fcTL", b"fdAT"})
# Channels by colour type: the two a preview may be (RGB, RGBA).
_PREVIEW_CHANNELS = {2: 3, 6: 4}
# Adam7's passes: each one's first column and row, and its steps across and down.
_ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))


def _kib(size: int) -> str:
  """[size] bytes in KiB to one place, as JavaScript's toFixed(1) writes it (a half rounds up)."""
  tenths = (size * 10 + 512) // 1024
  return f"{tenths // 10}.{tenths % 10}"


def _sixteen_nine(width: int) -> int:
  """The height 16:9 gives [width], rounded as JavaScript's Math.round rounds (a half up)."""
  return (width * 9 + 8) // 16


def _preview_sub_images(width: int, height: int, interlaced: bool) -> list[tuple[int, int]]:
  """The images a PNG's rows come in, width by height: the whole one, or each Adam7 pass (an empty one is none)."""
  if not interlaced:
    return [(width, height)]
  return [(-(-(width - x0) // dx), -(-(height - y0) // dy)) for x0, y0, dx, dy in _ADAM7]


def _preview_rows_ok(data: bytes, images: list[tuple[int, int]], channels: int) -> bool:
  """Whether [data], inflated image data, is exactly the rows [images] need (each a filter byte and its pixels) and
  each row's filter is one PNG has (0 to 4)."""
  at = 0
  for w, h in images:
    if not w or not h:
      continue
    stride = 1 + w * channels
    for _ in range(h):
      if at >= len(data) or data[at] > 4:
        return False
      at += stride
  return at == len(data)


def _inflate_exactly(data: bytes, expected: int) -> bytes | None:
  """[data], a zlib stream, inflated, or None when it isn't one or holds more than [expected] bytes (never inflating
  past that)."""
  try:
    inflater = zlib.decompressobj()
    out = inflater.decompress(data, expected)
    if not inflater.eof:
      # At [expected] bytes zlib stops short: what's left must be the stream's end, with nothing more in it.
      if inflater.decompress(inflater.unconsumed_tail, 1) or not inflater.eof:
        return None
  except zlib.error:
    return None
  return out


def preview_png_problems(png: bytes) -> list[str]:
  """[png]'s problems as a pack's preview, from its bytes: [] when it's fine (preview.ts pngProblems)."""
  if len(png) > PREVIEW_MAX_BYTES:
    return [f"preview.png is {_kib(len(png))} KiB: at most 256 KiB."]
  if len(png) < len(PNG_SIGNATURE) + 25 or png[:8] != PNG_SIGNATURE:
    return ["preview isn't a PNG: it must be a PNG image."]

  def broken(why: str) -> list[str]:
    return [f"preview.png can't be read: {why}."]

  at = 8
  ihdr = None
  idat: list[bytes] = []
  idat_done = False
  ended = False
  while at < len(png):
    if ended:
      return broken("it has bytes after its IEND chunk")
    if at + 12 > len(png):
      return broken("a chunk is cut short")
    (length,) = struct.unpack_from(">I", png, at)
    type_bytes = png[at + 4 : at + 8]
    if not _CHUNK_TYPE.fullmatch(type_bytes):
      return broken("a chunk has no valid type")
    kind = type_bytes.decode("latin-1")
    if length > 0x7FFFFFFF or at + 12 + length > len(png):
      return broken(f"its {kind} chunk is cut short")
    data = png[at + 8 : at + 8 + length]
    if binascii.crc32(png[at + 4 : at + 8 + length]) != struct.unpack_from(">I", png, at + 8 + length)[0]:
      return broken(f"its {kind} chunk's CRC is wrong")
    if ihdr is None and kind != "IHDR":
      return broken("it must start with its IHDR chunk")
    if type_bytes in _ANIMATION_CHUNKS:
      return ["preview.png is animated (an APNG): it must be one still frame."]
    if kind == "IHDR":
      if ihdr is not None:
        return broken("it has two IHDR chunks")
      if length != 13:
        return broken("its IHDR chunk is the wrong size")
      ihdr = data
    elif kind == "IDAT":
      if idat_done:
        return broken("its IDAT chunks are apart")
      idat.append(data)
    elif kind == "IEND":
      ended = True
    elif kind != "PLTE" and not type_bytes[0] & 0x20:
      # A critical chunk PNG doesn't define: a decoder must refuse it.
      return broken(f"it has a critical chunk PNG doesn't define ({kind})")
    if idat and kind != "IDAT":
      idat_done = True
    at += 12 + length
  if not ended:
    return broken("it has no IEND chunk")
  if not idat or ihdr is None:
    return broken("it has no image data (IDAT)")

  width, height, depth, colour, compression, filtering, interlace = struct.unpack(">IIBBBBB", ihdr)
  if not width or not height or width > 0x7FFFFFFF or height > 0x7FFFFFFF:
    return broken("its size is out of range")
  channels = _PREVIEW_CHANNELS.get(colour)
  if depth != 8 or not channels:
    return ["preview.png must be 8-bit RGB or RGBA (no palette, greyscale or 16-bit)."]
  if compression != 0 or filtering != 0 or interlace not in (0, 1):
    return broken("its IHDR names a method PNG lacks")

  problems = []
  if width > PREVIEW_MAX[0] or height > PREVIEW_MAX[1]:
    problems.append(f"preview.png is {width} × {height}: at most {PREVIEW_MAX[0]} × {PREVIEW_MAX[1]}.")
  elif width < PREVIEW_MIN[0] or height < PREVIEW_MIN[1]:
    problems.append(f"preview.png is {width} × {height}: at least {PREVIEW_MIN[0]} × {PREVIEW_MIN[1]}.")
  if height != _sixteen_nine(width):
    d = math.gcd(width, height)
    # Its own width, kept within the limits.
    w = min(PREVIEW_MAX[0], max(PREVIEW_MIN[0], width))
    problems.append(
      f"preview.png is {width // d}:{height // d} ({width} × {height}): make it 16:9, as {w} × {_sixteen_nine(w)}."
    )
  # Only a size it may be is inflated, so what's inflated is bounded (at most about 3.7 MB).
  if problems:
    return problems

  images = _preview_sub_images(width, height, interlace == 1)
  expected = sum(h * (1 + w * channels) for w, h in images if w and h)
  pixels = _inflate_exactly(b"".join(idat), expected)
  if pixels is None:
    return broken("its image data doesn't decode to its size")
  if not _preview_rows_ok(pixels, images, channels):
    return broken("its image data doesn't decode to its size, or a row has a filter PNG lacks")
  return []


def preview_png(value) -> tuple[bytes | None, list[str]]:
  """A bundle's "preview" member read: (its PNG, []) when it passes, else (None, every problem) (preview.ts
  readPreview). The member must be a string of exactly "data:image/png;base64," and standard, padded base64."""
  if not isinstance(value, str) or not value.startswith(PREVIEW_PREFIX):
    return None, [f'"preview" must be a PNG as a data URI: "{PREVIEW_PREFIX}…".']
  text = value[len(PREVIEW_PREFIX) :]
  if not text or len(text) % 4 != 0 or not _PREVIEW_BASE64.fullmatch(text):
    return None, [f'"preview" isn\'t valid base64 after "{PREVIEW_PREFIX}".']
  # Measured before it's decoded: a long one is refused without decoding it.
  size = len(text) // 4 * 3 - (2 if text.endswith("==") else 1 if text.endswith("=") else 0)
  if size > PREVIEW_MAX_BYTES:
    return None, [f"preview.png is {_kib(size)} KiB: at most 256 KiB."]
  png = base64.b64decode(text)
  problems = preview_png_problems(png)
  return (None, problems) if problems else (png, [])


def preview_problems(value) -> list[str]:
  """What's wrong with a bundle's "preview" member: [] when nothing is."""
  return preview_png(value)[1]


def preview_data_url(png: bytes) -> str:
  return PREVIEW_PREFIX + base64.b64encode(png).decode("ascii")


def read_preview_file(path: Path) -> tuple[bytes | None, list[str]]:
  """A folder's preview.png: (its bytes, []), or (None, why not), measured before it's read (one over 256 KiB is
  refused unread). Raises OSError when it can't be read."""
  size = path.stat().st_size
  if size > PREVIEW_MAX_BYTES:
    return None, [f"preview.png is {_kib(size)} KiB: at most 256 KiB."]
  png, problems = preview_png(preview_data_url(path.read_bytes()))
  return png, problems


def is_official(pack: Path, manifest: dict) -> bool:
  """Whether the pack in folder [pack] is official: in examples/, by CommaView itself."""
  return pack.resolve().parent == EXAMPLES.resolve() and manifest.get("author") == OFFICIAL_AUTHOR


@cache
def listed_flavors() -> tuple[str, ...] | None:
  """The listed forks' ids (the transformer's LISTED_FLAVORS, or LISTED_FORKS where there's no transformer), None
  where this checkout has neither."""
  if TRANSFORMER is None or not TRANSFORMER.is_file():
    if not LISTED_FORKS.is_file():
      return None
    forks = json.loads(LISTED_FORKS.read_text())
    if not isinstance(forks, list) or not all(isinstance(fork, str) for fork in forks):
      raise ValueError("must be a list of fork ids")
    return tuple(forks)
  spec = importlib.util.spec_from_file_location("commaview_transformer_check", TRANSFORMER)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return tuple(module.LISTED_FLAVORS)


def forks_problems(manifest: dict, official: bool, listed: bool = True) -> tuple[list[str], list[str]]:
  """The errors and warnings of manifest.forks, the forks the pack is for. Optional, but when it's there the catalog
  holds it to its rule: a list of at most 16 lowercase fork ids, none twice. An official pack must name at least one,
  each a fork CommaView lists; a community pack naming one that isn't listed is only warned of. [listed] False skips
  that last look (a community pack's errors never depend on it)."""
  if "forks" not in manifest:
    return (
      ['an official pack names the forks it\'s for: give the manifest "forks", e.g. ["bluepilot"]'] if official else []
    ), []
  forks = manifest["forks"]
  rule = f"manifest.forks must be a list of at most {MAX_FORKS} fork ids, each 1-40 of a-z, 0-9 and '-', none twice"
  if not isinstance(forks, list) or len(forks) > MAX_FORKS:
    return [rule], []
  errors = []
  if any(not isinstance(fork, str) or not FORK_ID_RE.fullmatch(fork) for fork in forks):
    errors.append(rule)
  elif len(set(forks)) != len(forks):
    errors.append(f"{rule}: {', '.join(sorted({fork for fork in forks if forks.count(fork) > 1}))} twice")
  if official and not forks:
    errors.append("an official pack's manifest.forks names at least one fork")
  if errors or not (listed or official):
    return errors, []
  try:
    flavors = listed_flavors()
  except ValueError as e:  # LISTED_FORKS isn't JSON, or isn't a list of ids (json's errors are ValueErrors too)
    message = f"manifest.forks can't be checked against the listed forks: {LISTED_FORKS.name} {e}"
    return ([message], []) if official else ([], [message])
  if flavors is None:
    message = "manifest.forks can't be checked against the listed forks: this checkout has no table of them"
    return ([message], []) if official else ([], [message])
  unlisted = [fork for fork in forks if fork not in flavors]
  if not unlisted:
    return [], []
  message = f"manifest.forks: {', '.join(unlisted)} isn't a fork CommaView lists ({', '.join(flavors)})"
  return ([message], []) if official else ([], [message])


def is_standalone(manifest: dict, overlay) -> bool:
  return isinstance(overlay, dict) and overlay.get("standalone") is True


def pack_shape_errors(manifest: dict, overlay, has_export: bool) -> list:
  """What's wrong with which halves a pack has: a standalone pack has no runtime half, any other pack has one."""
  runtime = "runtime" in manifest
  if is_standalone(manifest, overlay):
    errors = []
    if runtime:
      errors.append('a standalone pack (overlay "standalone": true) has no runtime: take "runtime" out of the manifest')
    if has_export:
      errors.append(
        'a standalone pack\'s export.py would never run: remove it, or give the manifest a "runtime" and '
        'the overlay "standalone": false'
      )
    return errors
  if not runtime:
    return [
      (
        "manifest.runtime is missing: a pack with a runtime half names it, and one drawn by the app alone says "
        'so in overlay.json ("standalone": true)'
      )
    ]
  if not isinstance(manifest.get("runtime"), dict):
    return ["manifest.runtime must be an object"]
  if not has_export:
    return ["export.py is missing"]
  return []


def bundle_errors(bundle, manifest_text: str | None = None) -> list:
  """What keeps [bundle] (a .cvpack, parsed) from being installed or submitted, [] when nothing does. [manifest_text]:
  the manifest's text in the bundle as it is, which the comma measures; without it, as write_bundle writes it. A
  "preview" is held to the catalog's rules, its problems last (as the catalog says them); the app and the comma
  never read it."""
  errors = _install_errors(bundle, manifest_text)
  # The catalog's rule for manifest.forks (the comma never reads it).
  if isinstance(bundle, dict) and isinstance(bundle.get("manifest"), dict):
    errors.extend(forks_problems(bundle["manifest"], official=False, listed=False)[0])
  if isinstance(bundle, dict) and "preview" in bundle:
    errors.extend(preview_problems(bundle["preview"]))
  return errors


def _install_errors(bundle, manifest_text: str | None) -> list:
  """What keeps [bundle] from being installed: bundle_errors without the preview."""
  if not isinstance(bundle, dict):
    return ["the bundle must be a JSON object"]
  errors = []
  if not _is_one(bundle.get("cvpack")):
    errors.append("cvpack must be 1")
  manifest = bundle.get("manifest")
  overlay = bundle.get("overlay")
  source = bundle.get("export")
  if not isinstance(manifest, dict):
    errors.append("manifest must be an object")
    manifest = {}
  if not _is_one(manifest.get("sdk")):
    errors.append("manifest.sdk must be 1")
  ext_id = manifest.get("id")
  if not isinstance(ext_id, str) or not ID_RE.fullmatch(ext_id):
    errors.append("manifest.id must be 1-32 of a-z, 0-9 and '-', not starting with '-'")
  version = manifest.get("version")
  if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
    errors.append("manifest.version must be 1-32 of 0-9, A-Z, a-z, '.', '+' and '-'")
  name = manifest.get("name")
  if not isinstance(name, str) or not 1 <= len(name) <= 64:
    errors.append("manifest.name must be 1-64 characters")
  # The comma writes the manifest's text in the bundle as it is, with a newline, and holds that to 16 KiB.
  if manifest_text is None:
    manifest_text = _bundle_manifest_text(manifest)
  if len(manifest_text.encode("utf-8", "backslashreplace")) + 1 > MAX_MANIFEST_BYTES:
    errors.append("manifest is over 16 KiB")
  runtime = manifest.get("runtime")
  if isinstance(runtime, dict) and "entry" in runtime and runtime["entry"] != "export.py":
    errors.append("manifest.runtime.entry must be export.py (or left out) in a bundle")
  standalone = is_standalone(manifest, overlay)
  if "runtime" in manifest:
    if not isinstance(source, str) or not source.strip():
      errors.append("export must be a string (export.py's source)")
    elif len(source.encode("utf-8", "surrogatepass")) > MAX_EXPORT_BYTES:
      errors.append("export is over 256 KiB")
  elif "export" in bundle:
    # The app refuses it: a standalone pack has nothing to run on the comma.
    errors.append("export is only for a pack with a runtime: add manifest.runtime, or leave export out")
  if not isinstance(overlay, dict):
    errors.append("overlay must be an object")
    return errors
  if overlay.get("id") != ext_id:
    errors.append("overlay.id must be manifest.id")
  if standalone and "runtime" in manifest:
    errors.append('a standalone pack (overlay "standalone": true) has no runtime: take "runtime" out of the manifest')
  if not standalone and "runtime" not in manifest:
    errors.append(
      "manifest.runtime is missing: a pack with a runtime half names it, and one drawn by the app alone "
      'says so in overlay.json ("standalone": true)'
    )
  # Written compactly, as the app reads the overlay out of a bundle.
  if len(_json_bytes(overlay)) > OVERLAY_MAX_BYTES:
    errors.append(f"overlay is over {OVERLAY_MAX_BYTES // 1024} KiB")
  overlay_errors, _, _ = overlay_problems(overlay)
  errors.extend(f"overlay: {error}" for error in overlay_errors if error != "overlay.id must be manifest.id")
  if "runtime" in manifest:
    # The comma reads the whole bundle: held to its JSON reader's rules too.
    errors.extend(_comma_json_problems(bundle))
  return errors


def _comma_json_problems(value) -> list:
  """What the comma's JSON reader (its control API's) refuses that Python's reads: containers nested past 64, and
  a lone surrogate (an escape like \\ud800 that's half a character) in any text. The first found."""
  stack = [(value, 1)]
  while stack:
    item, depth = stack.pop()
    # Any value, a number or text too: the comma reads none past its depth.
    if depth > COMMA_MAX_JSON_DEPTH:
      return [f"the bundle is nested past {COMMA_MAX_JSON_DEPTH} levels, which the comma refuses"]
    if isinstance(item, (dict, list)):
      members = item.items() if isinstance(item, dict) else enumerate(item)
      for key, member in members:
        if isinstance(key, str):
          stack.append((key, depth))  # a member's name is read at its object's depth
        stack.append((member, depth + 1))
    elif isinstance(item, str) and any("\ud800" <= c <= "\udfff" for c in item):
      return ["the bundle holds a lone surrogate (half a character), which the comma refuses"]
    elif isinstance(item, float) and not math.isfinite(item):
      # A literal past a double (1e999) reads as infinity here; the comma refuses it.
      return ["the bundle holds a number past what a double holds, which the comma refuses"]
  return []


def too_big(size: int, rest: int | None) -> str:
  """What to say of a bundle of [size] bytes, over 512 KiB, in the catalog's words (bundle.ts readBundle): [rest] is
  its size without its preview (None without one). When leaving the preview out would bring it under, the catalog
  says to export a smaller one, and this says how large one fits; otherwise the preview isn't what to make smaller."""
  if rest is None or rest > MAX_BUNDLE_BYTES:
    message = f"A .cvpack is at most 512 KiB; this is {size} bytes."
    if rest is not None:
      message += f" Without its preview it's still {-(-rest // 1024)} KiB: make the pack smaller."
    return message
  message = f"A .cvpack is at most 512 KiB; this is {-(-size // 1024)} KiB with its preview: export a smaller preview."
  # The PNG a preview member can hold in what's left, written as write_bundle writes one.
  member = len(f',\n  "preview": "{PREVIEW_PREFIX}"'.encode())
  room = min(PREVIEW_MAX_BYTES, max(0, MAX_BUNDLE_BYTES - rest - member) // 4 * 3)
  without = f"Without it the bundle is {-(-rest // 1024)} KiB"
  if room < 1024:
    return f"{message} {without}, which leaves no room for one: leave {PREVIEW_FILE} out."
  return f"{message} {without}, which leaves room for a {PREVIEW_FILE} of up to {room // 1024} KiB; or leave it out."


def _read_too_big(data: bytes) -> str:
  """too_big for a .cvpack's bytes as they are: without its preview, less the member as the catalog counts it (its
  key and its value written compactly, at most what it takes up)."""
  rest = None
  try:
    bundle = _strict_json(data)
    if isinstance(bundle, dict) and isinstance(bundle.get("preview"), str):
      member = '"preview":' + json.dumps(bundle["preview"], ensure_ascii=False)
      rest = len(data) - len(member.encode("utf-8", "surrogatepass"))
  except ValueError:
    pass
  return too_big(len(data), rest)


def read_bundle(path: Path):
  """A .cvpack's parsed bundle, or raises ValueError with what's wrong."""
  data = path.read_bytes()
  if len(data) > MAX_BUNDLE_BYTES:
    raise ValueError(_read_too_big(data))
  try:
    bundle = _strict_json(data)
  except ValueError as e:
    raise ValueError(f"the bundle isn't JSON: {e}") from None
  errors = bundle_errors(bundle, _member_texts(data.decode("utf-8")).get("manifest"))
  if errors:
    raise ValueError("; ".join(errors))
  return bundle


def _member_texts(text: str) -> dict:
  """Each member of the JSON object [text] (already read strictly) as its value's text exactly, by name."""
  decoder = json.JSONDecoder()

  def skip(i: int) -> int:
    while i < len(text) and text[i] in " \t\n\r":
      i += 1
    return i

  texts = {}
  i = skip(skip(0) + 1)  # past the {
  while i < len(text) and text[i] != "}":
    name, i = decoder.raw_decode(text, i)
    i = skip(skip(i) + 1)  # past the :
    _, end = decoder.raw_decode(text, i)
    texts[name] = text[i:end]
    i = skip(end)
    if text.startswith(",", i):
      i = skip(i + 1)
  return texts


def _bundle_manifest_text(manifest) -> str:
  """[manifest] as write_bundle writes it in a bundle: indented 2, one level in."""
  return json.dumps(manifest, ensure_ascii=False, indent=2).replace("\n", "\n  ")


def _json_bytes(value) -> bytes:
  """[value] as compact UTF-8 JSON (as a bundle's overlay is measured and unpacked), a lone surrogate (text a JSON
  escape can hold, but UTF-8 can't) written as its escape."""
  return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "backslashreplace")


def unpack_bundle(bundle: dict, root: Path) -> Path:
  """Writes [bundle] as the pack folder the comma's control API would, under [root]: its overlay compact, as the
  bundle's was measured."""
  folder = root / bundle["manifest"]["id"]
  folder.mkdir(parents=True)
  (folder / "manifest.json").write_bytes(_json_bytes(bundle["manifest"]))
  if not is_standalone(bundle["manifest"], bundle["overlay"]):
    (folder / "export.py").write_bytes(bundle["export"].encode("utf-8", "backslashreplace"))
  (folder / "overlay.json").write_bytes(_json_bytes(bundle["overlay"]))
  if "preview" in bundle:
    # The comma never writes it: this is the pack's folder as its author keeps it. Packed again, the preview is
    # written as standard base64, so a preview whose base64 had nonzero pad bits (which the catalog's reader takes)
    # comes back in its standard form; and the bundle is written as write_bundle writes one. Only a bundle
    # check-pack wrote comes back byte for byte, with the same digest.
    (folder / PREVIEW_FILE).write_bytes(base64.b64decode(bundle["preview"][len(PREVIEW_PREFIX) :]))
  return folder


def pack_bundle(folder: Path, manifest: dict) -> dict:
  """The bundle of the pack in [folder], or raises ValueError with what keeps it from being one."""
  runtime = manifest.get("runtime")
  entry = runtime.get("entry", "export.py") if isinstance(runtime, dict) else "export.py"
  if entry != "export.py":
    raise ValueError("a bundle carries export.py: runtime.entry must be export.py (or left out)")
  overlay_name = manifest.get("overlay", "overlay.json")
  if not isinstance(overlay_name, str) or Path(overlay_name).name != overlay_name:
    raise ValueError("manifest.overlay must name a file in the pack's folder")
  try:
    overlay = _strict_json((folder / overlay_name).read_bytes())
  except (OSError, ValueError) as e:
    raise ValueError(f"{overlay_name}: {e}") from None
  if is_standalone(manifest, overlay):
    # The app's alone: no export.py travels with it (the comma's control API refuses one without).
    bundle = {"cvpack": 1, "manifest": manifest, "overlay": overlay}
  else:
    try:
      source = (folder / "export.py").read_bytes().decode("utf-8")
    except (OSError, ValueError) as e:
      raise ValueError(f"export.py: {e}") from None
    bundle = {"cvpack": 1, "manifest": manifest, "export": source, "overlay": overlay}
  preview = folder / PREVIEW_FILE
  preview_errors: list[str] = []
  if preview.exists() or preview.is_symlink():
    try:
      png, preview_errors = read_preview_file(preview)
    except OSError as e:
      raise ValueError(f"{PREVIEW_FILE}: {e}") from None
    if png is not None:
      # Written as standard base64: a bundle unpacked and packed again has its preview's text in this form,
      # whatever form it came in (unpack_bundle).
      bundle["preview"] = preview_data_url(png)
  # The preview's problems last, as bundle_errors (and the catalog) says them.
  errors = bundle_errors(bundle) + preview_errors
  if errors:
    raise ValueError("; ".join(errors))
  size = len(bundle_bytes(bundle))
  if size > MAX_BUNDLE_BYTES:
    raise ValueError(_written_too_big(bundle, size))
  return bundle


def bundle_bytes(bundle: dict) -> bytes:
  """[bundle] as write_bundle writes it."""
  return (json.dumps(bundle, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _written_too_big(bundle: dict, size: int) -> str:
  if "preview" not in bundle:
    return too_big(size, None)
  return too_big(size, len(bundle_bytes({k: v for k, v in bundle.items() if k != "preview"})))


def write_bundle(bundle: dict, out: Path) -> str:
  data = bundle_bytes(bundle)
  if len(data) > MAX_BUNDLE_BYTES:
    raise ValueError(_written_too_big(bundle, len(data)))
  out.write_bytes(data)
  return hashlib.sha256(data).hexdigest()


class _SubMaster:
  def __init__(self, values, now):
    self.values = values
    self.recv_frame = dict.fromkeys(values, 10)
    self.recv_time = dict.fromkeys(values, now)
    self.logMonoTime = dict.fromkeys(values, 1000)
    self.valid = dict.fromkeys(values, True)
    self.alive = dict.fromkeys(values, True)
    self.updated = dict.fromkeys(values, True)

  def __getitem__(self, key):
    return self.values[key]


def _value(text: str):
  try:
    return json.loads(text)
  except ValueError:
    return text


def _namespace(value):
  """JSON as the attribute objects a cereal message reads like."""
  if isinstance(value, dict):
    return types.SimpleNamespace(**{key: _namespace(item) for key, item in value.items()})
  if isinstance(value, list):
    return [_namespace(item) for item in value]
  return value


def _ui_state(attrs: dict, services: dict):
  values = {
    "selfdriveState": types.SimpleNamespace(experimentalMode=False, enabled=True),
    "carState": types.SimpleNamespace(vEgo=10.0, aEgo=0.0),
    "deviceState": types.SimpleNamespace(started=True, deviceType="tizi"),
    "modelV2": types.SimpleNamespace(),
    "radarState": types.SimpleNamespace(
      leadOne=types.SimpleNamespace(status=True, dRel=32.5, vLead=8.0, radar=True),
      leadTwo=types.SimpleNamespace(status=False, dRel=0.0, vLead=0.0, radar=False),
    ),
    "pandaStates": [],
    "liveCalibration": types.SimpleNamespace(
      rpyCalib=[0, 0, 0], height=[1.22], calStatus=2, calPerc=100, wideFromDeviceEuler=[0, 0, 0]
    ),
  }
  values.update({name: _namespace(value) for name, value in services.items()})
  ui = types.SimpleNamespace(
    sm=_SubMaster(values, time.monotonic()),
    started=True,
    ignition=True,
    status="engaged",
    is_metric=True,
    started_frame=1,
    started_time=0.0,
    CP=types.SimpleNamespace(maxLateralAccel=3.0),
    params=None,
  )
  for name, value in attrs.items():
    setattr(ui, name, value)
  return ui


def main() -> int:
  # What a pack says is printed back (a layer's type, an image's name): text UTF-8 can't hold escaped, not a traceback.
  if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(errors="backslashreplace")
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("pack", type=Path, help="the pack's folder, or a .cvpack")
  parser.add_argument(
    "--attr", action="append", default=[], metavar="NAME=VALUE", help="a ui_state attribute (JSON value)"
  )
  parser.add_argument(
    "--module", action="append", default=[], help="a fork module the UI has loaded (imported if it can be, else empty)"
  )
  parser.add_argument(
    "--service",
    action="append",
    default=[],
    metavar="NAME=JSON",
    help="a service's latest message, as a JSON object (replaces the stand-in's)",
  )
  parser.add_argument("--frames", type=int, default=120)
  parser.add_argument(
    "--expect",
    choices=("active", "inactive", "disabled"),
    default="active",
    help="the state the pack should end in (disabled: a pack that tests the runtime's limits)",
  )
  parser.add_argument(
    "--bundle", type=Path, metavar="OUT.cvpack", help="write the pack as a .cvpack once the checks pass"
  )
  args = parser.parse_args()
  try:
    args.services = {}
    for item in args.service:
      name, _, text = item.partition("=")
      value = _strict_json(text.encode())
      if not isinstance(value, dict):
        raise ValueError("isn't a JSON object")
      args.services[name] = value
  except ValueError as e:
    print(f"FAIL --service {item}: {e}")
    return 1

  with tempfile.TemporaryDirectory() as unpacked:
    if args.pack.is_file():
      try:
        bundle = read_bundle(args.pack)
      except (OSError, ValueError) as e:
        print(f"FAIL {args.pack}: {e}")
        return 1
      pack = unpack_bundle(bundle, Path(unpacked))
      print(
        f"bundle   {args.pack}: {bundle['manifest']['id']} {bundle['manifest']['version']}, "
        f"sha256 {hashlib.sha256(args.pack.read_bytes()).hexdigest()}"
      )
    else:
      pack = args.pack
    return check(pack, args)


def name_warnings(overlay: dict, names: list, standalone: bool) -> list[str]:
  """What's worth saying of the names an overlay's expressions read: one the app doesn't give reads as null, a pack's
  block is always null in a standalone pack, and one of a higher level than the pack says is read as null by an app of
  that lower level."""
  level = overlay.get("level", 1)
  level = level if type(level) is int and level >= 1 else 1
  warnings = []
  # Without a reader's record of where each was read (a plain list), none is taken as outside a repeat.
  outside = frozenset(getattr(names, "outside", ()))
  for name in dict.fromkeys(names):
    since = NAME_LEVELS.get("item" if name.startswith("item.") else name, 1)
    if not name.startswith(("ext.", "item.")) and name not in KNOWN_NAMES:
      warnings.append(
        f"'{name}' isn't a name the app gives an expression (ext.*, {', '.join(sorted(KNOWN_NAMES))}): it reads as null"
      )
    elif since > level:
      warnings.append(
        f"'{name}' came with level {since}: give the pack \"level\": {since} (an app at level {level} reads it as null)"
      )
    elif (name == "index" or name == "item" or name.startswith("item.")) and name in outside:
      warnings.append(f"'{name}' is always null outside a repeated group (a group's \"repeat\" gives its copies it)")
    elif name.startswith("ext.") and standalone:
      warnings.append(f"'{name}' is always null in a standalone pack (nothing on the comma sends it)")
  return warnings


def check_overlay(pack: Path, manifest: dict):
  """The pack's overlay.json, checked as the app reads it (problems printed); None when the app would refuse it."""
  overlay_name = manifest.get("overlay", "overlay.json")
  if not isinstance(overlay_name, str) or Path(overlay_name).name != overlay_name:
    print("FAIL manifest.overlay must name a file in the pack's folder")
    return None
  path = pack / overlay_name
  try:
    data = path.read_bytes()
    if len(data) > OVERLAY_MAX_BYTES:
      raise ValueError(f"over {OVERLAY_MAX_BYTES // 1024} KiB")
    overlay = _strict_json(data)
  except (OSError, ValueError) as e:
    print(f"FAIL {overlay_name}: {e}")
    return None
  errors, warnings, names = overlay_problems(overlay, manifest.get("id"))
  for warning in warnings:
    print(f"warning  {overlay_name}: {warning}")
  for warning in name_warnings(overlay, names, is_standalone(manifest, overlay)):
    print(f"warning  {overlay_name}: {warning}")
  for error in errors:
    print(f"FAIL {overlay_name}: {error}")
  if errors:
    return None
  layers = overlay["layers"]
  total = _layers_in_all(layers)
  print(
    f"overlay  {len(layers)} layer(s)"
    + (f", {total} in all" if total != len(layers) else "")
    + f": {', '.join(layer['type'] for layer in layers)}"
    + (" (standalone)" if is_standalone(manifest, overlay) else "")
  )
  return overlay, names


def _layers_in_all(layers: list) -> int:
  return sum(1 + (_layers_in_all(layer["layers"]) if layer["type"] == "group" else 0) for layer in layers)


def check(pack: Path, args) -> int:
  manifest_path = pack / "manifest.json"
  try:
    manifest = _strict_json(manifest_path.read_bytes())
  except (OSError, ValueError) as e:
    print(f"FAIL {manifest_path}: {e}")
    return 1
  if not isinstance(manifest, dict):
    print(f"FAIL {manifest_path}: isn't a JSON object")
    return 1
  ext_id = manifest.get("id")
  if ext_id != pack.resolve().name:
    print(f"FAIL the folder must be named for the pack's id ({ext_id!r}), not {pack.resolve().name!r}")
    return 1
  checked = check_overlay(pack, manifest)
  if checked is None:
    return 1
  overlay, names = checked
  standalone = is_standalone(manifest, overlay)
  shape = pack_shape_errors(manifest, overlay, (pack / "export.py").exists())
  for error in shape:
    print(f"FAIL {error}")
  if shape:
    return 1
  fork_errors, fork_warnings = forks_problems(manifest, is_official(pack, manifest))
  for warning in fork_warnings:
    print(f"warning  {warning}")
  for error in fork_errors:
    print(f"FAIL {error}")
  if fork_errors:
    return 1
  if "forks" in manifest:
    print(
      f"forks    {', '.join(manifest['forks']) or 'none named'}"
      + (" (official)" if is_official(pack, manifest) else "")
    )
  if not check_preview(pack):
    return 1
  bundle = None
  if args.bundle is not None:
    # The bundle's rules first: no point running a pack that can't be installed.
    try:
      bundle = pack_bundle(pack, manifest)
    except ValueError as e:
      print(f"FAIL can't bundle: {e}")
      return 1

  if standalone:
    print("runtime  none: a standalone pack is drawn by the app alone, and nothing of it runs on the comma")
  elif check_runtime(pack, ext_id, args, names) != 0:
    return 1
  if bundle is not None:
    try:
      digest = write_bundle(bundle, args.bundle)
    except (OSError, ValueError) as e:
      print(f"FAIL can't write {args.bundle}: {e}")
      return 1
    print(f"bundle   {args.bundle} ({args.bundle.stat().st_size} bytes), sha256 {digest}")
  print("OK")
  return 0


def check_preview(pack: Path) -> bool:
  """Checks the folder's preview.png, if it has one, by the catalog's rules, and says what it found."""
  path = pack / PREVIEW_FILE
  if not (path.exists() or path.is_symlink()):
    print(f"preview  none ({PREVIEW_FILE} in the pack's folder adds one)")
    return True
  try:
    png, problems = read_preview_file(path)
  except OSError as e:
    print(f"FAIL {path}: {e}")
    return False
  for problem in problems:
    print(f"FAIL {problem}")
  if png is None:
    return False
  width, height = struct.unpack_from(">II", png, 16)
  print(f"preview  {PREVIEW_FILE}: {width} × {height}, {_kib(len(png))} KiB")
  return True


def load_pack_host() -> Any:
  """The overlay pack host (PACK_HOST), loaded as a module of its own: the comma's exporter carries the same text."""
  spec = importlib.util.spec_from_file_location("commaview_pack_host", PACK_HOST)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def check_runtime(pack: Path, ext_id: str, args, names: list) -> int:
  for name in args.module:
    try:
      importlib.import_module(name)
    except Exception:
      sys.modules[name] = types.ModuleType(name)
  sys.modules.setdefault("opendbc", types.ModuleType("opendbc"))
  car: Any = types.ModuleType("opendbc.car")
  car.ACCELERATION_DUE_TO_GRAVITY = 9.81
  sys.modules.setdefault("opendbc.car", car)

  with tempfile.TemporaryDirectory() as root:
    shutil.copytree(pack, Path(root) / ext_id)
    # A pack being checked isn't signed yet: this folder allows unreviewed packs, as a developer's comma would.
    (Path(root) / ".allow-unreviewed").write_text("")
    host_module = load_pack_host()
    # The packs' folder read as the comma's exporter reads its own, with PackHost in the exporter's place.
    try:
      extensions = host_module._Extensions(host_module.PackHost(), folder=root, off_file=str(Path(root) / "off"))
    except Exception as e:  # the exporter logs it and runs no pack: said so here
      print(f"FAIL the pack host couldn't read the pack: {type(e).__name__}: {e}")
      return 1
    if not extensions.packs:
      print("FAIL the pack host found no pack (is manifest.json's id the folder's name?)")
      return 1
    # Loaded as at the first offroad frame (a pack never loads onroad), then run for the frames asked for.
    extensions.load_pending()
    ui = _ui_state({name: _value(value) for name, value in (item.split("=", 1) for item in args.attr)}, args.services)
    durations = []
    payload = None
    for _ in range(args.frames):
      ui.sm.recv_time = {key: time.monotonic() for key in ui.sm.recv_time}
      started = time.monotonic_ns()
      extensions.observe(ui)
      payload = extensions.payloads(ui).get(ext_id, payload)
      durations.append(time.monotonic_ns() - started)
    status = extensions.statuses()[ext_id]
    durations.sort()
    print(f"state    {status['state']}" + (f" ({status['reason']})" if status.get("reason") else ""))
    print(f"payload  {json.dumps(payload)}")
    if durations:
      print(
        f"frame    median {durations[len(durations) // 2] / 1e6:.3f} ms, worst {durations[-1] / 1e6:.3f} ms "
        f"(budget {host_module.COMMAVIEW_EXTENSION_FRAME_BUDGET_NS / 1e6:.0f} ms)"
      )
    if isinstance(payload, dict):
      for name in sorted({name.split(".")[1] for name in names if name.startswith("ext.")} - set(payload)):
        print(f"warning  overlay.json reads ext.{name}, which the last payload didn't have: it reads as null")
    if status["state"] != args.expect:
      print(f"FAIL the pack is {status['state']}, not {args.expect}")
      return 1
    if args.expect == "active" and payload is None:
      print("FAIL the pack never sent a payload")
      return 1
  return 0


if __name__ == "__main__":
  sys.exit(main())
