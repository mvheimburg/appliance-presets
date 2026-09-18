# Appliance Presets

A Home Assistant integration for **staged presets**: named, multi-step
programmes for Home Connect appliances that neither the appliance nor Home
Connect can express on their own.

```
Pizza                               Langtidsstek
  1. HotAir  250 °C  preheat          1. HotAir  220 °C  20 min
  2. HotAir  250 °C  12 min           2. HotAir   75 °C  6 h
  3. Grill   220 °C  3 min            3. HotAir   50 °C  hold until aborted
```

Companion to [lovelace-appliance-panel](https://github.com/mvheimburg/lovelace-appliance-panel),
which shows and controls the appliances themselves. This integration only adds
presets.

> **Status: 0.1, not yet run against a real oven.** The engine is tested
> against a faked Home Connect Local oven. Before trusting it with dinner, run
> two programmes back to back from Home Assistant and check whether the second
> one needs remote start to be armed again at the appliance (see *Remote start*
> below).

## Installation

HACS → Integrations → custom repository `mvheimburg/appliance-presets`, or copy
`custom_components/appliance_presets` into your config. Then add one entry per
appliance under **Settings → Devices & services → Add integration → Appliance
Presets**. Only devices from Home Connect Local
(`homeconnect_ws`) that expose programme, start and abort controls can be added.

A **Presets** item appears in the sidebar for building and running presets.

## What you get

| Thing | What it is |
|---|---|
| `sensor.<appliance>_preset` | `idle`, `scheduled`, `running`, `finished` or `failed`. Attached to the appliance's own device. |
| Sidebar panel | Lists appliances and presets, edits steps and runs a preset now or ready at a time. |
| `appliance_presets.run` | `preset`, `start_at` or `ready_at`, `confirm` (must be true). Targets the status sensor. |
| `appliance_presets.abort` | Stops the preset and the appliance programme. Never needs confirmation. |
| `appliance_presets.save` / `delete` | Manage presets from scripts. Presets are stored in `.storage`, not YAML. |
| `appliance_presets_event` | `type`: `scheduled`, `started`, `step_started`, `step_finished`, `finished`, `aborted`, `failed` (with `reason`). |

Sensor attributes: `preset`, `step_index`, `step_count`, `step_name`,
`step_kind`, `step_program`, `step_setpoint`, `step_ends_at`, `started_at`,
`start_at`, `ready_at`, `estimated_finish`, `last_error`, `presets`,
`remote_start`.

### Step kinds

| Kind | Ends when | Appliance's own timer set to |
|---|---|---|
| `timed` | `duration_minutes` has passed | the step duration |
| `preheat` | current temperature is within 5 °C of `setpoint` (fails after `timeout_minutes`, default 45) | the timeout |
| `hold` | aborted, or `max_total_minutes` is reached. Only allowed as the last step. | what is left of `max_total_minutes` |

`ready_at` works backwards from the sum of the step durations, using the
*preheat estimate* option for preheat steps. The run itself waits for the
real temperature.

```yaml
action: appliance_presets.run
target:
  entity_id: sensor.dampovn_preset
data:
  preset: Pizza
  ready_at: "2026-09-18 18:00"
  confirm: true
```

## How a step transition works

Home Connect cannot change a running programme. Each step boundary is
therefore **abort → wait until stopped → check remote start → select programme
→ set temperature and duration → start → wait until running**. A step that
does not start is retried once. If it still fails, the preset fails with a
reason, and the appliance is left aborted. The oven coasts through the gap,
which normally takes a few seconds.

## Safety

- **Start needs `confirm: true`. Abort never needs confirmation.**
- **Remote start** is checked before scheduling and again at every
  transition. Many models clear it after each programme. The preset then fails
  with `remote_start_unavailable` and does not retry forever.
- **The appliance's own duration** is set on every step, so the oven stops by
  itself even if Home Assistant dies mid-run.
- **`max_total_minutes`** (default 12 h) aborts the preset and powers the
  appliance down.
- **Door left open**: if the door stays open longer than the grace period
  (default 120 s) while the setpoint is at least 150 °C, the preset aborts.
  The grace period leaves time to put the food in after preheating.
- **`require_home`** (default off) refuses to start while none of the chosen
  people are home.
- **Busy appliance**: if something else is already running when the preset
  starts, the preset fails with `appliance_busy` and leaves that programme alone.
- **Restart**: a scheduled preset is re-armed when Home Assistant starts. A
  start missed by more than 15 minutes is not run late. A running preset
  re-attaches if the appliance is still running. If the appliance stopped
  partway through a step, the preset fails with `interrupted`; it never
  restarts a hot oven blindly.

## Why an integration and not a Home Assistant app

An app (formerly add-on) would give a full-page UI through ingress. It would
cost more than it gives:

- **The timer has to live next to the state machine.** An app talks to Home
  Assistant over the WebSocket API, so every transition becomes a remote call.
  It also has to handle reconnects, and a restart of either side stops the
  roast until they reconnect. An integration reads states and presses buttons
  in-process, and its timers use Home Assistant's own scheduler.
- **Apps only run on Home Assistant OS and Supervised.** Container and Core
  installs cannot run them.
- **The UI does not need an app.** An integration can serve its own sidebar
  panel, as HACS does. This one registers `/appliance-presets` and serves a web
  component from `custom_components/appliance_presets/frontend/`. That panel
  gets the logged-in `hass` object, the theme and the WebSocket connection
  directly.
- **Entities, services, events and config entries** come for free and are
  what automations hook into ("steken er ferdig" on the kitchen speakers).

An app would be worth it if presets needed something Home Assistant cannot
host: heavy recipe content, image uploads, a separate database. That is out of
scope.

## Role resolution

The engine finds entities by **role** (operation state, programme select,
start/abort buttons, remote start, setpoint, current temperature, duration,
door, power). It uses Home Connect Local unique-id keys first and known
entity-id spellings second, so renamed entities still work. The table in
`roles.py` is a subset of the card's `src/roles.ts` and must agree with it.
A shared table is the stated goal (spec open question 4).

## Development

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements_test.txt ruff
.venv/bin/python -m pytest -q
.venv/bin/ruff check custom_components tests
```

Bump the version in both `pyproject.toml` and `manifest.json`. CI fails if
they differ, and merging to `main` tags `v<version>`.

## Not yet

- `select` / `button` / `number` entities for arming a preset from a
  dashboard. Today this is done with the sensor plus services.
- Per-step programme options (steam level, fast preheat, microwave power).
- A learned preheat estimate. The fixed *preheat estimate* option is the first
  cut the spec calls for.
- Pause/resume of a preset. If the appliance is paused, the preset's step
  timer keeps running.
- Moving the panel to a Lit + TypeScript build shared with the cards.
- Home Connect cloud integration support. The role table has the cloud
  spellings, but the config flow only offers `homeconnect_ws` devices.
