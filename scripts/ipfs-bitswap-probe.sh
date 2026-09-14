#!/usr/bin/env bash
#
# ipfs-bitswap-probe.sh
#
# Confirms or refutes one specific hypothesis about stalled IPFS replication
# between two Kubo nodes:
#
#   Bitswap never sends a WANT to a provider whose connection already existed
#   before Bitswap registered its libp2p connection notifier. bsnet registers
#   that notifier in Start(), and libp2p only reports connections opened after
#   that moment. A peer connected earlier -- for example because it is listed in
#   Bootstrap before `ipfs daemon` starts -- is therefore never recorded by the
#   Bitswap client. PeerManager.SendWants() then returns false without logging
#   anything: no WANT, no stream, block and byte counters stay at zero and
#   `partners` stays at 0 for the life of that connection. A fresh connect event
#   (disconnect + connect) is expected to heal it.
#
# This explains the observed symptom: `ipfs routing findprovs` finds the
# provider, Bitswap logs "Found peer for CID" and "Added peer to session", and
# the fetch then hangs until the caller's context is cancelled, with zero bytes
# moved on both nodes and no resource-manager rejection.
#
# Modes
#   inplace   probe an already running two-Kubo deployment (default: the
#             dark-operator-local-ha compose project)
#   isolated  build a throwaway two-Kubo pair on a private Docker network and
#             run both connection orderings: connected after startup (control,
#             expected to work) and connected before startup (bootstrap in the
#             config, expected to stall)
#
# Both modes run the same matrix: an initial fetch in each direction, a fresh
# connect in each direction, then the same fetches again. inplace also raises the
# Bitswap log level at runtime and counts the discriminating log lines; isolated
# sets GOLOG_LOG_LEVEL on the containers and snapshots the log before and after
# the fresh connect, which separates "never sent a want" from "sent and lost".
#
# Exit codes
#   0  hypothesis confirmed: the stall reproduces and a fresh connect cures it
#   2  hypothesis not confirmed in this environment
#   1  argument or environment error
#
# Reasoning, evidence and closure criteria:
#   docs/ipfs-private-swarm-bitswap-replication-review-2026-09-14.md
#
# Portable on purpose: bash 3.2 (the /bin/bash shipped with macOS) is the floor.
# No associative arrays, no `declare -g`, no mapfile. Small per-node state lives
# in files under a temporary directory, and there is a single exit trap.
#
set -euo pipefail

PROGNAME="${0##*/}"

IMAGE=""
FETCH_TIMEOUT=15
GRACE=5
DRY_RUN=0
KEEP=0
CLEANUP=0
PNET=0
SWARM_KEY=""
MODE=""
SERVER_CONTAINER=""
FETCHER_CONTAINER=""

NET_NAME="dark-bitswap-probe"
NODE_A="dark-probe-a"
NODE_B="dark-probe-b"
STATE_DIR=""

# Results of the last matrix. Plain globals on purpose: bash 3.2 (the /bin/bash
# that ships with macOS) has neither `declare -g` nor associative arrays, and
# this script has to run there too.
MATRIX_XY_INITIAL=""
MATRIX_YX_INITIAL=""
MATRIX_XY_AFTER=""
MATRIX_YX_AFTER=""
MATRIX_CONFIRMED=0

# Bitswap/libp2p subsystems worth capturing. `ipfs log ls` inside a container
# lists the real names if any of these ever drift.
LOG_LEVEL="bitswap=debug,bitswap/client=debug,bitswap/bsnet=debug,bitswap/connevtman=debug,bitswap/session=debug,routing/provqrymgr=debug"

# Subsystems raised to debug at runtime by the inplace mode. Names differ between
# releases, so every call is best effort and failures are ignored.
LOG_SUBSYSTEMS="bitswap bitswap/client bitswap/session bitswap/client/sesspeermgr bitswap/client/peermgr bitswap/client/msgq bitswap/bsnet bitswap/connevtman bitswap/server/decision routing/provqrymgr"
LOG_SINCE=""

# --------------------------------------------------------------------------
# output helpers
# --------------------------------------------------------------------------

step() { printf '\n== %s\n' "$*"; }
say()  { printf '%s\n' "$*"; }
note() { printf '  + %s\n' "$*" >&2; }
warn() { printf '!! %s\n' "$*" >&2; }
die()  { printf '!! %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<EOF
Usage:
  $PROGNAME inplace  [--server NAME] [--fetcher NAME] [--timeout SECS] [--cleanup] [--dry-run]
  $PROGNAME isolated [--image IMAGE] [--timeout SECS] [--grace SECS] [--pnet] [--keep] [--dry-run]
  $PROGNAME --help

inplace
  Probes two Kubo containers that are already running and connected. With no
  --server/--fetcher, both are discovered from the compose service labels
  (ipfs-storage-a / ipfs-storage-b). This touches the running deployment: it
  adds one raw diagnostic block to each node, briefly drops each node's
  connection to the other, and raises the Bitswap log level to debug for the
  duration of the probe (no restart; the previous levels are restored at the
  end, unless they could not be read).

isolated
  Creates its own Docker network and a throwaway Kubo pair. Nothing is written
  outside Docker; everything is removed at the end unless --keep is given.
  With --pnet both nodes also share a freshly generated swarm.key, which is the
  private-swarm part of the production setup. The stall reproduces with and
  without it, so run both if you want the comparison.

--dry-run
  Prints the plan and the outcome the hypothesis predicts. Executes nothing, so
  it also works on a machine without Docker.
EOF
}

