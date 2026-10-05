#!/bin/bash

# --persist=.floorp maps HOME/.floorp to the app's private directory only
# without home access. Pin the database location instead of following that
# path when filesystem permissions change. Never move or rewrite profiles.
app_dir="$HOME/.var/app/one.ablaze.floorp"
legacy_root="$app_dir/.floorp"
config_dir="$app_dir/config"
cache_dir="$app_dir/cache"
[[ ${XDG_CONFIG_HOME:-} == /* ]] && config_dir="$XDG_CONFIG_HOME"
[[ ${XDG_CACHE_HOME:-} == /* ]] && cache_dir="$XDG_CACHE_HOME"
xdg_root="$config_dir/floorp"
root_file="$app_dir/config/floorp-profile-root"

fail() {
    printf 'Floorp: %s\n' "$*" >&2
    exit 1
}

read_root() {
    local roots
    mapfile -t roots < "$root_file" || return 1
    [[ ${#roots[@]} == 1 && ${roots[0]} == /* ]] || return 1
    profile_root="${roots[0]}"
}

has_database() {
    # Keep even damaged or old metadata; a directory or empty marker alone
    # must not hide an existing database in the other location.
    [[ -s "$1/profiles.ini" || -s "$1/installs.ini" ]]
}

explicit_profile=false
for argument in "$@"; do
    case "${argument,,}" in
        -profile|--profile) explicit_profile=true; break ;;
    esac
done
[[ ${XRE_PROFILE_PATH:-} == /* ]] && explicit_profile=true

# Native Floorp ignores relative MOZ_APP_DATA paths. An absolute override is
# authoritative and must not change the saved location for ordinary launches.
if [[ ${MOZ_APP_DATA:-} != /* ]]; then
    if $explicit_profile; then
        # -profile selects its own directory and does not choose a default
        # database. Do not record a default based on this one-off launch.
        profile_root="$xdg_root"
        if [[ -f "$root_file" ]]; then
            read_root || profile_root="$xdg_root"
        fi
    elif [[ -e "$root_file" ]]; then
        read_root || fail "Invalid saved profile location in $root_file. Set MOZ_APP_DATA to the intended database directory to override it."
    else
        legacy_database=false
        xdg_database=false
        has_database "$legacy_root" && legacy_database=true
        has_database "$xdg_root" && xdg_database=true

        if $legacy_database && $xdg_database; then
            if [[ "$HOME/.floorp" -ef "$legacy_root" ]]; then
                # This is the actual persist backing, not a host profile.
                profile_root="$legacy_root"
            elif [[ ! -e "$HOME/.floorp" && ${MOZ_LEGACY_HOME:-} != 1* ]]; then
                # In this namespace the native resolver already uses XDG.
                profile_root="$xdg_root"
            else
                fail "Both Flatpak profile locations contain databases ($legacy_root and $xdg_root). Set MOZ_APP_DATA to the intended one, or use -profile with its full profile path. No profiles have been moved."
            fi
        elif $legacy_database; then
            profile_root="$legacy_root"
        elif $xdg_database; then
            profile_root="$xdg_root"
        elif [[ ${MOZ_LEGACY_HOME:-} == 1* ]]; then
            profile_root="$legacy_root"
        else
            profile_root="$xdg_root"
        fi

        # Publish a complete file without replacing another launch's choice.
        # The record lives in private config, not the permission-sensitive home
        # alias or the cache. No profiles.ini / installs.ini edits are needed.
        mkdir -p "$app_dir/config" || fail "Cannot create the private config directory."
        temporary_root=$(mktemp "$root_file.XXXXXX") || fail "Cannot record the profile location."
        if ! printf '%s\n' "$profile_root" > "$temporary_root"; then
            rm -f "$temporary_root"
            fail "Cannot record the profile location."
        fi
        ln -T "$temporary_root" "$root_file" 2>/dev/null
        rm -f "$temporary_root"
        read_root || fail "Cannot read the saved profile location."
    fi
    export MOZ_APP_DATA="$profile_root"
fi

[[ ${MOZ_LOCAL_APP_DATA:-} == /* ]] || export MOZ_LOCAL_APP_DATA="$cache_dir/floorp"
export TMPDIR="$cache_dir/tmp"
exec /app/lib/floorp/floorp --name one.ablaze.floorp "$@"
