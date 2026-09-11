#!/usr/bin/with-contenv bashio
# ---------------------------------------------------------------------------
# Greenery S Farm Bridge - add-on entrypoint
#
# The whole point of running this as an add-on: Supervisor already knows the
# MQTT broker's address and credentials, so the operator never types them.
# That single fact removes the most failure-prone step in the manual setup -
# the homeassistant.local / wrong-password problem that the Windows installer
# needs a third of its code to defend against.
# ---------------------------------------------------------------------------

set -e

bashio::log.info "Greenery S Farm Bridge starting..."

# --- Farm endpoint --------------------------------------------------------
FARM_HOST=$(bashio::config 'farm_host')
FARM_PORT=$(bashio::config 'farm_port')
export FARM_SSE_URL="http://${FARM_HOST}:${FARM_PORT}/farm-data"
bashio::log.info "Farm endpoint: ${FARM_SSE_URL}"

# --- MQTT: Supervisor first, manual override second -----------------------
MQTT_HOST=$(bashio::config 'mqtt_host')

if bashio::var.has_value "${MQTT_HOST}"; then
    bashio::log.info "Using the MQTT broker configured in the add-on options"
    export FARM_MQTT_HOST="${MQTT_HOST}"
    export FARM_MQTT_PORT="$(bashio::config 'mqtt_port')"
    export FARM_MQTT_USER="$(bashio::config 'mqtt_user')"
    export FARM_MQTT_PASS="$(bashio::config 'mqtt_password')"

    if [ "${FARM_MQTT_PORT}" = "0" ] || [ -z "${FARM_MQTT_PORT}" ]; then
        export FARM_MQTT_PORT="1883"
    fi

elif bashio::services.available "mqtt"; then
    bashio::log.info "Using the MQTT broker supplied by Home Assistant"
    export FARM_MQTT_HOST="$(bashio::services mqtt 'host')"
    export FARM_MQTT_PORT="$(bashio::services mqtt 'port')"
    export FARM_MQTT_USER="$(bashio::services mqtt 'username')"
    export FARM_MQTT_PASS="$(bashio::services mqtt 'password')"

else
    bashio::log.fatal "No MQTT broker found."
    bashio::log.fatal ""
    bashio::log.fatal "Fix it one of these two ways:"
    bashio::log.fatal "  1. Install and START the Mosquitto broker add-on."
    bashio::log.fatal "     Nothing else needed - this add-on picks it up."
    bashio::log.fatal "  2. Or fill in mqtt_host / mqtt_user / mqtt_password"
    bashio::log.fatal "     in this add-on's Configuration tab."
    bashio::exit.nok
fi

bashio::log.info "MQTT broker: ${FARM_MQTT_HOST}:${FARM_MQTT_PORT}"

# --- Reachability check ---------------------------------------------------
# Checked here rather than left to the bridge's retry loop, because "cannot
# reach the farm" and "broker refused us" look identical in a reconnect log
# and send people hunting the wrong problem.
if command -v nc >/dev/null 2>&1 && ! nc -z -w 5 "${FARM_HOST}" "${FARM_PORT}" 2>/dev/null; then
    bashio::log.warning "Cannot reach the farm at ${FARM_HOST}:${FARM_PORT}"
    bashio::log.warning "The bridge will keep retrying. Check that the hub"
    bashio::log.warning "controller is powered and on this network, and that"
    bashio::log.warning "farm_host in the Configuration tab is correct."
fi

# --- Log level ------------------------------------------------------------
case "$(bashio::config 'log_level')" in
    debug)   export FARM_LOG_LEVEL="DEBUG" ;;
    warning) export FARM_LOG_LEVEL="WARNING" ;;
    error)   export FARM_LOG_LEVEL="ERROR" ;;
    *)       export FARM_LOG_LEVEL="INFO" ;;
esac

bashio::log.info "Starting bridge"
exec /opt/venv/bin/python3 -u /app/farm_bridge.py
