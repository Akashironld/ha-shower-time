/* Hot Water Shower Time card — served by the shower_time integration. */
const CARD_VERSION = "0.8.0";

const T = {
  en: { title: "Shower time", min: "min", shower: "of showering", empty: "empty", full: "full",
        showers: "showers", water: "hot water", cold: "cold water", reheating: "REHEATING", inuse: "IN USE", fullat: "full", today: "Today", shower1: "shower", showersN: "showers", last: "last",
        low: "ALMOST GONE", learned: "Learned", updated: "updated", learning: "learning from your showers",
        selflearning: "self-learning", last24: "last 24 h", ago24: "-24 h", now: "now", yesterday: "yesterday", nohist: "no history yet — fills in by itself", unavailable: "Not available",
        pick: "Pick the 'Shower time' sensor of the Hot Water Shower Time integration.",
        sensed: "sensed", set: "set", head: "shower head", you: "you shower at",
        src: { water_meter: "water meter", manual: "set", eco: "water-saving", normal: "normal", rain: "rain shower" } },
  nl: { title: "Douchetijd", min: "min", shower: "douchen", empty: "leeg", full: "vol",
        showers: "douchebeurten", water: "warm water", cold: "koud water", reheating: "WORDT OPGEWARMD", inuse: "DOUCHT NU", fullat: "vol", today: "Vandaag", shower1: "douche", showersN: "douches", last: "laatste",
        low: "BIJNA OP", learned: "Geleerd", updated: "bijgewerkt", learning: "leert van jullie douches",
        selflearning: "zelflerend", last24: "laatste 24 u", ago24: "-24 u", now: "nu", yesterday: "gisteren", nohist: "nog geen historie — vult zich vanzelf", unavailable: "Niet beschikbaar",
        pick: "Kies de sensor 'Douchetijd' van de integratie Hot Water Shower Time.",
        sensed: "gemeten", set: "ingesteld", head: "douchekop", you: "jullie douchen op",
        src: { water_meter: "watermeter", manual: "ingevuld", eco: "waterbesparend", normal: "normaal", rain: "regendouche" } },
};

const fmt = (v, d = 0) => (v === null || v === undefined || isNaN(v)) ? "—" : Number(v).toFixed(d);

class ShowerTimeCard extends HTMLElement {
  static getConfigElement() { return document.createElement("shower-time-card-editor"); }

  static getStubConfig(hass) {
    const ent = Object.keys(hass.states).find((e) => e.startsWith("sensor.") &&
      hass.states[e].attributes && hass.states[e].attributes.typical_shower_minutes !== undefined);
    return { entity: ent || "", show_tank: true, show_history: true };
  }

  setConfig(config) {
    if (!config.entity) throw new Error("entity is required (the 'Shower time' sensor)");
    this._config = { show_tank: true, show_history: true, ...config };
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._hist = null; this._histAt = 0;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    const st = this._config && hass.states[this._config.entity];
    const key = st ? `${st.state}|${st.last_updated}|${hass.locale?.language}` : "none";
    if (key !== this._key) { this._key = key; this._render(); }
    this._maybeFetchHistory();
  }

  getCardSize() { return this._config && this._config.show_history ? 5 : 4; }
  getGridOptions() { return { columns: 6, min_columns: 4 }; }

  _t(k) {
    const l = ((this._hass && (this._hass.locale && this._hass.locale.language || this._hass.language)) || "en").slice(0, 2);
    return (T[l] || T.en)[k];
  }

  async _maybeFetchHistory() {
    if (!this._config || !this._config.show_history || !this._hass) return;
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
    const gap = x(series[0][0]) > 2 ? `<rect x="0" y="0" width="${x(series[0][0]).toFixed(1)}" height="${h}" fill="var(--divider-color, rgba(127,127,127,.12))" opacity=".35"/>` : "";
    const peak = series.reduce((m, p) => (p[1] > m[1] ? p : m), series[0]);
    this._spark = { t0, now, max, series, h, color };
    return `<div class="sparkwrap hover"><div class="cursor"></div><div class="dot"></div><div class="tip"></div><svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
      <defs><linearGradient id="sg" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stop-color="${color}" stop-opacity=".35"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
      ${base}${gap}<path d="${area}" fill="url(#sg)"/>
      <path d="${d}" fill="none" stroke="${color}" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>
      <div class="axis"><span>${this._t("ago24")}</span><span>max ${fmt(peak[1])} ${this._t("min")}</span><span>${this._t("now")}</span></div></div>`;
  }

