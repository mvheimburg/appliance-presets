// Appliance Presets sidebar panel: list, edit and run staged presets.
//
// Deliberately dependency-free and unbuilt for 0.1: it talks to the
// appliance_presets/* WebSocket commands and the run/abort services, and
// styles itself with Home Assistant theme variables. When it outgrows this
// file it should move to a Lit + TypeScript build like the Lovelace cards.

const STRINGS = {
  en: {
    title: "Appliance presets",
    none: "No appliances yet. Add one under Settings → Devices & services → Appliance Presets.",
    missing: "Missing controls",
    presets: "Presets",
    newPreset: "New preset",
    name: "Name",
    steps: "Steps",
    kind: "Kind",
    program: "Programme",
    setpoint: "°C",
    minutes: "min",
    timeout: "Timeout",
    timed: "Timed",
    preheat: "Preheat until reached",
    hold: "Hold until aborted",
    addStep: "Add step",
    save: "Save",
    delete: "Delete",
    run: "Run now",
    readyAt: "Ready at",
    schedule: "Schedule",
    abort: "Abort",
    confirmRun: "Start {preset} on {appliance}? This turns on a real appliance.",
    confirmDelete: "Delete {preset}?",
    remoteOff: "Remote start is off at the appliance. Arm it there before running.",
    remoteUnverified: "Remote start permission cannot be checked on this appliance.",
    status: "Status",
    step: "Step {n} of {total}: {name}",
    lastError: "Last error",
    saved: "Saved",
  },
  nb: {
    title: "Forhåndsvalg for apparater",
    none: "Ingen apparater ennå. Legg til et under Innstillinger → Enheter og tjenester → Appliance Presets.",
    missing: "Mangler kontroller",
    presets: "Forhåndsvalg",
    newPreset: "Nytt forhåndsvalg",
    name: "Navn",
    steps: "Steg",
    kind: "Type",
    program: "Program",
    setpoint: "°C",
    minutes: "min",
    timeout: "Tidsavbrudd",
    timed: "Tidsstyrt",
    preheat: "Forvarm til nådd",
    hold: "Hold til avbrutt",
    addStep: "Legg til steg",
    save: "Lagre",
    delete: "Slett",
    run: "Kjør nå",
    readyAt: "Ferdig kl.",
    schedule: "Planlegg",
    abort: "Avbryt",
    confirmRun: "Starte {preset} på {appliance}? Dette slår på et ekte apparat.",
    confirmDelete: "Slette {preset}?",
    remoteOff: "Fjernstart er av på apparatet. Slå det på der før du kjører.",
    remoteUnverified: "Tillatelse til fjernstart kan ikke sjekkes på dette apparatet.",
    status: "Status",
    step: "Steg {n} av {total}: {name}",
    lastError: "Siste feil",
    saved: "Lagret",
  },
};

const KINDS = ["preheat", "timed", "hold"];

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const shortProgram = (program) => String(program).split(".").pop();

class AppliancePresetsPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._appliances = [];
    this._presets = [];
    this._selected = null; // device_id
    this._draft = null; // preset being edited
    this._message = "";
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._load();
    else if (this._appliances.length) this._renderStatus();
  }

  set narrow(_value) {}
  set panel(_value) {}

  t(key, vars = {}) {
    const lang = (this._hass?.locale?.language || this._hass?.language || "en").startsWith("nb")
      ? "nb"
      : "en";
    let text = STRINGS[lang][key] ?? STRINGS.en[key] ?? key;
    for (const [k, v] of Object.entries(vars)) text = text.replace(`{${k}}`, v);
    return text;
  }

  async _load() {
    this._appliances = await this._hass.callWS({ type: "appliance_presets/appliances" });
    if (!this._selected && this._appliances.length) this._selected = this._appliances[0].device_id;
    await this._loadPresets();
  }

  async _loadPresets() {
    this._presets = this._selected
      ? await this._hass.callWS({ type: "appliance_presets/presets", device_id: this._selected })
      : [];
    this._render();
  }

  get _appliance() {
    return this._appliances.find((a) => a.device_id === this._selected);
  }

  _newDraft() {
    const program = this._appliance?.programs?.[0] ?? "";
    return { name: "", device_id: this._selected, steps: [{ kind: "timed", program, setpoint: null, duration_minutes: 30 }] };
  }

  _render() {
    const a = this._appliance;
    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <header><h1>${esc(this.t("title"))}</h1></header>
      <main>
        ${
          this._appliances.length
            ? `<nav>${this._appliances
                .map(
                  (x) =>
                    `<button class="tab ${x.device_id === this._selected ? "on" : ""}" data-device="${esc(x.device_id)}">${esc(x.title)}</button>`,
                )
                .join("")}</nav>`
            : `<p class="card">${esc(this.t("none"))}</p>`
        }
        ${a ? this._applianceHtml(a) : ""}
      </main>`;
    this._bind();
    this._renderStatus();
  }

  _applianceHtml(a) {
    const warn = a.missing.length
      ? `<p class="warn">${esc(this.t("missing"))}: ${esc(a.missing.join(", "))}</p>`
      : a.remote_start === "no"
        ? `<p class="warn">${esc(this.t("remoteOff"))}</p>`
        : a.remote_start === "unverified"
          ? `<p class="note">${esc(this.t("remoteUnverified"))}</p>`
          : "";
    return `
      <section class="card" id="status"></section>
      ${warn}
      <section class="card">
        <h2>${esc(this.t("presets"))}</h2>
        <ul class="presets">
          ${this._presets
            .map(
              (p) => `<li>
                <button class="link" data-edit="${esc(p.id)}">${esc(p.name)}</button>
                <span class="summary">${p.steps.map((s) => esc(shortProgram(s.program))).join(" → ")}</span>
                <span class="actions">
                  <input type="time" data-ready="${esc(p.id)}" aria-label="${esc(this.t("readyAt"))}">
                  <button data-run="${esc(p.id)}">${esc(this.t("run"))}</button>
                </span>
              </li>`,
            )
            .join("")}
        </ul>
        <button data-new>${esc(this.t("newPreset"))}</button>
      </section>
      ${this._draft ? this._editorHtml(a) : ""}
      ${this._message ? `<p class="note">${esc(this._message)}</p>` : ""}`;
  }

  _editorHtml(a) {
    const d = this._draft;
    const programs = a.programs.length ? a.programs : [...new Set(d.steps.map((s) => s.program))];
    const kinds = KINDS.filter((k) => k !== "preheat" || a.can_preheat);
    return `
      <section class="card editor">
        <label>${esc(this.t("name"))} <input data-field="name" value="${esc(d.name)}"></label>
        <h3>${esc(this.t("steps"))}</h3>
        <ol>
          ${d.steps
            .map(
              (s, i) => `<li data-step="${i}">
                <select data-k="kind" aria-label="${esc(this.t("kind"))}">
                  ${kinds.map((k) => `<option value="${k}" ${k === s.kind ? "selected" : ""}>${esc(this.t(k))}</option>`).join("")}
                </select>
                <select data-k="program" aria-label="${esc(this.t("program"))}">
                  ${programs.map((p) => `<option value="${esc(p)}" ${p === s.program ? "selected" : ""}>${esc(shortProgram(p))}</option>`).join("")}
                </select>
                ${
                  a.has_setpoint
                    ? `<label><input type="number" data-k="setpoint" value="${s.setpoint ?? ""}" min="${a.setpoint?.[0] ?? ""}" max="${a.setpoint?.[1] ?? ""}"> ${esc(this.t("setpoint"))}</label>`
                    : ""
                }
                ${
                  s.kind === "timed"
                    ? `<label><input type="number" data-k="duration_minutes" min="1" value="${s.duration_minutes ?? ""}"> ${esc(this.t("minutes"))}</label>`
                    : s.kind === "preheat"
                      ? `<label>${esc(this.t("timeout"))} <input type="number" data-k="timeout_minutes" min="1" value="${s.timeout_minutes ?? ""}" placeholder="45"> ${esc(this.t("minutes"))}</label>`
                      : ""
                }
                <button class="icon" data-remove="${i}" aria-label="${esc(this.t("delete"))}">✕</button>
              </li>`,
            )
            .join("")}
        </ol>
        <div class="row">
          <button data-add>${esc(this.t("addStep"))}</button>
          <span class="grow"></span>
          ${d.id ? `<button class="danger" data-delete>${esc(this.t("delete"))}</button>` : ""}
          <button class="primary" data-save>${esc(this.t("save"))}</button>
        </div>
      </section>`;
  }

  _renderStatus() {
    const box = this.shadowRoot.getElementById("status");
    const a = this._appliance;
    if (!box || !a?.entity_id) return;
    const st = this._hass.states[a.entity_id];
    if (!st) return;
    const at = st.attributes;
    const active = st.state === "running" || st.state === "scheduled";
    const detail =
      st.state === "running" && at.step_count
        ? this.t("step", { n: at.step_index + 1, total: at.step_count, name: at.step_name })
        : st.state === "scheduled"
          ? new Date(at.start_at).toLocaleString(this._hass.locale?.language)
          : "";
    box.innerHTML = `
      <div class="row">
        <strong>${esc(this._hass.formatEntityState?.(st) ?? st.state)}</strong>
        ${at.preset ? `<span>${esc(at.preset)}</span>` : ""}
        <span class="grow">${esc(detail)}</span>
        ${active ? `<button class="danger" data-abort>${esc(this.t("abort"))}</button>` : ""}
      </div>
      ${st.state === "failed" && at.last_error ? `<p class="warn">${esc(this.t("lastError"))}: ${esc(at.last_error)}</p>` : ""}`;
    box.querySelector("[data-abort]")?.addEventListener("click", () =>
      // Abort is never behind a confirmation.
      this._hass.callService("appliance_presets", "abort", {}, { entity_id: a.entity_id }),
    );
  }

  _bind() {
    const root = this.shadowRoot;
    root.querySelectorAll("[data-device]").forEach((el) =>
      el.addEventListener("click", () => {
        this._selected = el.dataset.device;
        this._draft = null;
        this._loadPresets();
      }),
    );
    root.querySelector("[data-new]")?.addEventListener("click", () => {
      this._draft = this._newDraft();
      this._render();
    });
    root.querySelectorAll("[data-edit]").forEach((el) =>
      el.addEventListener("click", () => {
        this._draft = structuredClone(this._presets.find((p) => p.id === el.dataset.edit));
        this._render();
      }),
    );
    root.querySelectorAll("[data-run]").forEach((el) =>
      el.addEventListener("click", () => this._run(el.dataset.run)),
    );
    root.querySelector("[data-field=name]")?.addEventListener("input", (e) => {
      this._draft.name = e.target.value;
    });
    root.querySelectorAll("[data-step]").forEach((li) => {
      const step = this._draft.steps[Number(li.dataset.step)];
      li.querySelectorAll("[data-k]").forEach((input) =>
        input.addEventListener("change", () => {
          const key = input.dataset.k;
          step[key] = input.type === "number" ? (input.value === "" ? null : Number(input.value)) : input.value;
          if (key === "kind") {
            if (step.kind !== "timed") step.duration_minutes = null;
            else step.duration_minutes ??= 30;
            this._render();
          }
        }),
      );
    });
    root.querySelectorAll("[data-remove]").forEach((el) =>
      el.addEventListener("click", () => {
        this._draft.steps.splice(Number(el.dataset.remove), 1);
        this._render();
      }),
    );
    root.querySelector("[data-add]")?.addEventListener("click", () => {
      const last = this._draft.steps.at(-1);
      this._draft.steps.push({ kind: "timed", program: last?.program ?? "", setpoint: last?.setpoint ?? null, duration_minutes: 30 });
      this._render();
    });
    root.querySelector("[data-save]")?.addEventListener("click", () => this._save());
    root.querySelector("[data-delete]")?.addEventListener("click", () => this._delete());
  }

  async _save() {
    const steps = this._draft.steps.map((s) =>
      Object.fromEntries(Object.entries(s).filter(([, v]) => v !== null && v !== "")),
    );
    try {
      await this._hass.callWS({ type: "appliance_presets/presets/save", preset: { ...this._draft, steps } });
      this._draft = null;
      this._message = this.t("saved");
    } catch (err) {
      this._message = err.message;
    }
    await this._loadPresets();
  }

  async _delete() {
    if (!confirm(this.t("confirmDelete", { preset: this._draft.name }))) return;
    await this._hass.callWS({ type: "appliance_presets/presets/delete", preset_id: this._draft.id });
    this._draft = null;
    await this._loadPresets();
  }

  async _run(presetId) {
    const a = this._appliance;
    const preset = this._presets.find((p) => p.id === presetId);
    const time = this.shadowRoot.querySelector(`[data-ready="${CSS.escape(presetId)}"]`)?.value;
    if (!confirm(this.t("confirmRun", { preset: preset.name, appliance: a.title }))) return;
    const data = { preset: presetId, confirm: true };
    if (time) {
      const ready = new Date();
      const [h, m] = time.split(":").map(Number);
      ready.setHours(h, m, 0, 0);
      if (ready < new Date()) ready.setDate(ready.getDate() + 1);
      data.ready_at = ready.toISOString();
    }
    try {
      await this._hass.callService("appliance_presets", "run", data, { entity_id: a.entity_id });
      this._message = "";
    } catch (err) {
      this._message = err.message;
    }
    this._render();
  }
}

const STYLE = `
  :host { display: block; min-height: 100vh; background: var(--primary-background-color); color: var(--primary-text-color); font-family: var(--paper-font-body1_-_font-family, inherit); }
  header { height: var(--header-height, 56px); display: flex; align-items: center; padding: 0 16px; background: var(--app-header-background-color, var(--primary-color)); color: var(--app-header-text-color, #fff); }
  h1 { font-size: 20px; font-weight: 400; margin: 0; }
  main { max-width: 900px; margin: 0 auto; padding: 16px; display: grid; gap: 16px; }
  nav { display: flex; gap: 8px; flex-wrap: wrap; }
  .card { background: var(--card-background-color); border-radius: var(--ha-card-border-radius, 12px); box-shadow: var(--ha-card-box-shadow, none); border: 1px solid var(--divider-color); padding: 16px; margin: 0; }
  h2, h3 { margin: 0 0 12px; font-weight: 500; }
  button { font: inherit; padding: 6px 12px; border-radius: 8px; border: 1px solid var(--divider-color); background: var(--secondary-background-color); color: inherit; cursor: pointer; }
  button.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: transparent; }
  button.danger { background: var(--error-color); color: #fff; border-color: transparent; }
  button.tab.on { background: var(--primary-color); color: var(--text-primary-color, #fff); }
  button.link { border: none; background: none; padding: 0; color: var(--primary-color); font-weight: 500; }
  button.icon { padding: 4px 8px; }
  input, select { font: inherit; padding: 4px 6px; border-radius: 6px; border: 1px solid var(--divider-color); background: var(--card-background-color); color: inherit; }
  input[type=number] { width: 5em; }
  ul.presets { list-style: none; padding: 0; margin: 0 0 12px; display: grid; gap: 8px; }
  ul.presets li { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
  .summary { color: var(--secondary-text-color); flex: 1; min-width: 8em; }
  .actions { display: flex; gap: 6px; }
  ol { padding-left: 20px; display: grid; gap: 8px; }
  ol li { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
  .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .grow { flex: 1; }
  .warn { color: var(--error-color); margin: 0; }
  .note { color: var(--secondary-text-color); margin: 0; }
`;

customElements.define("appliance-presets-panel", AppliancePresetsPanel);
