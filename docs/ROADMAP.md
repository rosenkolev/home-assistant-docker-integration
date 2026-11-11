<!-- cspell:words Coolify Dokploy Komodo Chadburn cronify Nextcloud Collabora Tika Gotenberg -->
# Roadmap

Planned features. Nothing here is built yet.

## Scheduled apps

Run an app on a schedule: start it, let it run, stop it. For example, a sync that copies files every night, a backup, or a check.

### What exists

- CasaOS (`x-casaos`) and Runtipi (`config.json`, `x-runtipi`) have no field for a schedule. Their own scheduled backups and updates use a host crontab.
- Other tools keep the schedule outside the app's files: Coolify and Dokploy scheduled tasks, Komodo procedures, Synology Task Scheduler, host cron.
- Scheduler containers (Ofelia, Chadburn, cronify, deck-chores) read labels, but they need the Docker socket, and Home Assistant doesn't see their jobs.
- What the integration has today: a switch per app, a status sensor, and the `docker_apps_event` event with `exit_code`. A Home Assistant automation can already start an app and react when it exits.

### Design

One format of our own, `x-docker-apps`. CasaOS, Runtipi and `docker compose` ignore it, and the compose engine skips `x-` keys.

```yaml
name: photos-sync

x-docker-apps:                 # app info, for apps that are not from a store
  version: 1
  title: Photos Sync
  description: Copies photos to the Immich library every night.
  icon: https://example.com/icon.png
  category: Backup

services:
  sync:
    image: rclone/rclone:1.71.2
    restart: "no"              # a job: Docker must not start it again
    command: sync remote:photos /data/photos --config /config/rclone.conf
    volumes:
      - ./config:/config
      - /mnt/nas/photos/external:/data/photos
    x-docker-apps:             # job settings, per service
      schedule: "0 3 * * *"    # cron, or "daily 03:00", "every 6h"
      mode: exit               # exit: runs until the command ends
                               # duration: stops after run_for, e.g. run_for: 30m
      timeout: 2h              # stopped if still running after this
      on_failure: notify       # notification when the exit code is not 0
```

- The app's `compose.yaml` and the user's `compose.override.yaml` use the same block. A schedule set in the panel goes to the override and wins, so it stays after updates.
- A schedule that comes with a store app is a suggestion. It runs only after the user confirms it, like host access.
- Job services are not started on install. A "Run now" button starts one outside its schedule.
- Store apps take their info from `x-casaos` or Runtipi. The `x-docker-apps` info only fills gaps, or describes apps the user writes.

### To do

- [ ] Read `x-docker-apps` (app info and job settings) and keep it through store installs.
- [ ] A "job" setting per service that writes `restart: "no"` to the override.
- [ ] Show the last run on the app card: finished, failed with the exit code, and when.
- [ ] Schedule dialog in the panel: schedule, mode, timeout, notification. It writes to the override.
- [ ] Run the schedules with Home Assistant time tracking. Stop on timeout; notify on failure.
- [ ] "Run now" button and a service action.
- [ ] A Home Assistant blueprint for users who want the schedule as an automation.
- [ ] Use the app info from `x-docker-apps` in the panel for apps that are not from a store.
- [ ] Document the format in `docs/REFERENCE.md`.

## Optional services

An app with several services where some are off by default and can be turned on, or on by default and can be turned off. The choice is made on install and can be changed later.

Examples:

- Nextcloud with Collabora or an office server that you can turn on.
- Paperless with Tika and Gotenberg for office documents.
- Immich with or without its machine learning service.
- An app that comes with its own database, where you turn the database off and use a shared one.

### What exists

- Compose has this already: `profiles`. A service with `profiles: [office]` starts only when the `office` profile is active. `docker compose` reads the active profiles from `COMPOSE_PROFILES`, also from the project's `.env` file.
- The integration recognizes profile services today, but no profile can be turned on: they are never started ([`_compose.py`](../custom_components/docker_apps/_compose.py), `inactive`). Orphan removal already leaves them alone.
- When a service depends on a service that is off, the integration skips that dependency. `docker compose` refuses it unless the dependency has `required: false`.
- CasaOS and Runtipi have no field for optional services. Runtipi apps can only change values through `form_fields`.

### Design

Each service can describe itself in `x-docker-apps`:

```yaml
services:
  db:
    image: postgres:16
    x-docker-apps:
      description: Holds the catalog. Can be turned off, but its data is then not used.
      optional: true    # false (default): the service is required and can't be turned off
      enabled: true     # true (default): on after install; false: off until turned on
```

| `optional` | `enabled` | Meaning |
|---|---|---|
| `false` (default) | `true` (default) | Required. Always on, no switch. |
| `true` | `true` | On by default, can be turned off. |
| `true` | `false` | Off by default, can be turned on. |
| `false` | `false` | Not valid: refused when the app is read. |

- A service without the block is required, except a service with `profiles`: it counts as `optional: true, enabled: false`, as in Compose.
- The install dialog lists the optional services with a switch set to `enabled`, and shows each `description`. Required services are listed without a switch, so the user sees everything the app runs.
- The same list is in an "Optional services" dialog for installed apps.

