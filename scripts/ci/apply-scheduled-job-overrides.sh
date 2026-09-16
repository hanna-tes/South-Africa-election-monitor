#!/usr/bin/env bash
set -euo pipefail

manifest_path="${1:-infra/scheduled-jobs.yml}"
job_name="${SCHEDULED_JOB_NAME:-}"
schedule_name="${SCHEDULE_NAME:-}"
group_name="${SCHEDULE_GROUP_NAME:-default}"

log() { echo "[scheduled-jobs] $*"; }
fail() {
  echo "[scheduled-jobs] ERROR - $*" >&2
  exit 1
}

strip_yaml_scalar() {
  sed -E \
    -e 's/^[[:space:]]+//' \
    -e 's/[[:space:]]+$//' \
    -e 's/^"//' \
    -e 's/"$//' \
    -e "s/^'//" \
    -e "s/'$//"
}

extract_job_field() {
  local field_name="$1"
  awk -v wanted_job="$job_name" -v field_name="$field_name" '
    /^[[:space:]]*-[[:space:]]*name:[[:space:]]*/ {
      value=$0
      sub(/^[[:space:]]*-[[:space:]]*name:[[:space:]]*/, "", value)
      gsub(/^["'\'']|["'\'']$/, "", value)
      in_job=(value == wanted_job)
      next
    }
    in_job && $0 ~ "^[[:space:]]*" field_name ":[[:space:]]*" {
      value=$0
      sub("^[[:space:]]*" field_name ":[[:space:]]*", "", value)
      print value
      exit
    }
  ' "$manifest_path" | strip_yaml_scalar
}

[[ -n "$job_name" ]] || fail "SCHEDULED_JOB_NAME is required."
[[ -f "$manifest_path" ]] || fail "scheduled job override file not found: $manifest_path"
[[ -n "$schedule_name" ]] || fail "SCHEDULE_NAME is required."
command -v aws >/dev/null 2>&1 || fail "aws CLI is required."
command -v jq >/dev/null 2>&1 || fail "jq is required."

if grep -q 'serviceLogicalName' "$manifest_path"; then
  fail "serviceLogicalName is not supported. Use targetService so the target is explicit in review."
fi

target_service="$(extract_job_field targetService)"
container_name="$(extract_job_field containerName)"
schedule_expression="$(extract_job_field schedule)"
timezone="$(extract_job_field timezone)"
command_json="$(extract_job_field command)"

[[ "$target_service" == "ethiopian-election-monitor" ]] || fail "job '$job_name' targetService must be 'ethiopian-election-monitor'; got '$target_service'."
[[ "$container_name" == "web" ]] || fail "job '$job_name' containerName must be 'web'; got '$container_name'."
[[ -n "$schedule_expression" ]] || fail "job '$job_name' schedule is required."
[[ -n "$timezone" ]] || fail "job '$job_name' timezone is required."
printf '%s' "$command_json" | jq -e 'type == "array" and length > 0 and all(.[]; type == "string" and length > 0)' >/dev/null ||
  fail "job '$job_name' command must be a non-empty JSON string array on one line."

log "Applying override for approved job '$job_name'."
log "AWS schedule: $schedule_name (group=$group_name)"
log "Target: service=$target_service container=$container_name"
log "Requested schedule: $schedule_expression ($timezone)"
log "Requested command: $command_json"

current_schedule="$(mktemp)"
updated_schedule="$(mktemp)"
trap 'rm -f "$current_schedule" "$updated_schedule"' EXIT

log "Reading current AWS Scheduler target..."
aws scheduler get-schedule \
  --name "$schedule_name" \
  --group-name "$group_name" \
  > "$current_schedule"

old_expression="$(jq -r '.ScheduleExpression' "$current_schedule")"
old_timezone="$(jq -r '.ScheduleExpressionTimezone // ""' "$current_schedule")"
old_command="$(jq -r --arg container "$container_name" '.Target.Input | fromjson | .runTaskInput.overrides.containerOverrides[] | select(.name == $container) | .command | @json' "$current_schedule")"

log "Current schedule: $old_expression (${old_timezone:-<none>})"
log "Current command: ${old_command:-<not found>}"

if [[ -z "$old_command" ]]; then
  fail "could not find container override for '$container_name' in scheduler target input."
fi

jq \
  --arg expression "$schedule_expression" \
  --arg timezone "$timezone" \
  --arg container "$container_name" \
  --argjson command "$command_json" \
  '
    .ScheduleExpression = $expression
    | .ScheduleExpressionTimezone = $timezone
    | .Target.Input = (
        (.Target.Input | fromjson)
        | .runTaskInput.overrides.containerOverrides = (
            .runTaskInput.overrides.containerOverrides
            | map(if .name == $container then .command = $command else . end)
          )
        | tojson
      )
  ' "$current_schedule" > "$updated_schedule"

if [[ "${DRY_RUN:-false}" == "true" ]]; then
  log "DRY_RUN=true; not updating AWS Scheduler."
  jq '{Name, ScheduleExpression, ScheduleExpressionTimezone, Target}' "$updated_schedule"
  exit 0
fi

log "Updating AWS Scheduler target..."
aws scheduler update-schedule \
  --name "$schedule_name" \
  --group-name "$group_name" \
  --schedule-expression "$(jq -r '.ScheduleExpression' "$updated_schedule")" \
  --schedule-expression-timezone "$(jq -r '.ScheduleExpressionTimezone' "$updated_schedule")" \
  --flexible-time-window "$(jq -c '.FlexibleTimeWindow' "$updated_schedule")" \
  --target "$(jq -c '.Target' "$updated_schedule")"

log "OK - scheduled job override applied."
