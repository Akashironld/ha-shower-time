<img src="assets/icon.png" alt="Hot Water Shower Time" width="128" align="right">

# Hot Water Shower Time

<a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=Akashironld&repository=ha-shower-time&category=integration"><img src="https://my.home-assistant.io/badges/hacs_repository.svg" alt="Open in HACS" height="36"></a>
<a href="https://ko-fi.com/akashironl/?hidefeed=true&widget=true&embed=true"><img src="https://storage.ko-fi.com/cdn/kofi2.png?v=3" alt="Buy me a coffee on Ko-fi" height="36"></a>

**How long can I still shower?** A self-learning Home Assistant integration that turns the temperature
sensors of your hot water tank into minutes of showering — and gets better every night by learning from
your own showers.

<p align="center"><img src="assets/card-en.png" alt="The Shower time dashboard card" width="460"></p>

- 🚿 **Shower time**, **showers left**, **hot water in litres** and **tank charge (%)**
- ⏱️ **Live while you shower** — counts down between sensor readings and corrects itself on every reading
- 🧠 **Learns by itself**: your shower flow, how your tank layers, when it really runs cold, standing
  losses, how fast it reheats, and the cold water temperature from the outdoor temperature
- 📒 **Shower log**: showers today, shower time today / this week, last shower
- 💶 **Cost per shower** — also with **dynamic tariffs**: the price of the electricity at the moment the
  tank was *reheated* is used, not the price when you shower
- 🔔 **Almost empty**, **hot water in use** and **full again at** for automations
- 🎨 **Dashboard card included** (no separate download): tank graphic with the hot layer, countdown,
  showers left, a 24-hour graph you can hover/touch, and today's showers with cost
- ⚙️ **Easy setup**: pick the tank sensors; the rest is suggested from the same device
  (works out of the box with the Weheat integration)

## How it works

A hot water tank is layered: hot water floats on cold water. While you shower, cold water enters at the
bottom and pushes the hot layer out at the top. The water at the tap stays hot until that boundary reaches
the outlet — then it goes cold quickly.

From the top and bottom sensor the integration estimates the heat in the tank, works out when the outlet
can no longer reach shower temperature, and divides the usable heat by what one minute of showering costs.
Every night at 03:45 it looks back at the reheat cycles and showers since the last run and nudges its
parameters towards what it saw. The starting knowledge only gives way slowly, so one odd day does not throw
it off.

It was developed and validated on 13 months of minute data of a Weheat heat pump with the Weheat 200 L
tank (449 reheat cycles, 471 showers). That data is available as the *Weheat 200 L* starting profile;
other tanks start from a generic profile and learn their own behaviour.

| What it learns | From |
| --- | --- |
| Heat per minute of showering (≈ shower flow) | heat drawn between two full reheats vs. the minutes cold water was coming in |
| Tank layering | reheats from a layered tank (needs a heat/energy sensor) |
| When the tank is really empty | reheats that start from an almost empty tank |
| Cold water temperature | how cold the bottom gets in long showers, against a 3-week outdoor average |
| Standing loss | quiet hours without showers or reheating |
| Reheat speed | every reheat |
| Typical shower length | the showers it sees |
| COP for hot water | heat and electricity meters over 30 days (optional) |

### Smart heat pump controllers

Controllers that heat the tank to different temperatures or top it up during the day (e.g. Weheat
Intelligence) are handled: a **complete** reheat (top and bottom within 2.5 °C) sets the new 100 %, also
when it heats to a lower temperature than last time; a **partial** top-up only raises the level. The
learning skips periods with a top-up in between, so it does not learn a too-low flow.

### Cold water temperature

Mains water follows the ground, which follows the weather weeks later: a few warm days hardly matter, a
cold month does. With an outdoor temperature sensor the model uses **cold water ≈ a + b × 3-week outdoor
average** and learns *a* and *b* from long showers (the slope only once the data spans the seasons).
Without one it uses a learned monthly table that changes smoothly between months.

## Shower temperature: automatic, sensed or set

