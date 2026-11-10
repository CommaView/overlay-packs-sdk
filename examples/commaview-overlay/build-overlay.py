#!/usr/bin/env python3
"""
Writes overlay.json for the commaview-overlay pack: CommaView's own onroad design (its road, instrument cluster, lead
reticle, status pill and signals) redrawn with an overlay pack's parts, offered in the app as a design of its own
("design"). The numbers are the CommaView design's (the app's own layout, chrome, palette and road
geometry), in its layout unit u = 1/540 of the road view's height,
turned into fractions of each group's box.

  python3 examples/commaview-overlay/build-overlay.py    (rewrites overlay.json beside it)
"""

import base64
import json
import math
import struct
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The cluster's box: CommaView's cluster, 401 u wide and 140.5 u tall, 16 u above the road view's bottom.
CW, CH = 401.0, 140.5
U = 1 / 540.0  # a u in fractions of the road view's height

TEAL, TEAL_BRIGHT, AMBER, QUIET, NEUTRAL = "#29BFAF", "#5FE0D2", "#F2A93B", "#98A4B3", "#A9B3BF"
CAUTION, DANGER, SIGNAL, TEXT, TEXT_SOFT, SCRIM = "#F5BE66", "#FF6B6B", "#3FB950", "#F4F7FA", "#E6ECF2", "#090B0EB8"


def cx(x_u):
  """x in the cluster's box, from u right of its centre."""
  return round(0.5 + x_u / CW, 4)


def cy(y_u):
  """y in the cluster's box, from u below its top."""
  return round(y_u / CH, 4)


def ch(length_u):
  """A length (radius, stroke, text size) in the cluster's box: a fraction of its height."""
  return round(length_u / CH, 4)


def baseline(base_u, size_u):
  """A text layer anchored at its bottom whose baseline is base_u: the bottom is 0.22 em below it."""
  return cy(base_u + 0.22 * size_u)


STATUS_COLOUR = "=status == 'override' ? '#F2A93B' : (engaged || status == 'long_only') ? '#5FE0D2' : '#A9B3BF'"


def by_status(engaged, lat_only, override, other):
  """A colour by engagement: speed-only reads as engaged, disengaged as `other`."""
  return (
    f"=pick(status, 'engaged', '{engaged}', 'long_only', '{engaged}', 'lat_only', '{lat_only}', "
    f"'override', '{override}', '{other}')"
  )


# Where the path stops: 1.5 m short of the lead, as CommaView's does.
PATH_END = "lead.present ? lead.distance - 1.5 : 250"


def road_layers():
  # The path: 1.8 m wide, its colour by engagement near to far (CommaViewPalette.pathStyle at full throttle blend),
  # fading out over its far 38%.
  path = {
    "type": "ribbon",
    "when": "status != 'unknown'",
    "along": "path",
    "from": -0.9,
    "to": 0.9,
    "end": PATH_END,
    "gradient": [
      [0, by_status("#29BFAF75", "#98A4B32E", "#F2A93B4D", "#98A4B329")],
      [1, by_status("#29BFAF40", "#98A4B31A", "#F2A93B29", "#98A4B314")],
    ],
    "fade": 0.38,
  }
  # Its rails: lit while openpilot steers, quiet and dashed while it drives the speed alone.
  rails = []
  for side in (-1, 1):
    inner, outer = sorted((side * 0.86, side * 0.94))
    rail = {"type": "ribbon", "along": "path", "from": inner, "to": outer, "end": PATH_END, "fade": 0.38}
    rails.append(
      rail | {"when": "engaged || status == 'override'", "color": "=status == 'override' ? '#F2A93BF2' : '#5FE0D2F2'"}
    )
    rails.append(rail | {"when": "status == 'long_only'", "color": "#98A4B3B3", "dash": [1.2, 1.2]})
  # Its flow: bands fixed to the road that pass under the car while openpilot drives the speed.
  flow = {
    "type": "ribbon",
    "when": "engaged && longControl",
    "along": "path",
    "from": -0.9,
    "to": 0.9,
    "end": PATH_END,
    "color": "#5FE0D226",
    "dash": [1.2, 4.8],
    "fade": 0.38,
  }
  # Lane lines as wide and as strong as the model is sure of them; road edges dashed in the curb's red.
  lanes = [
    {
      "type": "ribbon",
      "along": "laneLine",
      "index": i,
      "from": f"-0.12 * lane.{i}.prob",
      "to": f"0.12 * lane.{i}.prob",
      "color": "#E6F4F2D1",
      "opacity": f"clamp(lane.{i}.prob, 0, 1)",
      "fade": 0.3,
    }
    for i in range(4)
  ]
  edges = [
    {
      "type": "ribbon",
      "along": "roadEdge",
      "index": i,
      "from": -0.06,
      "to": 0.06,
      "color": "#FF7A6BCC",
      "dash": [1.5, 1.5],
      "opacity": f"clamp(1 - edge.{i}.std, 0, 1)",
      "fade": 0.3,
    }
    for i in range(2)
  ]
  return lanes + edges + [path] + rails + [flow]


