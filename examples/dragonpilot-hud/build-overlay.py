#!/usr/bin/env python3
"""
Writes overlay.json for the dragonpilot-hud pack: dragonpilot's comma 3X onroad additions, with the numbers of its
own UI (dragonpilot 0.11.1: selfdrive/ui/onroad/model_renderer.py, augmented_road_view.py, hud_renderer.py and the
comma four's mici/onroad/torque_bar.py at scale 4), which draws them in pixels of its 2100 x 1020 road view (its content rect), turned
into fractions of each layer's box. The rainbow's sixteen stops are written here, not by hand. Overlay level 2: every
layer draws on the app's comma 3X design alone (`screen`), the screen dragonpilot adds them to.

  python3 examples/dragonpilot-hud/build-overlay.py    (rewrites overlay.json beside it)
"""

import json
import math
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _export_constant(name: str) -> int:
  """A number export.py defines, read from it so the two can't disagree."""
  match = re.search(rf"^{name} = (\d+)", (HERE / "export.py").read_text(), re.M)
  assert match, f"export.py defines no {name}"
  return int(match.group(1))


# dragonpilot's comma 3X road view in pixels: its content rect, the 2160 x 1080 screen less the border band
# (UI_BORDER_SIZE) each side, where it draws the model and the HUD, as the app's road view is inside its band.
W, H = 2100.0, 1020.0
UI_BORDER_SIZE = 30.0
TRACK_SLOTS = _export_constant("TRACK_SLOTS")  # how many points export.py sends: the repeat's max
STOPS = 16  # the most a pathFill's gradient takes (level 2)
RAINBOW_TOP = 0.2  # above where the path's far end reaches on the road view, a crest's or a phone's included
COMMA_3X = "screen == 'comma3x'"
# dragonpilot's "hide HUD above a speed" (dp_ui_hide_hud_speed_kph): its speed, set speed, experimental button,
# torque bar and driver monitoring icon go above it.
HUD_FAST = "ext.hideHudSpeed > 0 && car.vEgo > ext.hideHudSpeed"
HUD_HIDDEN = f"{COMMA_3X} && {HUD_FAST}"
# Its phase: max(0.01, vEgo) / fps / 20 a UI frame, so max(0.01, vEgo) / 20 a second, from the drive's start.
PHASE = "(integrate(max(0.01, car.vEgo)) / 20)"


def r4(value: float) -> float:
  return round(value, 4)


def rainbow() -> dict:
  """
  dragonpilot's rainbow path: 20 hues over the road view's height, from its bottom, three times round the colour
  wheel, at alpha 128, turning with the car's speed as dragonpilot turns it (PHASE). A pathFill's gradient runs
  down the road view (0 its top) as dragonpilot's does, so the hue is three turns over the view; the sixteen stops
  sit over its lower part (RAINBOW_TOP down), where the path is: 2.4 turns, so each has six or seven.
  """
  stops = []
  for k in range(STOPS):
    position = RAINBOW_TOP + (1 - RAINBOW_TOP) * k / (STOPS - 1)
    # Where the stop is in dragonpilot's gradient (0 at the view's bottom), three turns of hue over the view.
    hue = f"((3 * {r4(1 - position)} + {PHASE}) % 1)"
    red = f"255 * clamp(abs({hue} * 6 - 3) - 1, 0, 1)"
    green = f"255 * clamp(2 - abs({hue} * 6 - 2), 0, 1)"
    blue = f"255 * clamp(2 - abs({hue} * 6 - 4), 0, 1)"
    stops.append([r4(position), f"=rgba(round({red}), round({green}), round({blue}), 0.5)"])
  return {"type": "pathFill", "when": f"{COMMA_3X} && ext.rainbow", "gradient": stops}


def borders() -> dict:
  """
  dragonpilot's border indicators: on each side a bar UI_BORDER_SIZE wide on the border band's outer edge, from 4
  bands below its top to 4 above its bottom. Blind spot alone: solid yellow. Blinker: green, off and on every half
  second from when it came on; with the blind spot too, yellow, every quarter second, keeping its state as the pace
  changes. Pinned to the comma 3X's band
  (level 2), the rectangle round the road view; the band's thickness there is how far its edge is outside the view.
  """
  thick_x = "(-hud.border.x / hud.border.width)"
  thick_y = "(hud.borderWidth / hud.border.height)"

  def bar(side: str) -> dict:
    # The car's own signal, as dragonpilot reads carState (the app's blinker.* includes openpilot's lane change's).
    blinker, blindspot = f"ext.blinker.{side}", f"blindspot.{side}"
    # dragonpilot toggles its show state every half second of the blinker (a quarter with the blind spot too), so a
    # blind spot coming or going mid-blink keeps the state and changes only the pace: the toggles so far are the
    # integral of the pace, from 0 each time the blinker comes on (dark first, lit after half a second). A blind spot
    # alone keeps the bar lit, so a blinker coming on over one starts lit (the second integral: a blind spot alone
    # since either came on). Both integrals are operands, worked out every frame (in a ternary's branch, or behind &&
    # or ||, one would miss the frames it isn't reached on), and start on the first frame drawn, a blinker already on
    # included.
    toggles = f"integrate({blinker} && {blindspot} ? 4 : 2, !{blinker})"
    solid_first = f"(integrate({blindspot} && !{blinker} ? 1 : 0, !({blinker} || {blindspot})) > 0 ? 1 : 0)"
    lit = f"floor({toggles} + {solid_first}) % 2 == 1"
    return {
      "type": "rect",
      "when": f"{lit} ? ({blinker} || {blindspot}) : ({blindspot} && !{blinker})",
      "x": 0 if side == "left" else f"1 - {thick_x}",
      "y": f"4 * {thick_y}",
      "width": thick_x,
      "height": f"1 - 8 * {thick_y}",
      "fill": f"={blindspot} ? '#FFFF00' : '#00FF00'",
    }

  # Always drawn on the comma 3X design: dragonpilot's border indicators have no setting.
  return {
    "type": "group",
    "when": COMMA_3X,
    "pin": {"hud": "border"},
    "layers": [bar("left"), bar("right")],
  }


