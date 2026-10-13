/* Hot Water Shower Time card — served by the shower_time integration. */
const CARD_VERSION = "0.1.1";

const T = {
  en: { title: "Shower time", min: "min", shower: "of showering", empty: "empty", full: "full",
        showers: "showers", water: "hot water", cold: "cold water", reheating: "REHEATING",
        low: "ALMOST GONE", learned: "Learned", updated: "updated", learning: "learning from your showers",
        selflearning: "self-learning", last24: "last 24 h", unavailable: "Not available",
        pick: "Pick the 'Shower time' sensor of the Hot Water Shower Time integration." },
  nl: { title: "Douchetijd", min: "min", shower: "douchen", empty: "leeg", full: "vol",
        showers: "douchebeurten", water: "warm water", cold: "koud water", reheating: "WORDT OPGEWARMD",
        low: "BIJNA OP", learned: "Geleerd", updated: "bijgewerkt", learning: "leert van jullie douches",
        selflearning: "zelflerend", last24: "laatste 24 u", unavailable: "Niet beschikbaar",
        pick: "Kies de sensor 'Douchetijd' van de integratie Hot Water Shower Time." },
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
    try {
      const end = new Date(), start = new Date(end.getTime() - 24 * 3600 * 1000);
      const res = await this._hass.callWS({ type: "history/history_during_period", start_time: start.toISOString(),
        end_time: end.toISOString(), entity_ids: [this._config.entity], minimal_response: true, no_attributes: true,
        significant_changes_only: false });
      const rows = (res && res[this._config.entity]) || [];
      this._hist = rows.map((r) => [((r.lu || r.lc || 0) * 1000) || Date.parse(r.last_changed || r.last_updated),
        parseFloat(r.s !== undefined ? r.s : r.state)]).filter((p) => !isNaN(p[1]) && p[0]);
      this._render();
    } catch (e) { this._hist = null; }
  }

  _sparkline(w, h, color) {
    const pts = this._hist;
    if (!pts || pts.length < 3) return "";
    const now = Date.now(), t0 = now - 24 * 3600 * 1000;
    const series = [...pts, [now, pts[pts.length - 1][1]]];
    const max = Math.max(10, ...series.map((p) => p[1]));
    const x = (t) => ((Math.max(t, t0) - t0) / (now - t0)) * w;
    const y = (v) => h - 2 - (v / max) * (h - 4);
    let d = `M ${x(series[0][0]).toFixed(1)} ${y(series[0][1]).toFixed(1)}`;
    for (let i = 1; i < series.length; i++) {          // step line: the value holds until the next change
      d += ` H ${x(series[i][0]).toFixed(1)} V ${y(series[i][1]).toFixed(1)}`;
    }
    const area = `${d} V ${h} H ${x(series[0][0]).toFixed(1)} Z`;
    return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
      <defs><linearGradient id="sg" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stop-color="${color}" stop-opacity=".35"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
      <path d="${area}" fill="url(#sg)"/><path d="${d}" fill="none" stroke="${color}" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>`;
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
      <text x="32" y="${top + 18}" class="tt">${fmt(a.top_temperature)}°</text>
      ${a.bottom_temperature !== null && a.bottom_temperature !== undefined
        ? `<text x="32" y="${top + H - 8}" class="tt">${fmt(a.bottom_temperature)}°</text>` : ""}
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
    const full = Math.max(a.full_tank_minutes || 0, m, 1);   // a tank above its usual temperature is 'full'
    const pct = Math.max(0, Math.min(100, (m / full) * 100));
    const pill = a.reheating ? `<span class="pill heat">${this._t("reheating")}</span>`
      : (m < low ? `<span class="pill low">${this._t("low")}</span>` : "");
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
            <div class="scale"><span>${this._t("empty")}</span><span>${this._t("full")} ≈ ${fmt(full)} ${this._t("min")}</span></div>
          </div>
        </div>
        <div class="chips">
          <div class="chip"><ha-icon icon="mdi:shower"></ha-icon><b>${fmt(a.showers)} × ${fmt(typical)} ${this._t("min")}</b><span>${this._t("showers")}</span></div>
          <div class="chip"><ha-icon icon="mdi:water"></ha-icon><b>≈ ${fmt(a.litres)} L</b><span>${this._t("water")} ${fmt(a.shower_temperature)}°</span></div>
          <div class="chip"><ha-icon icon="mdi:snowflake-thermometer"></ha-icon><b>${fmt(a.cold_water)}°</b><span>${this._t("cold")}</span></div>
        </div>
        ${this._config.show_history && this._hist && this._hist.length > 2 ? `
          <div class="hist"><div class="hl">${this._t("last24")}</div>${this._sparkline(300, 44, color)}</div>` : ""}
        <div class="foot">${this._t("learned")}: ≈ ${fmt(a.flow, 1)} L/min · ${fmt(a.observations)} obs.</div>
      </ha-card>`;
    this.shadowRoot.querySelector("ha-card").onclick = () => {
      const ev = new Event("hass-more-info", { bubbles: true, composed: true });
      ev.detail = { entityId: this._config.entity };
      this.dispatchEvent(ev);
    };
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
      .hist { margin-top: 12px; }
      .hl { font-size: 11px; color: var(--secondary-text-color); margin-bottom: 2px; }
      .spark { width: 100%; height: 44px; display: block; }
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
