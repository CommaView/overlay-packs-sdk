# dragonpilot HUD (official pack)

CommaView lists this pack as an official pack in the app's overlay pack catalog, offered to commas running dragonpilot.

dragonpilot (`dragonpilot/dragonpilot`) changes a few things on openpilot's onroad screen. This pack brings the ones
CommaView's runtime doesn't export to the app, read the way dragonpilot's own UI reads them (written against its
release `0.11.1`; `pre-build`'s UI is the same). All of them are on its comma 3X screen: its comma four screen adds
only ALKA, which the runtime exports itself (below). The pack needs overlay level 2: it draws on the app's comma 3X
design alone (`screen == 'comma3x'`), whichever comma sends it, since the design shown isn't always the comma's own.
It's `inactive` on every other fork (`requiresModules`: `dragonpilot.selfdrive.ui.dashy_qr`, which dragonpilot's UI
loads on every device).

## What dragonpilot adds, and where each goes

Against the openpilot commit dragonpilot 0.11.1 is built on (`selfdrive/ui`):

| dragonpilot draws | Where | Goes |
| --- | --- | --- |
| **ALKA** steering while openpilot is disengaged: the comma 3X's border in ALKA's blue, the comma four's path blue and its wheel shown. | `ui_state.py`, `onroad/augmented_road_view.py`, `mici/onroad/*` | **The runtime**: its own capability and `uiStateOnroad.dragonpilot.alkaActive`, which the app's dragonpilot source draws. A pack can draw a blue border, but not make the app's own status, path and wheel say the car is steering. |
| **The rainbow path** (`dp_ui_rainbow`). | `onroad/model_renderer.py` | This pack |
| **Lead stats** (`dp_ui_lead` Lead or All): the lead's speed and distance, and the time to reach it under 5 s. | `onroad/model_renderer.py` | This pack |
| **Radar tracks** (`dp_ui_lead` Radar or All): every `liveTracks` point, with its id, distance, offset and relative speed. | `onroad/model_renderer.py` | This pack, the 22 nearest ahead |
| **Border indicators**: blinker and blind spot bars on the border band's outer edges. | `onroad/augmented_road_view.py` | This pack |
| **The torque bar**: the comma four's, four times as big. | `onroad/hud_renderer.py`, `mici/onroad/torque_bar.py` | This pack |
| Hide the HUD above a speed (`dp_ui_hide_hud_speed_kph`): the speed, set speed, experimental button, torque bar and driver monitoring icon. | `onroad/augmented_road_view.py` | This pack (level 2's `hide`), on the comma 3X design. |
| Its own wheel icon in the experimental mode button. | `onroad/exp_button.py` | Skipped: branding, where CommaView draws its own button. |
| Display mode, screen timeouts, the comma 3's light sensor. | `ui_state.py` | Skipped: not on the screen. |

None of dragonpilot's onroad text is translated: it translates its settings (13 languages, through gettext and its
own `dragonpilot_<language>.po`), but the lead stats and radar tracks are numbers with English units (`kph`, `mph`,
`m`, `ft`, `ID:`, `dV:`). The pack draws the same text.

## What it shows

Every layer and hidden part waits for `screen == 'comma3x'`.

- **The rainbow path** (`_update_rainbow_gradient`): `pathFill` with sixteen stops, each colour an expression of the
  stop's place and the rainbow's phase (`rgba()` of the hue, HSV to RGB with `abs` and `clamp`), at dragonpilot's
  alpha of 128. The phase turns as dragonpilot's does, `max(0.01, vEgo) / 20` a second, worked out in the overlay
  (`integrate(max(0.01, car.vEgo)) / 20`), so it moves every frame the app draws. dragonpilot's gradient goes three
  times round the colour wheel over the road view's height, and so does the pack's (a pathFill's gradient runs down
  the road view too), its stops over the view's lower 80 %, where the path is.
