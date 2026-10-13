/* Hot Water Shower Time card — served by the shower_time integration. */
const CARD_VERSION = "0.9.0";

const T = {
  en: { title: "Shower time", min: "min", showers: "showers", shower1: "shower", water: "hot water",
        reheating: "REHEATING", inuse: "IN USE", low: "ALMOST GONE", fullat: "full", selflearning: "self-learning",
        learning: "learning", updated: "updated", today: "Today", lastshower: "Last shower", cooling: "Standing loss",
        perday: "per day", peryear: "/ year", none: "none yet", since: "Since reheat", left: "left", showered: "showered",
        cooled: "cooled", last24: "Last 24 hours", max: "max", ago24: "-24 h", now: "now", yesterday: "yesterday",
        nohist: "no history yet — fills in by itself", unavailable: "Not available", cold: "cold water", cop: "COP",
        pick: "Pick the 'Shower time' sensor of the Hot Water Shower Time integration.",
        sensed: "sensed", you: "you shower at" },
  nl: { title: "Douchetijd", min: "min", showers: "douches", shower1: "douche", water: "warm water",
        reheating: "WORDT OPGEWARMD", inuse: "DOUCHT NU", low: "BIJNA OP", fullat: "vol", selflearning: "zelflerend",
        learning: "leert", updated: "bijgewerkt", today: "Vandaag", lastshower: "Laatste douche", cooling: "Afkoeling",
        perday: "per dag", peryear: "/ jaar", none: "nog geen", since: "Sinds opwarmen", left: "over", showered: "gedoucht",
        cooled: "afgekoeld", last24: "Laatste 24 uur", max: "max", ago24: "-24 u", now: "nu", yesterday: "gisteren",
        nohist: "nog geen historie — vult zich vanzelf", unavailable: "Niet beschikbaar", cold: "koud water", cop: "COP",
        pick: "Kies de sensor 'Douchetijd' van de integratie Hot Water Shower Time.",
        sensed: "gemeten", you: "jullie douchen op" },
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

  _water(top) {                      // liquid colour from the top temperature (°C)
    if (top === null || top === undefined) return ["#9aa7b4", "#c3ccd6"];
    if (top >= 50) return ["#ff5a36", "#ffab4d"];
    if (top >= 42) return ["#f5a524", "#ffd27a"];
    return ["#3d8fe6", "#7cc4ff"];
  }

  _tank(a, pct) {
    const W = 76, H = 132, x = 6, y = 6, w = 64, h = 120, r = 22;
    const lvl = Math.max(0, Math.min(100, pct ?? 0));
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
      <text x="${x + w / 2}" y="${y + h / 2 + 6}" class="tpct">${pct === null || pct === undefined ? "—" : Math.round(lvl) + "%"}</text>
    </svg>`;
  }

  _money(v, a, lang) {
    if (v === null || v === undefined) return null;
    return `${a.currency || "€"} ${Number(v).toLocaleString(lang, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }

  _split(a) {                        // since the last reheat: left / showered / cooled
    const s = a.since_reheat;
    if (!s || !s.reference_minutes) return "";
    const tot = s.reference_minutes, p = (v) => Math.max(0, Math.min(100, (v / tot) * 100));
    const lang = this._hass.locale?.language || undefined;
    const t = new Date(s.since).toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit" });
    const day = new Date(s.since).toDateString() === new Date().toDateString() ? "" :
      `${new Date(s.since).toLocaleDateString(lang, { weekday: "short" })} `;
    return `<div class="split">
      <div class="sh"><span>${this._t("since")} ${day}${t}</span><span>${fmt(tot)} ${this._t("min")}</span></div>
      <div class="sbar"><i class="left" style="width:${p(s.remaining_minutes)}%"></i><i class="used" style="width:${p(s.showered_minutes)}%"></i><i class="cool" style="width:${p(s.cooling_minutes)}%"></i></div>
      <div class="legend"><span><b class="d left"></b>${this._t("left")} ${fmt(s.remaining_minutes)}</span>
        <span><b class="d used"></b>${this._t("showered")} ${fmt(s.showered_minutes)}</span>
        <span><b class="d cool"></b>${this._t("cooled")} ${fmt(s.cooling_minutes)}</span></div></div>`;
  }

  _tiles(a, lang) {
    const today = a.today_showers === undefined ? "" : `<div class="tile">
        <div class="tl"><ha-icon icon="mdi:calendar-today"></ha-icon>${this._t("today")}</div>
        <div class="tv">${a.today_showers} × · ${a.today_minutes} ${this._t("min")}</div>
        <div class="ts">${this._money(a.today_cost, a, lang) ?? "&nbsp;"}</div></div>`;
    let last = `<div class="tv">${this._t("none")}</div><div class="ts">&nbsp;</div>`;
    if (a.last) {
      const when = new Date(a.last.start);
      const day = when.toDateString() === new Date().toDateString() ? "" : `${when.toLocaleDateString(lang, { weekday: "short" })} `;
      last = `<div class="tv">${Math.round(a.last.minutes)} ${this._t("min")}${this._money(a.last.cost, a, lang) ? ` · ${this._money(a.last.cost, a, lang)}` : ""}</div>
        <div class="ts">${day}${when.toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit" })}</div>`;
    }
    const loss = a.standing_loss_per_day;
    const cool = loss ? `<div class="tile">
        <div class="tl"><ha-icon icon="mdi:thermometer-minus"></ha-icon>${this._t("cooling")}</div>
        <div class="tv">${this._money(loss.cost, a, lang) ?? `${fmt(loss.kwh_electricity, 1)} kWh`} <small>${this._t("perday")}</small></div>
        <div class="ts">${loss.cost_year !== null && loss.cost_year !== undefined ? `≈ ${a.currency || "€"} ${loss.cost_year} ${this._t("peryear")}` : `${fmt(loss.kwh_heat, 1)} kWh`}</div></div>` : "";
    return `<div class="tiles">${today}<div class="tile"><div class="tl"><ha-icon icon="mdi:shower-head"></ha-icon>${this._t("lastshower")}</div>${last}</div>${cool}</div>`;
  }

  _render() {
    if (!this.shadowRoot || !this._config) return;
    const hass = this._hass;
    if (!hass) return;
    const st = hass.states[this._config.entity];
    if (!st || st.state === "unavailable" || st.state === "unknown") {
      this.shadowRoot.innerHTML = `${this._style()}<ha-card><div class="na">${st ? this._t("unavailable") : this._t("pick")}</div></ha-card>`;
      return;
    }
    const a = st.attributes, m = parseFloat(st.state);
    const lang = hass.locale?.language || hass.language || undefined;
    const typical = a.typical_shower_minutes || 10;
    const low = a.low_threshold ?? 10;
    const color = m < low ? "var(--error-color, #e5484d)" : (m < 2 * typical ? "var(--warning-color, #f5a524)" : "var(--success-color, #30a46c)");
    const pct = a.fill_percent ?? (a.full_reference_minutes ? Math.round((m / a.full_reference_minutes) * 100) : null);
    const hhmm = (iso) => iso ? new Date(iso).toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit" }) : null;
    const fullAt = hhmm(a.reheat_full_at);
    const pill = a.reheating ? `<span class="pill heat"><ha-icon icon="mdi:fire"></ha-icon>${this._t("reheating")}${fullAt ? ` · ${this._t("fullat")} ${fullAt}` : ""}</span>`
      : (m < low ? `<span class="pill low"><ha-icon icon="mdi:alert"></ha-icon>${this._t("low")}</span>`
        : (a.showering ? `<span class="pill use"><ha-icon icon="mdi:shower-head"></ha-icon>${this._t("inuse")}</span>` : ""));
    const learned = a.learned_at ? new Date(a.learned_at) : null;
    const sub = learned ? `${this._t("selflearning")} · ${this._t("updated")} ${learned.toLocaleDateString(lang, { day: "numeric", month: "short" })}`
      : `${this._t("selflearning")} · ${this._t("learning")}`;
    const showers = a.showers === 1 ? this._t("shower1") : this._t("showers");
    const sensed = a.shower_temperature_mode === "sensed"
      ? ` · ${this._t("you")} ${fmt(this._deg(a.shower_temperature), 1)}°` : "";

    this.shadowRoot.innerHTML = `${this._style()}
      <ha-card>
        <div class="head">
          <div class="ico" style="--c:${color}"><ha-icon icon="mdi:shower-head"></ha-icon></div>
          <div class="ht"><div class="title">${this._config.title || this._t("title")}</div><div class="sub">${sub}</div></div>
          ${pill}
        </div>
        <div class="hero">
          ${this._config.show_tank ? this._tank(a, pct) : ""}
          <div class="main">
            <div class="big"><span class="num" style="color:${color}">${fmt(m)}</span><span class="unit">${this._t("min")}</span></div>
            <div class="what">≈ ${fmt(a.showers)} ${showers} · ${this._vol(a.litres)}</div>
            <div class="temps"><span><ha-icon icon="mdi:arrow-up-thin"></ha-icon>${fmt(this._deg(a.top_temperature))}°</span>
              ${a.bottom_temperature !== null && a.bottom_temperature !== undefined ? `<span><ha-icon icon="mdi:arrow-down-thin"></ha-icon>${fmt(this._deg(a.bottom_temperature))}°</span>` : ""}</div>
            ${this._split(a)}
          </div>
        </div>
        ${this._tiles(a, lang)}
        ${this._config.show_history ? `<div class="hist"><div class="hh"><span>${this._t("last24")}</span><span class="pk"></span></div>${this._sparkline(300, 52, color)}</div>` : ""}
        <div class="foot">${this._flow(a.flow)}${sensed} · ${this._t("cold")} ≈ ${fmt(this._deg(a.cold_water))}° · ${this._t("cop")} ${fmt(a.cop, 1)}</div>
      </ha-card>`;
    const pk = this.shadowRoot.querySelector(".pk");
    if (pk && this._peak !== null && this._peak !== undefined) pk.textContent = `${this._t("max")} ${fmt(this._peak)} ${this._t("min")}`;
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

  _style() {
    return `<style>
      ha-card { padding: 16px; cursor: pointer; overflow: hidden; }
      .na { color: var(--secondary-text-color); }
      .head { display: flex; align-items: center; gap: 12px; }
      .ico { width: 38px; height: 38px; border-radius: 12px; display: grid; place-items: center; flex: none;
             background: color-mix(in srgb, var(--c) 16%, transparent); color: var(--c); }
      .ico ha-icon { --mdc-icon-size: 22px; }
      .ht { flex: 1; min-width: 0; }
      .title { font-size: 16px; font-weight: 600; color: var(--primary-text-color); line-height: 1.2; }
      .sub { font-size: 12px; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .pill { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; font-weight: 700; letter-spacing: .02em;
              padding: 4px 10px 4px 8px; border-radius: 999px; flex: none; white-space: nowrap; }
      .pill ha-icon { --mdc-icon-size: 14px; }
      .pill.heat { background: color-mix(in srgb, #a855f7 16%, transparent); color: #a855f7; }
      .pill.use { background: color-mix(in srgb, #3d8fe6 16%, transparent); color: #3d8fe6; animation: pulse 1.6s ease-in-out infinite; }
      .pill.low { background: color-mix(in srgb, var(--error-color, #e5484d) 16%, transparent); color: var(--error-color, #e5484d); }
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
      .cool { background: repeating-linear-gradient(45deg, #f5a524 0 4px, color-mix(in srgb, #f5a524 55%, transparent) 4px 8px); }
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
      .nohist { position: absolute; inset: 0; display: grid; place-items: center; font-size: 11px; color: var(--secondary-text-color); }
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
