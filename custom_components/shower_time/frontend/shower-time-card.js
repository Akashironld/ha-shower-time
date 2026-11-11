/* Hot Water Shower Time card - served by the shower_time integration. */
(() => {   // own scope: safe if the file is loaded twice as a classic script (legacy frontend + a manual resource)
const CARD_VERSION = "0.15.0";

// Plain ES2017 on purpose (no ?. / ??): wall tablets often run an older browser that gets Home Assistant's
// legacy frontend. "a ?? b" is written as nn(a, b).
let UID = 0;   // per-instance suffix for SVG ids (several cards on one page)
const nn = (a, b) => (a === null || a === undefined ? b : a);

const T = {
  en: { title: "Shower time", min: "min", showers: "showers", shower1: "shower", water: "hot water",
        reheating: "REHEATING", inuse: "SHOWERING", hotwater: "HOT WATER", filling: "FILLING", low: "ALMOST GONE", shortat: "EMPTY BY", fullat: "full", enough: "enough for today", nextshower: "next shower", minleft: "min left", last24h: "last 24 h", tap: "tap", reheat: "reheat", topbottom: "top / bottom", flowl: "flow", showerat: "shower at", selflearning: "self-learning",
        learning: "learning", updated: "updated", today: "Today", lastshower: "Last shower", cooling: "Standing loss",
        perday: "per day", peryear: "/ year", none: "none yet", since: "Since reheat", left: "left", showered: "showered",
        cooled: "cooled", other: "tap / bucket", bathtap: "bath / tap", bath: "bath", reheated: "reheated", last24: "Last 24 hours", max: "max", ago24: "-24 h", now: "now", yesterday: "yesterday",
        nohist: "no history yet \u2014 fills in by itself", unavailable: "Not available", cold: "cold water", cop: "COP",
        pick: "Pick the 'Shower time' sensor of the Hot Water Shower Time integration.",
        sensed: "sensed", you: "you shower at",
        e_entity: "Sensor", e_title: "Title", e_style: "Style", e_style_hud: "New (instrument panel)", e_style_classic: "Classic", e_compact: "Compact (e.g. a wall tablet)", e_tank: "Tank",
        e_temps: "Temperatures", e_split: "Since reheat", e_tiles: "Today / last shower / standing loss",
        e_history: "Last 24 hours", e_footer: "Footer (flow, cold water, COP)", e_problems: "Warnings (leak, legionella)",
        e_language: "Language", e_time_format: "Time format", e_auto: "Like Home Assistant",
        p_leak: "Possible dripping hot tap or leak", p_heat_loss: "The tank loses more heat than it used to",
        p_legionella: "No legionella heat-up for {days} days", p_legionella_never: "No legionella heat-up seen",
        p_sensor: "A tank sensor is not reporting" },
  nl: { title: "Douchetijd", min: "min", showers: "douches", shower1: "douche", water: "warm water",
        reheating: "WORDT OPGEWARMD", inuse: "DOUCHT NU", hotwater: "WARM WATER", filling: "BAD / EMMER", low: "BIJNA OP", shortat: "OP OM", fullat: "vol", enough: "genoeg voor vandaag", nextshower: "volgende douche", minleft: "min over", last24h: "laatste 24 u", tap: "kraan", reheat: "opwarmen", topbottom: "boven / onder", flowl: "debiet", showerat: "douche op", selflearning: "zelflerend",
        learning: "leert", updated: "bijgewerkt", today: "Vandaag", lastshower: "Laatste douche", cooling: "Afkoeling",
        perday: "per dag", peryear: "/ jaar", none: "nog geen", since: "Sinds opwarmen", left: "over", showered: "gedoucht",
        cooled: "afgekoeld", other: "kraan / emmer", bathtap: "bad / kraan", bath: "bad", reheated: "bijgewarmd", last24: "Laatste 24 uur", max: "max", ago24: "-24 u", now: "nu", yesterday: "gisteren",
        nohist: "nog geen historie \u2014 vult zich vanzelf", unavailable: "Niet beschikbaar", cold: "koud water", cop: "COP",
        pick: "Kies de sensor 'Douchetijd' van de integratie Hot Water Shower Time.",
        sensed: "gemeten", you: "jullie douchen op",
        e_entity: "Sensor", e_title: "Titel", e_style: "Stijl", e_style_hud: "Nieuw (instrumentenpaneel)", e_style_classic: "Klassiek", e_compact: "Compact (bijv. een wandtablet)", e_tank: "Vat",
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

// The card keeps its state and logic in two "looks" (plain classes, not elements): the HUD panel (default) and the
// classic card of v0.14.0 (style: classic). The element only picks one per config and forwards to it.
class ShowerTimeBase {
  constructor(host) { this.host = host; }
  get shadowRoot() { return this.host.shadowRoot; }
  dispatchEvent(ev) { return this.host.dispatchEvent(ev); }

  setConfig(config) {
    if (!config.entity) throw new Error("entity is required (the 'Shower time' sensor)");
    this._config = Object.assign({}, config);
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
    let pts = null, events = [];
    try {                                   // recomputed from the tank sensors: full from day one
      const res = await this._hass.callWS({ type: "shower_time/history", entity_id: this._config.entity, hours: 24 });
      pts = (res && res.points) || [];
      events = res && Array.isArray(res.events) ? res.events : [];     // newer backends only
    } catch (e) {
      try {                                 // fall back to the recorded history of the sensor itself
        const end = new Date(), start = new Date(end.getTime() - 24 * 3600 * 1000);
        const res = await this._hass.callWS({ type: "history/history_during_period", start_time: start.toISOString(),
          end_time: end.toISOString(), entity_ids: [this._config.entity], minimal_response: true, no_attributes: true,
          significant_changes_only: false });
        pts = ((res && res[this._config.entity]) || []).map((r) => [((r.lu || r.lc || 0) * 1000) ||
          Date.parse(r.last_changed || r.last_updated), parseFloat(r.s !== undefined ? r.s : r.state)]);
      } catch (e2) { pts = []; }
      events = [];
    }
    if (this._dead) return;                 // the card switched to the other look meanwhile
    this._hist = pts.filter((p) => p[0] && !isNaN(p[1]));
    this._events = events;
    this._key = null;                       // force a redraw
    this._render();
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


  _id() { return (this._uid = this._uid || "st" + (++UID)); }


  // What the hot water use going on looks like. Older versions of the integration only said "showering".
  _useKind(a) {
    return a.hot_water_use ? (a.hot_water_use.kind || "unknown") : "shower";
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
    return `${a.currency_symbol || a.currency || "\u20ac"} ${Number(v).toLocaleString(lang, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }


  // The style and <ha-card> are created once and stay in place; renders only replace what is inside the card.
  // Styling tools such as card-mod add their own element next to <ha-card>, which would be wiped otherwise.
  _shell() {
    if (this._card && this._card.parentNode === this.shadowRoot) return this._card;
    this.shadowRoot.innerHTML = `${this._style()}<ha-card></ha-card>`;
    this._card = this.shadowRoot.querySelector("ha-card");
    this._first = true;
    this._card.addEventListener("click", () => {
      if (!this._clickable) return;
      const ev = new Event("hass-more-info", { bubbles: true, composed: true });
      ev.detail = { entityId: this._config.entity };
      this.dispatchEvent(ev);
    });
    return this._card;
  }

}

class ShowerTimeHud extends ShowerTimeBase {
  getCardSize() {                    // masonry views: 7 for the full card, less as parts are hidden
    return 1 + (this._config && this._config.compact ? 2 : 3) + (this._show("split") ? 1 : 0) +
      (this._show("tiles") ? 1 : 0) + (this._show("history") ? 1 : 0);
  }

  // Sections views: the height follows the content ("auto"). min_rows keeps a fixed height set in the editor
  // (grid_options: rows) from cutting the card off, with the next card drawn over it (#7). Heights in px measured
  // on a 360 px wide card (a row is 56 px plus an 8 px gap); a card half a section wide or less wraps more.
  getGridOptions() {
    let px = 340;                   // padding + header + hero (tank/ring, readouts when wrapped)
    if (this._config) {
      const st = this._hass && this._hass.states[this._config.entity];
      const problems = (st && st.attributes.problems && this._show("problems") && st.attributes.problems.length) || 0;
      px += problems * 30 + (this._config.compact ? -50 : 0) + (this._show("split") ? 46 : 0) +
        (this._show("tiles") ? 82 : 0) + (this._show("history") ? 120 : 0) + (this._show("footer") ? 26 : 0);
      const cols = this._config.grid_options && this._config.grid_options.columns;
      if (typeof cols === "number" && cols <= 6) px *= 1.25;
    }
    return { columns: 6, min_columns: 4, rows: "auto", min_rows: Math.ceil((px + 8) / 64) };
  }


  _sparkline(w, h) {
    const now = Date.now(), t0 = now - 24 * 3600 * 1000;
    const pts = (this._hist || []).filter((p) => p[0] <= now);
    const x = (t) => ((Math.max(t, t0) - t0) / (now - t0)) * w;
    const ln = (a, b, c, d2, extra) => `<line x1="${a}" x2="${b}" y1="${c}" y2="${d2}" stroke="${extra}" stroke-width="1" vector-effect="non-scaling-stroke"/>`;
    let grid = "";
    for (let i = 1; i <= 3; i++) {
      grid += ln(0, w, (h * i) / 4, (h * i) / 4, "var(--st-grid)") + ln((w * i) / 4, (w * i) / 4, 0, h, "var(--st-grid)");
    }
    grid += ln(0, w, h - 0.5, h - 0.5, "var(--st-border0)");
    const nowLine = `<line x1="${w - 0.5}" x2="${w - 0.5}" y1="0" y2="${h}" stroke="var(--st-hot)" stroke-width="1" stroke-dasharray="3 3" vector-effect="non-scaling-stroke"/>`;
    const BAND = { reheat: ["var(--st-violet)", ".22"], shower: ["var(--st-blue)", ".28"], bath: ["var(--st-teal)", ".2"], other: ["var(--st-teal)", ".2"] };
    const events = (this._events || []).filter((e) => e && isFinite(e.start) && isFinite(e.end) && e.end >= t0 && e.start <= now);
    const bands = events.map((e) => {
      const b = BAND[e.kind] || BAND.other, x0 = x(e.start);
      return `<rect x="${x0.toFixed(1)}" y="0" width="${Math.max(3, x(e.end) - x0).toFixed(1)}" height="${h}" fill="${b[0]}" opacity="${b[1]}"/>`;
    }).join("");
    if (!pts.length) {
      this._peak = null; this._spark = null;
      return `<div class="sparkwrap"><svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${grid}${nowLine}</svg>
        <div class="nohist">${this._t("nohist")}</div></div>`;
    }
    const series = [...pts, [now, pts[pts.length - 1][1]]];
    const max = Math.max(10, ...series.map((p) => p[1]));
    const y = (v) => h - 2 - (v / max) * (h - 6);
    let d = `M ${x(series[0][0]).toFixed(1)} ${y(series[0][1]).toFixed(1)}`;
    for (let i = 1; i < series.length; i++) {          // step line: the value holds until the next change
      d += ` H ${x(series[i][0]).toFixed(1)} V ${y(series[i][1]).toFixed(1)}`;
    }
    this._peak = series.reduce((m, p) => (p[1] > m[1] ? p : m), series[0])[1];
    this._spark = { t0, now, max, series, h, events };
    const gid = this._id() + "a", x0 = x(series[0][0]).toFixed(1);
    const area = `${d} V ${h} H ${x0} Z`;
    const lastY = ((y(series[series.length - 1][1]) / h) * 100).toFixed(1);
    return `<div class="sparkwrap hover"><div class="cursor"></div><div class="dot"></div><div class="tip"></div><div class="nowdot" style="top:${lastY}%"></div>
      <svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
      <defs><linearGradient id="${gid}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#5FE0D0" stop-opacity=".28"/><stop offset="1" stop-color="#5FE0D0" stop-opacity="0"/></linearGradient></defs>
      ${bands}${grid}${nowLine}<path d="${area}" fill="url(#${gid})"/>
      <path d="${d}" fill="none" stroke="var(--st-cyan)" stroke-width="1.5" vector-effect="non-scaling-stroke" style="filter:drop-shadow(0 0 3px var(--st-cyan))"/></svg>
      <div class="axis"><span>${this._t("ago24")}</span><span>${this._t("now")}</span></div></div>`;
  }


  // Tank as twelve stacked cells: one dashed line (cells 10.8 high, 3.6 apart) drawn in three pieces that start on
  // multiples of 14.4, so the dash pattern stays aligned: lit (hot) from the top, one mixing cell, the spent rest.
  _tank(a, share, live, scale) {
    const id = this._id(), P = 14.4, Y0 = 10;
    const num = (v) => typeof v === "number" && !isNaN(v);
    const hotN = share >= 0.97 ? 12 : Math.max(0, Math.min(11, Math.floor(share * 12)));
    const piece = (from, to, col, cls) => (to > from ? `<line${cls ? ` class="${cls}"` : ""} x1="26" x2="26" y1="${(Y0 + from * P).toFixed(1)}" y2="${(Y0 + to * P).toFixed(1)}" stroke="${col}" stroke-width="40" stroke-dasharray="10.8 3.6"/>` : "");
    const yEnd = Y0 + 12 * P;
    const grad = hotN > 0 ? `<defs><linearGradient id="${id}t" gradientUnits="userSpaceOnUse" x1="0" x2="0" y1="${Y0}" y2="${(Y0 + hotN * P).toFixed(1)}">
      <stop offset="0" stop-color="#FF6A3D"/><stop offset="1" stop-color="#FFB36B"/></linearGradient></defs>` : "";
    const cold = live ? "#5BA8FF" : "#1B3355";
    const cells = `<g class="litg">${piece(0, hotN, `url(#${id}t)`, "")}${hotN < 12 ? piece(hotN, hotN + 1, "#FFD9C2", "") : ""}</g>` +
      (hotN < 12 ? piece(hotN + 1, 12, cold, live ? "draw" : "") : "");
    const edge = live ? "var(--st-blue)" : "var(--st-cyan)";
    const hts = a.sensor_heights || {};
    const yAt = (hgt, dflt) => Y0 + (yEnd - Y0) * (1 - Math.max(0, Math.min(1, num(hgt) ? hgt : dflt)));
    const reading = (t, hgt, dflt) => {
      const yy = yAt(hgt, dflt).toFixed(1);
      return `<line x1="52" x2="60" y1="${yy}" y2="${yy}" stroke="var(--st-cyan)" stroke-width="1.5"/><text x="63" y="${(+yy + 4).toFixed(1)}" class="tr">${fmt(this._deg(t))}\u00b0</text>`;
    };
    let temps = "";
    if (this._show("temps")) {
      temps = reading(a.top_temperature, hts.top, 0.72);
      if (a.bottom_temperature !== null && a.bottom_temperature !== undefined) temps += reading(a.bottom_temperature, hts.bottom, 0.33);
    }
    let chev = "";
    if (live && hotN < 11) {
      const cy = (Y0 + (hotN + 1) * P + yEnd) / 2;
      chev = [cy - 8, cy + 10].map((c) => `<path d="M17 ${c + 6} L26 ${c - 3} L35 ${c + 6}" fill="none" stroke="#0A111C" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>`).join("");
    }
    const bt = yEnd + 8;
    return `<svg class="tank" viewBox="0 0 96 194" style="width:${Math.round(96 * scale)}px" aria-hidden="true">${grad}
      <g class="cells${a.reheating ? " pulse" : ""}">${cells}</g>${chev}
      <path d="M2 10 V2 H12 M40 2 H50 V10 M50 ${yEnd.toFixed(1)} V${bt.toFixed(1)} H40 M12 ${bt.toFixed(1)} H2 V${yEnd.toFixed(1)}" fill="none" stroke="${edge}" stroke-width="1.5"/>${temps}</svg>`;
  }


  // Ring gauge: tick ring, orbit, 40 gradient segments showing the fill (revealed through a mask) with a glowing end
  // dot, and an inner arc for a live shower (elapsed) or a reheat (unknown progress: a rotating quarter).
  _ring(m, pct, tone, live, reheating, elapsed, typical, scale) {
    const id = this._id();
    const clamp = (v) => Math.max(0, Math.min(100, v));
    const pv = clamp(nn(pct, 0));
    const p = pv.toFixed(1);
    const rot = `transform="rotate(-90 84 84)"`;
    const GR = { cyan: ["#5FE0D0", "#5BA8FF", "var(--st-cyan)"], amber: ["#F5A524", "#FF7A3D", "var(--st-amber)"], danger: ["#FF5C6C", "#FF8FB0", "var(--st-danger)"] }[tone];
    let inner = "";
    if (live) inner = `<circle class="inner pulse" cx="84" cy="84" r="58" fill="none" stroke="var(--st-blue)" stroke-width="3" pathLength="100" stroke-dasharray="${clamp((elapsed / typical) * 100).toFixed(1)} 100" ${rot}/>`;
    else if (reheating) inner = `<g class="spin"><circle class="inner pulse" cx="84" cy="84" r="58" fill="none" stroke="var(--st-violet)" stroke-width="3" pathLength="100" stroke-dasharray="25 75" ${rot}/></g>`;
    const top = live ? `<text x="84" y="50" class="rs" fill="var(--st-blue)">${Math.floor(elapsed)} / ${Math.round(typical)} ${this._t("min")}</text>`
      : `<text x="84" y="50" class="rs" fill="var(--st-cyan)">${pct === null || pct === undefined ? "\u2014" : Math.round(pv) + "%"}</text>`;
    const ang = (pv / 100) * 2 * Math.PI - Math.PI / 2;
    const dot = pv > 0.5 ? `<circle class="enddot" cx="${(84 + 70 * Math.cos(ang)).toFixed(1)}" cy="${(84 + 70 * Math.sin(ang)).toFixed(1)}" r="4.5" fill="#EAF8FF"/>` : "";
    return `<svg class="ring" viewBox="0 0 168 168" style="max-width:${Math.round(200 * scale)}px" role="img">
      <defs><linearGradient id="${id}g" gradientUnits="userSpaceOnUse" x1="14" y1="0" x2="154" y2="0"><stop offset="0" stop-color="${GR[0]}"/><stop offset="1" stop-color="${GR[1]}"/></linearGradient>
        <mask id="${id}" maskUnits="userSpaceOnUse" x="0" y="0" width="168" height="168">
        <circle cx="84" cy="84" r="70" fill="none" stroke="#fff" stroke-width="10" pathLength="100" stroke-dasharray="${p} 100" ${rot}/></mask></defs>
      <circle cx="84" cy="84" r="80" fill="none" stroke="var(--st-border0)" stroke-width="3" pathLength="100" stroke-dasharray="0.5 1.1667" ${rot}/>
      <circle cx="84" cy="84" r="80" fill="none" stroke="#2E6178" stroke-width="5" pathLength="100" stroke-dasharray="0.6 7.7333" ${rot}/>
      <g class="orbit"><circle cx="84" cy="84" r="63" fill="none" stroke="rgba(95,224,208,.22)" stroke-width="0.8" stroke-dasharray="2 4"/></g>
      <circle cx="84" cy="84" r="70" fill="none" stroke="var(--st-border0)" stroke-width="10" pathLength="100" stroke-dasharray="1.9 0.6" ${rot}/>
      <g class="glow" style="filter:drop-shadow(0 0 5px ${GR[2]})"><g mask="url(#${id})"><circle class="${this._first ? "segin" : ""}" cx="84" cy="84" r="70" fill="none" stroke="url(#${id}g)" stroke-width="10" pathLength="100" stroke-dasharray="1.9 0.6" ${rot}/></g>${dot}</g>
      <circle cx="84" cy="84" r="58" fill="none" stroke="var(--st-border0)" stroke-width="1"/>${inner}${top}
      <text x="84" y="101" class="rn" style="filter:drop-shadow(0 0 6px ${GR[2]})">${fmt(m)}</text><text x="84" y="120" class="rs">${this._t("minleft")}</text></svg>`;
  }


  // The readout column next to the ring: label over value, a coloured line on the left of each.
  _readouts(a, typical, nextAt) {
    const left = Math.max(0, Math.round(nn(a.showers, 0)));
    const fit = a.full_reference_minutes ? Math.round(a.full_reference_minutes / typical) : 0;
    const total = Math.min(8, Math.max(fit, left, 1));
    let bars = "";
    for (let i = 0; i < total; i++) bars += `<i class="${i < left ? "on" : ""}"></i>`;
    const ro = (label, value, extra, ac) => `<div class="ro" style="--ac:${ac || "var(--st-cyan)"}"><div class="rl">${label}</div><div class="rv">${value}</div>${extra || ""}</div>`;
    const has = (v) => v !== null && v !== undefined;
    let out = ro(this._t("water"), this._vol(a.litres));
    out += ro(a.showers === 1 ? this._t("shower1") : this._t("showers"), fmt(a.showers), `<div class="bars">${bars}</div>`);
    if (!(this._config && this._config.compact)) {
      if (this._show("temps") && has(a.top_temperature)) {
        out += ro(this._t("topbottom"), `${fmt(this._deg(a.top_temperature))}\u00b0${has(a.bottom_temperature) ? ` / ${fmt(this._deg(a.bottom_temperature))}\u00b0` : ""}`, "", "var(--st-hot)");
      }
      const nx = a.forecast && a.forecast.next_shower_at ? this._time(new Date(a.forecast.next_shower_at)) : "";
      out += nx ? ro(this._t("nextshower"), nx, "", "var(--st-blue)") : ro(this._t("flowl"), this._flow(a.flow), "", "var(--st-blue)");
    }
    return `<div class="ros">${out}</div>`;
  }


  _usePill(a) {
    const kind = this._useKind(a);
    if (kind === "shower") {
      const mins = a.current_shower && typeof a.current_shower.minutes === "number" ? ` \u00b7 ${Math.floor(a.current_shower.minutes)} ${this._t("min")}` : "";
      return `<span class="tag use">${this._t("inuse")}${mins}</span>`;
    }
    return `<span class="tag use">${this._t(kind === "fill" ? "filling" : "hotwater")}</span>`;
  }


  _split(a) {                        // since the last reheat: left / showered / bath or tap / cooled (+ reheated)
    const s = a.since_reheat;
    if (!s || !s.reference_minutes) return "";
    const ref = s.reference_minutes, tot = s.total_minutes || ref;
    const p = (v) => Math.max(0, Math.min(100, ((v || 0) / tot) * 100));
    const seg = (cls, v) => (p(v) > 0 ? `<i class="${cls}" style="width:${p(v)}%"></i>` : "");
    const other = s.other_minutes || 0, added = s.reheated_minutes || 0;
    const topAt = s.last_top_up_at ? ` (${this._time(new Date(s.last_top_up_at))})` : "";
    const lang = this._locale();
    const t = this._time(new Date(s.since));
    const day = new Date(s.since).toDateString() === new Date().toDateString() ? "" :
      `${new Date(s.since).toLocaleDateString(lang, { weekday: "short" })} `;
    return `<div class="split">
      <div class="sh"><span>${this._t("since")} ${day}${t}</span><span>${fmt(ref)} ${this._t("min")}</span></div>
      <div class="sbar">${seg("left", s.remaining_minutes)}${seg("used", s.showered_minutes)}${other >= 0.5 ? seg("other", other) : ""}${seg("cool", s.cooling_minutes)}</div>
      <div class="legend"><span><b class="d left"></b>${this._t("left")} ${fmt(s.remaining_minutes)}</span>
        <span><b class="d used"></b>${this._t("showered")} ${fmt(s.showered_minutes)}</span>${other >= 0.5 ? `
        <span><b class="d other"></b>${this._t(s.bath_minutes >= 0.5 ? "bathtap" : "other")} ${fmt(other)}</span>` : ""}
        <span><b class="d cool"></b>${this._t("cooled")} ${fmt(s.cooling_minutes)}</span>${added >= 0.5 || topAt ? `
        <span class="plus">+ ${this._t("reheated")} ${fmt(added)}${topAt}</span>` : ""}</div></div>`;
  }


  _tiles(a, lang) {
    // a bath is no shower, but today's hot water all the same: "+ bath" with its cost under the showers
    const bath = a.today_baths ? `+ ${a.today_baths > 1 ? `${a.today_baths} \u00d7 ` : ""}${this._t("bath")}${
      this._money(a.today_bath_cost, a, lang) ? ` ${this._money(a.today_bath_cost, a, lang)}` : ""}` : "";
    const sub = [this._money(a.today_cost, a, lang), bath].filter(Boolean).join(" \u00b7 ");
    const today = a.today_showers === undefined ? "" : `<div class="tile" style="--ac:var(--st-cyan)">
        <div class="tl"><ha-icon icon="mdi:calendar-today"></ha-icon>${this._t("today")}</div>
        <div class="tv">${a.today_showers} \u00d7 \u00b7 ${a.today_minutes} ${this._t("min")}</div>
        <div class="ts">${sub || "&nbsp;"}</div></div>`;
    let last = `<div class="tv">${this._t("none")}</div><div class="ts">&nbsp;</div>`;
    if (a.last) {
      const when = new Date(a.last.start);
      const day = when.toDateString() === new Date().toDateString() ? "" : `${when.toLocaleDateString(lang, { weekday: "short" })} `;
      // a shower hidden by a gap in the readings is estimated: "\u2248"
      const approx = a.last.estimated ? "\u2248 " : "";
      last = `<div class="tv">${approx}${Math.round(a.last.minutes)} ${this._t("min")}${this._money(a.last.cost, a, lang) ? ` \u00b7 ${this._money(a.last.cost, a, lang)}` : ""}</div>
        <div class="ts">${day}${this._time(when)}</div>`;
    }
    const loss = a.standing_loss_per_day;
    const cool = loss ? `<div class="tile" style="--ac:var(--st-amber)">
        <div class="tl"><ha-icon icon="mdi:thermometer-minus"></ha-icon>${this._t("cooling")}</div>
        <div class="tv">${nn(this._money(loss.cost, a, lang), `${fmt(loss.kwh_electricity, 1)} kWh`)} <small>${this._t("perday")}</small></div>
        <div class="ts">${loss.cost_year !== null && loss.cost_year !== undefined ? `\u2248 ${a.currency_symbol || a.currency || "\u20ac"} ${loss.cost_year} ${this._t("peryear")}` : `${fmt(loss.kwh_heat, 1)} kWh`}</div></div>` : "";
    return `<div class="tiles">${today}<div class="tile" style="--ac:var(--st-blue)"><div class="tl"><ha-icon icon="mdi:shower-head"></ha-icon>${this._t("lastshower")}</div>${last}</div>${cool}</div>`;
  }


  _render() {
    if (!this.shadowRoot || !this._config) return;
    const hass = this._hass;
    if (!hass) return;
    const st = hass.states[this._config.entity];
    if (!st || st.state === "unavailable" || st.state === "unknown") {
      this._clickable = false;
      this._shell().classList.remove("live");
      this._shell().innerHTML = `<div class="na">${st ? this._t("unavailable") : this._t("pick")}</div>`;
      return;
    }
    const a = st.attributes, m = parseFloat(st.state);
    const lang = this._locale();
    const typical = a.typical_shower_minutes || 10;
    const low = nn(a.low_threshold, 10);
    const pct = nn(a.fill_percent, a.full_reference_minutes ? Math.round((m / a.full_reference_minutes) * 100) : null);
    const num = (v) => typeof v === "number" && !isNaN(v);
    // the cells follow the fill level, the same figure as the ring: the hot layer alone (hot_fraction) reads fuller
    // than what is left to shower with, and two different levels side by side confuse
    const share = Math.max(0, Math.min(1, num(pct) ? pct / 100 : (num(a.hot_fraction) ? a.hot_fraction : 0)));
    const live = !!a.showering && this._useKind(a) !== "coil";
    const elapsed = live && a.current_shower && num(a.current_shower.minutes) ? a.current_shower.minutes : 0;
    const hhmm = (iso) => iso ? this._time(new Date(iso)) : null;
    const fullAt = hhmm(a.reheat_full_at);
    const fc = a.forecast;
    const tag = a.reheating ? `<span class="tag heat">${this._t("reheating")}${fullAt ? ` \u00b7 ${this._t("fullat")} ${fullAt}` : ""}</span>`
      : (m < low ? `<span class="tag low">${this._t("low")}</span>`
        : (live ? this._usePill(a)
          : (fc && fc.enough === false && fc.runs_out_at ? `<span class="tag short">${this._t("shortat")} ${hhmm(fc.runs_out_at)}</span>`
            : (fc && fc.enough === true ? `<span class="tag ok">${this._t("enough")}</span>` : ""))));
    const learned = a.learned_at ? new Date(a.learned_at) : null;
    const sub = learned ? `${this._t("selflearning")} \u00b7 ${this._t("updated")} ${learned.toLocaleDateString(lang, { day: "numeric", month: "short" })}`
      : `${this._t("selflearning")} \u00b7 ${this._t("learning")}`;
    const sensed = a.shower_temperature === null || a.shower_temperature === undefined ? ""
      : (a.shower_temperature_mode === "sensed" ? ` \u00b7 ${this._t("you")} ${fmt(this._deg(a.shower_temperature), 1)}\u00b0`
        : ` \u00b7 ${this._t("showerat")} ${fmt(this._deg(a.shower_temperature))}\u00b0`);
    const nextAt = fc && fc.next_shower_at ? `${this._t("nextshower")} ~${hhmm(fc.next_shower_at)}` : "";
    const scale = this._config.compact ? 0.75 : 1;
    const tank = this._show("tank");
    const tone = m < low ? "danger" : (m < 2 * typical ? "amber" : "cyan");

    this._clickable = true;
    this._shell().classList.toggle("live", live);
    this._shell().innerHTML = `
        <div class="head">
          <div class="ht"><div class="title">${this._config.title || this._t("title")}</div><div class="sub">${sub}</div></div>
          ${tag}
        </div>${this._show("problems") ? this._problems(a) : ""}
        <div class="hero">
          ${tank ? this._tank(a, share, a.showering && this._useKind(a) !== "coil", scale) : ""}
          <div class="ringwrap">${this._ring(m, pct, tone, live, !!a.reheating, elapsed, typical, scale)}</div>
          ${this._readouts(a, typical, nextAt)}
        </div>
        ${this._show("split") ? this._split(a) : ""}
        ${this._show("tiles") ? this._tiles(a, lang) : ""}
        ${this._show("history") ? `<div class="hist"><div class="hh"><span>${this._t("last24h")}</span><span class="pk">${nextAt}</span></div>${this._sparkline(300, 72)}</div>` : ""}
        ${this._show("footer") ? `<div class="foot">${this._flow(a.flow)}${sensed} \u00b7 ${this._t("cold")} \u2248 ${fmt(this._deg(a.cold_water))}\u00b0 \u00b7 ${this._t("cop")} ${fmt(a.cop_heat_pump != null ? a.cop_heat_pump : a.cop, 1)}</div>` : ""}`;
    this._first = false;
    const pk = this.shadowRoot.querySelector(".pk");
    if (pk && !nextAt && this._peak !== null && this._peak !== undefined) pk.textContent = `${this._t("max")} ${fmt(this._peak)} ${this._t("min")}`;
    this._bindHover();
  }


  _bindHover() {
    const wrap = this.shadowRoot.querySelector(".sparkwrap.hover");
    const d = this._spark;
    if (!wrap || !d) return;
    const svg = wrap.querySelector(".spark"), cur = wrap.querySelector(".cursor"),
      dot = wrap.querySelector(".dot"), tip = wrap.querySelector(".tip");
    const lang = this._locale();
    const KIND = { shower: "shower1", bath: "bath", other: "tap", reheat: "reheat" };
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
      const inside = d.events.filter((e) => e.start <= t && t <= e.end);
      const kind = inside.length ? ` \u00b7 ${this._t(KIND[inside[0].kind] || "tap")}` : "";
      tip.textContent = `${day}${this._time(when)} \u00b7 ${Math.round(v)} ${this._t("min")}${kind}`;
      cur.style.left = `${px}px`; dot.style.left = `${px}px`; dot.style.top = `${py}px`;
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


  // Always the dark HUD look, whatever the dashboard theme: the palette lives in these custom properties.
  // Gradients, glows and animation are extras: an old browser just shows the flat colours.
  _style() {
    const grid = "rgba(255,255,255,.03) 0 1px, transparent 1px 24px";
    return `<style>
      ha-card { --st-bg: #0A111C; --st-border: #1E3A4C; --st-border0: #1E3A4C; --st-grid: #14283A; --st-text: #D7ECF5; --st-num: #EAF8FF;
                --st-dim: #6F93A6; --st-cyan: #5FE0D0; --st-hot: #FF8A5B; --st-mix: #FFC3A8; --st-blue: #5BA8FF; --st-violet: #B58CFF;
                --st-amber: #F5A524; --st-danger: #FF5C6C; --st-teal: #2BB5C8;
                position: relative; background: #0A111C;
                background: radial-gradient(circle at 0 0, rgba(95,224,208,.10), transparent 55%),
                  radial-gradient(circle at 100% 100%, rgba(91,168,255,.07), transparent 50%),
                  repeating-linear-gradient(0deg, ${grid}), repeating-linear-gradient(90deg, ${grid}),
                  linear-gradient(160deg, #0C1626, #070C14);
                border: 1px solid var(--st-border); border-radius: 14px; padding: 16px; box-shadow: inset 0 0 24px rgba(95,224,208,.05);
                color: var(--st-text); cursor: pointer; overflow: hidden;
                font-family: ui-monospace, "SF Mono", "Cascadia Mono", "Roboto Mono", Menlo, Consolas, monospace; font-size: 13px; }
      ha-card.live { --st-border: #2A5E8C; }
      ha-card::before, ha-card::after { content: ""; position: absolute; width: 12px; height: 12px; pointer-events: none; }
      ha-card::before { top: 5px; left: 5px; border-top: 2px solid var(--st-cyan); border-left: 2px solid var(--st-cyan); }
      ha-card::after { bottom: 5px; right: 5px; border-bottom: 2px solid var(--st-cyan); border-right: 2px solid var(--st-cyan); }
      .na { color: var(--st-dim); }
      .head { display: flex; align-items: flex-start; gap: 12px; }
      .ht { flex: 1; min-width: 0; }
      .title { font-size: 14px; font-weight: 500; color: var(--st-text); line-height: 1.3; }
      .sub { font-size: 11px; letter-spacing: .06em; color: var(--st-dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .tag { font-size: 11px; line-height: 1; padding: 4px 8px; border: 1px solid currentColor; border-radius: 4px; flex: none; white-space: nowrap; }
      .tag.heat { color: var(--st-violet); background: rgba(181,140,255,.12); box-shadow: 0 0 10px rgba(181,140,255,.25); }
      .tag.use { color: var(--st-blue); background: rgba(91,168,255,.12); box-shadow: 0 0 10px rgba(91,168,255,.25); }
      .tag.low { color: var(--st-danger); background: rgba(255,92,108,.12); box-shadow: 0 0 10px rgba(255,92,108,.25); }
      .tag.short { color: var(--st-amber); background: rgba(245,165,36,.12); box-shadow: 0 0 10px rgba(245,165,36,.25); }
      .tag.ok { color: var(--st-cyan); background: rgba(95,224,208,.12); box-shadow: 0 0 10px rgba(95,224,208,.25); }
      .problems { margin-top: 12px; display: flex; flex-direction: column; gap: 4px; }
      .problem { display: flex; align-items: center; gap: 8px; font-size: 12px; padding: 5px 10px; border: 1px solid var(--st-amber);
                 border-radius: 4px; color: var(--st-amber); }
      .problem ha-icon { --mdc-icon-size: 16px; flex: none; }
      .hero { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-around; gap: 12px; margin-top: 12px; }
      .tank { flex: none; overflow: visible; }
      .litg { filter: drop-shadow(0 0 5px rgba(255,138,91,.55)); }
      .tr { font-size: 12px; fill: var(--st-text); }
      .ringwrap { flex: 1 1 150px; min-width: 0; display: flex; justify-content: center; }
      .ring { width: 100%; min-width: 0; height: auto; display: block; overflow: visible; }
      .rn { font-size: 52px; fill: var(--st-num); text-anchor: middle; }
      .rs { font-size: 11px; letter-spacing: .06em; fill: var(--st-dim); text-anchor: middle; }
      .ros { flex: 1 1 160px; max-width: 290px; min-width: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 10px 12px; }
      .ro { border-left: 2px solid var(--ac, var(--st-cyan)); padding-left: 9px; min-width: 0; }
      .rl { font-size: 11px; letter-spacing: .06em; color: var(--st-dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .rv { font-size: 20px; color: var(--st-num); line-height: 1.25; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .bars { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
      .bars i { width: 18px; height: 6px; border-radius: 2px; background: var(--st-border0); display: block; }
      .bars i.on { background: var(--st-hot); box-shadow: 0 0 6px rgba(255,138,91,.5); }
      .split { margin-top: 14px; }
      .sh { display: flex; justify-content: space-between; font-size: 11px; letter-spacing: .06em; color: var(--st-dim); margin-bottom: 4px; }
      .sbar { display: flex; gap: 2px; height: 8px; background: var(--st-grid); border-radius: 2px; }
      .sbar i { display: block; height: 100%; border-radius: 2px; }
      .left { background: var(--st-cyan); background: linear-gradient(90deg, #5FE0D0, #3DC4D8); }
      .sbar .left { box-shadow: 0 0 8px rgba(95,224,208,.45); }
      .used { background: var(--st-blue); } .other { background: var(--st-teal); } .cool { background: var(--st-amber); }
      .legend { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 11px; color: var(--st-dim); margin-top: 6px; }
      .legend span { display: inline-flex; align-items: center; gap: 5px; white-space: nowrap; }
      .d { width: 7px; height: 7px; display: inline-block; }
      .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(92px, 1fr)); gap: 8px; margin-top: 14px; }
      .tile { border: 1px solid var(--st-border0); border-left: 2px solid var(--ac, var(--st-cyan)); border-radius: 6px; padding: 8px 10px; min-width: 0;
              background: linear-gradient(135deg, rgba(95,224,208,.05), transparent); }
      .tl { display: flex; align-items: center; gap: 5px; font-size: 11px; letter-spacing: .06em; color: var(--st-dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .tl ha-icon { --mdc-icon-size: 14px; color: var(--ac, var(--st-cyan)); }
      .tv { font-size: 13px; line-height: 1.3; color: var(--st-num); margin-top: 3px; overflow-wrap: anywhere; }   /* wraps, never cuts a cost off */
      .tv small { font-size: 11px; color: var(--st-dim); }
      .ts { font-size: 11px; color: var(--st-dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .hist { margin-top: 14px; }
      .hh { display: flex; justify-content: space-between; font-size: 11px; letter-spacing: .06em; color: var(--st-dim); margin-bottom: 4px; }
      .sparkwrap { position: relative; }
      .sparkwrap.hover { cursor: crosshair; touch-action: pan-y; }
      .spark { width: 100%; height: 72px; display: block; }
      .nowdot { position: absolute; right: 0; width: 8px; height: 8px; margin: -4px -4px 0 0; border-radius: 50%; pointer-events: none;
                background: var(--st-cyan); box-shadow: 0 0 8px var(--st-cyan); }
      .nohist { position: absolute; top: 0; right: 0; bottom: 0; left: 0; display: grid; place-items: center; font-size: 11px; color: var(--st-dim); }
      .axis { display: flex; justify-content: space-between; font-size: 10px; color: var(--st-dim); margin-top: 2px; }
      .cursor { position: absolute; top: 0; height: 72px; width: 1px; background: var(--st-dim); opacity: 0; pointer-events: none; }
      .dot { position: absolute; width: 8px; height: 8px; margin: -4px 0 0 -4px; border-radius: 50%; opacity: 0; pointer-events: none;
             background: var(--st-cyan); box-shadow: 0 0 0 2px var(--st-bg); }
      .tip { position: absolute; top: -24px; padding: 3px 8px; border: 1px solid var(--st-cyan); border-radius: 4px; font-size: 11px; white-space: nowrap;
             background: var(--st-bg); color: var(--st-num); opacity: 0; pointer-events: none; transition: opacity .12s; }
      .sparkwrap.on .cursor { opacity: .6; } .sparkwrap.on .dot, .sparkwrap.on .tip { opacity: 1; }
      .foot { font-size: 11px; color: var(--st-dim); margin-top: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      @media (prefers-reduced-motion: no-preference) {
        @keyframes stpulse { 50% { opacity: .55; } }
        @keyframes stcells { 50% { opacity: .65; } }
        @keyframes stshim { 50% { opacity: .82; } }
        @keyframes stfade { from { opacity: 0; } to { opacity: 1; } }
        @keyframes strot { to { transform: rotate(360deg); } }
        @keyframes stdot { 50% { box-shadow: 0 0 14px var(--st-cyan); transform: scale(1.25); } }
        .tag.use, .inner.pulse { animation: stpulse 1.6s ease-in-out infinite; }
        .cells.pulse { animation: stcells 2.4s ease-in-out infinite; }
        .litg { animation: stshim 5s ease-in-out infinite; }
        .segin { animation: stfade .8s ease-out both; }
        .orbit, .spin { transform-origin: 84px 84px; }
        .orbit { animation: strot 60s linear infinite; }
        .spin { animation: strot 2.4s linear infinite; }
        .nowdot { animation: stdot 2s ease-in-out infinite; }
      }
    </style>`;
  }
}

// The classic look: exactly the card of v0.14.0 (follows the dashboard theme).
class ShowerTimeClassic extends ShowerTimeBase {
  getCardSize() {                    // masonry views: 5 for the full card (as before), less as parts are hidden
    return 1 + (this._show("tank") || this._show("temps") ? 1 : 0) + (this._show("split") ? 1 : 0) +
      (this._show("tiles") ? 1 : 0) + (this._show("history") ? 1 : 0);
  }

  // Sections views: the height follows the content ("auto"). min_rows keeps a fixed height set in the editor
  // (grid_options: rows) from cutting the card off, with the next card drawn over it (#7). Heights in px measured
  // on a 360 px wide card (a row is 56 px plus an 8 px gap); a card half a section wide or less wraps more.
  getGridOptions() {
    let px = 174;
    if (this._config) {
      const st = this._hass && this._hass.states[this._config.entity];
      const problems = (st && st.attributes.problems && this._show("problems") && st.attributes.problems.length) || 0;
      px += problems * 22 + (this._show("tank") || this._show("temps") ? 60 : 0) + (this._show("split") ? 32 : 0) +
        (this._show("tiles") ? 82 : 0) + (this._show("history") ? 100 : 0) + (this._show("footer") ? 26 : 0);
      const cols = this._config.grid_options && this._config.grid_options.columns;
      if (typeof cols === "number" && cols <= 6) px *= 1.25;
    }
    return { columns: 6, min_columns: 4, rows: "auto", min_rows: Math.ceil((px + 8) / 64) };
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
        <span><b class="d other"></b>${this._t(s.bath_minutes >= 0.5 ? "bathtap" : "other")} ${fmt(other)}</span>` : ""}
        <span><b class="d cool"></b>${this._t("cooled")} ${fmt(s.cooling_minutes)}</span>${added >= 0.5 || topAt ? `
        <span class="plus">+ ${this._t("reheated")} ${fmt(added)}${topAt}</span>` : ""}</div></div>`;
  }


  _tiles(a, lang) {
    // a bath is no shower, but today's hot water all the same: "+ bath" with its cost under the showers
    const bath = a.today_baths ? `+ ${a.today_baths > 1 ? `${a.today_baths} \u00d7 ` : ""}${this._t("bath")}${
      this._money(a.today_bath_cost, a, lang) ? ` ${this._money(a.today_bath_cost, a, lang)}` : ""}` : "";
    const sub = [this._money(a.today_cost, a, lang), bath].filter(Boolean).join(" \u00b7 ");
    const today = a.today_showers === undefined ? "" : `<div class="tile">
        <div class="tl"><ha-icon icon="mdi:calendar-today"></ha-icon>${this._t("today")}</div>
        <div class="tv">${a.today_showers} \u00d7 \u00b7 ${a.today_minutes} ${this._t("min")}</div>
        <div class="ts">${sub || "&nbsp;"}</div></div>`;
    let last = `<div class="tv">${this._t("none")}</div><div class="ts">&nbsp;</div>`;
    if (a.last) {
      const when = new Date(a.last.start);
      const day = when.toDateString() === new Date().toDateString() ? "" : `${when.toLocaleDateString(lang, { weekday: "short" })} `;
      // a shower hidden by a gap in the readings is estimated: "\u2248"
      const approx = a.last.estimated ? "\u2248 " : "";
      last = `<div class="tv">${approx}${Math.round(a.last.minutes)} ${this._t("min")}${this._money(a.last.cost, a, lang) ? ` \u00b7 ${this._money(a.last.cost, a, lang)}` : ""}</div>
        <div class="ts">${day}${this._time(when)}</div>`;
    }
    const loss = a.standing_loss_per_day;
    const cool = loss ? `<div class="tile">
        <div class="tl"><ha-icon icon="mdi:thermometer-minus"></ha-icon>${this._t("cooling")}</div>
        <div class="tv">${nn(this._money(loss.cost, a, lang), `${fmt(loss.kwh_electricity, 1)} kWh`)} <small>${this._t("perday")}</small></div>
        <div class="ts">${loss.cost_year !== null && loss.cost_year !== undefined ? `\u2248 ${a.currency_symbol || a.currency || "\u20ac"} ${loss.cost_year} ${this._t("peryear")}` : `${fmt(loss.kwh_heat, 1)} kWh`}</div></div>` : "";
    return `<div class="tiles">${today}<div class="tile"><div class="tl"><ha-icon icon="mdi:shower-head"></ha-icon>${this._t("lastshower")}</div>${last}</div>${cool}</div>`;
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

class ShowerTimeCard extends HTMLElement {
  static getConfigElement() { return document.createElement("shower-time-card-editor"); }

  static getStubConfig(hass) {
    const ent = Object.keys(hass.states).find((e) => e.startsWith("sensor.") &&
      hass.states[e].attributes && hass.states[e].attributes.typical_shower_minutes !== undefined);
    return { entity: ent || "" };
  }

  setConfig(config) {
    if (!config.entity) throw new Error("entity is required (the 'Shower time' sensor)");
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    const Look = config.style === "classic" ? ShowerTimeClassic : ShowerTimeHud;
    const fresh = !this._look || this._look.constructor !== Look;
    if (fresh) {                                  // another look: start clean, nothing of the old one stays
      if (this._look) this._look._dead = true;
      this.shadowRoot.innerHTML = "";
      this._look = new Look(this);
      this._look._hass = this._hass;
    }
    this._look.setConfig(config);
    if (fresh && this._hass) this._look._maybeFetchHistory();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._look) this._look.hass = hass;
  }

  getCardSize() { return this._look ? this._look.getCardSize() : 5; }

  getGridOptions() { return this._look ? this._look.getGridOptions() : { columns: 6, min_columns: 4, rows: "auto", min_rows: 5 }; }
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
    if (v.style !== "classic") delete v.style;
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
    const data = Object.assign({}, this._config, { compact: !!this._config.compact, style: this._config.style === "classic" ? "classic" : "hud",
      language: this._config.language || "auto", time_format: this._config.time_format || "auto" });
    SECTIONS.forEach((p) => { data["show_" + p] = shows(this._config, p); });
    this._form.hass = this._hass;
    this._form.data = data;
    this._form.schema = [
      { name: "entity", required: true, selector: { entity: { filter: { integration: "shower_time", domain: "sensor" } } } },
      { name: "title", selector: { text: {} } },
      { name: "style", selector: { select: { mode: "dropdown", options: [
        { value: "hud", label: t.e_style_hud }, { value: "classic", label: t.e_style_classic }] } } },
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