THREAT = (
  "=lead.vRel < -0.3 && lead.distance / -lead.vRel < 2.5 ? '#FF6B6B' : "
  "(lead.vRel < -0.3 && lead.distance / -lead.vRel < 5) || lead.gap < 0.9 ? '#F5BE66' : '#5FE0D2'"
)


def bracket(left, top):
  """An L-shaped corner of the reticle, its corner at (left ? 0 : 1, top ? 0 : 1) of the box."""
  arm, w = 0.3, 0.06
  x0, x1 = (0.0, arm) if left else (1.0, 1.0 - arm)
  y0, y1 = (0.0, arm) if top else (1.0, 1.0 - arm)
  xi = w if left else 1.0 - w
  yi = w if top else 1.0 - w
  return {"type": "polygon", "points": [[x0, y0], [x1, y0], [x1, yi], [xi, yi], [xi, y1], [x0, y1]], "fill": THREAT}


def lead_layers():
  reticle = {
    "type": "group",
    "when": "lead.present && status != 'unknown'",
    # A car's outline, 1.9 m by 1.45 m where the lead is, padded: CommaView's lock-on brackets.
    "pin": {"lead": True, "size": [2.5, 1.9]},
    "y": 0.004,
    "layers": [
      {
        "type": "rect",
        "x": 0,
        "y": 0,
        "width": 1,
        "height": 1,
        "radius": 0.04,
        "fill": "=lead.vRel < -0.3 && lead.distance / -lead.vRel < 2.5 ? '#FF6B6B24' : '#5FE0D20F'",
      },
      bracket(True, True),
      bracket(False, True),
      bracket(True, False),
      bracket(False, False),
    ],
  }
  readout = {
    "type": "group",
    "when": "lead.present && status != 'unknown'",
    "pin": {"lead": True},
    "anchor": "top",
    "y": 0.012,
    "width": 0.3,
    "height": 0.055,
    "layers": [
      {
        "type": "text",
        "x": 0.5,
        "y": 0.5,
        "anchor": "center",
        "size": 0.62,
        "font": "condensed",
        "text": "{metric ? lead.distance : lead.distance * 3.28084|0} {metric ? 'm' : 'ft'}"
        "{lead.gap != null && lead.gap < 10 ? ' · ' + round(lead.gap, 1) + ' s' : ''}",
        "color": "=lead.vRel < -0.3 && lead.distance / -lead.vRel < 2.5 ? '#FF6B6B' : "
        "(lead.vRel < -0.3 && lead.distance / -lead.vRel < 5) || lead.gap < 0.9 ? '#F5BE66' : '#E6ECF2'",
        "background": "#090B0EB8",
        "padding": [0.5, 0.25],
        "radius": 0.4,
      },
    ],
  }
  return [reticle, readout]