# --------------------------------------------------------------------------
# docker plumbing (dry-run aware)
# --------------------------------------------------------------------------

dx() { # dx <docker args...>
  if [ "$DRY_RUN" = 1 ]; then note "docker $*"; return 0; fi
  docker "$@"
}

dx_quiet() {
  if [ "$DRY_RUN" = 1 ]; then note "docker $*"; return 0; fi
  docker "$@" >/dev/null 2>&1 || true
}

nx() { # nx <container> <shell command...>
  local c="$1"; shift
  if [ "$DRY_RUN" = 1 ]; then note "docker exec $c sh -c $*"; return 0; fi
  docker exec "$c" sh -c "$*"
}

capture() { # capture <container> <shell command...> : stdout only, never fails
  local c="$1"; shift
  if [ "$DRY_RUN" = 1 ]; then note "docker exec $c sh -c $*"; stub_for "$*"; return 0; fi
  local out
  out="$(docker exec "$c" sh -c "$*" 2>/dev/null)" || out=""
  printf '%s' "$(printf '%s' "$out" | tr -d '\r')"
}

# --- dry-run stubs. They model what the hypothesis predicts, so --dry-run
# --- prints a coherent plan and a predicted verdict. Never used for real runs.
# --- The stub state lives in a file because command substitution runs in a
# --- subshell: a variable set inside "$(...)" would never come back.

STUB_SERVER_ID="12D3KooWStubProvider0000000000000000000000000"
STUB_CID="bafkreistubdiagnosticblock000000000000000000000000000000000"
STUB_BROKEN=0                 # 1 = the connection predates Bitswap (stall expected)
STUB_STATE=""

stub_init()   { STUB_STATE="$(mktemp)"; : >"$STUB_STATE"; }
stub_mark()   { [ -n "$STUB_STATE" ] && printf '%s\n' "$1" >>"$STUB_STATE"; return 0; }
stub_has()    { [ -n "$STUB_STATE" ] && grep -qxF -- "$1" "$STUB_STATE"; }
stub_clear()  { [ -n "$STUB_STATE" ] && : >"$STUB_STATE"; return 0; }

stub_for() {
  case "$*" in
    *"ipfs id"*)       echo "$STUB_SERVER_ID" ;;
    *"block put"*)     echo "$STUB_CID" ;;
    *findprovs*)       echo "$STUB_SERVER_ID" ;;
    *"swarm peers"*)   printf '/dns4/%s/tcp/4001/p2p/%s\n' "$NODE_A" "$STUB_SERVER_ID" ;;
    *"swarm addrs"*)   printf '/dns4/%s/tcp/4001/p2p/%s\n' "$NODE_A" "$STUB_SERVER_ID" ;;
    *"stats bitswap"*) if stub_has fetched; then printf 'bitswap status\n\tpartners [1]\n'; else printf 'bitswap status\n\tpartners [0]\n'; fi ;;
    *"log ls"*)        echo "bitswap" ;;
    *)                 echo "" ;;
  esac
}

wait_api() { # wait_api <container> [seconds]
  local c="$1" limit="${2:-90}" waited=0
  if [ "$DRY_RUN" = 1 ]; then note "wait until the Kubo API answers in $c (up to ${limit}s)"; return 0; fi
  while [ "$waited" -lt "$limit" ]; do
    if docker exec "$c" ipfs id >/dev/null 2>&1; then
      say "  + the Kubo API in $c is up (after ${waited}s)"
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  return 1
}

partners_of() { # partners_of <container> : bitswap partner count, or ?
  printf '%s' "$(capture "$1" "ipfs stats bitswap --human" |
    sed -n 's/.*partners \[\([0-9]*\)\].*/\1/p' | tail -n 1)"
}

wait_bitswap() { # wait_bitswap <container> [seconds] : wait until Bitswap has started
  local c="$1" limit="${2:-30}" waited=0
  if [ "$DRY_RUN" = 1 ]; then note "wait until Bitswap has started in $c"; return 0; fi
  while [ "$waited" -lt "$limit" ]; do
    # This is the actual component endpoint and remains stable even when a
    # Kubo release changes its startup log wording.
    if docker exec "$c" ipfs stats bitswap --human >/dev/null 2>&1; then
      say "  + Bitswap is up in $c (after ${waited}s)"
      return 0
    fi
    # Do not use grep -q here: with pipefail, its early exit can make
    # `docker logs` fail with SIGPIPE after it has already found the line.
    if docker logs "$c" 2>&1 | grep 'broadcast control' >/dev/null; then
      say "  + Bitswap is up in $c (after ${waited}s)"
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  warn "could not confirm from the log that Bitswap started in $c (wording may have"
  warn "changed); continuing after a fixed grace period instead"
  return 0
}