The tank only feels **heat per minute** = flow × (shower temperature − cold water). A 6 L/min shower at
40 °C and a 7 L/min shower at 37 °C cost the tank exactly the same, so no calculation on tank data alone
can tell them apart. That is fine for the minutes — they only depend on the heat per minute, which is
learned — so by default you do not have to know your shower temperature at all.

| Setting | Shower temperature | Litres |
| --- | --- | --- |
| **Automatic** (default) | not needed — litres are shown at a 38 °C reference | estimated |
| + **water meter / flow sensor** | **sensed**: cold water + learned heat ÷ measured flow while you shower | measured |
| + **shower-head type** (water-saving 6 / normal 8 / rain 12 L/min) | calculated | from the type |
| + **flow filled in yourself** | calculated | from your value |
| **Set it myself** | your fixed value | estimated |

With a known flow you get an extra `sensor.<name>_shower_temperature` ("you shower at ≈ 39.4 °C"). Expect
about ±1–2 °C at first.

### Measuring your shower-head flow (one minute, one bucket)

1. Turn the shower on the way you normally shower — same temperature, same tap position.
2. Hold a bucket under the shower head and time how many seconds it takes to collect **10 litres**.
3. Flow in L/min = **600 ÷ seconds** (50 s = 12, 75 s = 8, 100 s = 6 L/min).

No bucket with litre marks? Catch the water for 10 seconds, measure it with a jug and multiply by 6.

## Cost per shower

Choose any electricity price sensor in the options (€/kWh, ct/kWh or €/MWh; from your supplier, Nord Pool,
Tibber, evcc, …). **Use an all-in price** (including taxes) — a bare spot price gives far too low costs.

With dynamic tariffs you pay when the tank is **reheated**. The integration keeps a price for the hot water
in the tank: heat a reheat adds is booked at the price of that moment ÷ COP, blended with the heat already
in the tank. A shower costs the heat it takes out × that price. The COP is learned from the heat and
electricity meters for hot water when available (both suggested automatically for Weheat), otherwise the
value you set (heat pump ≈ 2–3.5, electric boiler 1.0).

## Requirements

- Home Assistant 2025.1 or newer with the recorder (default). The logo shows from Home Assistant 2026.
- A temperature sensor at the **top** of the tank. A **bottom** sensor is strongly recommended: without it
  the model cannot see showers, so the live countdown, the shower log and most of the learning do not work.
- Optional, each makes it smarter: an entity that shows the tank is being reheated, a heat (energy) sensor
  for hot water, an electricity sensor for hot water, an outdoor temperature sensor, a water meter, a price
  sensor.
- °C and °F sensors both work.

## Installation

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Akashironld&repository=ha-shower-time&category=integration)

1. Click the button above (or in HACS: ⋮ → **Custom repositories** → add
   `https://github.com/Akashironld/ha-shower-time`, category *Integration*)
2. **Download** Hot Water Shower Time and restart Home Assistant
3. Add the integration — click the button below, or **Settings → Devices & services → Add integration → Hot Water
   Shower Time**

