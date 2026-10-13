# Changelog

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