block_stat_line() { # block_stat_line <container> <cid> [timeout] : one line, returns rc
  local c="$1" cid="$2" t="${3:-$FETCH_TIMEOUT}" out rc
  if [ "$DRY_RUN" = 1 ]; then
    note "docker exec $c sh -c \"timeout $t ipfs block stat '$cid'\""
    if [ "$STUB_BROKEN" = 0 ] || stub_has healed; then
      stub_mark fetched; echo "Size: 24"; return 0
    fi
    echo "Error: context canceled"
    return 1
  fi
  out="$(docker exec "$c" sh -c "timeout $t ipfs block stat '$cid'" 2>&1)" && rc=0 || rc=$?
  out="$(printf '%s' "$out" | tr -d '\r' | head -n 1)"
  printf '%s' "$out"
  return "$rc"
}

fetch_status_of() { # fetch_status_of <rc> <output> : ok | hang | error
  local rc="$1" out="$2"
  case "$out" in
    *"context canceled"*|*"context deadline exceeded"*|*"context deadline"*|*"timed out"*)
      printf 'hang'; return 0 ;;
  esac
  if [ "$rc" = 0 ]; then printf 'ok'; return 0; fi
  if [ "$rc" = 124 ]; then printf 'hang'; return 0; fi
  case "$out" in
    ""|*"Error"*|*"error"*) printf 'hang' ;;
    *) printf 'error' ;;
  esac
  return 0
}

# --------------------------------------------------------------------------
# the matrix: initial fetch per direction, fresh connect, fetch again
# --------------------------------------------------------------------------

safe_name() { printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_'; }
state_file() { printf '%s/%s' "$STATE_DIR" "$1"; }

node_id() { # node_id <container>
  local c="$1" f id attempt=0
  f="$(state_file "id.$(safe_name "$c")")"
  if [ -f "$f" ]; then cat "$f"; return 0; fi
  # Kubo can accept the first API request while its local peer identity query
  # is still transiently unavailable (observed in master builds). Do not let a
  # one-shot read turn that startup race into a false Bitswap result.
  while [ "$attempt" -lt 10 ]; do
    id="$(capture "$c" "ipfs id -f='<id>'")"
    case "$id" in
      ""|*"error"*|*" "*) sleep 1; attempt=$((attempt + 1)) ;;
      *) break ;;
    esac
  done
  case "$id" in
    ""|*"error"*|*" "*) die "cannot read the peer ID of $c (is the daemon running?)" ;;
  esac
  printf '%s' "$id" >"$f"
  printf '%s' "$id"
}

cid_of() { # cid_of <container> : the diagnostic block prepared on that node, if any
  local f
  f="$(state_file "cid.$(safe_name "$1")")"
  [ -f "$f" ] && cat "$f" || true
}

reset_node_state() { # reset_node_state <container...> : nodes were recreated
  local c
  for c in "$@"; do
    rm -f "$(state_file "id.$(safe_name "$c")")" \
          "$(state_file "cid.$(safe_name "$c")")"
  done
}

connected_addr_of() { # connected_addr_of <container> <peer id> : multiaddr or empty
  local c="$1" peer="$2" out cands pick
  out="$(capture "$c" "ipfs swarm peers; ipfs swarm addrs")"
  cands="$(printf '%s' "$out" | tr ' ' '\n' | grep -- "/p2p/$peer\$" | grep -v '/ip4/127\.')"
  # An inbound connection leaves the peer's ephemeral source port in the
  # peerstore. Prefer the announced DNS name, then the standard P2P port, and
  # only then whatever is left.
  pick="$(printf '%s' "$cands" | grep '/dns4/[^/]*/tcp/4001/' | head -n 1)"
  [ -n "$pick" ] || pick="$(printf '%s' "$cands" | grep '/tcp/4001/' | head -n 1)"
  [ -n "$pick" ] || pick="$(printf '%s' "$cands" | head -n 1)"
  printf '%s' "$pick"
}

prepare() { # prepare <server> : put and announce one raw diagnostic block
  local server="$1" f cid content
  f="$(state_file "cid.$(safe_name "$server")")"
  if [ -f "$f" ]; then return 0; fi
  # The content must differ per node: an identical payload produces an identical
  # CID, and then the other node already has the block locally, so its "fetch"
  # never touches the network and measures nothing.
  content="bitswap-probe-$(safe_name "$server")-$$-$RANDOM"
  say "  + creating a raw diagnostic block on $server"
  cid="$(capture "$server" "printf '%s' '$content' | ipfs block put --format=raw --mhtype=sha2-256")"
  cid="$(printf '%s' "$cid" | tr -d '\r\n')"
  [ -n "$cid" ] || die "could not create the diagnostic block on $server"
  printf '%s' "$cid" >"$f"
  nx "$server" "ipfs routing provide '$cid'" >/dev/null 2>&1 || true
  say "    cid=$cid"
}