def gauge_layers():
  center_y = 94.5
  layers = [
    {
      "type": "arc",
      "cx": 0.5,
      "cy": cy(center_y),
      "r": ch(88),
      "start": 180,
      "sweep": 180,
      "stroke": "#FFFFFF2E",
      "strokeWidth": ch(5),
    }
  ]
  for i in range(11):
    angle = math.radians(180 + 18 * i)
    major = i in (0, 5, 10)
    inner, outer = (71 if major else 75), 81
    layers.append(
      {
        "type": "line",
        "x1": cx(inner * math.cos(angle)),
        "y1": cy(center_y + inner * math.sin(angle)),
        "x2": cx(outer * math.cos(angle)),
        "y2": cy(center_y + outer * math.sin(angle)),
        "stroke": "#FFFFFF78",
        "strokeWidth": ch(1.8 if major else 1.3),
      }
    )
  layers.append(
    {
      "type": "arc",
      "when": "speed != null",
      "cx": 0.5,
      "cy": cy(center_y),
      "r": ch(88),
      "start": 180,
      # Eased toward the speed, as CommaView's gauge moves, and glowing in its colour.
      "sweep": "180 * clamp(smooth(speed, 250) / (metric ? 140 : 90), 0.01, 1)",
      "stroke": STATUS_COLOUR,
      "strokeWidth": ch(5),
      "glow": {"color": STATUS_COLOUR, "radius": ch(9)},
    }
  )
  layers.append(
    {
      "type": "text",
      "text": "{speed|0}",
      "x": 0.5,
      "y": baseline(82.5, 58),
      "anchor": "bottom",
      "size": ch(58),
      "font": "condensed",
      "color": TEXT,
      "outline": "#00000096",
    }
  )
  layers.append(
    {
      "type": "text",
      "text": "{speedUnit}",
      "tracking": 0.12,
      "x": 0.5,
      "y": baseline(101.5, 12),
      "anchor": "bottom",
      "size": ch(12),
      "color": TEXT_SOFT,
    }
  )
  return {"type": "group", "layers": layers}


def sign_layers():
  # The sign of where the car is (signStyle): the US's and Canada's white rectangle, else the round ("Vienna") one.
  x, y, r = cx(154.38), cy(62.2), 27.88
  limit = "{speedLimit == null ? '---' : '' + speedLimit}"
  rectangle = "signStyle == 'mutcd' || signStyle == 'canada'"
  w, h = 0.78 * r, 1.0 * r
  plate = {
    "type": "group",
    "when": rectangle,
    "x": round(x - w / CW, 4),
    "y": cy(62.2 - h),
    "width": round(2 * w / CW, 4),
    "height": ch(2 * h),
    "layers": [
      {
        "type": "rect",
        "when": "speedLimitOver",
        "x": -0.12,
        "y": -0.09,
        "width": 1.24,
        "height": 1.18,
        "radius": 0.16,
        "fill": "#F5BE6659",
        "stroke": CAUTION,
        "strokeWidth": 0.03,
      },
      {
        "type": "rect",
        "x": 0,
        "y": 0,
        "width": 1,
        "height": 1,
        "radius": 0.1,
        "fill": "#FFFFFF",
        "stroke": "#0B0D10",
        "strokeWidth": 0.035,
      },
      {
        "type": "text",
        "text": "{signStyle == 'canada' ? 'MAXIMUM' : 'SPEED LIMIT'}",
        "x": 0.5,
        "y": 0.08,
        "anchor": "top",
        "size": 0.14,
        "weight": "bold",
        "color": "#0B0D10",
        "wrap": {"width": 0.8, "lines": 2},
      },
      {
        "type": "text",
        "text": limit,
        "x": 0.5,
        "y": 0.95,
        "anchor": "bottom",
        "size": 0.46,
        "font": "condensed",
        "maxWidth": 0.86,
        "color": "=speedLimit == null ? '#919B95' : '#0B0D10'",
      },
    ],
  }
  return {
    "type": "group",
    "layers": [
      {
        "type": "circle",
        "when": f"speedLimitOver && !({rectangle})",
        "cx": x,
        "cy": y,
        "r": ch(r + 4.1),
        "fill": "#F5BE6659",
        "stroke": CAUTION,
        "strokeWidth": ch(2.05),
      },
      {"type": "circle", "when": f"!({rectangle})", "cx": x, "cy": y, "r": ch(r), "fill": "#FFFFFF"},
      {
        "type": "circle",
        "when": f"!({rectangle})",
        "cx": x,
        "cy": y,
        "r": ch(r * 0.9),
        "stroke": "#E52A2A",
        "strokeWidth": ch(r * 0.2),
      },
      plate,
      {
        "type": "text",
        "when": f"!({rectangle})",
        "text": limit,
        "x": x,
        "y": baseline(62.2 + 0.36 * 0.9 * r, 0.9 * r),
        "anchor": "bottom",
        "size": ch(0.9 * r),
        "font": "condensed",
        "color": "=speedLimit == null ? '#919B95' : '#0B0D10'",
      },
    ],
  }


