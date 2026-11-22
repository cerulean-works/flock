#!/usr/bin/env bash
# Starts one ephemeral GitHub Actions runner for one job, then exits so Docker restarts it clean.
#
# 1. As `runner`: wipe everything the previous job could have written, and copy a fresh runner
#    from the root-owned /opt/actions-runner.
# 2. As root: sign in as the GitHub App and register a just-in-time runner. The App's key lives
#    only in this root process's environment, which `runner` can't read.
# 3. Run the runner as `runner`, with an environment that holds no credentials. A just-in-time
#    runner takes exactly one job and then deregisters itself.
set -euo pipefail

: "${GITHUB_APP_ID:?Set GITHUB_APP_ID to the GitHub App's ID.}"
: "${GITHUB_APP_PRIVATE_KEY_B64:?Set GITHUB_APP_PRIVATE_KEY_B64 to the App's private key, base64-encoded.}"
: "${RUNNER_REPOSITORY:?Set RUNNER_REPOSITORY to owner/repo.}"
RUNNER_LABELS=${RUNNER_LABELS:-flock-ci}

if [ "$(id -u)" != 0 ]; then
  echo "The entrypoint must start as root so it can keep the App key from jobs." >&2
  exit 1
fi

# Sleep before exiting on an error, so Docker's restart loop doesn't hammer GitHub's API.
fail() {
  echo "flock-runner: $*" >&2
  sleep 30
  exit 1
}

private_key=$(printf '%s' "$GITHUB_APP_PRIVATE_KEY_B64" | base64 -d) ||
  fail "GITHUB_APP_PRIVATE_KEY_B64 isn't valid base64."
unset GITHUB_APP_PRIVATE_KEY_B64

# A clean environment for everything that runs as `runner`.
runner_env=(
  HOME=/home/runner
  USER=runner
  LANG=C.UTF-8
  PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
  ImageOS=ubuntu24
  DOTNET_RUNNING_IN_CONTAINER=true
  RUNNER_MANUALLY_TRAP_SIG=1
  ACTIONS_RUNNER_PRINT_LOG_TO_STDOUT=1
)
as_runner() {
  setpriv --reuid=runner --regid=runner --clear-groups -- env -i "${runner_env[@]}" "$@"
}

# 1. Clean slate. /home/runner is this container's own volume and /tmp a tmpfs, so nothing here
# reaches other containers, but both outlive a restart.
# A job may have left directories it can't list, so make them deletable first.
as_runner find /home/runner /tmp /dev/shm -mindepth 1 -type d -exec chmod u+rwx {} + 2>/dev/null || true
as_runner find /home/runner /tmp /dev/shm -mindepth 1 -delete ||
  fail "couldn't clean up after the previous job."
as_runner cp -a /opt/actions-runner /home/runner/actions-runner

# 2. Register a just-in-time runner.
b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }
now=$(date +%s)
header=$(printf '{"alg":"RS256","typ":"JWT"}' | b64url)
payload=$(printf '{"iat":%d,"exp":%d,"iss":"%s"}' "$((now - 60))" "$((now + 540))" "$GITHUB_APP_ID" | b64url)
signature=$(printf '%s.%s' "$header" "$payload" |
  openssl dgst -sha256 -sign <(printf '%s\n' "$private_key") | b64url) ||
  fail "couldn't sign with the App's private key."
jwt="$header.$payload.$signature"
unset private_key

api() {
  curl --silent --show-error --fail-with-body --retry 3 \
    -H "Accept: application/vnd.github+json" -H "X-GitHub-Api-Version: 2022-11-28" "$@"
}

installation_id=$(api -H "Authorization: Bearer $jwt" \
  "https://api.github.com/repos/$RUNNER_REPOSITORY/installation" | jq -er .id) ||
  fail "the App isn't installed on $RUNNER_REPOSITORY."

# The token can only manage runners of this one repository, and is revoked once used.
token=$(jq -n --arg repo "${RUNNER_REPOSITORY#*/}" \
  '{repositories: [$repo], permissions: {administration: "write"}}' |
  api -X POST -H "Authorization: Bearer $jwt" --data @- \
    "https://api.github.com/app/installations/$installation_id/access_tokens" | jq -er .token) ||
  fail "couldn't get an installation token."
unset jwt

name="flock-$(hostname)-$(openssl rand -hex 3)"
jitconfig=$(jq -n --arg name "$name" --arg labels "$RUNNER_LABELS" \
  '{name: $name, runner_group_id: 1, labels: ($labels | split(",")), work_folder: "_work"}' |
  api -X POST -H "Authorization: Bearer $token" --data @- \
    "https://api.github.com/repos/$RUNNER_REPOSITORY/actions/runners/generate-jitconfig" |
  jq -er .encoded_jit_config) || fail "couldn't register a runner."

api -X DELETE -H "Authorization: Bearer $token" https://api.github.com/installation/token >/dev/null ||
  echo "flock-runner: couldn't revoke the installation token; it expires within an hour." >&2
unset token

echo "flock-runner: registered $name with labels $RUNNER_LABELS."

# 3. Take one job.
exec setpriv --reuid=runner --regid=runner --clear-groups -- \
  env -i -C /home/runner/actions-runner "${runner_env[@]}" ./run.sh --jitconfig "$jitconfig"