attempt() { # attempt <fetcher> <server> : sets ATTEMPT_STATUS, ATTEMPT_NOTE, ATTEMPT_PARTNERS
  local fetcher="$1" server="$2" cid out rc t="$FETCH_TIMEOUT"
  cid="$(cid_of "$server")"
  [ -n "$cid" ] || die "no diagnostic block prepared on $server"
  say "  + $fetcher fetching $cid from $server (timeout ${FETCH_TIMEOUT}s)"
  case "$(capture "$fetcher" "ipfs routing findprovs '$cid' -n 1")" in
    *"$(node_id "$server")"*) say "    findprovs: provider visible" ;;
    "")                       say "    findprovs: no provider record yet" ;;
    *)                        say "    findprovs: unexpected answer" ;;
  esac
  if [ "$DRY_RUN" = 1 ]; then
    out="$(block_stat_line "$fetcher" "$cid")" && rc=0 || rc=$?
    ATTEMPT_PARTNERS_MID="${STUB_MID_PARTNERS:-0}"
  else
    local out_file rc_file bg
    out_file="$(state_file "fetch.out.$$.$RANDOM")"
    rc_file="$(state_file "fetch.rc.$$.$RANDOM")"
    ( docker exec "$fetcher" sh -c "timeout $t ipfs block stat '$cid'" \
        >"$out_file" 2>&1; printf '%s' "$?" >"$rc_file" ) &
    bg=$!
    sleep 2
    ATTEMPT_PARTNERS_MID="$(partners_of "$fetcher")"
    wait "$bg" || true
    rc="$(cat "$rc_file" 2>/dev/null || echo 1)"
    out="$(tr -d '\r' <"$out_file" 2>/dev/null | head -n 1)"
  fi
  ATTEMPT_STATUS="$(fetch_status_of "$rc" "$out")"
  ATTEMPT_NOTE="$out"
  case "$out" in
    "") ATTEMPT_NOTE="no output: the CLI was stopped before it printed anything" ;;
  esac
  say "    result: $ATTEMPT_STATUS ($ATTEMPT_NOTE)"
  if [ -n "${ATTEMPT_PARTNERS_MID:-}" ]; then
    say "    partners while the fetch was in flight: $ATTEMPT_PARTNERS_MID"
  fi
  ATTEMPT_PARTNERS="$(partners_of "$fetcher")"
}

bounce() { # bounce <fetcher> <server> : drop and re-establish the connection
  local fetcher="$1" server="$2" peer peer_addr
  peer="$(node_id "$server")"
  peer_addr="$(connected_addr_of "$fetcher" "$peer")"
  if [ -z "$peer_addr" ]; then
    warn "no address for $peer known to $fetcher; cannot force a fresh connect"
    return 1
  fi
  say "  + fresh connect in $fetcher: disconnect /p2p/$peer, then connect $peer_addr"
  if [ "$DRY_RUN" = 1 ]; then
    note "docker exec $fetcher ipfs swarm disconnect /p2p/$peer"
    note "docker exec $fetcher ipfs swarm connect $peer_addr"
    stub_mark healed
    return 0
  fi
  if ! docker exec "$fetcher" ipfs swarm disconnect "/p2p/$peer" >/dev/null 2>&1; then
    warn "the explicit disconnect failed in $fetcher; refusing to treat connect as fresh"
    return 1
  fi
  docker exec "$fetcher" ipfs swarm connect "$peer_addr" >/dev/null 2>&1 || \
    warn "the explicit connect failed in $fetcher"
  sleep 2
  return 0
}

run_matrix() { # run_matrix <node x> <node y>
  local x="$1" y="$2" xy_ok=0 yx_ok=0
  [ "$DRY_RUN" = 1 ] && stub_clear

  step "Partners before any fetch"
  say "  $x: $(partners_of "$x")   $y: $(partners_of "$y")"

  step "Initial fetch in both directions, without touching the connections"
  prepare "$y"
  attempt "$x" "$y"; MATRIX_XY_INITIAL="$ATTEMPT_STATUS"
  prepare "$x"
  attempt "$y" "$x"; MATRIX_YX_INITIAL="$ATTEMPT_STATUS"

  step "Same fetches again, after a fresh connect in each direction"
  if bounce "$x" "$y"; then attempt "$x" "$y"; MATRIX_XY_AFTER="$ATTEMPT_STATUS"; else MATRIX_XY_AFTER="skipped"; fi
  if bounce "$y" "$x"; then attempt "$y" "$x"; MATRIX_YX_AFTER="$ATTEMPT_STATUS"; else MATRIX_YX_AFTER="skipped"; fi

  case "$MATRIX_XY_INITIAL:$MATRIX_XY_AFTER" in hang:ok) xy_ok=1 ;; esac
  case "$MATRIX_YX_INITIAL:$MATRIX_YX_AFTER" in hang:ok) yx_ok=1 ;; esac
  MATRIX_CONFIRMED=0
  if [ "$xy_ok" = 1 ] && [ "$yx_ok" = 1 ]; then MATRIX_CONFIRMED=2
  elif [ "$xy_ok" = 1 ] || [ "$yx_ok" = 1 ]; then MATRIX_CONFIRMED=1
  fi
  return 0
}

