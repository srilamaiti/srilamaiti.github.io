#!/usr/bin/env bash
# Daily AI News Tracker -- grants that require workspace-admin privileges.
# Idempotent: re-running is safe.
#
# Required Databricks CLI auth (admin identity):
#   databricks auth login --host <<..>>

set -euo pipefail

APP_NAME="daily-ai-news-tracker"
WAREHOUSE_ID="76f5a569205afced"
GROUP_NAME="AAD-APP-SPDD-DBX-DataScientist"

echo "Granting CAN_USE on app '${APP_NAME}' to group '${GROUP_NAME}'..."
databricks apps set-permissions "${APP_NAME}" --json "$(cat <<JSON
{
  "access_control_list": [
    {"group_name": "${GROUP_NAME}", "permission_level": "CAN_USE"}
  ]
}
JSON
)"

echo "Granting CAN_USE on SQL warehouse '${WAREHOUSE_ID}' to '${GROUP_NAME}'..."
databricks permissions update sql/warehouses "${WAREHOUSE_ID}" --json "$(cat <<JSON
{
  "access_control_list": [
    {"group_name": "${GROUP_NAME}", "permission_level": "CAN_USE"}
  ]
}
JSON
)"

echo "Done. Verify:"
echo "  databricks apps get-permissions ${APP_NAME}"
echo "  databricks permissions get sql/warehouses ${WAREHOUSE_ID}"
