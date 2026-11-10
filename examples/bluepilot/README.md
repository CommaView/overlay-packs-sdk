# BluePilot (official pack)

CommaView lists this pack as an official pack in the app's overlay pack catalog, offered to commas running BluePilot.

CommaView lists BluePilot (`BluePilotDev/bluepilot`) but its runtime exports
only what BluePilot shares with sunnypilot. This pack brings what BluePilot adds to sunnypilot's onroad screen, read
the way BluePilot's own widgets read it (written against `bp-7.0` at `773f7f1d`; `bp-dev` at `5ea746ae` has no onroad
change from it). Every element follows the BluePilot setting that turns it on, so the app shows what the driver chose
on the comma.

It runs only on BluePilot (`requiresModules`: `openpilot.selfdrive.ui.bp.onroad.blindspot_renderer`, which both of
BluePilot's road views import). It needs overlay level 2: export.py sends both of BluePilot's screens' figures, and
overlay.json draws the comma 3X's or the comma four's by the design on screen (`screen`), which isn't always the
comma's own; on CommaView's design it draws the comma 3X's.

## Every element BluePilot adds or changes, and what this pack does with it

BluePilot changes no sunnypilot onroad file: it swaps its own `*BP` classes in from `selfdrive/ui/layouts/main.py`
(comma 3X) and `selfdrive/ui/mici/layouts/main.py` (comma four). Paths below are under `selfdrive/ui/bp/`.

| Element | Device | BluePilot's files | Shows | Decision |
| --- | --- | --- | --- | --- |
| Hybrid battery gauge | 3X | `onroad/hybrid_battery_gauge.py` (flat), `onroad/hybrid_battery_gauge_arched.py` | A battery outline filled to `carStateBP.hybridBattery.socActual` (red < 20 %, yellow < 50 %, green), the %, volts and signed amps (green charging, red drawing). `FordPrefHybridBatteryStatus`. | **Pack**, flat style, on the comma 3X design (on CommaView's it would sit on the design's cluster); it moves right with the HUD while the confidence-ball strip shows, as BluePilot's does, so the driver-monitor icon doesn't cover it. |
| Power flow gauge | both | `onroad/power_flow_gauge.py`, `onroad/powerflow_gauge_arched.py`, `mici/onroad/powerflow_gauge.py` | `carStateBP.hybridDrive`: demand / regen as a centre-zero bar (3X, with the power flow mode and why the engine is on), a bar up the left edge (comma four); in electric drive, brackets at the demand that starts the engine. `FordPrefHybridPowerFlow`. | **Pack**: the 3X flat bar on the comma 3X design (not CommaView's, whose cluster is there), and the comma four's edge bar or, with `FordPrefHybridPowerFlowAlternate`, its ring round the design's wheel (two `arc`s from the bottom: regen up the left, demand up the right, in a box 1.8 times the wheel placed by `hud.wheel.*`, since a group pinned to the wheel is clipped to it). The 3X's arched style isn't drawn (gap 2). |
| Brake lights | both | `onroad/hud_renderer_bp.py` (`_brakes_on`), `mici/onroad/hud_renderer_bp.py` | The current speed (3X) or the wheel (comma four) turns red while `carStateBP.brakeLightStatus.brakeLightsOn`. `ShowBrakeStatus`. | **Pack**: on the comma 3X design the design's speed is hidden and the pack draws it in red in its place (`hide`, `"pin": {"hud": "speed"}`); on the comma four's, a red ring on its wheel; a red BRAKE pill elsewhere. |
| Lateral mode letter | both | `_draw_lateral_control_overlay` in both HUDs | "A" blue (angle), "C" orange (curvature), "OP" grey on a light disc over the wheel, from `controllerStateBP.activeLateralMode` (Ford cars only). `BpShowLateralControl`. | **Pack**, pinned to the design's wheel (`"pin": {"hud": "wheel"}`); where BluePilot's 3X puts it on a design without one. The comma four's tap to switch modes isn't (gap 5). |
| Confidence ball and MADS beam | both | `mici/onroad/confidence_ball_bp.py` | A ball whose height is the model's confidence (`modelV2.meta.disengagePredictions`, the stock ball's filter), green / amber / red, white on override; a cyan or purple beam while MADS steers or brakes alone. Left strip on the 3X (`BPShowConfidenceBall`), right panel on the comma four (always). | **Pack**: the 3X's left strip; on the comma four design it replaces the design's own ball on its panel (`hide`, `"pin": {"hud": "confidenceBall"}`). BluePilot also moves the 3X HUD right to make room, and so does the pack while the strip shows (`inset` with a `when`). |
| Blind spot screen edges | both | `onroad/blindspot_renderer.py` | Red glows from the left or right edge while `carState.left/rightBlindspot`, pulsing every 3 s. `ShowBlindspotOverlay`. | **Pack**, from the app's own `blindspot.left/right`. |
| Radar lead overlay | 3X | `onroad/chevron_metrics_bp.py`, `onroad/model_renderer_bp.py` | Under the lead, three boxes (distance, speed, time gap), outlined blue for a radar lead and red for a vision one, a triangle pointing at it; under 100 ft (until 125 ft) at the top of the road, pointing down. `FordPrefShowRadarLeadOverlay`, `FordPrefRadarOverlaySize`. | **Pack**, at size 1, the first lead only. The size setting and the second lead aren't followed. |
| Lead chevron colours, smoothing and size | 3X | `onroad/model_renderer_bp.py` | The lead chevron blue (radar) or red / yellow (vision), smoothed and scaled; shown even without openpilot longitudinal. | **Skip**: the app draws its own lead and a pack can't recolour it (gap 1); the lead overlay's outline carries radar / vision. |
| Complication | comma four | `mici/onroad/complication.py` | Lower right: the lead's speed (orange slower, green faster), the speed, the lead's distance or the time to the lead, fading over 3 s after the lead goes. `mici_complication`. | **Pack**. |
| Lane lines, road edges, path edges | 3X | `onroad/model_renderer_bp.py` | Wider lanes; inner lanes green engaged, grey on override, black disengaged, with glows; new path edges in the status colour. `BPDisableLaneLineStatusColor`. | **Pack**, the lane lines on the comma 3X design: the design's hidden and BluePilot's drawn along each (level 2's lane-line ribbon), 0.05 m × probability either side from 0.4 probability (in Minimal Driving View 0.075 m for the current lane's lines and 0.09 m for the outer ones, as BluePilot widens them), the current lanes in the status colour brightened by probability (white with `BPDisableLaneLineStatusColor`, a neutral blue in Minimal Driving View), the outer ones white; without BluePilot's glow. Its road edges and path edges are the design's. |
| Path smoothing | 3X | `onroad/model_renderer_bp.py` | Gaussian and temporal smoothing of the projected path. | **Skip**: cosmetic; the app draws the model's path. |
| Rainbow lanes and BluePilot's rainbow path | both | `bluepilot/ui/lib/bp_shaders.py` | A GLSL rainbow on the inner lanes (`BPRainbowLines`) and on sunnypilot's RainbowMode path. | **Skip**: the app already draws sunnypilot's rainbow path; an animated hue along a ribbon isn't a layer (gap 3). |
| Steering wheel | both | `onroad/exp_button_bp.py`, `mici/onroad/hud_renderer_bp.py` | The wheel turns with `steeringAngleDeg` (`BPAnimateSteeringWheel`), in the comma 3X's or comma four's icon (`BPSteeringWheelIconStyle`), or a theme's. | **Skip**: the design's own wheel; `car.steeringAngle` could turn a drawn one, but the pack has no picture of the design's wheel to turn (gap 1). |
| Driver monitoring icon style | both | `onroad/driver_state_bp.py`, `lib/dm_icon_style.py` | Either device's DM icon on either. `BPDMStylingChoice`. | **Skip**: the app's own icon (gap 1). |
| Set speed in the theme's accent | comma four | `mici/onroad/hud_renderer_bp.py` | | **Skip** (gap 1). |
| Torque bar | both | `onroad/torque_bar_renderer_bp.py`, `torque_bar_state_bp.py`, `mici/onroad/torque_bar_bp.py` | Sunnypilot's torque bar, smoother, lifted over the gauges or as a strip along them. | **Skip**: the app draws its own from `steer.torque`. |
| Informational alerts as a pill | 3X | `onroad/alert_renderer_bp.py` | | **Skip**: packs are kept away from alerts, on purpose. |
| Minimal Driving View, hidden border | both | `onroad/cameraview_bp.py`, the road views | `BPHideCameraView` paints the camera black; `BPHideOnroadBorder`. | **Pack**: a black `backdrop` while `BPHideCameraView` is on, and the design's `border` hidden while `BPHideOnroadBorder` is. |
| Theme packs and scenes | both | `lib/theme_pack.py`, `lib/theme_scene.py`, `lib/theme_scene_assets.py` | 11 seasonal colour packs for the path, lanes, lead and wheel, a sky, and scenes with particles, decor, a mascot and fireworks. | **Skip**: cosmetic, and beyond the picture and layer limits (gap 4). |
| 8-Bit "Rad Racer" | both | `onroad/rad_racer_*.py`, `mici/onroad/rad_racer_mici.py` | A pixel-art road, skyline and cars; on the 3X a purple cluster in place of the HUD. | **Skip**: a design of its own (`"design"`) could start it, but its sprites and scrolling road need more than the format has (gap 4). |
| Controls debug panel, comma four lateral debug | both | `bluepilot/ui/widgets/debug/`, `mici/onroad/lateral_debug_mici.py` | Graphs of steering and longitudinal control, an angle-factor stepper. | **Skip**: tuning tools that write params; not for the app. |

Nothing here needs native code in the exporter: what a driver gets from BluePilot's screen (its gauges, the lead
overlay, the lateral mode, the brake lights) the pack can draw, and what it can't is BluePilot's restyling of
elements the app already draws its own way.