matrix_report() { # matrix_report <x> <y> [test|control]
  local x="$1" y="$2" kind="${3:-test}"
  step "Result"
  printf '  %-16s %-10s %-12s\n' "direction" "initial" "after connect"
  printf '  %-16s %-10s %-12s\n' "$x -> $y" "$MATRIX_XY_INITIAL" "$MATRIX_XY_AFTER"
  printf '  %-16s %-10s %-12s\n' "$y -> $x" "$MATRIX_YX_INITIAL" "$MATRIX_YX_AFTER"
  say ""
  if [ "$kind" = "control" ]; then
    case "$MATRIX_XY_INITIAL:$MATRIX_YX_INITIAL" in
      ok:ok) say "  Control OK: with the connection made after startup, both directions"
             say "  transfer blocks. The plumbing itself is fine."
             return 0 ;;
      *)     say "  Control FAILED: a connection made after startup should always be enough,"
             say "  so this run cannot test the hypothesis. Try a longer --grace."
             return 2 ;;
    esac
  fi
  case "$MATRIX_CONFIRMED" in
    2) say "  CONFIRMED: both directions stalled while the connection predated Bitswap's"
       say "  notifier, and one fresh connect event cured both."
       return 0 ;;
    1) say "  CONFIRMED in one direction. The other direction either did not stall, or was"
       say "  already healed by the first fresh connect."
       return 0 ;;
  esac
  case "$MATRIX_XY_INITIAL:$MATRIX_YX_INITIAL" in
    ok:ok) say "  NOT reproduced: both directions already work. Nothing to reproduce here." ;;
    *) say "  NOT confirmed: at least one fetch still failed after a fresh connect, which"
       say "  points away from connection registration. While a fetch is hung, collect a"
       say "  goroutine dump and look for a stream-open error or a resource-manager"
       say "  rejection in the daemon log:"
       say "    docker exec <fetcher> curl -s 'http://127.0.0.1:5001/debug/pprof/goroutine?debug=2'"
       say "    docker exec <fetcher> ipfs diag profile"
       say "    docker exec <fetcher> ipfs log ls   # real subsystem names for GOLOG_LOG_LEVEL" ;;
  esac
  return 2
}

# --------------------------------------------------------------------------
# runtime log level (inplace mode): no restart, levels restored afterwards
# --------------------------------------------------------------------------

log_prev_file() { state_file "loglevel.$(safe_name "$1").$(safe_name "$2")"; }

enable_bitswap_debug() { # enable_bitswap_debug <container...>
  local c s lvl f
  for c in "$@"; do
    for s in $LOG_SUBSYSTEMS; do
      f="$(log_prev_file "$c" "$s")"
      # `ipfs log level <subsystem>` answers with the current level; if that form
      # is unsupported the answer is empty and we leave that level alone.
      lvl="$(capture "$c" "ipfs log level $s" | tr -d '\r\n' | awk '{print $NF}')"
      case "$lvl" in
        debug|info|warn|error|dpanic|panic|fatal) printf '%s' "$lvl" >"$f" ;;
        *) : >"$f" ;;
      esac
      nx "$c" "ipfs log level $s debug" >/dev/null 2>&1 || true
    done
    say "    $c: bitswap is now at level '$(capture "$c" "ipfs log level bitswap")'"
  done
}

restore_bitswap_debug() { # restore_bitswap_debug <container...> : best effort
  local c s prev f
  for c in "$@"; do
    for s in $LOG_SUBSYSTEMS; do
      f="$(log_prev_file "$c" "$s")"
      prev="$( [ -s "$f" ] && cat "$f" || true )"
      case "$prev" in
        ""|debug) continue ;;
      esac
      nx "$c" "ipfs log level $s $prev" >/dev/null 2>&1 || true
    done
  done
  return 0
}

logs_report_since() { # logs_report_since <container> <unix timestamp>
  local c="$1" since="$2" p
  if [ "$DRY_RUN" = 1 ]; then note "docker logs --since $since $c | grep -c <fingerprint>"; return 0; fi
  say "  $c: $(docker logs --since "$since" "$c" 2>/dev/null | wc -l | tr -d ' ') log lines since the probe started"
  for p in "PeerConnected notification" "new queue for" "Found peer for CID" "Added peer to session" "WANT_BLOCK" "cannot reserve"; do
    printf '    %-30s %s\n' "$p" \
      "$(docker logs --since "$since" "$c" 2>&1 | grep -c -F -- "$p" || true)"
  done
}

# --------------------------------------------------------------------------
# mode: inplace
# --------------------------------------------------------------------------

discover() { # discover <compose service name>
  if [ "$DRY_RUN" = 1 ]; then printf '%s' "<discovered:$1>"; return 0; fi
  docker ps --filter "label=com.docker.compose.service=$1" --format '{{.Names}}' | head -n 1
}