def crescent(points, depth_u, bow_u, top):
  """Points along CommaView's torque surface: y(x) = -bow (x² + x⁴/4), thickness depth (1 - x²)^0.65."""
  out = []
  for x in points:
    y = -bow_u * (x * x + 0.25 * x**4)
    if not top:
      y += depth_u * (1 - x * x) ** 0.65
    out.append((x, y))
  return out


def torque_layers():
  base_y, half = 131.5, 132.0
  # 15 points a side: a polygon has at most 32.
  xs = [i / 7 - 1 for i in range(15)]
  outline = crescent(xs, 4.0, 6.0, True) + crescent(list(reversed(xs)), 4.0, 6.0, False)
  track = {"type": "polygon", "points": [[cx(x * half), cy(base_y + y)] for x, y in outline], "fill": "#273E46F5"}
  # The active part: from the centre to the effort, on its side, every point worked out from steer.torque.
  fractions = [0, 0.25, 0.5, 0.75, 1.0]
  top, bottom = [], []
  for f in fractions:
    t = f"(steer.torque * {f})"
    x = f"0.5 + {t} * {round(half / CW, 4)}"
    top.append([x, f"{cy(base_y)} - {round(6 / CH, 5)} * ({t} * {t})"])
    bottom.append([x, f"{cy(base_y)} - {round(6 / CH, 5)} * ({t} * {t}) + {round(6 / CH, 5)} * (1 - {t} * {t})"])
  active = {
    "type": "polygon",
    "when": "steer.torque != null && abs(steer.torque) > 0.02",
    "points": top + list(reversed(bottom)),
    "fill": "=abs(steer.torque) > 0.9 ? '#F26A5F' : abs(steer.torque) > 0.75 ? '#D9A94F' : '#29BFAF'",
  }
  tick = {
    "type": "line",
    "x1": 0.5,
    "y1": cy(base_y - 2.8),
    "x2": 0.5,
    "y2": cy(base_y + 2.8),
    "stroke": "#4A767FD1",
    "strokeWidth": ch(0.8),
  }
  return {"type": "group", "when": "steer.torque != null && engaged", "layers": [track, active, tick]}


def chevron(x_u, direction):
  """One turn-signal chevron, pointing out (direction -1 left, 1 right), as a filled ">"."""
  hx, hy, w = 8.8, 12.0, 4.4
  tip = direction * 0.45 * hx
  back = -direction * 0.45 * hx
  pts = [
    (back, -hy),
    (back + direction * w, -hy),
    (tip + direction * w, 0),
    (back + direction * w, hy),
    (back, hy),
    (tip, 0),
  ]
  return [[cx(x_u + px), cy(62.2 + py)] for px, py in pts]


