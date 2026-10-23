<!-- cspell:words aiohttp -->
# Contributing

## Project Structure

The code is located at `custom_components/docker_apps`

```shell
custom_components/docker_apps
  |- brand/              #-> integration icon
  |- translations/       #-> UI strings
  |- www/                #-> Apps panel and app launcher card (plain web components, no build step)
  |- __init__.py         #-> setup, panel, repairs
  |- _docker_client.py   #-> async Docker Engine API client (aiohttp, unix socket or TCP)
  |- _compose.py         #-> compose engine in Python (load, interpolate, up, down, pull)
  |- _compose_file.py    #-> edit image tags in compose / .env files without reformatting
  |- _registry.py        #-> container registry client (tags, digests)
  |- _versions.py        #-> "same shape" tag versions
  |- _store.py           #-> CasaOS and Runtipi formats, normalized StoreApp, rendering
  |- _paths.py           #-> host path <-> Home Assistant container path mapping
  |- _selfupdate.py      #-> one-shot updater for the Home Assistant container
  |- _models.py          #-> data models
  |- apps.py             #-> AppManager: managed state, update checks, updates, rollback, installs
  |- catalog.py          #-> store repositories (download, cache)
  |- coordinator.py      #-> coordinators: Docker state + events, stats, disk, updates
  |- config_flow.py      #-> config flow, options flow, repository subentry flow
  |- services.py         #-> actions
  |- websocket.py        #-> websocket commands for the panel (state, store, resources, live logs)
  |- sensor.py, switch.py, button.py, update.py #-> entities
  |- diagnostics.py      #-> diagnostics download
```

## Setup

1. Create virtual environment and install packages

   ```shell
   # setup venv
   python3 -m venv .venv

   # activate: shell
   source .venv/bin/activate

   # activate: powershell
   ./.venv/Scripts/activate.ps1

   # install requirements
   pip3 install -r ./requirements.txt

   # setup pre-commit
   pre-commit install
   ```

1. Run all checks

   ```shell
   ruff check . && coverage run --data-file reports/.coverage -m pytest -v -s && coverage report -m --data-file reports/.coverage
   ```

## Tests

Tests in `tests/unit` are plain pytest and run on every OS. They use `tests/fake_docker.py`, an in-memory Docker engine, and real files in `tmp_path`.

Write tests that don't depend on the OS: use POSIX-style host paths in strings and `tmp_path` for real files.
