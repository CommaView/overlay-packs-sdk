# carrotpilot HUD (example pack)

CommaView lists carrotpilot (`ajouatom/openpilot`) but
doesn't export what its onroad screen adds to openpilot's. This pack brings some of the most visible additions to
the app, read the way carrotpilot's own UI reads them (written against `carrot-wip` at
`2cd1d797`).

## What it shows

All of it is carrotpilot's comma 3X HUD; its comma four HUD draws none of it, so the pack is `inactive` there (its
`applies()`), as it is on every other fork (`requiresModules`: `openpilot.selfdrive.ui.carrot_param_cache`, which
carrotpilot's UIState imports on every device).

- **The follow-distance line** (`selfdrive/ui/onroad/model_renderer.py`, `_draw_tf_marker_carrot`): a thin white
  line across the path at `longitudinalPlan.desiredDistance`, the gap carrotpilot's planner keeps to the lead.
  Drawn with `pathLine`, which follows the model's path to that distance as carrotpilot's does, 1 m either side.
  Its label ("25 m") sits beside the line's right end: a `group` pinned to the path
  (`"pin": {"path": "ext.followDistance", "offset": 1.0}`) holding a `text`.
- **The speed limit box** (`selfdrive/ui/onroad/hud_renderer.py`, `_draw_carrot_speed_limit_box`): carrotMan's
  speed camera limit (`xSpdLimit`, labelled `CAM`, unless `xSignType` is 22) or else the road's limit
  (`nRoadLimitSpeed`, labelled `LIMIT`), in the comma's units, rounded to mph as carrotpilot rounds it. A `group`
  where carrotpilot's comma 3X HUD puts it (bottom left, in fractions of its 2160×1080 road view): a `rect` with
  carrotpilot's white outline, and the number as white digits with a dark outline (`{ext.speedLimit|0}`). The
  box blinks red and yellow for a camera as carrotpilot's does (its blink timer: 16 frames at 20 Hz, red for 9
  of them), a colour expression over `clock`:
  `"=ext.camera ? (clock % 800 < 450 ? '#FF0000D2' : '#FFFF00D2') : ext.overLimit ? '#FF0000D2' : '#FFFFFFD2'"`.
  For a road limit it's red when the car's speed (the cluster's, once the car sends one, as carrotpilot's HUD) is
  more than 2 over it, white otherwise. The `LIMIT` / `CAM` label is a `text` layer above it.
- **The radar distance box at the lead** (`model_renderer.py`, the `_carrot_radar_dist` box): the lead's distance
  to a tenth of a metre in an orange-outlined box above the lead car, a `group` pinned to it
  (`"pin": {"lead": true}`). It reads the app's own `lead.distance`, so it needs nothing from `export.py`.
  carrotpilot draws it at the path's end beside the vision distance's blue box, and turns it red for a radar
  track it hasn't matched; the pack shows the radar box alone, always orange.

## What it can't show yet, and why

| carrotpilot draws | Why it's left out |
| --- | --- |
| Its red / green traffic light icons (`carrotMan.trafficState`) and turn-by-turn arrows (`xTurnInfo`, `turn_l.png` …). | Drawable now (`image` layers from the pack's `images`, or `circle`s), but they need `export.py` to send `trafficState` and the turn info, and carrotpilot's icons as PNGs (96 KiB in all). Left out to keep the example small. |
| The vision distance box, and the lead box's colour by radar track. | Needs `modelV2.leadsV3` and the radar track id in the payload; the same pinned group would draw them. |
| Its path colours by mode, the brake-light outline, the animated path styles, blind-spot barriers. | `pathFill` can tint the path, but not outline it or draw beside it: a `pathOutline` layer and a lane-edge layer would be needed. |
| carrotpilot's dark rounded panel behind its HUD. | A `rect` can draw it, but the design's own HUD would sit on top of it in the wrong place: CommaView's comma 3X HUD isn't carrotpilot's. |

Easy to add with today's layers, and left out to keep the example small: TPMS (`carState.tpms`, four `text`
layers), the device-state lines (CPU temperature, memory, voltage, with `rect` bars), and carrotpilot's date and
time (the [`datetime`](../datetime) pack draws one for any fork).

## Check it

```
tools/check-pack.py examples/carrot-hud \
  --module openpilot.selfdrive.ui.carrot_param_cache \
  --service 'longitudinalPlan={"desiredDistance": 24.6}' \
  --service 'carrotMan={"xSpdLimit": 50, "xSignType": 1, "nRoadLimitSpeed": 60}'
```