def torque_gradient(torque: str, side: str) -> dict:
  """
  The torque line's colours (torque_bar.py): a gradient across the screen from the bar's centre to 65% of the way
  to the background bar's end on the torque's side, white turning orange at its centre and yellow at its end past 0.75
  of the torque, while engaged (as dragonpilot's shader draws it); a faint white otherwise. An arc's gradient runs across its circle's box (level
  2 colours and coordinates as expressions), where the bar's centre is at 0.5 and its ends sin(6.35 degrees) / 2 of
  the box either side; `side` (1 right, -1 left) turns it toward the torque.
  """
  half = math.sin(math.radians(6.35)) / 2
  end = f"0.5 + {r4(0.65 * half)} * {side}"
  orange = f"clamp((abs({torque}) - 0.75) * 4, 0, 1)"
  return {
    "linear": [0.5, 0, end, 0],
    "stops": [
      # dragonpilot's shader runs a horizontal gradient from its end point: the orange at the centre, the yellow at
      # the 65 % point (the app's Sp3XTorqueBarTest records the same). Its blended colours are opaque.
      [0, f"=ext.torqueEngaged ? mixColor('#FFFFFF', '#FF7300', {orange}) : '#FFFFFF59'"],
      [1, f"=ext.torqueEngaged ? mixColor('#FFFFFF', '#FFC800', {orange}) : '#FFFFFF59'"],
    ],
  }


def torque_bar() -> dict:
  """
  The comma four's torque bar, which dragonpilot draws on the comma 3X four times as big: an arc of a circle 4800 px
  in radius, centred 8 px x 4 right of the view's middle and below its bottom, 12.7 degrees wide around the top, its
  thickness 56 to 224 px and its offset from the bottom 88 to 104 px as the torque grows past half; the torque from
  the top to either side, its colours a gradient (torque_gradient); a grey dot at the centre under half. The arc is too big
  for one box (an arc's radius is at most twice its box's height), so it's drawn in a box 4 road views tall: a group
  2 views tall holding one twice its height.
  """
  box = 2 * 2 * H  # the inner group's height in pixels: 4080
  torque = "smooth(ext.torque, 100)"  # its FirstOrderFilter, 0.1 s
  over_half = f"clamp((abs({torque}) - 0.5) * 2, 0, 1)"
  thickness = f"(56 + 168 * {over_half})"
  offset = f"(88 + 16 * {over_half})"
  cx = r4((W / 2 + 8 * 4) / W)
  cy = f"({H:g} + 4800 - {offset}) / {box:g}"
  radius = f"(4800 + {thickness} / 2) / {box:g}"
  arc = {"type": "arc", "cx": cx, "cy": cy, "r": radius, "strokeWidth": f"{thickness} / {box:g}"}
  # The app strokes an arc with round caps half its thickness long; dragonpilot's bar keeps 28 px corners. So each end
  # is drawn short by the difference, in degrees of the circle: nothing below half torque (56 px, 28 px caps).
  cap = f"(({thickness} / 2 - 28) / (4800 + {thickness} / 2) * 57.2958)"
  side = f"({torque} < 0 ? -1 : 1)"
  return {
    "type": "group",
    # Faded in and out over 0.1 s as dragonpilot's _torque_line_alpha_filter; above the hide speed not drawn at all,
    # as dragonpilot skips its whole HUD there.
    "when": f"{COMMA_3X} && !({HUD_FAST})",
    "opacity": "smooth(ext.torqueBar ? 1 : 0, 100)",
    "height": 2,
    "layers": [
      {
        "type": "group",
        "height": 2,
        "layers": [
          {
            **arc,
            "start": f"-96.35 + {cap}",
            "sweep": f"12.7 - 2 * {cap}",
            "stroke": f"=ext.torqueEngaged ? rgba(255, 255, 255, 0.25 + 0.25 * {over_half}) : '#FFFFFF26'",
          },
          {
            **arc,
            # One arc, toward the torque's side (level 2's gradient coordinates as expressions). The torque is read
            # only outside a ternary's branches, so each smooth() answers every frame and eases as dragonpilot's one
            # filter does. Its far end drawn short by the cap's excess, and its start that much past the top, so with
            # its cap it starts 28 px past the centre as dragonpilot's does.
            "start": f"-90 + {side} * {cap}",
            "sweep": f"{side} * max(0, 6.35 * min(abs({torque}), 1) - 2 * {cap})",
            "stroke": torque_gradient(torque, side),
          },
          {
            "type": "circle",
            "when": f"abs({torque}) < 0.5",
            "cx": cx,
            "cy": r4((H - 88 - 28) / box),
            "r": r4(20 / box),
            "fill": "#B6B6B6E6",
          },
        ],
      }
    ],
  }