def signal_layers(side):
  direction = -1 if side == "left" else 1
  x = -228.5 if side == "left" else 228.26
  colour = f"=blindspot.{side} ? '#F5BE66' : '#3FB950'"
  blink = "clock % 750 < 300 ? 1 : 0.45"
  return {
    "type": "group",
    "when": f"blinker.{side} || blindspot.{side}",
    "layers": [
      {"type": "circle", "when": f"blinker.{side}", "cx": cx(x), "cy": cy(62.2), "r": ch(24.8), "fill": "#090B0E9C"},
      {
        "type": "polygon",
        "when": f"blinker.{side}",
        "points": chevron(x - direction * 7.2, direction),
        "fill": colour,
        "opacity": blink,
      },
      {
        "type": "polygon",
        "when": f"blinker.{side}",
        "points": chevron(x + direction * 7.2, direction),
        "fill": colour,
        "opacity": blink,
      },
      # The blind spot, above: a car in the caution colour (the pack's white car, tinted); with the blinker on, a tag.
      {
        "type": "rect",
        "when": f"blindspot.{side} && !blinker.{side}",
        "x": cx(x - 22),
        "y": cy(62.2 - 68),
        "width": round(44 / CW, 4),
        "height": ch(40),
        "radius": ch(8.8),
        "fill": SCRIM,
        "stroke": "#F5BE66B3",
        "strokeWidth": ch(1.5),
      },
      {
        "type": "image",
        "when": f"blindspot.{side} && !blinker.{side}",
        "image": "car",
        "x": cx(x - 14),
        "y": cy(62.2 - 64),
        "width": round(28 / CW, 4),
        "height": ch(32),
        "tint": CAUTION,
      },
      {
        "type": "text",
        "when": f"blindspot.{side} && blinker.{side}",
        "text": "BLIND SPOT",
        "x": cx(x),
        "y": cy(62.2 - 24.8 - 6 - 10),
        "anchor": "center",
        "size": ch(11),
        "color": CAUTION,
        "background": SCRIM,
      },
    ],
  }


# The control line, by state.
CONTROL = (
  "{pick(status, 'engaged', longControl ? 'STEERING ACTIVE · SPEED ACTIVE' : 'STEERING ACTIVE', "
  "'lat_only', 'STEERING ACTIVE · SPEED MANUAL', 'long_only', 'STEERING MANUAL · SPEED ACTIVE', "
  "'override', 'DRIVER OVERRIDE', 'MANUAL CONTROL')}"
)


def cluster():
  active = "setSpeed != null && (engaged || status == 'long_only')"
  layers = [
    # The glass: its body darkening downward with a rim and a soft shadow, and a reflection across it.
    {
      "type": "rect",
      "x": 0,
      "y": 0,
      "width": 1,
      "height": 1,
      "radius": 0.24,
      "fill": {"linear": [0, 0, 0, 1], "stops": [[0, "#102B30B4"], [1, "#070C12E7"]]},
      "stroke": {"linear": [0, 0, 0, 1], "stops": [[0, "#65B4B05A"], [1, "#29BFAF2E"]]},
      "strokeWidth": ch(1),
      "glow": {"color": "#00000066", "radius": ch(14), "dy": ch(4)},
    },
    {
      "type": "rect",
      "x": 0,
      "y": 0,
      "width": 1,
      "height": 1,
      "radius": 0.24,
      "fill": {"linear": [0, 0, 1, 1], "stops": [[0, "#6FBBB722"], [0.4, "#6FBBB700"], [1, "#29BFAF0A"]]},
    },
    {
      "type": "line",
      "x1": cx(-112.5),
      "y1": 0.22,
      "x2": cx(-112.5),
      "y2": 0.78,
      "stroke": "#FFFFFF24",
      "strokeWidth": ch(1),
    },
    {
      "type": "line",
      "x1": cx(112.5),
      "y1": 0.22,
      "x2": cx(112.5),
      "y2": 0.78,
      "stroke": "#FFFFFF24",
      "strokeWidth": ch(1),
    },
    # MAX: the set speed, teal while openpilot holds it.
    {
      "type": "text",
      "text": "MAX",
      "x": cx(-154.5),
      "y": baseline(48, 15),
      "anchor": "bottom",
      "size": ch(15),
      "font": "condensed",
      "tracking": 0.08,
      "color": f"={active} ? '#5FE0D2' : '#98A4B3'",
    },
    {
      "type": "text",
      "text": "{setSpeed == null ? '---' : '' + setSpeed}",
      "x": cx(-154.5),
      "y": baseline(82.5, 38),
      "anchor": "bottom",
      "size": ch(38),
      "font": "condensed",
      "color": f"={active} ? '#F4F7FA' : '#98A4B3'",
    },
    gauge_layers(),
    {
      "type": "text",
      "text": CONTROL,
      "x": 0.5,
      "y": baseline(114.5, 8),
      "anchor": "bottom",
      "size": ch(8),
      "tracking": 0.14,
      "maxWidth": round(200 / CW, 4),
      "color": TEXT_SOFT,
    },
    sign_layers(),
    torque_layers(),
    signal_layers("left"),
    signal_layers("right"),
  ]
  # Its box: 401 x 140.5 u, centred 16 u above the road view's bottom, the same shape in any window.
  return {
    "type": "group",
    "when": "status != 'unknown' && speed != null",
    "x": 0.05,
    "y": round(1 - 16 * U - CH * U, 4),
    "width": 0.9,
    "height": round(CH * U, 4),
    "aspect": round(CW / CH, 4),
    "anchor": "bottom",
    "layers": layers,
  }


