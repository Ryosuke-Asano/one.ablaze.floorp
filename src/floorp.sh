#!/bin/bash

floorp_binary=/app/lib/floorp/floorp

# Native help/version handling precedes profile selection. Forward these
# requests before reading or recording a database location, including the
# flag=value syntax accepted by the native argument parser.
for argument in "$@"; do
    information_flag="${argument%%=*}"
    case "${information_flag,,}" in
        -v|--v|-version|--version|-full-version|--full-version|-h|--h|-help|--help|-\?|--\?)
            exec "$floorp_binary" --name one.ablaze.floorp "$@"
            ;;
    esac
done

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

trim_ini() {
    trimmed_ini="${1#"${1%%[![:space:]]*}"}"
    trimmed_ini="${trimmed_ini%"${trimmed_ini##*[![:space:]]}"}"
}

is_home_alias() {
    [[ "$1" == "$HOME/.floorp" || "$1" == "$HOME/.floorp/"* ]]
}

check_ini_section() {
    local ini_section="$1" ini_relative="$2" ini_path="$3" ini_default="$4" ini_backup="$5"
    if [[ "$ini_section" =~ ^Profile[0-9]+$ && "$ini_relative" != 1 ]] && is_home_alias "$ini_path"; then
        unsafe_alias_path="$ini_path"
        return 0
    fi
    if [[ -n "$ini_section" ]] && { [[ "$ini_section" == Install* ]] || $ini_backup; } && is_home_alias "$ini_default"; then
        unsafe_alias_path="$ini_default"
        return 0
    fi
    return 1
}

find_alias_reference() {
    local ini_file="$1" ini_line ini_key trimmed_ini
    local ini_section="" ini_relative="" ini_path="" ini_default="" ini_backup=false
    local -a ini_lines
    [[ "$ini_file" == */installs.ini ]] && ini_backup=true
    unsafe_alias_path=""
    mapfile -t ini_lines < "$ini_file" || fail "Cannot inspect profile metadata in $ini_file."
    for ini_line in "${ini_lines[@]}"; do
        trim_ini "$ini_line"
        case "$trimmed_ini" in
            ''|\#*|\;*) continue ;;
            \[*\])
                check_ini_section "$ini_section" "$ini_relative" "$ini_path" "$ini_default" "$ini_backup" && return 0
                ini_section="${trimmed_ini:1:${#trimmed_ini}-2}"
                trim_ini "$ini_section"
                ini_section="$trimmed_ini"
                ini_relative="" ini_path="" ini_default=""
                ;;
            *=*)
                ini_line="$trimmed_ini"
                trim_ini "${ini_line%%=*}"
                ini_key="$trimmed_ini"
                trim_ini "${ini_line#*=}"
                case "$ini_key" in
                    IsRelative) ini_relative="$trimmed_ini" ;;
                    Path) ini_path="$trimmed_ini" ;;
                    Default) ini_default="$trimmed_ini" ;;
                esac
                ;;
        esac
    done
    check_ini_section "$ini_section" "$ini_relative" "$ini_path" "$ini_default" "$ini_backup"
}

check_profile_alias() {
    # A direct profile selection is authoritative. MOZ_APP_DATA only selects
    # the database; absolute profile paths in it still need this check.
    $explicit_profile && return 0
    [[ "$HOME/.floorp" -ef "$legacy_root" ]] && return 0
    local ini_file private_profile
    for ini_file in "$1/profiles.ini" "$1/installs.ini"; do
        [[ -f "$ini_file" ]] || continue
        if find_alias_reference "$ini_file"; then
            private_profile="$legacy_root${unsafe_alias_path#"$HOME/.floorp"}"
            fail "Stored absolute profile path $unsafe_alias_path refers to a different home backing. Use -profile \"$private_profile\" to select the app-private profile directly. MOZ_APP_DATA alone does not change an absolute profile path. No profile data has been changed."
        fi
    done
}

explicit_profile=false
for argument in "$@"; do
    profile_flag="${argument%%=*}"
    case "${profile_flag,,}" in
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

        check_profile_alias "$profile_root"

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

# Recheck saved roots and explicit database overrides on every ordinary or
# named-profile launch. A permission change can retarget an absolute Path even
# though profiles.ini itself stays in the same private directory.
check_profile_alias "$MOZ_APP_DATA"

[[ ${MOZ_LOCAL_APP_DATA:-} == /* ]] || export MOZ_LOCAL_APP_DATA="$cache_dir/floorp"
export TMPDIR="$cache_dir/tmp"
exec "$floorp_binary" --name one.ablaze.floorp "$@"