Turning a service on:

- A service that is off by default uses a Compose profile. The active profiles are written to `COMPOSE_PROFILES` in the app's `.env`, so `docker compose` on the host starts the same services.

Turning a service off:

- Write `profiles: [disabled]` for it in `compose.override.yaml`.
- Services that depend on it get `required: false` for that dependency in the override, so `docker compose` accepts it too.
- Its data stays. Turning it on again uses the same data.
- When a required service depends on it, the dialog says so and asks first.

Using a shared database instead of the app's own:

- The app says which service is its database and which variables point to it:

  ```yaml
  services:
    db:
      image: postgres:16
      x-docker-apps:
        description: The app's own database.
        optional: true
        replaces: database      # can be replaced by a shared database
        variables: [DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME]
  ```

- On install the user chooses "own database" or a database from another installed app. The integration turns the service off, sets the variables, and joins the network of the other app.
- Store apps have no block, so their services are required. The user can make one optional by adding the block in the override, for example in Settings.

### To do

- [ ] Active profiles per app: stored in the settings and written as `COMPOSE_PROFILES` to `.env`. The compose engine starts the services of active profiles.
- [ ] Install dialog: a list of optional services with on and off switches.
- [ ] An "Optional services" dialog for installed apps. Saving recreates the app, starts the services that were turned on, and removes the ones that were turned off. Their data stays.
- [ ] Turning a service off: `profiles: [disabled]` in the override, and `required: false` for services that depend on it.
- [ ] `x-docker-apps` per service: `description`, `optional` (default `false`), `enabled` (default `true`), `replaces`, `variables`. Refuse `optional: false` with `enabled: false`.
- [ ] Show every service with its description on install and in the app details; switches only for optional ones.
- [ ] Shared database: pick a database from another app, set the variables, join its network. Check the version and that it is reachable.
- [ ] Updates keep the user's choices. A new optional service in an update starts off.
- [ ] Tests, and documentation in `docs/REFERENCE.md`.

## Dependencies

An app, or a service in it, that needs something else to run first: another service of the same app, another app (a shared database, a reverse proxy, a VPN), or a service outside Docker (a NAS share, a host port).

### What exists

- Inside one app, `depends_on` works when the app is created or recreated: the engine starts services in order and waits for `service_started`, `service_healthy` or `service_completed_successfully` ([`_compose.py`](../custom_components/docker_apps/_compose.py), `_wait_for`, `start_order`). A dependency that is unhealthy, stops or fails stops the start with a message.
- Not covered:
  - Starting a stopped app from its switch: the containers start without the `depends_on` order and waits. To check.
  - When Docker or the host restarts, Docker starts all containers with a restart policy at once, in no order. A service can start before its database is ready and fail, or keep restarting.
  - Compose has no dependency between projects. One app can't say it needs another app.
  - Dependencies on a service that is off (an optional service) are skipped without a message.

### Design

Inside one app:

- Use the same order and waits for every start: install, update, recreate, the app switch, "Run now" and scheduled runs.
- After Docker starts again, check apps whose dependencies were not ready when they started. Restart them once the dependency is healthy.

Between apps, declared in `x-docker-apps` per service:

```yaml
services:
  app:
    image: example/app:2.1
    x-docker-apps:
      requires:
        - app: postgres          # an installed app, by name or store app
          condition: healthy     # started, healthy or completed
          timeout: 2m
          start: true            # start it when it is stopped
        - host: /mnt/nas/media   # a folder that must be mounted
        - port: 192.168.0.10:445 # a host and port that must answer
```

- Before an app or a job starts, its requirements are checked in order. A stopped app is started when `start: true`; otherwise the start waits or fails with a message that says what is missing.
- A shared database chosen in "Optional services" adds a `requires` entry for that app on its own.
- Stopping, updating or removing an app that others require shows those apps first and asks. An update of a database can stop its dependent apps and start them again after.
- A scheduled job can require an app only for its run: the app is started for the job and stopped after, when it was stopped before.
- Loops (A requires B, B requires A) are refused on install and in Settings.
- The app card shows "Waiting for postgres" while it waits, and the dependencies in its details.
- The user can add or remove requirements in Settings; they go to the override like other changes.

### To do

- [ ] Use the `depends_on` order and waits for the app switch, the start action and scheduled runs.
- [ ] After a Docker or host restart, restart services that started before their dependency was healthy.
- [ ] A message when a dependency is an optional service that is off.
- [ ] `x-docker-apps.requires`: apps, folders and host ports, with condition, timeout and `start`.
- [ ] Check and start requirements before an app or job starts; "Waiting for ..." status on the card.
- [ ] Ask before stopping, updating or removing an app that others require; stop and start dependents around a database update.
- [ ] Refuse dependency loops.
- [ ] Requirements editable in Settings, saved to the override.
- [ ] Tests, and documentation in `docs/REFERENCE.md`.
