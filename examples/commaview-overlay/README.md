# commaview-overlay: CommaView's own design as a pack

CommaView's onroad design (the "CommaView" design in the app's Display settings) redrawn with nothing but an overlay
pack's parts: an example of a whole design made as a pack. Its overlay.json has a `design`, so the app offers it in
Display as a design of its own, "CommaView, as a pack": the road camera and this pack, with the app's own alerts on
top. Added over another design, it draws there too. It's a standalone pack: every value it shows is one the app
already gives an expression (`setSpeed`, `speedLimit`, `signStyle`, `steer.torque`, `blinker.*`, `blindspot.*`,
`lead.*`, `lane.N.prob`, `edge.N.std`, `dm.*`), so nothing runs on the comma and it draws on any fork.

What it draws:

- the road, as `ribbon`s: the path in its engagement colours (teal engaged, grey steering only and disengaged, amber
  override), fading out and stopping 1.5 m short of the lead; its rails, dashed while openpilot drives the speed alone;
  its flow, bands fixed to the road; lane lines as wide and strong as the model is sure of them; dashed red road edges;
- the instrument cluster at the bottom: its glass (gradients and a glow for its shadow), MAX and the set speed, the
  speed gauge (track, ticks, an arc lit in the status colour, eased toward the speed with `smooth()` and glowing) with
  the speed and unit, the control line (`pick()` by state), the speed-limit sign of the car's region (`signStyle`: a
  rectangle in the US and Canada, else round) with its over-the-limit halo, the steering torque crescent, and the turn
  signals and blind spots either side;
- a glow along the view's edge (`edgeGlow`) while the blinker's on with a car in that blind spot;
- the lead car's lock-on brackets, sized in metres where it is and coloured by how fast it closes, with its distance
  and time gap on a pill;
- the status pill, sized to its word (a text's `background` with `padding` and `radius`), its dot glowing for a moment
  when the state changes (`since()` and `mixColor()`); the experimental chip, its atom three ellipses drawn as one SVG
  `path`; the header shade, the CommaView lockup and the driver-attention line;
- over another design only (`!design`), its own alert card, faded in when its words change: as a design, the app
  draws its alerts over the pack.

`build-overlay.py` writes `overlay.json` from the design's own numbers (its layout unit and colours); edit that, not
the JSON. Preview it in the pack studio, my.commaview.com/studio.