## Gaps in the pack format (overlay level 2)

Level 2 closed what kept the pack from BluePilot's layout at level 1: hiding and pinning to the design's parts (the
brake-red speed, the lateral letter on the wheel, the comma four's ball), the design on screen (`screen`, where it
read `ext.device`), Minimal Driving View (a backdrop), the hidden border, and the ball's colours as expression
stops (one circle, where it was five). What's left:

1. **A pack can't restyle the app's own elements.** The design's set speed, wheel, DM icon and lead chevron keep their
   look: a pack can hide one and draw its own, but has no picture of the design's wheel or icon to turn or tint.
2. **The arched gauges.** Text along an arc (level 2) could write their mode and reason, but their geometry isn't
   drawn yet: the pack draws the flat style for both settings.
3. **No colour that animates along a ribbon.** BluePilot's rainbow lanes are a shader.
4. **Limits for skins.** 8 pictures and 96 KiB of PNG, 128 layers, no sprite sheets or particles: BluePilot's
   theme packs and Rad Racer don't fit.
5. **Display only.** The comma four's tap on the letter switches BluePilot's lateral mode; packs can't take input,
   by design.

### Level 2's second part

Two later additions to level 2 closed two more of what kept the pack from
BluePilot's layout (closed, so not in the list above): the HUD, and the battery gauge with it, moves over for the
confidence-ball strip only while it shows (an `inset` with a `when`, where before it moved for good or not at all), and
BluePilot's lane lines are drawn along the model's lane lines (`"along": "laneLine"`, where the design's had to stay). Gradient directions as expressions and repeats past 16 aren't
needed here.

## What needs a test in a Ford

None of it has run on a comma. In a Ford hybrid on BluePilot `bp-7.0`, with the settings on:

- the battery and power flow figures against the comma's gauges (charging and drawing, regen, electric drive with
  the engine-start brackets), on both devices;
