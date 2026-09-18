"""Constants for the Appliance Presets integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "appliance_presets"
PLATFORMS: list[Platform] = [Platform.SENSOR]

# The appliance integration whose entities we drive.
APPLIANCE_INTEGRATION = "homeconnect_ws"

# Config entry data / options
CONF_DEVICE_ID = "device_id"
CONF_PREHEAT_MINUTES = "preheat_minutes"
CONF_MAX_TOTAL_MINUTES = "max_total_minutes"
CONF_ABORT_ON_DOOR_OPEN = "abort_on_door_open"
CONF_DOOR_MIN_SETPOINT = "door_min_setpoint"
CONF_DOOR_GRACE_SECONDS = "door_grace_seconds"
CONF_REQUIRE_HOME = "require_home"
CONF_PERSON_ENTITIES = "person_entities"

DEFAULT_PREHEAT_MINUTES = 15
DEFAULT_MAX_TOTAL_MINUTES = 12 * 60
DEFAULT_ABORT_ON_DOOR_OPEN = True
# Opening the door above this setpoint starts the grace timer. Below it (a
# low roast, a warming hold) an open door is left alone.
DEFAULT_DOOR_MIN_SETPOINT = 150
# Long enough to slide a pizza in after preheat, short enough to catch a door
# left open on a 250 °C oven.
DEFAULT_DOOR_GRACE_SECONDS = 120
DEFAULT_REQUIRE_HOME = False

# Step kinds
STEP_TIMED = "timed"  # run for duration_minutes
STEP_PREHEAT = "preheat"  # run until current temperature reaches the setpoint
STEP_HOLD = "hold"  # run until aborted (bounded by max_total_minutes)
STEP_KINDS = (STEP_TIMED, STEP_PREHEAT, STEP_HOLD)

# Preheat is considered reached within this many degrees of the setpoint.
PREHEAT_TOLERANCE = 5
DEFAULT_PREHEAT_TIMEOUT_MINUTES = 45

# Transition workflow timing (seconds)
STOP_TIMEOUT = 60
START_TIMEOUT = 30
START_ATTEMPTS = 2
# After a restart, how long to wait for the appliance to report again before
# deciding what state it is in.
RECOVERY_TIMEOUT = 180
# A scheduled start missed by more than this (HA was down) is not started late.
MISSED_START_GRACE = 15 * 60

# Runner states (sensor state)
STATE_IDLE = "idle"
STATE_SCHEDULED = "scheduled"
STATE_RUNNING = "running"
STATE_FINISHED = "finished"
STATE_FAILED = "failed"
ACTIVE_STATES = {STATE_SCHEDULED, STATE_RUNNING}

# Normalised appliance operation states (see appliance.normalize_operation)
OP_OFF = "off"
OP_READY = "ready"
OP_DELAYED = "delayed"
OP_RUNNING = "running"
OP_PAUSED = "paused"
OP_FINISHED = "finished"
OP_ERROR = "error"
OP_ACTION_REQUIRED = "action_required"
OP_ABORTING = "aborting"
OP_OFFLINE = "offline"
OP_UNKNOWN = "unknown"
BUSY_OPERATIONS = {OP_DELAYED, OP_RUNNING, OP_PAUSED, OP_ACTION_REQUIRED, OP_ABORTING}

# Services
SERVICE_RUN = "run"
SERVICE_ABORT = "abort"
SERVICE_SAVE = "save"
SERVICE_DELETE = "delete"

ATTR_PRESET = "preset"
ATTR_START_AT = "start_at"
ATTR_READY_AT = "ready_at"
ATTR_CONFIRM = "confirm"

# Events: hass.bus event type is EVENT_TYPE, payload has "type" set to one of EVENT_*
EVENT_TYPE = f"{DOMAIN}_event"
EVENT_SCHEDULED = "scheduled"
EVENT_STARTED = "started"
EVENT_STEP_STARTED = "step_started"
EVENT_STEP_FINISHED = "step_finished"
EVENT_FINISHED = "finished"
EVENT_ABORTED = "aborted"
EVENT_FAILED = "failed"

# Failure reasons carried by EVENT_FAILED and the last_error attribute
REASON_REMOTE_START = "remote_start_unavailable"
REASON_BUSY = "appliance_busy"
REASON_OFFLINE = "appliance_offline"
REASON_START_FAILED = "start_failed"
REASON_STOP_FAILED = "stop_failed"
REASON_INVALID_PROGRAM = "invalid_program"
REASON_INVALID_SETPOINT = "invalid_setpoint"
REASON_MISSING_ROLE = "missing_role"
REASON_PREHEAT_TIMEOUT = "preheat_timeout"
REASON_DOOR_OPENED = "door_opened"
REASON_MAX_DURATION = "max_duration"
REASON_NOBODY_HOME = "nobody_home"
REASON_INTERRUPTED = "interrupted"
REASON_MISSED_START = "missed_start"
REASON_APPLIANCE_STOPPED = "appliance_stopped"

# Storage
STORAGE_VERSION = 1
PRESETS_STORAGE_KEY = f"{DOMAIN}.presets"
RUN_STORAGE_KEY = f"{DOMAIN}.run"  # suffixed with the config entry id

# Frontend panel
PANEL_URL_PATH = "appliance-presets"
PANEL_COMPONENT = "appliance-presets-panel"
PANEL_STATIC_URL = f"/{DOMAIN}_static"