[![Add the integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=shower_time)

## Configuration

The setup has two screens. Only the first one needs input; the second is filled in for you where possible
(for Weheat everything is suggested) and can be left as it is.

### Screen 1 — the tank

| Setting | | What it is for | Weheat example |
| --- | --- | --- | --- |
| **Name** | required | Name of the device and the start of the entity ids | `Hot water` |
| **Top temperature sensor** | **required** | Temperature at the top of the tank (the outlet side) | `sensor.…_dhw_top_temperature` |
| **Bottom temperature sensor** | strongly recommended | Sees cold water coming in while you shower. Without it: no live countdown, no shower log and hardly any learning | `sensor.…_dhw_bottom_temperature` |
| **Tank volume** | **required** | Litres, from the type plate | `200` |
| **Shower temperature** | required, default *Automatic* | *Automatic* needs nothing; *Set it myself* uses the fixed value below it | Automatic |

### Screen 2 — make it smarter (all optional)

| Setting | What it adds | Without it | Weheat example |
| --- | --- | --- | --- |
| **Starting knowledge** | Where the model starts before it has learned from your showers | *Generic tank* | *Weheat 200 L* (suggested automatically) |
| **Entity that shows reheating** + **states** | Exact reheat moments, "Reheating" sensor, "Full again at" | Reheats are recognised from the temperature rising | `sensor.blackbird_p80_heat_pump` with `dhw, legionella_prevention` |
| **Heat delivered to hot water** (energy) | Learns how your tank layers; more precise flow | Fixed layering from the starting knowledge | `sensor.…_energy_output_dhw` |
| **Outdoor temperature sensor** | Cold water temperature from the 3-week outdoor average | Learned monthly table | `sensor.…_outside_temperature` |
| **Electricity used for hot water** (energy) | Learns the COP for the cost per shower | The COP you fill in | `sensor.…_electricity_used_dhw` |
| **COP for hot water** | Used when the COP cannot be learned | 2.5 | heat pump ≈ 2–3.5, electric boiler 1.0 |
| **Electricity price sensor** | Cost per shower and per day (use an all-in price) | Only kWh, no money | your supplier, Nord Pool, Tibber, evcc… |
| **Shower-head flow** | *Water meter*, *type* or *fill in myself*: litres and the temperature you shower at | Litres at a 38 °C reference | *I don't know* |
| **Flow, filled in yourself** | Only with *Fill in myself* (bucket method above) | — | `7.2` |
| **Water meter or flow sensor** | Only with *Water meter*: measures the shower-head flow | — | — |

### Change it later

**Settings → Devices & services → Hot Water Shower Time → Configure** lets you change everything except the name,
the top sensor and the starting knowledge, plus the **"Almost empty" threshold** (default 10 minutes). The model keeps what it learned.
To start learning from scratch, remove the integration and add it again.

### Good to know

- The first estimate is there right away; the first learning run follows two minutes after setup and then every
  night at 03:45 (or press **Learn now**).
- The shower log is filled with the past week from your history on the first start.
- The card is added from the dashboard editor: search for **Shower time**.

## Entities

| Entity | |
| --- | --- |
| `sensor.<name>_shower_time` | minutes of showering left; the card reads all details from its attributes |
| `sensor.<name>_showers_left` | showers of your typical length |
| `sensor.<name>_hot_water` | litres at shower temperature |
| `sensor.<name>_tank_charge` | % — 100 % right after the last complete reheat |
| `sensor.<name>_showers_today`, `_shower_time_today`, `_shower_time_this_week`, `_last_shower` | shower log |
| `sensor.<name>_shower_cost_today` | cost of today's showers (with a price sensor) |
| `sensor.<name>_full_again_at` | while reheating: when the tank is full again (attribute: enough for one shower) |
| `sensor.<name>_shower_temperature` | with a known shower-head flow or a fixed temperature |
| `binary_sensor.<name>_almost_empty` | below the threshold (default 10 min) |
| `binary_sensor.<name>_hot_water_in_use` | someone is showering / drawing hot water now |
| `binary_sensor.<name>_reheating` | the tank is being reheated (with a reheat entity) |
| `sensor.<name>_learned_model` | diagnostic: everything learned so far |
| `button.<name>_learn_now` | learn from the history right away |

## Dashboard card

Add a card and search for **Shower time**, or use YAML:

```yaml
type: custom:shower-time-card
entity: sensor.hot_water_shower_time
show_tank: true      # tank graphic with the hot layer
show_history: true   # last 24 hours (hover or touch to read values)
```

The graph is filled from the tank sensors' history, so it is complete from the first day. Hover or touch it to read
the value at any moment:

<p align="center"><img src="assets/card-en-hover.png" alt="Card with the graph tooltip" width="460"></p>

What you see: the tank filled to its charge level, the minutes of showering left, roughly how many showers and
litres that is, where the hot water went since the last reheat (left / showered / cooled), today's showers and
their cost, the last shower, what standing loss costs per day, and the last 24 hours.

**Styling:** the card is a standard `ha-card`, so your theme (`ha-card-background`, `ha-card-border-radius`,
`ha-card-box-shadow`, …) applies, and [card-mod](https://github.com/thomasloven/lovelace-card-mod) works as on any
other card:

```yaml
type: custom:shower-time-card
entity: sensor.hot_water_shower_time
card_mod:
  style: |
    ha-card {
      background-color: rgba(36, 37, 41, 0.3);
      box-shadow: none;
      border: none;
    }
```

## Automation example

Blink the bathroom light orange when the hot water is almost gone during a shower:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.hot_water_almost_empty
    to: "on"
conditions:
  - condition: state
    entity_id: binary_sensor.hot_water_hot_water_in_use
    state: "on"
actions:
  - action: scene.create
    data: { scene_id: before_blink, snapshot_entities: [light.bathroom] }
  - repeat:
      count: 3
      sequence:
        - action: light.turn_on
          target: { entity_id: light.bathroom }
          data: { rgb_color: [255, 140, 0], brightness_pct: 100 }
        - delay: 0.6
        - action: light.turn_off
          target: { entity_id: light.bathroom }
        - delay: 0.4
  - action: scene.turn_on
    target: { entity_id: scene.before_blink }
mode: single
```

## Reporting a problem

Open an [issue](https://github.com/Akashironld/ha-shower-time/issues/new/choose) and attach the **diagnostics
file**: *Settings → Devices & services → Hot Water Shower Time → ⋮ → Download diagnostics*. It contains the
settings, what has been learned and the tank readings of the last 24 hours (no passwords or personal data), so
your situation can be replayed exactly.

## Limitations

- Without a bottom sensor showers are hard to see: no live countdown or log, little learning.
- Costs are in electricity; a gas-heated tank is not supported for cost.
- The cold water starting values are for the Netherlands/Belgium/Germany; elsewhere the model learns the
  local values, but starts a few degrees off.
- Live updates follow your sensors: with readings every few minutes the countdown runs on its own for up to
  4 minutes between readings.

## Nederlands

Hoe lang kan ik nog douchen? Deze integratie rekent de temperatuursensoren van je boiler om naar minuten
douchen en leert elke nacht van jullie eigen douches: het debiet, hoe de boiler gelaagd is, de
koudwatertemperatuur (uit de buitentemperatuur), wanneer de boiler echt leeg is en hoe snel hij opwarmt.
Met live aftellen tijdens het douchen, een douchelogboek, kosten per douche (ook bij een dynamisch tarief:
de prijs op het moment van opwarmen telt), vulgraad, "weer vol om" en een dashboardkaart. Volledig
Nederlandstalig in Home Assistant.

<p align="center"><img src="assets/card-nl.png" alt="De kaart Douchetijd" width="420"></p>

**Installeren:** klik op de knop *Open in HACS* bovenaan (of HACS → ⋮ → **Aangepaste repositories** →
`https://github.com/Akashironld/ha-shower-time`, categorie *Integratie*), download, herstart Home Assistant en
voeg de integratie toe via **Instellingen → Apparaten en diensten → Integratie toevoegen → Hot Water Shower Time**.

**Instellen:** alleen de **temperatuursensor bovenin** en de **inhoud van de boiler** zijn verplicht. Een **sensor
onderin** is sterk aanbevolen (zonder ziet het model geen douches). Al het andere is optioneel en wordt bij Weheat
automatisch voorgesteld: opwarmstand, warmte- en stroommeter voor warm water, buitentemperatuur, prijssensor en het
debiet van de douchekop. Achteraf wijzigen kan via **Instellingen → Apparaten en diensten → Hot Water Shower Time →
Configureren**.

Debiet van je douchekop meten: zet de douche aan zoals je normaal doucht, houd een emmer eronder en kijk
hoeveel seconden het duurt tot er 10 liter in zit. Debiet = 600 ÷ seconden (75 s = 8 L/min).

## Support

Hot Water Shower Time is free and open source. If it saves you a cold shower (or some money) and you'd like to
say thanks, you can buy me a coffee ☕ — the button opens the Ko-fi tip panel straight away, no account needed:

<a href="https://ko-fi.com/akashironl/?hidefeed=true&widget=true&embed=true"><img src="https://storage.ko-fi.com/cdn/kofi2.png?v=3" alt="Buy me a coffee on Ko-fi" height="36"></a>

Vind je het handig? Een koffie via [Ko-fi](https://ko-fi.com/akashironl/?hidefeed=true&widget=true&embed=true) wordt zeer gewaardeerd. ☕

## License

MIT