def lead_stats(lead: str, pin) -> dict:
  """
  dragonpilot's lead stats: under the lead's chevron, its speed and distance in 40 px text ("54 kph | 32.5 m"), and
  below it in 80 px the time to reach it at the car's speed, while that's under 5 s; under each of the two leads it
  draws, and only while openpilot controls the speed, as its lead chevrons. Its chevron's height grows as the lead
  comes closer; here the text sits a fixed 54 px below the lead.
  """
  height = 130.0
  speed = f"({lead}.vRel + car.vEgo) * (metric ? 3.6 : 2.237)"
  distance = f"{lead}.distance * (metric ? 1 : 3.28084)"
  return {
    "type": "group",
    "when": f"{COMMA_3X} && ext.leadStats && longControl",
    "pin": {"lead": pin},
    "anchor": "top",
    "y": r4(54 / H),
    "width": r4(1080 / W),
    "height": r4(height / H),
    "layers": [
      {
        "type": "text",
        "text": f"{{{speed}|0}} {{metric ? 'kph' : 'mph'}} | {{{distance}|0.0}} {{metric ? 'm' : 'ft'}}",
        "x": 0.5,
        "y": 0,
        "anchor": "top",
        "size": r4(40 / height),
        "color": "#FFFFFF",
      },
      {
        "type": "text",
        "when": f"car.vEgo > 0 && {lead}.distance / car.vEgo < 5",
        "text": f"{{{lead}.distance / car.vEgo|0.0}}s",
        "x": 0.5,
        "y": r4(40 / height),
        "anchor": "top",
        "size": r4(80 / height),
        "color": "#FFFFFF",
      },
    ],
  }


def tracks() -> dict:
  """
  dragonpilot's radar tracks: at each point a red dot of 10 px and to its right four lines of 40 px text, the first
  20 px above it: its id, distance, lateral offset and relative speed, in metres and m/s or feet and mph. A group
  repeated for each point export.py sends (level 2), pinned to the road at the point in the car's frame: dRel ahead,
  yRel to the left (dragonpilot maps it at -yRel, right of the car), as dragonpilot places it.
  """
  width, height = 324.0, 184.0
  length = "(metric ? 1 : 3.28084)"
  lines = [
    "ID: {item.id|int}",
    f"d: {{item.d * {length}|0.00}} {{metric ? 'm' : 'ft'}}",
    f"y: {{item.y * {length}|0.00}} {{metric ? 'm' : 'ft'}}",
    "dV: {item.v * (metric ? 1 : 2.23694)|0.00} {metric ? 'm/s' : 'mph'}",
  ]
  return {
    "type": "group",
    # export.py sends no tracks while the setting doesn't show them.
    "when": COMMA_3X,
    "repeat": {"over": "ext.tracks", "max": TRACK_SLOTS},
    "pin": {"car": ["item.d", "-item.y"]},
    "anchor": "topLeft",
    "y": r4(-20 / H),
    "width": r4(width / W),
    "height": r4(height / H),
    "layers": [
      {"type": "circle", "cx": 0, "cy": r4(20 / height), "r": r4(10 / height), "fill": "#FF0000C8"},
      *[
        {
          "type": "text",
          "text": line,
          "x": r4(15 / width),
          "y": r4(40 * i / height),
          "anchor": "topLeft",
          "size": r4(40 / height),
          "color": "#FFFFFF",
        }
        for i, line in enumerate(lines)
      ],
    ],
  }


def overlay() -> dict:
  return {
    "format": 1,
    "level": 2,
    "id": "dragonpilot-hud",
    "hide": [{"part": part, "when": HUD_HIDDEN} for part in ("speed", "setSpeed", "wheel", "driverMonitor")],
    "layers": [
      rainbow(),
      tracks(),
      lead_stats("lead", 1),
      lead_stats("lead2", 2),
      borders(),
      torque_bar(),
    ],
  }


if __name__ == "__main__":
  (HERE / "overlay.json").write_text(json.dumps(overlay(), indent=1) + "\n")
