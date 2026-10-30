#!/usr/bin/env bash
# Driver-only setup; these helpers never install into the caller's real HOME.
arm_source_redirect() {
  local repo="$1" work="$2" serve="$3"
  local https='https://github.com/NousResearch/hermes-agent.git'
  local ssh='git@github.com:NousResearch/hermes-agent.git'
  local actual real_git quoted_git cfg="$work/gitconfig" shim="$work/shim"
  actual="$(git -C "$repo" remote get-url origin)"
  real_git="$(command -v git)"
  quoted_git="$(printf '%q' "$real_git")"
  # Export the real git so later checks can observe the TRANSPORT url. After
  # this function the shim shadows `git` and reports the official origin for
  # `remote get-url origin` (so fork detection sees it); any check that must see
  # the file:// redirect instead has to bypass the shim via this path.
  export HERMES_E2E_REAL_GIT="$real_git"
  # A global file survives install.sh replacing GIT_CONFIG_COUNT/KEY_n/VALUE_n.
  printf '' > "$cfg"
  for url in "$actual" "$https" "$ssh"; do
    "$real_git" config --file "$cfg" --add "url.file://$serve.insteadOf" "$url"
  done
  export GIT_CONFIG_GLOBAL="$cfg"
  # serve.git publishes commits on main, not releases: HEAD's installer would otherwise
  # install the latest stable release its tags name. Older installers ignore the variable.
  export HERMES_INSTALL_CHANNEL=main
  [ "$(git -C "$repo" remote get-url origin)" = "file://$serve" ] \
    || fail 'git URL redirect did not reach the staged repository'

  # Transport is local, but fork detection must still see the official origin.
  mkdir -p "$shim"
  cat > "$shim/git" <<EOF
#!/usr/bin/env bash
prev2=""
prev1=""
for arg in "\$@"; do
  if [ "\$prev2" = remote ] && [ "\$prev1" = get-url ] && [ "\$arg" = origin ]; then
    printf '%s\n' '$https'
    exit 0
  fi
  prev2="\$prev1"
  prev1="\$arg"
done
exec $quoted_git "\$@"
EOF
  chmod +x "$shim/git"
  export PATH="$shim:$PATH"
  [ "$(git -C "$repo" remote get-url origin)" = "$https" ] \
    || fail 'git origin shim did not report the official repository'
  ok "git transport redirected via $cfg; origin reported by $shim/git"
}

# NEXT changes an Electron source input, so strict Desktop provenance checks
# exercise a rebuild. A root-only marker would correctly reuse the old bundle.
# The private index leaves refs and checkout untouched; new objects reach the bare clone.
resolve_update_ref() (
  local repo="$1" parent="$2" ref="$3" blob desktop_blob tree index
  if [ "$ref" != NEXT ]; then
    git -C "$repo" rev-parse "${ref}^{commit}"
    return
  fi
  set -o pipefail
  index="$(mktemp -t hermes-e2e-next-index.XXXXXX)" || return
  trap 'rm -f "$index"' EXIT
  rm -f "$index"
  export GIT_INDEX_FILE="$index"
  blob="$(printf 'synthetic next commit for the HEAD -> NEXT install E2E leg\n' \
    | git -C "$repo" hash-object -w --stdin)" || return
  desktop_blob="$( { git -C "$repo" show "$parent:apps/desktop/electron/app-version.ts" || exit; \
    printf '\n// Synthetic NEXT Desktop input for install/update E2E.\n'; } \
    | git -C "$repo" hash-object -w --stdin)" || return
  git -C "$repo" read-tree "$parent" || return
  git -C "$repo" update-index --add --cacheinfo "100644,$blob,.hermes-e2e-next" || return
  git -C "$repo" update-index --cacheinfo "100644,$desktop_blob,apps/desktop/electron/app-version.ts" || return
  tree="$(git -C "$repo" write-tree)" || return
  GIT_AUTHOR_NAME='Hermes E2E' GIT_AUTHOR_EMAIL='e2e@hermes.invalid' \
    GIT_COMMITTER_NAME='Hermes E2E' GIT_COMMITTER_EMAIL='e2e@hermes.invalid' \
    git -C "$repo" commit-tree "$tree" -p "$parent" -m 'e2e: synthetic next commit'
)

run_source_installer() {
  local repo="$1" work="$2" logs="$3" ref="$4" label="$5" desktop="${6:-}"
  local script="$work/install-$label.sh" text help_text rc=0
  # Buffer before grep: git show | grep -q can lose to SIGPIPE under pipefail.
  text="$(git -C "$repo" show "$ref:scripts/install.sh")" || return
  git -C "$repo" show "$ref:scripts/install.sh" > "$script" || return
  local flags=(--skip-setup)
  # Historical (pre-PM) installers ran their own Playwright/npm browser
  # install, which is slow and can prompt for sudo; skip it there. A PM
  # installer (it enters `pm.cli`) gets no flag: its default install carries
  # the browser (Chromium), which is what users get, so the leg exercises it.
  # Rejection messages also mention --skip-browser, so trust only the help.
  help_text="$(bash "$script" --help < /dev/null 2>/dev/null)" || help_text=""
  if grep -qF -- --skip-browser <<< "$help_text" && ! grep -qF 'pm.cli' <<< "$text"; then
    flags+=(--skip-browser)
  fi
  if [ "$desktop" = desktop ]; then
    grep -qF -- --include-desktop <<< "$text" \
      || { fail "ref $ref does not support --include-desktop; this leg cannot mean what it claims"; return 1; }
    flags+=(--include-desktop)
  fi
  bash "$script" "${flags[@]}" < /dev/null 2>&1 | ts_prefix > "$logs/install-$label.log" || rc=$?
  log_group "install.sh ($label) transcript" "$logs/install-$label.log"
  [ "$rc" -eq 0 ] || { fail "install.sh ($label) exited $rc; log at $logs/install-$label.log"; return "$rc"; }
}
