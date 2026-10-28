# Changelog

## v0.13.3 — A long shower is no longer lost

**Recommended for everyone** — a long shower that used up the hot layer of the tank could vanish from the log: it was taken for heat leaving through the coil, and its heat showed as cooling in the *Since reheat* bar (#10).

### Fixes
- **A long shower is no longer taken for the coil** (#10). When the top of the tank cools while its bottom is still warm, the heat left without water: a heat pump starting a reheat, or heat taken out through the coil. But a long shower does the same once it has used up the hot layer: on a 200 L tank, 17 minutes into a 20-minute shower the top began to fall while the bottom was still above 30 °C on its way down, and the whole shower was dropped. Now that only counts as the coil while less than 4 K has left the tank (a coil dips the top long before that), and never while the water meter shows water running. The same goes for uses found in the history and uses hidden by a gap in the readings.

A shower lost this way does not come back in the log.

### After updating
Update in HACS and restart Home Assistant. Nothing to set.

## v0.13.2 — Cold water no longer learned too warm

**Recommended for everyone** — the cold water temperature the integration learned crept up to 25 °C within days. The shower time shown was too high because of it, the heat and cost of a shower too low, and washing up could count as a shower again (#10).

### Fixes
- **The bottom of the tank after a shower is no longer taken for the mains water.** The bottom sensor never reads colder than the mains water, but after most showers it stays far above it: a shower lets in about a fifth of the tank, and that mixes with the warm water that was there. On a 200 L tank the bottom ended at 25–38 °C after an ordinary shower, with mains water of 17–18 °C, and that reading pushed the cold water up to 25 °C. Now the coldest bottom reading of a day can only *lower* the cold water, where the tank showed colder water than expected (the outdoor line once that happened on two days), at most 4 °C below the starting values; it never raises it. Readings while reheating are left out: a heat pump starting up cools the bottom for a moment.
- **Readings kept from earlier nights count once.** With an outdoor sensor the cold water line was refitted every night on top of the previous night's result, with all kept readings again, so the same few showers pushed it further each night.
- **Cold water learned too warm is repaired** at the first start after updating: months above the starting value go back to it, and the outdoor line is refitted from the kept readings the new way. Values learned lower stay.

With too warm cold water a shower minute seemed to take less heat than it does. On a 200 L tank with 22 °C instead of 16.5 °C: 20 minutes left instead of 17, a full tank 47 minutes instead of 37, a logged shower's heat and cost a quarter too low, and the pace check that tells washing up from a shower (#10) became lax. At 25 °C, and in winter, the gap grows larger.

### After updating
Update in HACS and restart Home Assistant. Nothing to set: the cold water is repaired by itself. The shower time shown can drop a few minutes; the lower figure is the right one.

## v0.13.1 — Emptied options stay empty, washing up is no shower

**Recommended for everyone who emptied a field under Configure, and for everyone with a bathroom signal or water meter** — a COP or other setting filled in at setup came back after you emptied it, and stayed in use (#12), and washing up that took as much heat as a shower could still be logged as one (#10).

### Fixes
- **A field you empty under Configure stays empty** (#12). What you fill in at setup is kept apart from what Configure saves. An emptied field was left out when saving, so the value from setup came back — and was still used, for example a COP you wanted learned. Emptied fields are now saved as empty, and an emptied bathroom signal is no longer suggested again.
- **Washing up is no shower, even with a shower's heat** (#10). Washing up with the tap on and off can take as much heat as the shower threshold, but over a long time: a third of a shower's pace or less, where real showers run at 0.6 times and up. A use of 6 minutes or more that takes heat at less than 0.35 times a shower's pace is now a tap, unless the bathroom signal saw it. This is only judged while the bottom of the tank is still warm (15 °C above the cold water) — with a cool bottom a shower shows little at the sensors — and when the tank sensors report at least every 3 minutes, so the pace can be measured. Checked on two weeks of real readings: no shower changed, and the washing up from #10 became a tap.
- **With a bathroom signal, a steady flow on the water meter alone no longer confirms a shower early** (#10). A kitchen tap can run as steadily as a shower, so only the bathroom signal lowers the threshold now. The water meter still times the shower and still confirms a short one.
- **Washing up no longer teaches the model about your showers.** A use that was followed live and logged as a tap is left out when the shower length and flow are learned from the history, and the history gets the same pace check. Washing up taken for a shower could pull the measured shower-head flow down, and with it raise the shower temperature shown.

### Improvements
- **Diagnostics** now show why each use counted as a shower or a tap (the heat it took, the signals that saw it, the rule that decided), what the bathroom signal and water meter showed in the last 24 hours, and the learned COP.

### After updating
Update in HACS and restart Home Assistant. Filled in a COP (or anything else) at setup that you want the integration to work out itself? Open Configure, empty the field and save.

## v0.13.0 — Hand washing is no shower, no false "reheated", bath yes/no

**Recommended for everyone with a bathroom signal or water meter, and for everyone who sees "+ reheated" while nothing reheated** — hand washing at the hour your household showers could be logged as a shower, and the *Since reheat* bar could show a reheat that never happened (#10).

### Fixes
- **Hand washing at shower time stays a tap** (#10). Since v0.12.1 a short use (3+ minutes, 1.2+ K) at the time your household showers counts as a shower. With a bathroom signal or water meter it now also has to see the bathroom in use or a shower-like flow — during the use or just after, as a humidity sensor rises late. Without either, nothing changes.
- **"+ reheated" only after a real reheat** (#10). The *Since reheat* bar closes its books: left + showered + other + cooled = full. When the logged uses added up to more than the tank lost, the rest showed as "+ reheated", even without any reheat. Now that only happens when a reheat or top-up was seen; otherwise the uses in the bar are scaled back to what the tank lost (attribute `overcounted_minutes` says by how much). The shower log itself is unchanged.

### Improvements
- **There is a bath on this tank** (yes/no) in setup and in the options. Turn it off when you only have showers and taps: a fast fill (a bucket, a sink) is then never counted as a bath. On by default, so nothing changes for existing installs.

### After updating
Update in HACS and restart Home Assistant. No bath? Turn off *There is a bath on this tank* under Settings → Devices & services → Hot Water Shower Time → Configure.

## v0.12.1 — Short showers

**Recommended for everyone** — a short shower could be logged as a tap, and the start of a use after a quiet tank
was not measured.

### Fixes
- **The start of a use is measured again after a quiet tank** (#8). Most tank sensors only change state when the
  value changes; when the bottom had not changed for half an hour, the first fall of a shower was spread over that
  half hour, too slow to count, and only the part after the next reading was measured — about half of a short
  shower. A fall now counts from when the heat pump last reported (its other temperatures change every few
  minutes), so the whole use is measured. Showers also come out a little longer and closer to the truth
- **Short showers count.** A use below the shower threshold is a shower when it ran 3 minutes or more, took 1.2 K
  or more and came at a time this household showers: within an hour of the time of day two or more of its showers
  were logged (and more showers than taps there), or within an hour after a logged shower — one showers after the
  other. Checked on nine days of real readings: two short showers of 4 minutes that were taps before are now
  showers; no tap became a shower

### After updating
Update in HACS and restart Home Assistant.

## v0.12.0 — Gaps in the readings, baths count as sessions

**Recommended for everyone** — a heat pump whose data stopped for a while could turn a shower into a "bath filled in
0 minutes", and a bath now counts as a session, like a shower.

### Fixes
- **Gaps in the tank readings** (seen on a Weheat: its cloud stopped for 48 minutes while someone showered).
  When the tank's device sent no temperature for 15 minutes, a tank sensor was unavailable, or a reading was stale
  (the tank falls faster than any bath could), the pace of the fall is unknown. The hot water used meanwhile is now
  logged once, from the heat it took, after the readings have settled: as a shower (≈, attribute `estimated`) when
  that was enough heat for one — starting when the bathroom signal came on in the gap, else in the middle of it —
  else as other hot water. Before, it was a bath that filled in seconds. Its length comes from the heat, drawn a
  little towards your usual shower length (tested on real showers: typically 18 % off, at most 28 %) A quiet heat pump at night (up to ~20
  minutes) is no gap: the first reading of a shower after it is still followed live
- **The bath threshold follows your showers.** It was twice the model's shower minute, which leans on the cold water
  estimate: with cold water estimated a few degrees too warm it came close to how fast a real shower lowers the tank
  early on. It is now twice the measured pace of your own showers (from five of them), with a wider margin until
  then, and a shower only turns into a fill once 4 K has left the tank
- What the tank lost by itself during a gap is not counted as hot water used

### Improvements
- **A bath counts as a session**: in the shower log (kind `bath`), *Showers today*, the costs and the totals, in
  shower minutes of heat (≈ on the card, "bath" under the time), with `duration` for how long it ran. *Since
  reheat* counts it as showered; *tap / bucket* is what is left as other hot water. A bucket or a filled sink (less
  than six shower minutes of heat) stays other hot water
- The last-shower tile on the card marks an estimated session with ≈

### After updating
Update in HACS and restart Home Assistant. The bath threshold learns your shower pace from the next five showers.

## v0.11.4 — A closer fit in sections

**Recommended if you set a fixed height for the card in a sections dashboard** — v0.11.3 kept the card from being
cut off, but left up to one and a half rows empty below it.

### Fixes
- **Closer minimum height** (#7): the rows the card asks for at least are now worked out from measured heights
  per part, and the line with warnings only counts when there are warnings. A full card now asks for 8 rows
  instead of 9; a fixed height can still never cut it off

### After updating
Update in HACS, restart Home Assistant and refresh the dashboard. For an exact fit, set the card's height to
automatic in its *Layout* tab (or remove `rows:` from `grid_options`): then it follows the content to the pixel.

## v0.11.3 — The card keeps its height in sections

**Recommended if the card sits in a sections dashboard with a card below it** — a fixed height on the card let the
next card draw over it.

### Fixes
- **A card below no longer covers this one** (#7). A fixed height for the card in a sections view (`grid_options:
  rows`, for example 1 after resizing it in the editor) cut the card off, and the card below was drawn over it. The
  card now asks for an automatic height and tells Home Assistant how many rows it needs at least, from the parts
  it shows (more when it is half a section wide or less), so a smaller height can no longer be set

### After updating
Update in HACS and restart Home Assistant, then refresh the dashboard. A card with a fixed height grows to what it
needs; set its height to automatic in the card's *Layout* tab (or remove `rows:` from `grid_options`) to let it
follow the content exactly.

## v0.11.2 — Your COP, a better "full again at"

**Recommended if you filled in a COP, or the time until the tank is full looks too long** — a COP filled in was
ignored once it could be learned, and "full again at" ran more than 10 minutes late on a steady reheat.

### Fixes
- **A COP you fill in is now always used.** It was only a fallback, so with heat and electricity meters a learned
  value replaced it (reported on Tweakers: 3 filled in, 2.2 shown). Leave the field empty to learn it; the 2.5 the
  form used to fill in by itself counts as empty
- **The card shows the heat pump's own COP**, as its app shows it. It showed the COP per kWh that reaches the tank,
  which is lower when the heat meter counts more than arrives (pipes, coil — on a Weheat about a quarter lower). The costs
  still use that tank COP, now also for a COP you fill in (new attributes `cop_heat_pump`, `cop_source`)
- **"Full again at" follows the reheat going on now.** After 20 minutes it uses the pace this reheat shows instead
  of the learned average, which also holds slow and boosted reheats. It counts towards the usual tank average
  after a complete reheat (the median, so the weekly legionella heat-up does not push it up), not towards the top
  temperature

### After updating
Update in HACS and restart Home Assistant. Filled in a COP before? Check it under Settings → Devices & services →
Hot Water Shower Time → Configure; empty it to let it be learned.

## v0.11.1 — A bath is not a shower, top-ups on the card

**Recommended if you fill a bath, or use a bathroom signal or a water meter** — a bath could be counted as a
shower, and half of its heat as *cooled*.

### Fixes
- **A bath is no longer a shower.** With a bathroom signal or a water meter a bath was taken for a shower within a
  minute. A bath tap runs about twice a shower head's flow, so half of its heat showed up as *cooled* in *Since
  reheat* (reported on Tweakers). Hot water that leaves the tank at twice this tank's shower pace is now a bath or
  bucket being filled, also when it looked like a shower at first. It is logged as *Other hot water*, with the heat
  and cost it really took
- **Water meter:** a flow of 1.6 times the shower head's or more (when the head flow is known) is a bath tap, not a
  shower
- **Small tanks (80–120 L):** a normal shower is no longer taken for a fill — the test now follows this tank's
  shower pace instead of a fixed rate
- Baths found in the history no longer teach the shower length or flow

### Improvements
- **Since reheat** on the card has a part for *bath / tap* (by the heat it took, also while it runs). *Cooled* is the
  tank's measured standing loss instead of whatever was left over
- **Top-ups:** a partial reheat in between, say to 45 °C, keeps *Since reheat* going and shows as
  *+ reheated 11 (14:05)*. Only a reheat back to about full (90 % of the last one, or any complete reheat the next
  day) starts it again. Each top-up — when, how long, minutes added, top temperature — is in the new `top_ups`
  attribute, with `other_minutes`, `reheated_minutes` and `total_minutes`

### After updating
Update in HACS and restart Home Assistant. No settings or dashboards to change; on a phone or tablet, refresh the
dashboard once to load the new card.

## v0.11.0 — Card in the mobile apps, water meter live, warnings

**Recommended for everyone** — fixes the card missing in the Home Assistant apps (*Custom element doesn't exist*).

### Fixes
- **"Custom element doesn't exist: shower-time-card" in the mobile apps and on tablets** (#4). Since Home Assistant
  2026.9 the frontend swaps in a new element registry while it starts, and a card loaded too early stayed
  invisible. The card is now a dashboard resource when dashboards are managed in the UI (like every HACS card),
  and registers again if it was loaded early anyway. Dashboards with resources in YAML keep working as before
- **Enough for today knows your reheats:** it learns per hour when the tank is usually reheated and only warns when
  no reheat is likely before the shortage. Before, a tank reheated every afternoon showed "EMPTY BY 21:30" all
  morning (new attribute `reheat_chance`)
- **Days with Home Assistant off** (or the tank sensors silent for two hours) no longer count as days without
  showers
- The dashboard resource is left alone when it is up to date (it was rewritten on every start)

### Improvements
- **Water meter followed live:** a steady, shower-like flow (4–16 L/min for two minutes) recognises a shower in about
  half the time, and the meter's start and stop make the shower length exact. Cold water alone (a toilet, the
  washing machine) is never a shower. Works with a meter total (L, m³) or a flow sensor (L/min)
- **Warnings on the card:** a possible dripping tap or leak, raised heat loss, a late legionella heat-up or a tank
  sensor that is not reporting show as a line under the title (`show_problems: false` hides them)
- **Language and clock on the card:** times follow the 24/12-hour setting of your profile, and two new card options
  set them per card — `language` (`en`, `nl`) and `time_format` (`24`, `12`), also in the visual editor
- **Enough for today** uses the tank's measured heat loss when it is known
- **Fewer entities for a new installation:** *Showers left*, *Hot water*, *Shower temperature*, *Shower time this
  week*, *Shower time expected today*, *Showers total* and *Shower time total* start switched off (Settings →
  Entities). Existing installations keep them as they are
- **New logo:** a glossy hot-and-cold tank with a shower head, in Home Assistant, HACS and the README
- Development: tests that run the integration inside a real test Home Assistant, Dependabot, and releases
  published automatically from this changelog

### After updating
Update in HACS and restart Home Assistant. If the card was missing in the app: close the app fully and open it
again (or clear its cache) once.

## v0.10.1

- **Fix: v0.10.0 did not load on Home Assistant before mid-2025** (it imported a constant that older versions do
  not have). The dashboard resource is now found the way every supported version keeps it
- Learning uses **only real showers** for the shower length and the flow: washing up is taken off as the little heat
  it took, and heat that left without water (a heat pump starting a reheat, heat taken out through the coil) no
  longer counts as showering
- **More accurate shower length**: a running shower is held on to with a lower threshold than one is started with
  (the end of a shower lowers the tank more slowly), and with a bathroom signal a shower starts when the bathroom
  signal came on and the start threshold is lower (a full, hot tank responds slowly). A real 8–9 minute shower was
  logged as 6 minutes before, 9 now
- Large, frequently changing attributes (shower log, forecast, since-reheat overview…) are no longer written to the
  database; the card reads them live as before
- Fewer long-term statistics: *Showers left*, *Showers today*, *Shower time today* and *Shower time this week* no
  longer keep statistics (averages of daily counters say nothing; the totals cover the long run). Home Assistant
  shows a one-time notice for each with a button to remove their old statistics. *Hot water* changes in steps of
  10 L, which saves about three quarters of its database writes

## v0.10.0

- **Card on iPhone/iPad and in the mobile apps**: the card could be missing ("configuration error", not in the card
  picker) when an app showed a cached start page. The integration now also adds the card to the dashboard
  resources, like cards installed through HACS, so dashboards load it every time they open (same URL, loaded once;
  removed again with the integration). Dashboards whose resources are in YAML are unchanged
- *Hot water* (litres) uses the device class for a stored volume, so Home Assistant no longer warns about it
- **Enough for today?** Learns when the household showers (per hour, per weekday) and, after a week, compares the
  minutes still expected today with what the tank holds: *Enough for today*, *Shower time expected today* (runs out
  at, next shower) and **EMPTY BY 21:30** on the card
- **Shower or washing up?** A hot water use shows as HOT WATER first and becomes SHOWERING once enough heat has left
  at a shower's pace (threshold learned per household), or within a minute with the new optional **bathroom
  signal** (humidity, the power of the bathroom fan, a fan/light/motion that switches on; suggested automatically).
  A bath or bucket filled with hot water only is FILLING. Heat leaving without water (a heat pump starting a reheat,
  heat taken out through the coil) is not counted. Only showers go in the log; the rest is *Other hot water today*.
  Checked on 11 days of a real tank: all 8 logged showers recognised; washing up and three heat-pump events were
  not counted as showers
- **The shower going on now**: *Showering* (recognised showers only), *Current shower* (minutes and cost so far, also
  in the card's label) and a **Shower** event entity (`shower_started`, `shower_ended` with minutes, kWh and cost,
  `hot_water_used` for other use)
- **Blueprints**: hot water almost gone (flashing lights), shower timer, shower summary notification, hot water will
  not last today — with import buttons in the README
- **Totals** for long-term statistics: showers, shower time, shower electricity (energy dashboard) and cost
- **Leak and insulation watch**: the tank's heat-loss coefficient (W/K) measured in quiet, evenly hot stretches,
  against its own baseline (*Raised heat loss*, *Tank heat loss* in W), and a dripping hot tap recognised from the
  bottom cooling steadily faster than the top at night — with a notice in Settings → Repairs
- **Legionella watch**: *Last legionella heat-up* (whole tank ≥ 60 °C) and *Legionella heat-up overdue* (8 days);
  both adjustable
- **Health checks** in Settings → Repairs: tank sensor not reporting, top and bottom swapped, learning failing
- Diagnostics include the habits, the heat-loss measurements and the legionella state

## v0.9.6

- Dashboard card: **every part can be hidden** — `show_tank`, `show_temps`, `show_split`, `show_tiles`,
  `show_history`, `show_footer` — and **`compact: true`** shows only the tank and the minutes, e.g. for a wall
  tablet. A part you switch on or off yourself always wins over `compact`. All toggles are in the visual editor
  (English and Dutch labels); existing cards look exactly the same

## v0.9.5

- Dashboard card: **card-mod styling now sticks** — the card keeps its `ha-card` in place and only refreshes what is
  inside, so styles added by card-mod are no longer wiped on every update (#1). Theme variables such as
  `ha-card-background` keep working as before
- Card code runs in its own scope and is pure ASCII, so it also loads when the file is included twice or served
  without a character set (legacy frontend, manual resources)
- README: styling example

## v0.9.4

- Dashboard card now also works on **older browsers and wall tablets** (it showed an empty red error card there):
  the card is loaded in Home Assistant's legacy frontend too, and uses no syntax that older browsers cannot read

## v0.9.3

- Public release: install with one click via the **Open in HACS** button in the README, and add the integration
  with the **Add integration** button
- CI: HACS validation runs without exceptions (the logo ships in the integration's own `brand` folder)

## v0.9.2 — first release

How long can I still shower? A self-learning hot water model for Home Assistant, with its own dashboard card.

### Shower time
- Shower time (minutes), showers left, hot water in litres and **tank charge (%)** from the tank's top and bottom sensors
- Layered-tank model: the hot layer floats on cold water; "empty" is when the outlet can no longer reach shower temperature
- **Live while you shower**: counts down between sensor readings and corrects itself on every reading
- Binary sensors **Almost empty**, **Hot water in use** and **Reheating**; **Full again at** while the tank reheats

### Learning (every night at 03:45, or with the *Learn now* button)
- Heat per minute of showering, tank layering, when the tank is really empty, standing loss, reheat speed,
  typical shower length and the COP for hot water
- **Cold water temperature from a 3-week outdoor average** (learned), or a smooth monthly table
- Handles **smart controllers** (e.g. Weheat Intelligence): complete reheats to any temperature set 100 %,
  partial top-ups only raise the level and are skipped in the learning
- Starting profiles: generic, and *Weheat 200 L* (fitted on 13 months of minute data, 471 showers)

### Shower log and cost
- Showers today, shower time today / this week, last shower; the past week is filled from the history
- **Cost per shower with dynamic tariffs**: the electricity price at the moment the tank was *reheated*
  (blended per kWh of heat in the tank), with a learned or configured COP; any price sensor (€/kWh, ct/kWh, €/MWh)
- **Standing loss**: where the hot water went since the last reheat (left / showered / cooled) and what cooling costs per day

### Shower temperature
- **Automatic** by default (no need to know it); sensed from a **water meter**, a **shower-head type** or a
  **flow you fill in** (with a one-minute bucket method in the setup screen); or set it yourself

### Dashboard card (served by the integration, no separate download)
- Tank filled to the charge level, big countdown, since-reheat split, today / last shower / standing-loss tiles,
  a 24-hour graph (complete from day one, hover or touch to read values), status pills
- Follows the Home Assistant theme (light/dark), °F and US gallons, English and Dutch, visual editor

### Other
- Easy setup: reheat state, heat and electricity meters and outdoor sensor are suggested from the same device
- Robust against missing readings (e.g. −1 °C during outages) and °F sensors
- Logo (light and dark) served from the integration (Home Assistant 2026+)
- **Diagnostics download** and GitHub issue forms, so problems can be replayed exactly

---
Like it? Support the project on [Ko-fi](https://ko-fi.com/akashironl) ☕