STATUS_WORD = (
  "{pick(status, 'engaged', 'ENGAGED', 'lat_only', 'STEERING ONLY', 'long_only', 'SPEED ONLY', "
  "'override', 'OVERRIDE', 'NOT ENGAGED')}"
)
# The pill's glass: the scrim with the status colour's tint over it, as one colour.
PILL = by_status("#1B3B3AE6", "#1B3B3AE6", "#3A2E1CE6", "#22262BE6")
# For a moment after the state changes, the dot glows: bright at the change, gone 0.9 s later.
CHANGED = "ease(clamp(since(status) / 900, 0, 1))"


def atom(cx_, cy_, rx, ry):
  """The experimental glyph: three ellipses, 60 degrees apart, each two SVG arcs round (cx_, cy_)."""
  parts = []
  for degrees in (0, 60, 120):
    a = math.radians(degrees)
    x0, y0 = cx_ - rx * math.cos(a), cy_ - rx * math.sin(a)
    x1, y1 = cx_ + rx * math.cos(a), cy_ + rx * math.sin(a)
    parts.append(
      f"M{x0:.4f} {y0:.4f} A{rx} {ry} {degrees} 1 0 {x1:.4f} {y1:.4f} A{rx} {ry} {degrees} 1 0 {x0:.4f} {y0:.4f} Z"
    )
  return " ".join(parts)


def status():
  # Bottom left (driver's side, left-hand drive), level with the cluster, 16 u in from the left: a box 200 u wide holding
  # the experimental chip (28 u tall) 8 u above the pill (32 u), so 68 u tall. Lengths are fractions of its height.
  h = 68.0

  def v(u):
    return round(u / h, 4)

  pill_top = 36.0
  size = 14.0
  pill = [
    # The word on its pill: the text's own background, as wide as the word (measured where it's drawn), its leading
    # spaces leaving room for the dot.
    {
      "type": "text",
      "text": "    " + STATUS_WORD,
      "x": round(0.08 + 0.7 * size / 200, 4),
      "y": v(pill_top + 16 + 0.36 * size + 0.22 * size),
      "anchor": "bottomLeft",
      "size": v(size),
      "weight": "semibold",
      "tracking": 0.08,
      "color": STATUS_COLOUR,
      "background": PILL,
      "padding": [0.7, 0.62],
      "radius": 1.2,
    },
    {
      "type": "circle",
      "when": "status == 'engaged'",
      "cx": 0.15,
      "cy": v(pill_top + 16),
      "r": v(6),
      "fill": STATUS_COLOUR,
      "glow": {"color": f"=mixColor('#5FE0D2FF', '#5FE0D200', {CHANGED})", "radius": v(10)},
    },
    {
      "type": "circle",
      "when": "status != 'engaged'",
      "cx": 0.15,
      "cy": v(pill_top + 16),
      "r": v(6),
      "stroke": STATUS_COLOUR,
      "strokeWidth": v(2.04),
      "glow": {"color": f"=mixColor('#A9B3BFFF', '#A9B3BF00', {CHANGED})", "radius": v(10)},
    },
    {
      "type": "arc",
      "when": "status == 'lat_only' || status == 'long_only'",
      "cx": 0.15,
      "cy": v(pill_top + 16),
      "r": v(6),
      "start": "status == 'lat_only' ? 90 : 0",
      "sweep": 180,
      "fill": STATUS_COLOUR,
    },
    # The experimental chip: its atom glyph, three ellipses as one path, and its word on a pill.
    {
      "type": "group",
      "when": "experimental",
      "x": 0,
      "y": 0,
      "width": 1,
      "height": v(28),
      "layers": [
        {
          "type": "text",
          "text": "      EXPERIMENTAL",
          "x": round(0.08 + 0.7 * 0.45 * 28 / 200, 4),
          "y": 0.73,
          "anchor": "bottomLeft",
          "size": 0.45,
          "weight": "semibold",
          "tracking": 0.1,
          "color": TEXT_SOFT,
          "background": SCRIM,
          "padding": [0.7, 0.55],
          "radius": 1.2,
        },
        # A path's numbers are fractions of its box: a square group 20 u a side, so the ellipses keep their shape.
        {
          "type": "group",
          "x": 0.11,
          "y": round(4 / 28, 4),
          "width": round(20 / 200, 4),
          "height": round(20 / 28, 4),
          "layers": [
            {"type": "path", "d": atom(0.5, 0.5, 0.48, 0.18), "stroke": "#FFB25B", "strokeWidth": 0.06},
            {"type": "circle", "cx": 0.5, "cy": 0.5, "r": 0.1, "fill": "#FFB25B"},
          ],
        },
      ],
    },
  ]
  top = 1 - 16 * U - CH * U + 46.2 * U - pill_top * U
  return {
    "type": "group",
    "when": "status != 'unknown'",
    "x": 0,
    "y": round(top, 4),
    "width": 0.5,
    "height": round(h * U, 4),
    "aspect": round(200 / h, 4),
    "anchor": "topLeft",
    "layers": pill,
  }