  _deg(v) {                         // the integration works in °C; show °F when Home Assistant does
    if (v === null || v === undefined || isNaN(v)) return v;
    const f = this._hass && this._hass.config && this._hass.config.unit_system && this._hass.config.unit_system.temperature === "°F";
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

  _tank(a, color) {
    const hot = Math.max(0, Math.min(1, a.hot_fraction ?? 1));
    const H = 112, top = 8, inner = H - 4;
    const hotH = inner * hot;
    const heat = a.reheating ? `<animate attributeName="opacity" values=".55;1;.55" dur="2.4s" repeatCount="indefinite"/>` : "";
    return `<svg class="tank" viewBox="0 0 64 ${H + 16}" aria-hidden="true">
      <defs>
        <linearGradient id="hot" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stop-color="#ff5a36"/><stop offset=".75" stop-color="#ff9a3c"/><stop offset="1" stop-color="#ffc46b" stop-opacity=".85"/></linearGradient>
        <linearGradient id="coldg" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stop-color="#7cc4ff" stop-opacity=".9"/><stop offset="1" stop-color="#3a7bd5"/></linearGradient>
        <clipPath id="clip"><rect x="10" y="${top}" width="44" height="${H}" rx="16"/></clipPath>
      </defs>
      <g clip-path="url(#clip)">
        <rect x="10" y="${top}" width="44" height="${H}" fill="url(#coldg)"/>
        <rect x="10" y="${top}" width="44" height="${(hotH + 4).toFixed(1)}" fill="url(#hot)">${heat}</rect>
        <rect x="10" y="${(top + hotH - 3).toFixed(1)}" width="44" height="8" fill="#ffd7a8" opacity=".35"/>
      </g>
      <rect x="16" y="${top + 10}" width="5" height="${H - 20}" rx="2.5" fill="#fff" opacity=".18"/>
      <rect x="10" y="${top}" width="44" height="${H}" rx="16" fill="none" stroke="var(--divider-color, rgba(127,127,127,.4))" stroke-width="2"/>
      <text x="32" y="${top + 18}" class="tt">${fmt(this._deg(a.top_temperature))}°</text>
      ${a.bottom_temperature !== null && a.bottom_temperature !== undefined
        ? `<text x="32" y="${top + H - 8}" class="tt">${fmt(this._deg(a.bottom_temperature))}°</text>` : ""}
    </svg>`;
  }

  _render() {
    if (!this.shadowRoot || !this._config) return;
    const hass = this._hass;
    const st = hass && hass.states[this._config.entity];
    if (!hass) return;
    if (!st || st.state === "unavailable" || st.state === "unknown") {
      this.shadowRoot.innerHTML = `${this._style()}<ha-card><div class="na">${st ? this._t("unavailable") : this._t("pick")}</div></ha-card>`;
      return;
    }
    const a = st.attributes, m = parseFloat(st.state);
    const typical = a.typical_shower_minutes || 10;
    const low = a.low_threshold ?? 10;
    const color = m < low ? "var(--error-color, #e5484d)" : (m < 2 * typical ? "var(--warning-color, #f5a524)" : "var(--success-color, #30a46c)");
    // 100 % = the minutes right after the last reheat; it drops with showering and standing loss
    const full = Math.max(a.full_reference_minutes || a.full_tank_minutes || 0, m, 1);
    const pctNum = a.fill_percent !== undefined && a.fill_percent !== null ? a.fill_percent : Math.round((m / full) * 100);
    const pct = Math.max(0, Math.min(100, pctNum));
    const pctUsual = pct;
    const lang = hass.locale?.language || hass.language || undefined;
    const hhmm = (iso) => iso ? new Date(iso).toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit" }) : null;
    const fullAt = hhmm(a.reheat_full_at);
    const pill = a.reheating ? `<span class="pill heat">${this._t("reheating")}${fullAt ? ` · ${this._t("fullat")} ${fullAt}` : ""}</span>`
      : (m < low ? `<span class="pill low">${this._t("low")}</span>`
        : (a.showering ? `<span class="pill use">${this._t("inuse")}</span>` : ""));
    const title = this._config.title || this._t("title");
    const learned = a.learned_at ? new Date(a.learned_at) : null;
    const when = learned ? learned.toLocaleDateString(hass.locale?.language || undefined, { day: "numeric", month: "short" }) : null;
    const sub = `${this._t("selflearning")} · ${learned ? `${this._t("updated")} ${when}` : this._t("learning")}`;

    this.shadowRoot.innerHTML = `${this._style()}
      <ha-card>
        <div class="head">
          <div class="ico" style="--c:${color}"><ha-icon icon="mdi:shower-head"></ha-icon></div>
          <div class="ht"><div class="title">${title}</div><div class="sub">${sub}</div></div>
          ${pill}
        </div>
        <div class="body">
          ${this._config.show_tank ? this._tank(a, color) : ""}
          <div class="main">
            <div class="big"><span class="num" style="color:${color}">${fmt(m)}</span><span class="unit">${this._t("min")}</span></div>
            <div class="what">${this._t("shower")}</div>
            <div class="bar"><div style="width:${pct}%;background:${color}"></div></div>
            <div class="scale"><span>${this._t("empty")}</span><b class="pct" style="color:${color}">${pctUsual}%</b><span>${this._t("full")} ≈ ${fmt(full)} ${this._t("min")}</span></div>
          </div>
        </div>
        <div class="chips">
          <div class="chip"><ha-icon icon="mdi:shower"></ha-icon><b>${fmt(a.showers)} × ${fmt(typical)} ${this._t("min")}</b><span>${this._t("showers")}</span></div>
          <div class="chip"><ha-icon icon="mdi:water"></ha-icon><b>≈ ${this._vol(a.litres)}</b><span>${this._t("water")} ${a.shower_temperature_mode === "reference" ? "≈" : ""}${fmt(this._deg(a.shower_temperature), a.shower_temperature_mode === "sensed" ? 1 : 0)}°</span></div>
          <div class="chip"><ha-icon icon="mdi:snowflake-thermometer"></ha-icon><b>≈ ${fmt(this._deg(a.cold_water))}°</b><span>${this._t("cold")}</span></div>
        </div>
        ${this._config.show_history ? `
          <div class="hist"><div class="hl">${this._t("last24")}</div>${this._sparkline(300, 44, color)}</div>` : ""}
        ${this._today(a, lang)}
        <div class="foot">${this._foot(a)}</div>
      </ha-card>`;
    this._bindHover();
    this.shadowRoot.querySelector("ha-card").onclick = () => {
      const ev = new Event("hass-more-info", { bubbles: true, composed: true });
      ev.detail = { entityId: this._config.entity };
      this.dispatchEvent(ev);
    };
  }

  _bindHover() {
    const wrap = this.shadowRoot.querySelector(".sparkwrap.hover");
    const d = this._spark;
    if (!wrap || !d) return;
    const svg = wrap.querySelector(".spark"), cur = wrap.querySelector(".cursor"),
      dot = wrap.querySelector(".dot"), tip = wrap.querySelector(".tip");
    const lang = this._hass.locale?.language || this._hass.language || undefined;
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
      tip.textContent = `${day}${when.toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit" })} · ${Math.round(v)} ${this._t("min")}`;
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

  _today(a, lang) {
    if (a.today_showers === undefined) return "";
    const cur = a.currency || "€";
    const money = (v) => v === null || v === undefined ? null :
      `${cur} ${Number(v).toLocaleString(lang, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    const parts = [`${a.today_showers} ${a.today_showers === 1 ? this._t("shower1") : this._t("showersN")}`,
      `${a.today_minutes} ${this._t("min")}`];
    if (money(a.today_cost)) parts.push(money(a.today_cost));
    let last = "";
    if (a.last) {
      const when = new Date(a.last.start);
      const day = when.toDateString() === new Date().toDateString() ? "" : `${when.toLocaleDateString(lang, { weekday: "short" })} `;
      last = ` <span class="dim">· ${this._t("last")} ${day}${when.toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit" })}: ${Math.round(a.last.minutes)} ${this._t("min")}${money(a.last.cost) ? ` · ${money(a.last.cost)}` : ""}</span>`;
    }
    return `<div class="today"><ha-icon icon="mdi:calendar-today"></ha-icon><span><b>${this._t("today")}</b> ${parts.join(" · ")}${last}</span></div>`;
  }

  _foot(a) {
    const src = (this._t("src") || {})[a.head_flow_source];
    if (a.shower_temperature_mode === "sensed") {
      return `${this._t("you")} ≈ ${fmt(this._deg(a.shower_temperature), 1)}° (${this._t("sensed")}) · ${this._t("head")} ${this._flow(a.flow)}${src ? ` (${src})` : ""}`;
    }
    if (a.shower_temperature_mode === "fixed") {
      return `${this._t("learned")}: ≈ ${this._flow(a.flow)} · ${fmt(this._deg(a.shower_temperature))}° (${this._t("set")}) · ${fmt(a.observations)} obs.`;
    }
    return `${this._t("learned")}: ≈ ${this._flow(a.flow)} ${this._t("water")} ≈ ${fmt(this._deg(38))}° · ${fmt(a.observations)} obs.`;
  }

  _style() {
    return `<style>
      ha-card { padding: 16px; cursor: pointer; overflow: hidden; }
      .na { padding: 8px 0; color: var(--secondary-text-color); }
      .head { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }
      .ico { width: 40px; height: 40px; border-radius: 50%; display: grid; place-items: center; flex: none;
             background: color-mix(in srgb, var(--c) 18%, transparent); color: var(--c); }
      .ico ha-icon { --mdc-icon-size: 22px; }
      .ht { flex: 1; min-width: 0; }
      .title { font-size: 16px; font-weight: 600; color: var(--primary-text-color); }
      .sub { font-size: 12px; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .pill { font-size: 10px; font-weight: 700; letter-spacing: .04em; padding: 4px 8px; border-radius: 999px; flex: none; }
      .pill.heat { background: color-mix(in srgb, #a855f7 18%, transparent); color: #a855f7; }
      .pill.use { background: color-mix(in srgb, #3d8fe6 20%, transparent); color: #3d8fe6; animation: pulse 1.6s ease-in-out infinite; }
      @keyframes pulse { 50% { opacity: .55; } }
      .pill.low { background: color-mix(in srgb, var(--error-color, #e5484d) 18%, transparent); color: var(--error-color, #e5484d); }
      .body { display: flex; align-items: center; gap: 14px; }
      .tank { width: 70px; flex: none; }
      .tt { font-size: 10px; font-weight: 700; fill: #fff; text-anchor: middle; paint-order: stroke; stroke: rgba(0,0,0,.35); stroke-width: 2px; }
      .main { flex: 1; min-width: 0; }
      .big { display: flex; align-items: baseline; gap: 6px; }
      .num { font-size: 46px; font-weight: 700; line-height: 1; letter-spacing: -1px; }
      .unit { font-size: 16px; color: var(--secondary-text-color); font-weight: 500; }
      .what { font-size: 13px; color: var(--secondary-text-color); margin: 2px 0 10px; }
      .bar { height: 8px; border-radius: 999px; background: var(--divider-color, rgba(127,127,127,.2)); overflow: hidden; }
      .bar > div { height: 100%; border-radius: 999px; transition: width .6s ease; }
      .scale { display: flex; justify-content: space-between; font-size: 11px; color: var(--secondary-text-color); margin-top: 4px; }
      .chips { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-top: 14px; }
      .chip { background: var(--secondary-background-color, rgba(127,127,127,.08)); border-radius: 12px; padding: 8px 10px;
              display: grid; grid-template-columns: auto 1fr; column-gap: 6px; align-items: center; min-width: 0; }
      .chip ha-icon { --mdc-icon-size: 16px; color: var(--secondary-text-color); grid-row: span 2; }
      .chip b { font-size: 14px; color: var(--primary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .chip span { font-size: 11px; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .hist { margin-top: 12px; overflow: visible; }
      .hl { font-size: 11px; color: var(--secondary-text-color); margin-bottom: 2px; }
      .sparkwrap { position: relative; }
      .sparkwrap.hover { cursor: crosshair; touch-action: pan-y; }
      .cursor { position: absolute; top: 0; height: 44px; width: 1px; background: var(--secondary-text-color); opacity: 0; pointer-events: none; }
      .dot { position: absolute; width: 8px; height: 8px; margin: -4px 0 0 -4px; border-radius: 50%; opacity: 0; pointer-events: none;
             box-shadow: 0 0 0 2px var(--card-background-color, var(--ha-card-background, #1c1c1c)); }
      .tip { position: absolute; top: -26px; padding: 3px 8px; border-radius: 8px; font-size: 11px; font-weight: 600; white-space: nowrap;
             background: var(--primary-text-color); color: var(--card-background-color, var(--ha-card-background, #1c1c1c));
             opacity: 0; pointer-events: none; transition: opacity .12s; }
      .sparkwrap.on .cursor { opacity: .5; } .sparkwrap.on .dot, .sparkwrap.on .tip { opacity: 1; }
      .pct { font-size: 11px; font-weight: 700; }
      .spark { width: 100%; height: 44px; display: block; }
      .nohist { position: absolute; inset: 0; display: grid; place-items: center; font-size: 11px; color: var(--secondary-text-color); }
      .axis { display: flex; justify-content: space-between; font-size: 10px; color: var(--secondary-text-color); margin-top: 2px; }
      .today { display: flex; gap: 6px; align-items: center; margin-top: 10px; font-size: 12px; color: var(--primary-text-color); }
      .today ha-icon { --mdc-icon-size: 16px; color: var(--secondary-text-color); flex: none; }
      .today .dim { color: var(--secondary-text-color); }
      .foot { font-size: 11px; color: var(--secondary-text-color); margin-top: 10px; }
    </style>`;
  }
}

class ShowerTimeCardEditor extends HTMLElement {
  setConfig(config) { this._config = config; this._render(); }
  set hass(hass) { this._hass = hass; this._render(); }
  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (s) => ({ entity: "Sensor", title: "Title", show_tank: "Show tank",
        show_history: "Show last 24 h" })[s.name] || s.name;
      this._form.addEventListener("value-changed", (ev) => {
        this._config = ev.detail.value;
        this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: this._config }, bubbles: true, composed: true }));
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.data = this._config;
    this._form.schema = [
      { name: "entity", required: true, selector: { entity: { filter: { integration: "shower_time", domain: "sensor" } } } },
      { name: "title", selector: { text: {} } },
      { type: "grid", name: "", schema: [
        { name: "show_tank", selector: { boolean: {} } },
        { name: "show_history", selector: { boolean: {} } } ] },
    ];
  }
}

if (!customElements.get("shower-time-card")) customElements.define("shower-time-card", ShowerTimeCard);
if (!customElements.get("shower-time-card-editor")) customElements.define("shower-time-card-editor", ShowerTimeCardEditor);
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "shower-time-card")) {
  window.customCards.push({ type: "shower-time-card", name: "Shower time", preview: true,
    description: "How long you can still shower — self-learning hot water estimate." });
}
console.info(`%c SHOWER-TIME-CARD %c ${CARD_VERSION} `, "background:#30a46c;color:#fff;border-radius:3px 0 0 3px",
  "background:#222;color:#fff;border-radius:0 3px 3px 0");
