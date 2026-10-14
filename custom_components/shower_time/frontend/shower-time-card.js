/* Hot Water Shower Time card - served by the shower_time integration. */
(() => {   // own scope: safe if the file is loaded twice as a classic script (legacy frontend + a manual resource)
const CARD_VERSION = "0.11.2";

// Plain ES2017 on purpose (no ?. / ??): wall tablets often run an older browser that gets Home Assistant's
// legacy frontend. "a ?? b" is written as nn(a, b).
const nn = (a, b) => (a === null || a === undefined ? b : a);

const T = {
  en: { title: "Shower time", min: "min", showers: "showers", shower1: "shower", water: "hot water",
        reheating: "REHEATING", inuse: "SHOWERING", hotwater: "HOT WATER", filling: "FILLING", low: "ALMOST GONE", shortat: "EMPTY BY", fullat: "full", selflearning: "self-learning",
        learning: "learning", updated: "updated", today: "Today", lastshower: "Last shower", cooling: "Standing loss",
        perday: "per day", peryear: "/ year", none: "none yet", since: "Since reheat", left: "left", showered: "showered",
        cooled: "cooled", other: "bath / tap", reheated: "reheated", last24: "Last 24 hours", max: "max", ago24: "-24 h", now: "now", yesterday: "yesterday",
        nohist: "no history yet \u2014 fills in by itself", unavailable: "Not available", cold: "cold water", cop: "COP",
        pick: "Pick the 'Shower time' sensor of the Hot Water Shower Time integration.",
        sensed: "sensed", you: "you shower at",
        e_entity: "Sensor", e_title: "Title", e_compact: "Compact (e.g. a wall tablet)", e_tank: "Tank",
        e_temps: "Temperatures", e_split: "Since reheat", e_tiles: "Today / last shower / standing loss",
        e_history: "Last 24 hours", e_footer: "Footer (flow, cold water, COP)", e_problems: "Warnings (leak, legionella)",
        e_language: "Language", e_time_format: "Time format", e_auto: "Like Home Assistant",
        p_leak: "Possible dripping hot tap or leak", p_heat_loss: "The tank loses more heat than it used to",
        p_legionella: "No legionella heat-up for {days} days", p_legionella_never: "No legionella heat-up seen",
        p_sensor: "A tank sensor is not reporting" },
  nl: { title: "Douchetijd", min: "min", showers: "douches", shower1: "douche", water: "warm water",
        reheating: "WORDT OPGEWARMD", inuse: "DOUCHT NU", hotwater: "WARM WATER", filling: "BAD / EMMER", low: "BIJNA OP", shortat: "OP OM", fullat: "vol", selflearning: "zelflerend",
        learning: "leert", updated: "bijgewerkt", today: "Vandaag", lastshower: "Laatste douche", cooling: "Afkoeling",
        perday: "per dag", peryear: "/ jaar", none: "nog geen", since: "Sinds opwarmen", left: "over", showered: "gedoucht",
        cooled: "afgekoeld", other: "bad / kraan", reheated: "bijgewarmd", last24: "Laatste 24 uur", max: "max", ago24: "-24 u", now: "nu", yesterday: "gisteren",
        nohist: "nog geen historie \u2014 vult zich vanzelf", unavailable: "Niet beschikbaar", cold: "koud water", cop: "COP",
        pick: "Kies de sensor 'Douchetijd' van de integratie Hot Water Shower Time.",
        sensed: "gemeten", you: "jullie douchen op",
        e_entity: "Sensor", e_title: "Titel", e_compact: "Compact (bijv. een wandtablet)", e_tank: "Vat",
        e_temps: "Temperaturen", e_split: "Sinds opwarmen", e_tiles: "Vandaag / laatste douche / afkoeling",
        e_history: "Laatste 24 uur", e_footer: "Onderregel (debiet, koud water, COP)", e_problems: "Waarschuwingen (lek, legionella)",
        e_language: "Taal", e_time_format: "Tijdnotatie", e_auto: "Zoals Home Assistant",
        p_leak: "Mogelijk druppelende warmwaterkraan of lek", p_heat_loss: "Het vat verliest meer warmte dan vroeger",
        p_legionella: "Al {days} dagen geen legionella-opwarming", p_legionella_never: "Geen legionella-opwarming gezien",
        p_sensor: "Een vatsensor geeft geen waarde" },
};

// Parts of the card that can be hidden with show_<part>: false. "compact: true" hides all but the tank and the
// minutes; a show_<part> set explicitly always wins (e.g. compact with show_tiles: true).
const SECTIONS = ["tank", "temps", "split", "tiles", "history", "footer", "problems"];   // problems stay in compact
const COMPACT_HIDES = ["temps", "split", "tiles", "history", "footer"];
const showDefault = (config, part) => !(config && config.compact && COMPACT_HIDES.indexOf(part) >= 0);
const shows = (config, part) => {
  const v = config ? config["show_" + part] : undefined;
  return v === undefined || v === null ? showDefault(config, part) : !!v;
};
const lang2 = (hass) => ((hass && ((hass.locale && hass.locale.language) || hass.language)) || "en").slice(0, 2);

const fmt = (v, d = 0) => (v === null || v === undefined || isNaN(v)) ? "\u2014" : Number(v).toFixed(d);

class ShowerTimeCard extends HTMLElement {
  static getConfigElement() { return document.createElement("shower-time-card-editor"); }

  static getStubConfig(hass) {
    const ent = Object.keys(hass.states).find((e) => e.startsWith("sensor.") &&
      hass.states[e].attributes && hass.states[e].attributes.typical_shower_minutes !== undefined);
    return { entity: ent || "" };
  }

  setConfig(config) {
    if (!config.entity) throw new Error("entity is required (the 'Shower time' sensor)");
    this._config = Object.assign({}, config);
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._hist = null; this._histAt = 0;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    const st = this._config && hass.states[this._config.entity];
    const key = st ? `${st.state}|${st.last_updated}|${hass.locale && hass.locale.language}` : "none";
    if (key !== this._key) { this._key = key; this._render(); }
    this._maybeFetchHistory();
  }

  _show(part) { return shows(this._config, part); }

  getCardSize() {                    // masonry views: 5 for the full card (as before), less as parts are hidden
    return 1 + (this._show("tank") || this._show("temps") ? 1 : 0) + (this._show("split") ? 1 : 0) +
      (this._show("tiles") ? 1 : 0) + (this._show("history") ? 1 : 0);
  }
  getGridOptions() { return { columns: 6, min_columns: 4 }; }

  // Language of the card: its own setting (e.g. a Dutch wall tablet on an English account), else the user's.
  _lang() {
    const own = this._config && this._config.language;
    return own && T[own] ? own : lang2(this._hass);
  }

  // Locale for numbers and dates: the user's, unless the card is set to another language.
  _locale() {
    const user = (this._hass && ((this._hass.locale && this._hass.locale.language) || this._hass.language)) || undefined;
    const own = this._config && this._config.language;
    if (own && T[own]) return user && user.slice(0, 2) === own ? user : own;
    return user;
  }

  // Clock times: 24 or 12 hours from the card's setting, else the user's profile (Profile -> Time format).
  _time(date) {
    const opts = { hour: "2-digit", minute: "2-digit" };
    const own = this._config && this._config.time_format;
    const profile = this._hass && this._hass.locale && this._hass.locale.time_format;
    const choice = own === "24" || own === "12" ? own : profile;
    if (choice === "24") opts.hour12 = false;
    if (choice === "12") opts.hour12 = true;
    return date.toLocaleTimeString(choice === "system" ? undefined : this._locale(), opts);
  }

  _t(k) {
    return (T[this._lang()] || T.en)[k];
  }

  async _maybeFetchHistory() {
    if (!this._config || !this._show("history") || !this._hass) return;
    if (Date.now() - this._histAt < 5 * 60 * 1000) return;
    this._histAt = Date.now();
    let pts = null;
    try {                                   // recomputed from the tank sensors: full from day one
      const res = await this._hass.callWS({ type: "shower_time/history", entity_id: this._config.entity, hours: 24 });
      pts = (res && res.points) || [];
    } catch (e) {
      try {                                 // fall back to the recorded history of the sensor itself
        const end = new Date(), start = new Date(end.getTime() - 24 * 3600 * 1000);
        const res = await this._hass.callWS({ type: "history/history_during_period", start_time: start.toISOString(),
          end_time: end.toISOString(), entity_ids: [this._config.entity], minimal_response: true, no_attributes: true,
          significant_changes_only: false });
        pts = ((res && res[this._config.entity]) || []).map((r) => [((r.lu || r.lc || 0) * 1000) ||
          Date.parse(r.last_changed || r.last_updated), parseFloat(r.s !== undefined ? r.s : r.state)]);
      } catch (e2) { pts = []; }
    }
    this._hist = pts.filter((p) => p[0] && !isNaN(p[1]));
    this._key = null;                       // force a redraw
    this._render();
  }

  _sparkline(w, h, color) {
    const now = Date.now(), t0 = now - 24 * 3600 * 1000;
    const pts = (this._hist || []).filter((p) => p[0] <= now);
    const x = (t) => ((Math.max(t, t0) - t0) / (now - t0)) * w;
    const base = `<line x1="0" x2="${w}" y1="${h - 1}" y2="${h - 1}" stroke="var(--divider-color, rgba(127,127,127,.35))" stroke-width="1" stroke-dasharray="3 4" vector-effect="non-scaling-stroke"/>`;
    if (!pts.length) {
      this._peak = null;
      return `<div class="sparkwrap"><svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${base}</svg>
        <div class="nohist">${this._t("nohist")}</div></div>`;
    }
    const series = [...pts, [now, pts[pts.length - 1][1]]];
    const max = Math.max(10, ...series.map((p) => p[1]));
    const y = (v) => h - 2 - (v / max) * (h - 6);
    let d = `M ${x(series[0][0]).toFixed(1)} ${y(series[0][1]).toFixed(1)}`;
    for (let i = 1; i < series.length; i++) {          // step line: the value holds until the next change
      d += ` H ${x(series[i][0]).toFixed(1)} V ${y(series[i][1]).toFixed(1)}`;
    }
    const area = `${d} V ${h} H ${x(series[0][0]).toFixed(1)} Z`;
    this._peak = series.reduce((m, p) => (p[1] > m[1] ? p : m), series[0])[1];
    this._spark = { t0, now, max, series, h, color };
    return `<div class="sparkwrap hover"><div class="cursor"></div><div class="dot"></div><div class="tip"></div>
      <svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
      <defs><linearGradient id="sg" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stop-color="${color}" stop-opacity=".30"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
      ${base}<path d="${area}" fill="url(#sg)"/>
      <path d="${d}" fill="none" stroke="${color}" stroke-width="1.8" vector-effect="non-scaling-stroke"/></svg>
      <div class="axis"><span>${this._t("ago24")}</span><span>${this._t("now")}</span></div></div>`;
  }

  _deg(v) {                         // the integration works in Celsius; show Fahrenheit when Home Assistant does
    if (v === null || v === undefined || isNaN(v)) return v;
    const f = this._hass && this._hass.config && this._hass.config.unit_system && this._hass.config.unit_system.temperature === "\u00b0F";
    return f ? v * 9 / 5 + 32 : v;
  }

  _flow(f) {
    const g = this._hass && this._hass.config && this._hass.config.unit_system && this._hass.config.unit_system.volume === "gal";
    return g ? `${fmt(f / 3.785, 1)} gal/min` : `${fmt(f, 1)} L/min`;
  }

  _vol(l) {                         // litres, or US gallons when Home Assistant uses them
    const g = this._hass && this._hass.config && this._hass.config.unit_system && this._hass.config.unit_system.volume === "gal";
    return g ? `${fmt(l / 3.785)} gal` : `${fmt(l)} L`;
  }

  _water(top) {                      // liquid colour from the top temperature (Celsius)
    if (top === null || top === undefined) return ["#9aa7b4", "#c3ccd6"];
    if (top >= 50) return ["#ff5a36", "#ffab4d"];
    if (top >= 42) return ["#f5a524", "#ffd27a"];
    return ["#3d8fe6", "#7cc4ff"];
  }

  _tank(a, pct) {
    const W = 76, H = 132, x = 6, y = 6, w = 64, h = 120, r = 22;
    const lvl = Math.max(0, Math.min(100, nn(pct, 0)));
    const ly = y + h - (h * lvl) / 100;                 // liquid surface
    const [c1, c2] = this._water(a.top_temperature);
    const wave = `M ${x} ${ly} q 8 -5 16 0 t 16 0 t 16 0 t 16 0 V ${y + h} H ${x} Z`;
    const anim = a.reheating ? `<animateTransform attributeName="transform" type="translate" values="0 0;-16 0;0 0" dur="3s" repeatCount="indefinite"/>` : "";
    return `<svg class="tank" viewBox="0 0 ${W} ${H}" aria-hidden="true">
      <defs>
        <linearGradient id="liq" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${c1}"/><stop offset="1" stop-color="${c2}"/></linearGradient>
        <clipPath id="clip"><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}"/></clipPath>
      </defs>
      <rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="var(--secondary-background-color, rgba(127,127,127,.12))"/>
      <g clip-path="url(#clip)"><g>${anim}<path d="${wave} M ${x + w} ${ly} q 8 -5 16 0 V ${y + h} H ${x + w} Z" fill="url(#liq)"/></g></g>
      <rect x="${x + 9}" y="${y + 14}" width="6" height="${h - 28}" rx="3" fill="#fff" opacity=".22"/>
      <rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="none" stroke="var(--divider-color, rgba(127,127,127,.35))" stroke-width="1.5"/>
      <text x="${x + w / 2}" y="${y + h / 2 + 6}" class="tpct">${pct === null || pct === undefined ? "\u2014" : Math.round(lvl) + "%"}</text>
    </svg>`;
  }

  // What the hot water use going on looks like. Older versions of the integration only said "showering".
  _useKind(a) {
    return a.hot_water_use ? (a.hot_water_use.kind || "unknown") : "shower";
  }

  _usePill(a) {
    const kind = this._useKind(a);
    if (kind === "shower") {
      return `<span class="pill use"><ha-icon icon="mdi:shower-head"></ha-icon>${this._t("inuse")}${a.current_shower ? ` \u00b7 ${Math.floor(a.current_shower.minutes)} ${this._t("min")}` : ""}</span>`;
    }
    if (kind === "fill") {
      return `<span class="pill water"><ha-icon icon="mdi:bathtub-outline"></ha-icon>${this._t("filling")}</span>`;
    }
    return `<span class="pill water"><ha-icon icon="mdi:water-outline"></ha-icon>${this._t("hotwater")}</span>`;
  }

  // Things that need attention (a possible leak, raised heat loss, legionella, a silent sensor), from the integration.
  _problems(a) {
    const list = Array.isArray(a.problems) ? a.problems : [];
    if (!list.length) return "";
    const text = (p) => {
      if (p.key === "legionella") {
        return p.days === null || p.days === undefined ? this._t("p_legionella_never") : this._t("p_legionella").replace("{days}", p.days);
      }
      return this._t("p_" + p.key) || p.key;
    };
    return `<div class="problems">${list.map((p) => `<div class="problem"><ha-icon icon="mdi:alert-outline"></ha-icon><span>${text(p)}</span></div>`).join("")}</div>`;
  }

  _money(v, a, lang) {
    if (v === null || v === undefined) return null;
    return `${a.currency || "\u20ac"} ${Number(v).toLocaleString(lang, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }

  _split(a) {                        // since the last reheat: left / showered / bath or tap / cooled (+ reheated)
    const s = a.since_reheat;
    if (!s || !s.reference_minutes) return "";
    const ref = s.reference_minutes, tot = s.total_minutes || ref;
    const p = (v) => Math.max(0, Math.min(100, ((v || 0) / tot) * 100));
    const other = s.other_minutes || 0, added = s.reheated_minutes || 0;
    const topAt = s.last_top_up_at ? ` (${this._time(new Date(s.last_top_up_at))})` : "";
    const lang = this._locale();
    const t = this._time(new Date(s.since));
    const day = new Date(s.since).toDateString() === new Date().toDateString() ? "" :
      `${new Date(s.since).toLocaleDateString(lang, { weekday: "short" })} `;
    return `<div class="split">
      <div class="sh"><span>${this._t("since")} ${day}${t}</span><span>${fmt(ref)} ${this._t("min")}</span></div>
      <div class="sbar"><i class="left" style="width:${p(s.remaining_minutes)}%"></i><i class="used" style="width:${p(s.showered_minutes)}%"></i>${other >= 0.5 ? `<i class="other" style="width:${p(other)}%"></i>` : ""}<i class="cool" style="width:${p(s.cooling_minutes)}%"></i></div>
      <div class="legend"><span><b class="d left"></b>${this._t("left")} ${fmt(s.remaining_minutes)}</span>
        <span><b class="d used"></b>${this._t("showered")} ${fmt(s.showered_minutes)}</span>${other >= 0.5 ? `
        <span><b class="d other"></b>${this._t("other")} ${fmt(other)}</span>` : ""}
        <span><b class="d cool"></b>${this._t("cooled")} ${fmt(s.cooling_minutes)}</span>${added >= 0.5 || topAt ? `
        <span class="plus">+ ${this._t("reheated")} ${fmt(added)}${topAt}</span>` : ""}</div></div>`;
  }

  _tiles(a, lang) {
    const today = a.today_showers === undefined ? "" : `<div class="tile">
        <div class="tl"><ha-icon icon="mdi:calendar-today"></ha-icon>${this._t("today")}</div>
        <div class="tv">${a.today_showers} \u00d7 \u00b7 ${a.today_minutes} ${this._t("min")}</div>
        <div class="ts">${nn(this._money(a.today_cost, a, lang), "&nbsp;")}</div></div>`;
    let last = `<div class="tv">${this._t("none")}</div><div class="ts">&nbsp;</div>`;
    if (a.last) {
      const when = new Date(a.last.start);
      const day = when.toDateString() === new Date().toDateString() ? "" : `${when.toLocaleDateString(lang, { weekday: "short" })} `;
      last = `<div class="tv">${Math.round(a.last.minutes)} ${this._t("min")}${this._money(a.last.cost, a, lang) ? ` \u00b7 ${this._money(a.last.cost, a, lang)}` : ""}</div>
        <div class="ts">${day}${this._time(when)}</div>`;
    }
    const loss = a.standing_loss_per_day;
    const cool = loss ? `<div class="tile">
        <div class="tl"><ha-icon icon="mdi:thermometer-minus"></ha-icon>${this._t("cooling")}</div>
        <div class="tv">${nn(this._money(loss.cost, a, lang), `${fmt(loss.kwh_electricity, 1)} kWh`)} <small>${this._t("perday")}</small></div>
        <div class="ts">${loss.cost_year !== null && loss.cost_year !== undefined ? `\u2248 ${a.currency || "\u20ac"} ${loss.cost_year} ${this._t("peryear")}` : `${fmt(loss.kwh_heat, 1)} kWh`}</div></div>` : "";
    return `<div class="tiles">${today}<div class="tile"><div class="tl"><ha-icon icon="mdi:shower-head"></ha-icon>${this._t("lastshower")}</div>${last}</div>${cool}</div>`;
  }

  // The style and <ha-card> are created once and stay in place; renders only replace what is inside the card.
  // Styling tools such as card-mod add their own element next to <ha-card>, which would be wiped otherwise.
  _shell() {
    if (this._card && this._card.parentNode === this.shadowRoot) return this._card;
    this.shadowRoot.innerHTML = `${this._style()}<ha-card></ha-card>`;
    this._card = this.shadowRoot.querySelector("ha-card");
    this._card.addEventListener("click", () => {
      if (!this._clickable) return;
      const ev = new Event("hass-more-info", { bubbles: true, composed: true });
      ev.detail = { entityId: this._config.entity };
      this.dispatchEvent(ev);
    });
    return this._card;
  }

  _render() {
    if (!this.shadowRoot || !this._config) return;
    const hass = this._hass;
    if (!hass) return;
    const st = hass.states[this._config.entity];
    if (!st || st.state === "unavailable" || st.state === "unknown") {
      this._clickable = false;
      this._shell().innerHTML = `<div class="na">${st ? this._t("unavailable") : this._t("pick")}</div>`;
      return;
    }
    const a = st.attributes, m = parseFloat(st.state);
    const lang = this._locale();
    const typical = a.typical_shower_minutes || 10;
    const low = nn(a.low_threshold, 10);
    const color = m < low ? "var(--error-color, #e5484d)" : (m < 2 * typical ? "var(--warning-color, #f5a524)" : "var(--success-color, #30a46c)");
    const pct = nn(a.fill_percent, a.full_reference_minutes ? Math.round((m / a.full_reference_minutes) * 100) : null);
    const hhmm = (iso) => iso ? this._time(new Date(iso)) : null;
    const fullAt = hhmm(a.reheat_full_at);
    const pill = a.reheating ? `<span class="pill heat"><ha-icon icon="mdi:fire"></ha-icon>${this._t("reheating")}${fullAt ? ` \u00b7 ${this._t("fullat")} ${fullAt}` : ""}</span>`
      : (m < low ? `<span class="pill low"><ha-icon icon="mdi:alert"></ha-icon>${this._t("low")}</span>`
        : (a.showering && this._useKind(a) !== "coil" ? this._usePill(a)
          : (a.forecast && a.forecast.enough === false && a.forecast.runs_out_at
            ? `<span class="pill short"><ha-icon icon="mdi:clock-alert-outline"></ha-icon>${this._t("shortat")} ${hhmm(a.forecast.runs_out_at)}</span>` : "")));
    const learned = a.learned_at ? new Date(a.learned_at) : null;
    const sub = learned ? `${this._t("selflearning")} \u00b7 ${this._t("updated")} ${learned.toLocaleDateString(lang, { day: "numeric", month: "short" })}`
      : `${this._t("selflearning")} \u00b7 ${this._t("learning")}`;
    const showers = a.showers === 1 ? this._t("shower1") : this._t("showers");
    const sensed = a.shower_temperature_mode === "sensed"
      ? ` \u00b7 ${this._t("you")} ${fmt(this._deg(a.shower_temperature), 1)}\u00b0` : "";

    this._clickable = true;
    this._shell().innerHTML = `
        <div class="head">
          <div class="ico" style="--c:${color}"><ha-icon icon="mdi:shower-head"></ha-icon></div>
          <div class="ht"><div class="title">${this._config.title || this._t("title")}</div><div class="sub">${sub}</div></div>
          ${pill}
        </div>${this._show("problems") ? this._problems(a) : ""}
        <div class="hero">
          ${this._show("tank") ? this._tank(a, pct) : ""}
          <div class="main">
            <div class="big"><span class="num" style="color:${color}">${fmt(m)}</span><span class="unit">${this._t("min")}</span></div>
            <div class="what">\u2248 ${fmt(a.showers)} ${showers} \u00b7 ${this._vol(a.litres)}</div>
            ${this._show("temps") ? `<div class="temps"><span><ha-icon icon="mdi:arrow-up-thin"></ha-icon>${fmt(this._deg(a.top_temperature))}\u00b0</span>
              ${a.bottom_temperature !== null && a.bottom_temperature !== undefined ? `<span><ha-icon icon="mdi:arrow-down-thin"></ha-icon>${fmt(this._deg(a.bottom_temperature))}\u00b0</span>` : ""}</div>` : ""}
            ${this._show("split") ? this._split(a) : ""}
          </div>
        </div>
        ${this._show("tiles") ? this._tiles(a, lang) : ""}
        ${this._show("history") ? `<div class="hist"><div class="hh"><span>${this._t("last24")}</span><span class="pk"></span></div>${this._sparkline(300, 52, color)}</div>` : ""}
        ${this._show("footer") ? `<div class="foot">${this._flow(a.flow)}${sensed} \u00b7 ${this._t("cold")} \u2248 ${fmt(this._deg(a.cold_water))}\u00b0 \u00b7 ${this._t("cop")} ${fmt(a.cop_heat_pump != null ? a.cop_heat_pump : a.cop, 1)}</div>` : ""}`;
    const pk = this.shadowRoot.querySelector(".pk");
    if (pk && this._peak !== null && this._peak !== undefined) pk.textContent = `${this._t("max")} ${fmt(this._peak)} ${this._t("min")}`;
    this._bindHover();
  }

  _bindHover() {
    const wrap = this.shadowRoot.querySelector(".sparkwrap.hover");
    const d = this._spark;
    if (!wrap || !d) return;
    const svg = wrap.querySelector(".spark"), cur = wrap.querySelector(".cursor"),
      dot = wrap.querySelector(".dot"), tip = wrap.querySelector(".tip");
    const lang = this._locale();
    const valueAt = (t) => {                     // step series: the last point at or before t
      let v = null;
      for (const p of d.series) { if (p[0] <= t) v = p[1]; else break; }
      return v;
    };
    const show = (ev) => {
      const r = svg.getBoundingClientRect();
      const fx = Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width));
      const t = d.t0 + fx * (d.now - d.t0);
      const v = valueAt(t);
      if (v === null) { hide(); return; }
      const px = fx * r.width, py = (d.h - 2 - (v / d.max) * (d.h - 6)) / d.h * r.height;
      const when = new Date(t), today = new Date();
      const day = when.toDateString() === today.toDateString() ? "" :
        (new Date(today.getTime() - 86400000).toDateString() === when.toDateString() ? this._t("yesterday") + " " :
          when.toLocaleDateString(lang, { weekday: "short" }) + " ");
      tip.textContent = `${day}${this._time(when)} \u00b7 ${Math.round(v)} ${this._t("min")}`;
      cur.style.left = `${px}px`; dot.style.left = `${px}px`; dot.style.top = `${py}px`;
      dot.style.background = d.color;
      wrap.classList.add("on");
      const tw = tip.offsetWidth;
      tip.style.left = `${Math.max(0, Math.min(r.width - tw, px - tw / 2))}px`;
    };
    const hide = () => wrap.classList.remove("on");
    wrap.addEventListener("pointermove", show);
    wrap.addEventListener("pointerdown", (ev) => { show(ev); try { wrap.setPointerCapture(ev.pointerId); } catch (e) {} });
    wrap.addEventListener("pointerleave", hide);
    wrap.addEventListener("pointerup", (ev) => { if (ev.pointerType !== "mouse") setTimeout(hide, 1500); });
    wrap.addEventListener("click", (ev) => ev.stopPropagation());   // looking at the graph should not open the dialog
  }

  _style() {
    return `<style>
      ha-card { padding: 16px; cursor: pointer; overflow: hidden; }
      .na { color: var(--secondary-text-color); }
      .head { display: flex; align-items: center; gap: 12px; }
      .problems { margin-top: 12px; display: flex; flex-direction: column; gap: 4px; }
      .problem { display: flex; align-items: center; gap: 8px; font-size: 12px; font-weight: 600; padding: 6px 10px;
                 border-radius: 10px; color: var(--warning-color, #f5a524); background: rgba(245,165,36,.12);
                 background: color-mix(in srgb, var(--warning-color, #f5a524) 12%, transparent); }
      .problem ha-icon { --mdc-icon-size: 16px; flex: none; }
      .ico { width: 38px; height: 38px; border-radius: 12px; display: grid; place-items: center; flex: none;
             background: rgba(127,127,127,.14); background: color-mix(in srgb, var(--c) 16%, transparent); color: var(--c); }
      .ico ha-icon { --mdc-icon-size: 22px; }
      .ht { flex: 1; min-width: 0; }
      .title { font-size: 16px; font-weight: 600; color: var(--primary-text-color); line-height: 1.2; }
      .sub { font-size: 12px; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .pill { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; font-weight: 700; letter-spacing: .02em;
              padding: 4px 10px 4px 8px; border-radius: 999px; flex: none; white-space: nowrap; }
      .pill ha-icon { --mdc-icon-size: 14px; }
      .pill.heat { background: rgba(168,85,247,.16); background: color-mix(in srgb, #a855f7 16%, transparent); color: #a855f7; }
      .pill.use { background: rgba(61,143,230,.16); background: color-mix(in srgb, #3d8fe6 16%, transparent); color: #3d8fe6; animation: pulse 1.6s ease-in-out infinite; }
      .pill.water { background: rgba(127,127,127,.16); background: color-mix(in srgb, var(--secondary-text-color, #888) 16%, transparent); color: var(--secondary-text-color, #888); }
      .pill.short { background: rgba(245,165,36,.16); background: color-mix(in srgb, var(--warning-color, #f5a524) 16%, transparent); color: var(--warning-color, #f5a524); }
      .pill.low { background: rgba(229,72,77,.16); background: color-mix(in srgb, var(--error-color, #e5484d) 16%, transparent); color: var(--error-color, #e5484d); }
      @keyframes pulse { 50% { opacity: .55; } }
      .hero { display: flex; align-items: center; gap: 18px; margin-top: 14px; }
      .tank { width: 76px; flex: none; }
      .tpct { font-size: 15px; font-weight: 700; fill: #fff; text-anchor: middle; paint-order: stroke; stroke: rgba(0,0,0,.25); stroke-width: 3px; }
      .main { flex: 1; min-width: 0; }
      .big { display: flex; align-items: baseline; gap: 6px; }
      .num { font-size: 52px; font-weight: 700; line-height: .95; letter-spacing: -1.5px; }
      .unit { font-size: 18px; color: var(--secondary-text-color); font-weight: 500; }
      .what { font-size: 14px; color: var(--primary-text-color); margin-top: 6px; }
      .temps { display: flex; gap: 12px; font-size: 12px; color: var(--secondary-text-color); margin-top: 4px; }
      .temps span { display: inline-flex; align-items: center; }
      .temps ha-icon { --mdc-icon-size: 16px; }
      .split { margin-top: 12px; }
      .sh { display: flex; justify-content: space-between; font-size: 11px; color: var(--secondary-text-color); margin-bottom: 4px; }
      .sbar { display: flex; height: 8px; border-radius: 999px; overflow: hidden; background: var(--divider-color, rgba(127,127,127,.2)); }
      .sbar i { display: block; height: 100%; }
      .left { background: var(--success-color, #30a46c); }
      .used { background: #3d8fe6; }
      .other { background: #2bb5c8; }
      .cool { background: #f5a524; background: repeating-linear-gradient(45deg, #f5a524 0 4px, color-mix(in srgb, #f5a524 55%, transparent) 4px 8px); }
      .legend { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 11px; color: var(--secondary-text-color); margin-top: 5px; }
      .legend span { display: inline-flex; align-items: center; gap: 5px; white-space: nowrap; }
      .d { width: 8px; height: 8px; border-radius: 2px; display: inline-block; }
      .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(92px, 1fr)); gap: 8px; margin-top: 16px; }
      .tile { background: var(--secondary-background-color, rgba(127,127,127,.08)); border-radius: 12px; padding: 9px 11px; min-width: 0; }
      .tl { display: flex; align-items: center; gap: 5px; font-size: 11px; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .tl ha-icon { --mdc-icon-size: 14px; }
      .tv { font-size: 15px; font-weight: 600; color: var(--primary-text-color); margin-top: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .tv small { font-size: 11px; font-weight: 400; color: var(--secondary-text-color); }
      .ts { font-size: 11px; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .hist { margin-top: 16px; }
      .hh { display: flex; justify-content: space-between; font-size: 11px; color: var(--secondary-text-color); margin-bottom: 4px; }
      .sparkwrap { position: relative; }
      .sparkwrap.hover { cursor: crosshair; touch-action: pan-y; }
      .spark { width: 100%; height: 52px; display: block; }
      .nohist { position: absolute; top: 0; right: 0; bottom: 0; left: 0; display: grid; place-items: center; font-size: 11px; color: var(--secondary-text-color); }
      .axis { display: flex; justify-content: space-between; font-size: 10px; color: var(--secondary-text-color); margin-top: 2px; }
      .cursor { position: absolute; top: 0; height: 52px; width: 1px; background: var(--secondary-text-color); opacity: 0; pointer-events: none; }
      .dot { position: absolute; width: 8px; height: 8px; margin: -4px 0 0 -4px; border-radius: 50%; opacity: 0; pointer-events: none;
             box-shadow: 0 0 0 2px var(--card-background-color, var(--ha-card-background, #1c1c1c)); }
      .tip { position: absolute; top: -24px; padding: 3px 8px; border-radius: 8px; font-size: 11px; font-weight: 600; white-space: nowrap;
             background: var(--primary-text-color); color: var(--card-background-color, var(--ha-card-background, #1c1c1c));
             opacity: 0; pointer-events: none; transition: opacity .12s; }
      .sparkwrap.on .cursor { opacity: .5; } .sparkwrap.on .dot, .sparkwrap.on .tip { opacity: 1; }
      .foot { font-size: 11px; color: var(--secondary-text-color); margin-top: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    </style>`;
  }
}

class ShowerTimeCardEditor extends HTMLElement {
  setConfig(config) { this._config = config; this._render(); }
  set hass(hass) { this._hass = hass; this._render(); }

  // The toggles show what the card really shows. Switching "compact" resets the parts to its preset, and a part
  // that matches the preset is left out of the YAML, so the configuration stays short.
  _changed(value) {
    const prev = this._config || {};
    const v = Object.assign({}, value);
    if (!!v.compact !== !!prev.compact) SECTIONS.forEach((p) => { delete v["show_" + p]; });
    else SECTIONS.forEach((p) => { if (v["show_" + p] === showDefault(v, p)) delete v["show_" + p]; });
    if (!v.compact) delete v.compact;
    if (!v.title) delete v.title;
    if (!v.language || v.language === "auto") delete v.language;
    if (!v.time_format || v.time_format === "auto") delete v.time_format;
    this._config = v;
    this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: v }, bubbles: true, composed: true }));
  }

  _render() {
    if (!this._hass || !this._config) return;
    const t = T[lang2(this._hass)] || T.en;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (s) => t["e_" + s.name.replace("show_", "")] || s.name;
      this._form.addEventListener("value-changed", (ev) => this._changed(ev.detail.value));
      this.appendChild(this._form);
    }
    const data = Object.assign({}, this._config, { compact: !!this._config.compact,
      language: this._config.language || "auto", time_format: this._config.time_format || "auto" });
    SECTIONS.forEach((p) => { data["show_" + p] = shows(this._config, p); });
    this._form.hass = this._hass;
    this._form.data = data;
    this._form.schema = [
      { name: "entity", required: true, selector: { entity: { filter: { integration: "shower_time", domain: "sensor" } } } },
      { name: "title", selector: { text: {} } },
      { name: "compact", selector: { boolean: {} } },
      { type: "grid", name: "", schema: [
        { name: "language", selector: { select: { mode: "dropdown", options: [
          { value: "auto", label: t.e_auto }, { value: "en", label: "English" }, { value: "nl", label: "Nederlands" }] } } },
        { name: "time_format", selector: { select: { mode: "dropdown", options: [
          { value: "auto", label: t.e_auto }, { value: "24", label: "24h (13:45)" }, { value: "12", label: "12h (1:45 PM)" }] } } }] },
      { type: "grid", name: "", schema: SECTIONS.map((p) => ({ name: "show_" + p, selector: { boolean: {} } })) },
    ];
  }
}

// Home Assistant 2026.9+ swaps window.customElements for a scoped registry (a polyfill) while it starts. A card
// defined before that swap lives only in the old registry, where dashboards no longer look ("Custom element
// doesn't exist", seen in the mobile apps). So define in the registry that is there now, and again in the new one
// as soon as it appears.
const ELEMENTS = [["shower-time-card", ShowerTimeCard], ["shower-time-card-editor", ShowerTimeCardEditor]];
const defineIn = (registry) => {
  for (const [name, cls] of ELEMENTS) {
    try {
      if (!registry.get(name)) registry.define(name, cls);
    } catch (e) { /* defined meanwhile by another copy of this file */ }
  }
};
let registry = window.customElements;
defineIn(registry);
const recheck = () => {
  if (window.customElements !== registry) {
    registry = window.customElements;
    defineIn(registry);
  }
};
if (registry.whenDefined) registry.whenDefined("home-assistant").then(recheck);
let checks = 0;
const timer = setInterval(() => { recheck(); if (++checks >= 120) clearInterval(timer); }, 250);   // the first 30 s
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "shower-time-card")) {
  window.customCards.push({ type: "shower-time-card", name: "Shower time", preview: true,
    description: "How long you can still shower \u2014 self-learning hot water estimate." });
}
console.info(`%c SHOWER-TIME-CARD %c ${CARD_VERSION} `, "background:#30a46c;color:#fff;border-radius:3px 0 0 3px",
  "background:#222;color:#fff;border-radius:0 3px 3px 0");
})();