- **Lead stats**: under each of the two leads (`"pin": {"lead": 1}` and `{"lead": 2}`), its speed
  (`lead.vRel + car.vEgo`, in kph or mph) and distance (m or ft), and under 5 s, `lead.distance / car.vEgo` in
  seconds, as dragonpilot works it out (the time at the car's own speed); only while openpilot controls the speed
  (`longControl`), as dragonpilot draws its lead chevrons. All from the app's own `lead.*`, `lead2.*` and `car.*`.
- **Radar tracks**: export.py sends the 22 nearest `liveTracks` points ahead the pin can place (up to 250 m ahead
  and 20 m to a side) as a list (`{"id", "d", "y", "v"}`), and
  one `group` repeats over it (`"repeat": {"over": "ext.tracks", "max": 22}`), pinned to the road in the car's frame
  at each point (`"pin": {"car": ["item.d", "-item.y"]}`, `yRel` being to the left), with dragonpilot's red dot and
  its four lines.
- **Border indicators**: a `group` pinned to the comma 3X's border band (`"pin": {"hud": "border"}`), with two
  `rect`s where dragonpilot draws them, as wide as the band (`-hud.border.x / hud.border.width` of the band's box),
  from four bands below its top to four above its bottom. A blind spot alone is solid yellow; the blinker green, off
  and on every half second from when it came on; both, yellow every quarter second. As dragonpilot toggles its
  show state, a blind spot coming or going mid-blink changes the pace and keeps the state, and a blinker coming on
  over a blind spot starts lit: the toggles are `integrate()` of the pace, from 0 as the blinker comes on, in the
  condition of a `? :` so they're worked out every frame (a blinker already on when drawing starts blinks too). From
  the car's own blinkers (`carState`, sent by export.py: the app's `blinker.*` includes openpilot's lane change
  blinker, which dragonpilot's bars don't show) and the app's `blindspot.*`. The bars need the app's level 2, which
  draws groups pinned to the band after the band.
- **The torque bar**, fading in and out over 0.1 s as dragonpilot's: two `arc`s, the background and the torque from the top toward its side, and the centre dot
  under half, with dragonpilot's sizes (radius 4800 px, 12.7 degrees, 56 to 224 px thick, 88 to 104 px from the
  bottom; each end drawn short by how much longer the app's round caps are than dragonpilot's 28 px corners) and its
  colours: a gradient across the screen from the bar's centre to 65% of the way to its end on the
  torque's side, white turning orange at its centre and yellow at its end past 0.75 of the torque, while engaged, opaque
  (level 2's expression stops and coordinates: one arc, its gradient turned toward the torque's side); a faint white otherwise. The
  torque is `-carOutput.actuatorsOutput.torque`, eased as dragonpilot's filter does (`smooth(ext.torque, 100)`); not
  for a car that steers by angle, not while dragonpilot's status is disengaged, ALKA's included, as its own screen
  fades it out, and not while the HUD is hidden (not drawn at all above the hide speed, as dragonpilot skips its
  whole HUD there).
- **Hide the HUD above a speed**: export.py sends `dp_ui_hide_hud_speed_kph` in m/s (`hideHudSpeed`, 0 when off), and
  the overlay hides the design's `speed`, `setSpeed`, `wheel` and `driverMonitor` while `car.vEgo` is over it. The
  app's alerts stay, as dragonpilot's do.

`build-overlay.py` writes `overlay.json` from dragonpilot's pixel numbers (its 2100 × 1020 content rect, the road
view inside its border band, as the app's): run it after changing it or export.py's `TRACK_SLOTS`, which it reads.

## Where the format falls short

Level 2 closed most of what this pack lacked at level 1: the rainbow's stops and phase (16 stops, `integrate`), the
radar tracks (a repeat over a list, pinned in the car's frame, where they were 8 slots pinned to the path's
centre), the border bars (pinned to the band, not covering the camera), which screen it's on (`screen`, not
`gui_app.big_ui()`), and hiding the HUD. What's left:

| Gap | What it costs this pack |
| --- | --- |
| A repeat's copies each count their layers toward the 128, and a car pin reaches 250 m ahead and 20 m to a side. | dragonpilot draws every `liveTracks` point; the pack the 22 nearest ahead within the pin's reach (a repeat may make 64 copies, but 22 of the dot and dragonpilot's four lines are what the 128 leave). |
| An arc's radius is at most twice its box's height, and a group at most twice its parent. | The torque bar's 4800 px circle is drawn in two nested groups, a box four road views tall. It works; it isn't obvious. |
| Positions are fractions of the app's road view, whose shape isn't the comma's. | dragonpilot's pixel layout is stretched to the phone's road view: the torque bar keeps its place, not its proportions. |
| A pinned group can't follow the pinned thing's own size. | dragonpilot's lead text sits under the chevron, which grows as the lead comes closer; the pack's sits a fixed 54 px below the lead. |
| No language in expressions, and no strings per language in overlay.json. | Not needed here (dragonpilot's onroad text is English), but a pack whose text should be translated has to send it from export.py, translated by the fork's own `tr()`: in the comma's language, not the phone's. |
| Its own wheel icon. | Skipped: branding, where the app's design draws its own button. |
| No value an expression keeps until told otherwise. | dragonpilot toggles its border bars on a count from when they came on; after the blind spot changes the pace mid-blink, the pack's next toggle comes a pace after the last, up to a quarter second from dragonpilot's. |
| A pack's groups draw above the road layers. | dragonpilot draws its radar tracks under its path and lane lines (its 50 % rainbow tints them); the pack's sit on top. |

### Level 2's second part

Four later additions to level 2: the torque line is one arc whose gradient's end
is an expression, turned toward the torque's side, where it was one arc a side; repeats may now make 64 copies, and
the pack sends the 22 nearest tracks the layer budget leaves room for, where it sent 16. A conditional inset and a
lane-line ribbon aren't needed here (dragonpilot moves no HUD part and keeps openpilot's lane lines).

## Check it

```
tools/check-pack.py examples/dragonpilot-hud \
  --module dragonpilot.selfdrive.ui.dashy_qr --attr dp_ui_lead=3 --attr dp_ui_rainbow=true --attr dp_ui_hide_hud_speed_ms=0 \
  --service 'liveTracks={"points": [{"trackId": 3, "dRel": 18.0, "yRel": 3.1, "vRel": 0.4}]}' \
  --service 'carOutput={"actuatorsOutput": {"torque": -0.4}}'
```

On a comma: dragonpilot 0.11.1 on a comma 3X, with Rainbow path and Display Lead Stats (All) on in its settings,
a car with radar for the tracks (straight road and curve), a drive with the blinker and a car in the blind spot, and
Hide HUD above a speed set; Live View on the app's comma 3X design, and on another design (nothing drawn).