- with the brake lights: the comma 3X design's red speed in the design's place, the comma four's red wheel ring; the
  A / C / OP letter on the design's wheel as the car changes lateral mode;
- the confidence ball against the comma's, engaged and with MADS steering alone; the blind spot edges;
- the lead overlay's figures, its radar / vision outline and its close-range layout; the comma four's complication;
- Minimal Driving View (`BPHideCameraView`) blacks the app's camera out under the road and widens the lane lines,
  and `BPHideOnroadBorder` hides the comma 3X design's border;
- the lane lines' colours (engaged, override, disengaged, `BPDisableLaneLineStatusColor`) and widths against
  BluePilot's, with and without Minimal Driving View;
- with `BPShowConfidenceBall`, the comma 3X design's HUD moving over for the ball strip, and the battery gauge with
  it, clear of the driver-monitor icon;
- each on the app's comma 3X, comma four and CommaView designs, whichever comma it is;
- that Live View's Details report the pack `active`, and that BluePilot's screen keeps its frame rate.

## Check it

```
tools/check-pack.py examples/bluepilot \
  --module openpilot.selfdrive.ui.bp.onroad.blindspot_renderer \
  --service 'carStateBP={"hybridDrive":{"dataAvailable":true,"throttleDemandPercent":51,"powerFlowMode":"Idle"},"hybridBattery":{"dataAvailable":true,"socActual":63,"voltActual":272,"ampsActual":-12}}' \
  --service 'controllerStateBP={"activeLateralMode":"curvature"}'
```

The checker can't set BluePilot's params, so the gauges and the letter stay off there; CommaView's own tests
of the pack turn them on.