run_inplace() {
  local x="$1" y="$2" rc=0 cid
  STUB_BROKEN=1   # dry-run only: the assumption is that the deployment is still stalled
  step "Inplace probe: $x <-> $y"
  say "  This adds one raw diagnostic block to each node and briefly drops the"
  say "  connection between them."

  if [ "$DRY_RUN" = 0 ]; then
    for c in "$x" "$y"; do
      docker inspect "$c" >/dev/null 2>&1 || die "container not found: $c"
      docker exec "$c" ipfs id >/dev/null 2>&1 || die "$c does not answer 'ipfs id'"
    done
  fi

  step "Control: libp2p streams must work (Bitswap is a different protocol)"
  local x_id y_id
  x_id="$(node_id "$x")"; y_id="$(node_id "$y")"
  say "  $x peer id: $x_id"
  say "  $y peer id: $y_id"
  if [ "$DRY_RUN" = 1 ]; then
    note "docker exec $x sh -c 'ipfs ping -n 2 $y_id'"
  elif nx "$x" "ipfs ping -n 2 '$y_id'" >/dev/null 2>&1; then
    say "  ping $x -> $y: ok (streams work, so the failure is specific to Bitswap)"
  else
    warn "ping $x -> $y failed: the problem is not specific to Bitswap; fix that first"
  fi

  step "Raising the Bitswap log level at runtime (no restart, restored at the end)"
  if [ "$DRY_RUN" = 1 ]; then
    note "docker exec <node> ipfs log level <subsystem> debug"
  else
    LOG_SINCE="$(date +%s)"
    enable_bitswap_debug "$x" "$y"
    say "  enabled for: $LOG_SUBSYSTEMS"
  fi

  run_matrix "$x" "$y"
  matrix_report "$x" "$y" || rc=2

  step "Daemon log during the probe (best effort)"
  if [ "$DRY_RUN" = 1 ] || [ -z "$LOG_SINCE" ]; then
    say "  (skipped)"
  else
    logs_report_since "$x" "$LOG_SINCE"
    logs_report_since "$y" "$LOG_SINCE"
    say "  These counts cover the whole probe, so they do not separate before from"
    say "  after the fresh connect; the isolated mode does. What matters here: any"
    say "  'WANT_BLOCK' means a want really went out on the wire, and any"
    say "  'cannot reserve' means the resource manager was rejecting streams after all."
  fi
  restore_bitswap_debug "$x" "$y"

  step "Diagnostic blocks left behind"
  for c in "$x" "$y"; do
    cid="$(cid_of "$c")"
    [ -n "$cid" ] || continue
    if [ "$CLEANUP" = 1 ]; then
      dx_quiet exec "$c" ipfs block rm "$cid"
      say "  $c: removed $cid"
    else
      say "  $c: docker exec $c ipfs block rm $cid"
    fi
  done
  return "$rc"
}

# --------------------------------------------------------------------------
# mode: isolated
# --------------------------------------------------------------------------

NODE_SETUP="$(cat <<'EOS'
set -eu
export IPFS_PATH=/data/ipfs
if [ -n "${SWARM_KEY_HEX:-}" ]; then
  printf '/key/swarm/psk/1.0.0/\n/base16/\n%s\n' "$SWARM_KEY_HEX" > "$IPFS_PATH/swarm.key"
  chmod 600 "$IPFS_PATH/swarm.key"
  export LIBP2P_FORCE_PNET=1
  echo "private swarm: swarm.key installed"
fi
if [ ! -f "$IPFS_PATH/config" ]; then ipfs init --profile=server >/dev/null; fi
ipfs config profile apply autoconf-off >/dev/null 2>&1 || echo "note: autoconf-off profile unavailable"
# Kubo development builds reject the public AutoConf endpoint whenever a
# private swarm key is present. Keep this explicit rather than depending on a
# profile whose fields may evolve between releases.
ipfs config --json AutoConf.Enabled false
# `autoconf-off` was introduced as a profile convenience, but development
# builds can omit it while retaining the literal "auto" values.  Those values
# are invalid once AutoConf is disabled, so express the complete private-swarm
# posture here instead of leaving noisy/ambiguous daemon configuration.
ipfs config --json DNS.Resolvers null
ipfs config --json Routing.DelegatedRouters null
ipfs config --json Ipns.DelegatedPublishers null
ipfs config Routing.Type dht
ipfs config --json AutoTLS.Enabled false
ipfs config --json Swarm.Transports.Network.Websocket false
ipfs config Addresses.API /ip4/0.0.0.0/tcp/5001
ipfs config Addresses.Gateway /ip4/127.0.0.1/tcp/8080
ipfs config --json Addresses.AppendAnnounce "[\"/dns4/$(hostname)/tcp/4001\"]"
ipfs config --json Swarm.AddrFilters "[]"
ipfs bootstrap rm --all >/dev/null
if [ -n "${BOOTSTRAP_ADDR:-}" ]; then
  ipfs bootstrap add "$BOOTSTRAP_ADDR" >/dev/null
  echo "configured bootstrap $BOOTSTRAP_ADDR"
fi
echo "starting $(ipfs version --number) as $(hostname)"
exec ipfs daemon --migrate=true
EOS
)"

start_node() { # start_node <name> <bootstrap address or empty>
  local name="$1" boot="$2"
  if [ "$DRY_RUN" = 1 ]; then
    note "docker rm -f $name"
    note "docker run -d --name $name --hostname $name --network $NET_NAME --tmpfs /data/ipfs --entrypoint /bin/sh -e GOLOG_LOG_LEVEL=<bitswap debug> -e BOOTSTRAP_ADDR='${boot:-}' -e SWARM_KEY_HEX='${SWARM_KEY:+<64 hex>}' $IMAGE -c '<kubo setup, then exec ipfs daemon>'"
    return 0
  fi
  docker rm -f "$name" >/dev/null 2>&1 || true
  # Kubo images declare /data/ipfs as a volume. A tmpfs makes each probe phase
  # genuinely fresh and prevents an anonymous-volume repo.lock from a prior
  # interrupted run contaminating the next measurement.
  docker run -d --name "$name" --hostname "$name" --network "$NET_NAME" --tmpfs /data/ipfs --entrypoint /bin/sh \
    -e GOLOG_LOG_LEVEL="$LOG_LEVEL" -e BOOTSTRAP_ADDR="$boot" \
    -e SWARM_KEY_HEX="$SWARM_KEY" \
    "$IMAGE" -c "$NODE_SETUP" >/dev/null
}