def chrome():
  header = {
    "type": "rect",
    "x": 0,
    "y": 0,
    "width": 1,
    "height": 0.122,
    "fill": {"linear": [0, 0, 0, 1], "stops": [[0, "#0000006E"], [1, "#00000000"]]},
  }
  # The lockup: two slashes in a teal gradient and the word, 0.68 opaque, top left.
  brand = {
    "type": "group",
    "x": 0.012,
    "y": round(16 * U, 4),
    "width": 0.4,
    "height": 0.045,
    "aspect": 7.3,
    "anchor": "topLeft",
    "opacity": 0.68,
    "layers": [
      {
        "type": "polygon",
        "points": [[0.0, 1], [0.0625, 0], [0.125, 0], [0.0625, 1]],
        "fill": {"linear": [0, 1, 1, 0], "stops": [[0, "#1C9A8D"], [1, "#5CE6D6"]]},
      },
      {
        "type": "polygon",
        "points": [[0.091, 1], [0.1536, 0], [0.2163, 0], [0.1536, 1]],
        "fill": {"linear": [0, 1, 1, 0], "stops": [[0, "#1C9A8D"], [1, "#5CE6D6"]]},
      },
      {
        "type": "text",
        "text": "CommaView",
        "x": 0.255,
        "y": 0.98,
        "anchor": "bottomLeft",
        "size": 1.0,
        "color": TEXT,
        "outline": "#00000073",
      },
    ],
  }
  attention = {
    "type": "rect",
    "when": "engaged && dm.awareness != null && dm.awareness > 0",
    "x": "0.5 - dm.awareness / 2",
    "y": 0,
    "width": "dm.awareness",
    "height": "dm.level > 0 ? 0.009 : 0.0056",
    "fill": "=dm.level >= 2 ? '#FF6B6B' : dm.level == 1 ? '#F5BE66' : '#5FE0D28C'",
  }
  return [header, brand, attention]


