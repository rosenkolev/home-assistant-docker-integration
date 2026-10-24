# Docker Apps reference

Details for the [Docker Apps](../README.md) integration.

1. [Configuration](#configuration)
1. [Entities](#entities)
1. [Updates](#updates)
1. [App stores](#app-stores)
1. [App settings](#app-settings)
1. [App launcher card](#app-launcher-card)
1. [Actions](#actions)
1. [Events](#events)

## Configuration

The setup asks for:

| Option | Default | Description |
| - | - | - |
| Docker URL | `unix:///var/run/docker.sock` | The socket, or a socket proxy such as `tcp://socket-proxy:2375` |
| Apps folder | found from your compose projects, else `/opt/docker-apps` | Host folder with one folder per app (`<app>/compose.yaml`) |
| App data folder | a sibling `volumes` folder if your apps use one, else `/opt/docker-apps-data` | Host folder for the data of apps installed from a store |
| Read-only | off | Monitor only: no switches, buttons or changing actions |

**Options** (the gear on the integration page): the same folders, read-only, how often to check for updates (default 6 hours) and the **untracked images** list (see [Updates](#updates)).

Both folders must be mounted into the Home Assistant container at the same path as on the host. Without the apps folder mount the integration still monitors apps, reads logs and starts or stops them. A repair issue tells you when a mount is missing.

## Entities

About **5 entities per app**, no matter how many containers the app has. Container details are attributes of the status sensor and are shown in the panel.

| Entity | Description |
| - | - |
| `sensor.<app>_status` | `running`, `partial`, `starting`, `unhealthy`, `stopped` or `updating`. The `containers` attribute lists each container's state, health, image, start time and restarts. |
| `sensor.<app>_cpu` | CPU % of all containers of the app |
| `sensor.<app>_memory` | Memory of all containers of the app |
| `switch.<app>_running` | Start or stop the whole app |
| `button.<app>_restart` | Restart the app |
| `update.<app>_update` | Only for managed apps, see [Updates](#updates) |
| `sensor.docker_<host>_running_containers` | Running containers on the host |
| `sensor.docker_<host>_images` | Images on the host |
| `sensor.docker_<host>_reclaimable_space` | Space that prune can free (checked hourly) |

Each app is a device, linked to the Docker host device. The device page has a **Visit** link to the app's web UI. When an app is removed, its device and entities are removed too.

## Updates

Each app has a **Managed** option:

| | Unmanaged | Managed |
| --- | --- | --- |
| Default for | Projects found on the host | Apps installed from a store, or projects you switch to managed |
| Status, CPU, RAM, logs, start/stop | Yes | Yes |
| Update checks and update entity | No | Yes |
| Changes your files | Never | Only when you press Install or Rollback |

For a managed app:

* **Same kind of tag:** `v2.1.0` moves to `v2.2.0`, `v2.1.0-rknn` to `v2.2.0-rknn`, never to `latest` or a release candidate. Floating tags such as `latest` are checked by image digest.
* **One package:** all tracked services of the app move together. When the version comes from `.env` (`image: immich-server:${IMMICH_VERSION}`), the `.env` value changes; otherwise only the `image:` line changes, so comments and layout stay.
* **Untracked images:** databases (postgres, mysql, mariadb, mongo, ...) and caches (redis, valkey, memcached, ...) are not updated with the app. Change the list in the integration options, or use the **Track** toggle per service in the panel. Untracked services still show when a newer version exists, and can be updated alone.
* **Upstream compose (optional, per app):** the URL of the app's compose file for each release, with `{version}`, for example `https://github.com/immich-app/immich/releases/download/{version}/docker-compose.yml`. Services that the new version needs in a newer version are then updated with it. If it needs a new **database major version**, the update is **on hold**: Install is refused and a repair explains how to upgrade the database first.
* **Rollback:** before an update the compose files and `.env` are backed up in `<app>/.docker-apps/history/`. After the update every container must be running and healthy; otherwise the backup is restored automatically. Older versions can be installed again from the update entity (last 3 kept).
* **Home Assistant itself:** a short-lived container from the new Home Assistant image finishes the update and removes itself.

## App stores

Add a repository in **Settings > Devices & services > Docker Apps > Add app store repository**. Known repositories are offered, or paste any URL:

| Format | URL |
| - | - |
| CasaOS | a GitHub repository (`https://github.com/IceWhaleTech/CasaOS-AppStore`), or a `.zip`/`.tar.gz` archive |
| Runtipi | a GitHub repository (`https://github.com/runtipi/runtipi-appstore`) or an archive |

Installed apps are plain compose projects in `<apps folder>/<name>/`:

```text
compose.yaml            # from the store, replaced by package updates
compose.override.yaml   # your changes (devices, extra volumes, ...), kept across updates
.env                    # variables and generated secrets
.docker-apps/           # store manifest and update history
```

Store apps are managed: when the store publishes a new package, it shows in **Settings > Updates**.

The install dialog checks the app's ports first. When the web port is taken, the next free one is filled in; a taken fixed port blocks the install. Apps that only work over HTTPS (linuxserver.io desktop images such as VS Code) open with `https://`. An install keeps running when the page is closed; the store shows it as **Installing…**, and a failed install leaves a notification.

## App settings

The app menu in the panel has two dialogs for apps with a `compose.override.yaml` (every store app):

* **Settings & passwords:** the environment variables of each container, as compose resolves them. Passwords are hidden until you show them.
* **Folders & volumes:** the folders and Docker volumes of each container. Add a host folder or a named volume, optionally read-only; suggestions list the folders of Home Assistant, the Docker volumes and the folders other apps use. The folders that come with the app are locked: removing them can lose the app's data.

Saving writes only your changes to `compose.override.yaml` and recreates the changed containers. If the new containers can't be created, the file is put back and the app keeps running as before.

## App launcher card

```yaml
type: custom:docker-apps-card
title: Apps          # optional
exclude:             # optional, compose project names to hide
  - whoami
```

The card shows each app with **Show in app launcher** turned on (in the app menu of the panel). The option is on by default for apps installed from a store, and off for other apps. Tap an app to open its web UI; an app without a web port opens its details.

## Actions

| Action | Description |
| - | - |
| `docker_apps.logs` | Recent log lines of an app or a container (response) |
| `docker_apps.manage` | Turn Managed or Show in app launcher on or off, choose tracked services, tag filters, upstream compose URL |
| `docker_apps.update_services` | Update single services, also untracked ones |
| `docker_apps.set_environment` | Change environment variables of one service (kept in `compose.override.yaml`) |
| `docker_apps.set_volumes` | Set the folders and volumes of one service; the app's own ones must stay |
| `docker_apps.recreate` | Bring an app up again from its compose files (optionally pull first) |
| `docker_apps.container_start` / `_stop` / `_restart` | Control one container |
| `docker_apps.prune` | Remove stopped containers, dangling or unused images, unused volumes, networks or build cache |
| `docker_apps.check_updates` | Check for updates now (optionally refresh the repositories) |
| `docker_apps.install` / `docker_apps.uninstall` | Install a store app, or remove an app |

Read logs in a script:

```yaml
action: docker_apps.logs
data:
  app: immich
  tail: 50
response_variable: result
```

## Events

Container events are fired as `docker_apps_event` (`app`, `container`, `service`, `action`, `type`), for example `action: unhealthy`, `die`, `oom` or `start`:

```yaml
triggers:
  - trigger: event
    event_type: docker_apps_event
    event_data:
      action: unhealthy
actions:
  - action: notify.notify
    data:
      message: "{{ trigger.event.data.container }} is unhealthy"
```
