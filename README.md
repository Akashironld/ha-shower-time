# Hot Water Shower Time

**How long can I still shower?** A self-learning Home Assistant integration that turns the temperature sensors
of your hot water tank into minutes of showering — and gets better every night by learning from your own showers.

- 🚿 **Shower time** in minutes, **showers left** and **litres of hot water** at your shower temperature
- 🧠 **Learns by itself** from the recorder history: your shower flow, how your tank layers, the cold water
  temperature through the seasons, standing losses and when the tank really runs cold
- 🔔 **Almost empty** binary sensor for automations (e.g. blink the bathroom light orange)
- 🎨 **Dashboard card included** — a tank that shows the hot layer, the countdown, showers left and the last 24 hours.
  No extra download: it is installed together with the integration
- ⚙️ **Easy setup**: pick the top (and ideally bottom) tank sensor; reheat state and heat meter are suggested
  automatically from the same device (works out of the box with the Weheat integration)

## How it works

A hot water tank is layered: hot water floats on cold water. While you shower, cold water enters at the
bottom and pushes the hot layer out at the top. The water at the tap stays hot until that boundary
reaches the outlet — then it goes cold quickly.

With a top and a bottom sensor the integration estimates the tank's heat content, works out when the outlet
can no longer reach your shower temperature, and divides the usable heat by what one minute of showering
costs. Every night at 03:45 it looks back at the reheat cycles and showers since the last run and nudges its
parameters towards what it observed. The starting knowledge only gives way slowly, so one odd day does not
throw it off.

It was developed and validated on 13 months of minute data of a Weheat heat pump with the Weheat 200 L tank
(449 reheat cycles, 471 showers). That data is available as the *Weheat 200 L* starting profile; other tanks
start from a generic profile and learn their own behaviour.

| What it learns | From |
| --- | --- |
| Shower flow (L/min at your shower temperature) | heat drawn between two reheats vs. the minutes the bottom sensor showed cold water coming in |
| Tank layering | reheat cycles from a layered tank (needs a heat/energy sensor) |
| When the tank is really empty | reheats that start from an almost empty tank |
| Cold water temperature per month | how cold the bottom gets after long showers |
| Standing loss | quiet hours without showers or reheating |
| Typical shower length | the showers it sees |

## Shower temperature: automatic, sensed or set

The tank only feels **heat per minute** = flow × (shower temperature − cold water). A 6 L/min shower at 40 °C and a
7 L/min shower at 37 °C cost the tank exactly the same, so no calculation on tank data alone can tell them apart.
That is fine for the minutes — they only depend on the heat per minute, which is learned — so by default you do
not have to know your shower temperature at all.

| Setting | Shower temperature | Litres |
| --- | --- | --- |
| **Automatic** (default) | not needed — litres are shown at a 38 °C reference | estimated |
| + **water meter / flow sensor** | **sensed**: cold water + learned heat ÷ measured flow while you shower | measured |
| + **shower-head type** (water-saving 6 / normal 8 / rain 12 L/min) | calculated | from the type |
| + **flow filled in yourself** (e.g. 7.2 L/min — fill a 10 L bucket and time it) | calculated | from your value |
| **Set it myself** | your fixed value | estimated |

With a known flow you get an extra `sensor.<name>_shower_temperature` ("you shower at ≈ 39.4 °C"). Expect about
±1–2 °C at first; it sharpens as the model learns. A water meter in L/min or a cumulative meter in litres or m³
both work; the median over many showers keeps a toilet flush during a shower from disturbing it.

## Requirements

- Home Assistant 2025.1 or newer with the recorder enabled (default)
- A temperature sensor at the **top** of the tank. A **bottom** sensor is strongly recommended — without it the
  model cannot see showers and stays with its starting knowledge
- Optional, makes it smarter: an entity that shows the tank is being reheated, and a sensor with the heat
  (energy, kWh) delivered to the hot water

## Installation

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/OWNER/ha-shower-time`, category *Integration*
2. Install **Hot Water Shower Time** and restart Home Assistant
3. **Settings → Devices & services → Add integration → Hot Water Shower Time**

## Entities

| Entity | |
| --- | --- |
| `sensor.<name>_shower_time` | minutes of showering left; the card reads all details from its attributes |
| `sensor.<name>_showers_left` | number of showers of your typical length |
| `sensor.<name>_hot_water` | litres at your shower temperature |
| `binary_sensor.<name>_almost_empty` | on below the threshold (default 10 min, change in the options) |
| `binary_sensor.<name>_reheating` | the tank is being reheated (if a reheat entity is configured) |
| `sensor.<name>_learned_model` | diagnostic: everything learned so far |
| `button.<name>_learn_now` | learn from the history right away |

## Dashboard card

Add a card and search for **Shower time**, or use YAML:

```yaml
type: custom:shower-time-card
entity: sensor.hot_water_shower_time
show_tank: true      # tank graphic with the hot layer
show_history: true   # last 24 hours
```

## Automation example

Blink the bathroom light orange when the hot water is almost gone:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.hot_water_almost_empty
    to: "on"
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

## Nederlands

Hoe lang kan ik nog douchen? Deze integratie rekent de temperatuursensoren van je boiler om naar minuten
douchen en leert elke nacht van jullie eigen douches: het debiet, hoe de boiler gelaagd is, de
koudwatertemperatuur per seizoen en wanneer de boiler echt leeg is. Inclusief dashboardkaart. Volledig
Nederlandstalig in Home Assistant.

## License

MIT