stop_node() { dx_quiet rm -f "$1"; }

logs_snapshot() { # logs_snapshot <container> <file>
  [ "$DRY_RUN" = 1 ] && return 0
  docker logs "$1" >"$2" 2>&1 || true
}

logs_report() { # logs_report <container> <before> <after>
  local c="$1" before="$2" after="$3" p
  [ "$DRY_RUN" = 1 ] && return 0
  [ -s "$before" ] || { say "  (no log snapshot available for $c)"; return 0; }
  say "  occurrences in $c's log, before / after the fresh connect:"
  for p in "PeerConnected notification" "new queue for" "Found peer for CID" "Added peer to session" "WANT_BLOCK"; do
    printf '    %-30s %s / %s\n' "$p" \
      "$(grep -c -F -- "$p" "$before" || true)" \
      "$(grep -c -F -- "$p" "$after" || true)"
  done
  say "    A zero for 'PeerConnected notification' and 'new queue for' before the fresh"
  say "    connect is the fingerprint this probe looks for. Zeros everywhere mean the"
  say "    log level never reached those subsystems: check 'ipfs log ls'."
}

gen_hex32() { # 64 hex characters, from whatever is available
  if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import os; print(os.urandom(32).hex())'
  elif command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    od -A n -t x1 -N 32 /dev/urandom | tr -d ' \n'
  fi
}

run_isolated() {
  local a="$NODE_A" b="$NODE_B" rc=0 a_id

  step "Isolated reproduction on network $NET_NAME (image $IMAGE)"
  if [ "$PNET" = 1 ]; then
    SWARM_KEY="$(gen_hex32)"
    case "$SWARM_KEY" in
      [0-9a-fA-F][0-9a-fA-F]*) [ "${#SWARM_KEY}" = 64 ] || die "could not generate a valid swarm key" ;;
      *) die "could not generate a valid swarm key" ;;
    esac
    say "  private swarm enabled: both nodes share one generated swarm key"
  fi
  if [ "$KEEP" = 0 ]; then stop_node "$a"; stop_node "$b"; fi
  dx_quiet network create "$NET_NAME"

  # ---- phase 1: connection established AFTER startup (control)
  step "Phase 1/2: peers connected AFTER startup (expected: fetch works)"
  start_node "$a" ""
  wait_api "$a" 120 || die "the Kubo A API never came up; check: docker logs $a"
  a_id="$(node_id "$a")"
  start_node "$b" ""
  wait_api "$b" 120 || die "the Kubo B API never came up; check: docker logs $b"
  wait_bitswap "$b" 30
  say "  waiting ${GRACE}s so Kubo B is fully started before the connection happens"
  [ "$DRY_RUN" = 1 ] || sleep "$GRACE"
  STUB_BROKEN=0
  if [ "$DRY_RUN" = 1 ]; then
    note "docker exec $b ipfs swarm connect /dns4/$a/tcp/4001/p2p/$a_id"
  else
    if docker exec "$b" ipfs swarm connect "/dns4/$a/tcp/4001/p2p/$a_id" >/dev/null 2>&1; then
      say "  connected after startup"
    else
      warn "the explicit connect failed in $b"
    fi
    sleep 2
  fi
  run_matrix "$b" "$a"
  local p1_initial="$MATRIX_XY_INITIAL" p1_after="$MATRIX_XY_AFTER"
  matrix_report "$b" "$a" control || true

  # ---- phase 2: connection established BEFORE Bitswap starts (bootstrap)
  step "Phase 2/2: peers connected BEFORE startup, via Bootstrap (expected: stall)"
  stop_node "$a"; stop_node "$b"
  # Isolated nodes use tmpfs repos, so restarting them creates fresh identities
  # and removes the diagnostic blocks. Do not carry either fact across phases.
  reset_node_state "$a" "$b"
  start_node "$a" ""
  wait_api "$a" 120 || die "the Kubo A API never came up in phase 2"
  a_id="$(node_id "$a")"
  say "  starting Kubo B with $a as its bootstrap address, which is what the"
  say "  production entrypoint does before 'exec ipfs daemon'"
  start_node "$b" "/dns4/$a/tcp/4001/p2p/$a_id"
  wait_api "$b" 120 || die "the Kubo B API never came up in phase 2"
  if [ "$DRY_RUN" = 0 ]; then
    sleep "$GRACE"
    if [ -z "$(connected_addr_of "$b" "$a_id")" ]; then
      warn "$b is not connected to $a, so the early connection did not happen and this"
      warn "phase cannot test the hypothesis; check: docker logs $b"
    else
      say "  $b is connected to $a already, before any probe request"
    fi
    logs_snapshot "$b" "$STATE_DIR/b-before.log"
  fi
  STUB_BROKEN=1
  run_matrix "$b" "$a"
  local p2_initial="$MATRIX_XY_INITIAL" p2_after="$MATRIX_XY_AFTER" p2_confirmed="$MATRIX_CONFIRMED"
  logs_snapshot "$b" "$STATE_DIR/b-after.log"
  matrix_report "$b" "$a" || true
  step "What the daemon log shows"
  logs_report "$b" "$STATE_DIR/b-before.log" "$STATE_DIR/b-after.log"

  step "Verdict"
  printf '  %-44s %-10s %-12s\n' "phase" "initial" "after connect"
  printf '  %-44s %-10s %-12s\n' "1: connected after startup (control)" "$p1_initial" "$p1_after"
  printf '  %-44s %-10s %-12s\n' "2: connected before startup (bootstrap)" "$p2_initial" "$p2_after"
  say ""
  if [ "$p1_initial" != "ok" ]; then
    say "  INCONCLUSIVE: the control phase failed, and an explicit connect after startup"
    say "  should always be enough. Try a longer --grace, or rerun with --keep and look at"
    say "  the containers by hand."
    rc=2
  elif [ "$p2_confirmed" != 0 ]; then
    say "  CONFIRMED: same image, same configuration, only the ordering of the connection"
    say "  differs. Connected after startup works; connected before startup stalls; one"
    say "  fresh connect event cures it. Bitswap never registered the pre-existing"
    say "  connection, so it never sent a WANT."
    rc=0
  else
    say "  NOT reproduced: connecting through Bootstrap before startup did not stall here."
    say "  Either this image establishes that connection after Bitswap is running, or the"
    say "  stall needs another ingredient. Rerun with --keep and take a goroutine dump"
    say "  while a fetch hangs."
    rc=2
  fi
  if [ "$KEEP" = 1 ]; then
    say ""
    say "  Containers $a and $b kept on network $NET_NAME."
  fi
  return "$rc"
}