def alert():
  # Over another design only: as a design of its own, the app draws its alerts over the pack, always.
  tone = "alert.severity == 'critical' ? '#FF6B6B' : alert.severity == 'warning' ? '#F5BE66' : '#E6ECF2'"
  shown = "!design && alert && alert.size != 'full'"
  return {
    "type": "group",
    "when": shown,
    "x": 0.2,
    "y": round(16 * U + 42 * U, 4),
    "width": 0.6,
    "height": 0.16,
    "aspect": 4.2,
    "anchor": "top",
    # Faded in over a quarter second when its words change (since() remembers them from frame to frame).
    "opacity": "ease(clamp(since(alert.text1) / 250, 0, 1))",
    "layers": [
      {
        "type": "rect",
        "x": 0,
        "y": 0,
        "width": 1,
        "height": 1,
        "radius": 0.16,
        "fill": "#090B0EE6",
        "glow": {"color": "#00000080", "radius": 0.12, "dy": 0.04},
      },
      {
        "type": "rect",
        "x": 0,
        "y": 0,
        "width": 1,
        "height": 1,
        "radius": 0.16,
        "fill": f"=({tone}) + (alert.severity == 'critical' ? '3D' : '24')",
        "stroke": f"=({tone}) + 'BF'",
        "strokeWidth": 0.02,
      },
      {
        "type": "text",
        "text": "{alert.text1}",
        "x": 0.06,
        "y": 0.47,
        "anchor": "bottomLeft",
        "size": 0.3,
        "weight": "semibold",
        "maxWidth": 0.88,
        "color": "#FFFFFF",
      },
      {
        "type": "text",
        "text": "{alert.text2}",
        "x": 0.06,
        "y": 0.56,
        "anchor": "topLeft",
        "size": 0.2,
        "wrap": {"width": 0.88, "lines": 2},
        "color": "#FFFFFFE0",
      },
    ],
  }


def blind_spot_glows():
  """A car in the blind spot with the blinker on that side: that edge of the view glows in the caution colour."""
  return [
    {
      "type": "edgeGlow",
      "when": f"blindspot.{side} && blinker.{side}",
      "color": CAUTION,
      "reach": 0.09,
      "sides": [side],
      "opacity": "0.7 + 0.3 * sin(clock % 1000 * 0.36)",
    }
    for side in ("left", "right")
  ]


def car_png(size=48):
  """A white car seen from behind, on transparent, 4x supersampled: tinted to any colour by the image layer."""
  ss = 4
  n = size * ss

  def inside(x, y):
    u, v = x / n, y / n

    def rounded(x0, y0, x1, y1, r):
      if not (x0 <= u <= x1 and y0 <= v <= y1):
        return False
      dx = max(x0 + r - u, 0, u - (x1 - r))
      dy = max(y0 + r - v, 0, v - (y1 - r))
      return dx * dx + dy * dy <= r * r

    body = rounded(0.12, 0.38, 0.88, 0.78, 0.1)
    cabin = rounded(0.22, 0.16, 0.78, 0.46, 0.08)
    window = rounded(0.29, 0.22, 0.71, 0.42, 0.04)
    wheels = rounded(0.16, 0.72, 0.32, 0.9, 0.04) or rounded(0.68, 0.72, 0.84, 0.9, 0.04)
    lights = rounded(0.17, 0.5, 0.3, 0.6, 0.03) or rounded(0.7, 0.5, 0.83, 0.6, 0.03)
    return (body or cabin or wheels) and not window and not lights

  rows = []
  for py in range(size):
    row = bytearray([0])
    for px in range(size):
      hits = sum(inside(px * ss + i + 0.5, py * ss + j + 0.5) for i in range(ss) for j in range(ss))
      row += bytes([255, 255, 255, round(255 * hits / (ss * ss))])
    rows.append(bytes(row))

  def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

  png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
  png += chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b"")
  return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def overlay():
  """The pack's overlay.json, as a dict."""
  return {
    "format": 1,
    "id": "commaview-overlay",
    "standalone": True,
    # Offered in the app's Display settings as a design of its own: the road camera and this pack, the app's alerts on
    # top. Added over another design, it draws there too (`design` is false then).
    "design": {"name": "CommaView, as a pack"},
    "images": {"car": car_png()},
    "layers": road_layers() + chrome() + lead_layers() + [cluster(), status(), alert()] + blind_spot_glows(),
  }


def main():
  (HERE / "overlay.json").write_text(json.dumps(overlay(), indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
  main()
