# Floorp for Flatpak (one.ablaze.floorp)

Flathub: https://flathub.org/apps/one.ablaze.floorp

Browser source code: https://github.com/Floorp-Projects/Floorp

### Based on

https://github.com/flathub/org.mozilla.firefox.BaseApp

https://searchfox.org/mozilla-central/source/taskcluster/docker/firefox-flatpak

## How to install (from Flathub)
```
flatpak install flathub one.ablaze.floorp
```

## How to build
```sh
flatpak-builder build-dir one.ablaze.floorp.yml
```
To test:
```sh
flatpak-builder --run build-dir one.ablaze.floorp.yml floorp
```

## Profile location

The launcher's default database locations are in the app's private storage instead
of a home path whose backing changes with `--filesystem=home`. Relative
profile entries continue to select the same profile; absolute entries need the
check described below. It keeps an existing database in
`~/.var/app/one.ablaze.floorp/.floorp` or
`~/.var/app/one.ablaze.floorp/config/floorp` without moving profiles or rewriting
`profiles.ini` / `installs.ini`. New installations use the config location.
Absolute `XDG_CONFIG_HOME` and `XDG_CACHE_HOME` values are supported; unset or
relative values fall back to this app's private config and cache directories.

The first ordinary launch records the selected database root in
`~/.var/app/one.ablaze.floorp/config/floorp-profile-root`. This record survives
filesystem permission changes. Subsequent XDG config changes do not change the
recorded profile location. An absolute `MOZ_APP_DATA` override selects a different
database for that launch without replacing the record; `MOZ_LOCAL_APP_DATA` also
remains supported. `-P` selects a named profile inside the selected database, and
`-profile /full/path/to/profile` can select a profile directory directly.

If both private locations contain databases, the launcher only records the
location when the current native selection can be determined: the home path is
the private legacy backing, or no home legacy path exists and native Floorp uses
XDG. If a separate host `.floorp` path or a forced legacy setting makes this
ambiguous, it stops and asks for an explicit `MOZ_APP_DATA` or `-profile` choice.
The host `.floorp` directory is not a default database candidate. An invalid
saved record also requires an explicit choice; a direct `-profile` launch remains
available.
No profile data is deleted, migrated, or merged by the launcher.

Before an ordinary or `-P` launch, the selected database is checked for absolute
profile Paths or install Defaults that reference `HOME/.floorp`. This includes
`profiles.ini`, the `installs.ini` backup, saved database locations, and explicit
`MOZ_APP_DATA` overrides. If the home path is the private legacy backing, these
entries are safe to use. If it refers to a different backing or is unavailable,
the launcher stops before starting Floorp. It does not rewrite the metadata.
The check is conservative: any stored alias reference can stop a launch,
including a currently unused profile or an older backup mapping.

Use `-profile /full/private/path/to/profile` to select that profile directly,
for example a profile directory under
`~/.var/app/one.ablaze.floorp/.floorp`. `MOZ_APP_DATA` alone only chooses the
database and cannot change an absolute profile Path stored in it. Stable absolute
custom paths remain supported, and similarly named paths such as `.floorp-other`
do not trigger this check. Direct `-profile` (including `--profile=/full/path`)
and restart `XRE_PROFILE_PATH` selections remain authoritative.

Native help and version flags (`h`, `help`, `?`, `v`, `version`, `full-version`)
are forwarded before database selection. They work even with ambiguous databases
or a malformed saved location and do not create a database-location record.

## Launcher tests

Run the isolated shell fixtures with Python's standard library:

```sh
python3 -m unittest discover -s tests -v
```

Optional native tests use a Floorp binary, a temporary home, `--no-remote`, and
headless launches. They check both database locations across simulated home
namespace changes and explicit profile selection:

```sh
FLOORP_TEST_BINARY=/absolute/path/to/floorp python3 -m unittest discover -s tests -v
```

Set `FLOORP_TEST_WORK_DIR` to a dedicated directory to keep fixtures and native
logs. These tests do not create a Flatpak sandbox. Before release, test the built
Flatpak with home permission off, on, and off again, including old legacy and
XDG profiles, both databases, a host profile, and `home:ro` / `host` permissions.
Confirm the same active profile, preferences, cookies, extensions, and Floorp
OS/MCP behavior. The actual profile root for the reporter of
[Floorp #2837](https://github.com/Floorp-Projects/Floorp/issues/2837) has not yet
been verified in their sandbox.