# --------------------------------------------------------------------------
# argument parsing and dispatch
# --------------------------------------------------------------------------

while [ "$#" -gt 0 ]; do
  case "$1" in
    inplace|isolated) MODE="$1" ;;
    --server)  SERVER_CONTAINER="${2:?--server needs a value}"; shift ;;
    --fetcher) FETCHER_CONTAINER="${2:?--fetcher needs a value}"; shift ;;
    --image)   IMAGE="${2:?--image needs a value}"; shift ;;
    --timeout) FETCH_TIMEOUT="${2:?--timeout needs a value}"; shift ;;
    --grace)   GRACE="${2:?--grace needs a value}"; shift ;;
    --pnet)    PNET=1 ;;
    --keep)    KEEP=1 ;;
    --cleanup) CLEANUP=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
  shift
done

[ -n "$MODE" ] || { usage >&2; die "choose a mode: inplace or isolated"; }
if [ "$MODE" = "isolated" ] && [ -z "$IMAGE" ]; then
  SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
  IMAGE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["base"]["images"]["kubo"])' "$SCRIPT_DIR/../deployment_v3/catalog_data/dark-platform-baseline-v1.0.json")"
fi
case "$FETCH_TIMEOUT" in ''|*[!0-9]*) die "--timeout must be a whole number of seconds" ;; esac
case "$GRACE" in ''|*[!0-9]*) die "--grace must be a whole number of seconds" ;; esac
if [ "$DRY_RUN" = 0 ] && ! command -v docker >/dev/null 2>&1; then
  die "docker not found; '$PROGNAME $MODE --dry-run' prints the plan without it"
fi

# One exit trap for everything. It only touches globals and defaults: by the
# time it fires, function locals are gone and `set -u` would otherwise abort it,
# turning any exit code into 1.
cleanup_all() {
  rm -rf "${STATE_DIR:-}" 2>/dev/null || true
  rm -f "${STUB_STATE:-}" 2>/dev/null || true
  if [ "${MODE:-}" = "isolated" ] && [ "${KEEP:-0}" = 0 ] && [ "${DRY_RUN:-0}" = 0 ]; then
    docker rm -f "${NODE_A:-}" "${NODE_B:-}" >/dev/null 2>&1 || true
    docker network rm "${NET_NAME:-}" >/dev/null 2>&1 || true
  fi
}

STATE_DIR="$(mktemp -d)"
trap cleanup_all EXIT
rc=0
[ "$DRY_RUN" = 1 ] && stub_init
case "$MODE" in
  inplace)
    SERVER_CONTAINER="${SERVER_CONTAINER:-$(discover ipfs-storage-a)}"
    FETCHER_CONTAINER="${FETCHER_CONTAINER:-$(discover ipfs-storage-b)}"
    [ -n "$SERVER_CONTAINER" ] && [ -n "$FETCHER_CONTAINER" ] ||
      die "could not discover both Kubo containers; pass --server and --fetcher"
    run_inplace "$FETCHER_CONTAINER" "$SERVER_CONTAINER" || rc=$?
    ;;
  isolated)
    run_isolated || rc=$?
    ;;
esac

if [ "$DRY_RUN" = 1 ]; then
  step "Dry run"
  say "  Nothing was executed. Every verdict above is what the hypothesis predicts, not"
  say "  a measurement."
fi
exit "$rc"
