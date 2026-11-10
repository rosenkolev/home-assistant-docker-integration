# cspell:words seccomp

from custom_components.docker_apps._access import host_access

APP = "/srv/apps/demo"
DATA = "/srv/data"


def access(service: dict) -> list[str]:
    return host_access({"services": {"app": service}}, APP, DATA)


def test__own_folders_ports_and_time_zone_are_no_access():
    assert (
        access(
            {
                "image": "demo",
                "ports": ["8080:80"],
                "volumes": [
                    "./config:/config",
                    f"{DATA}/demo:/data",
                    f"{DATA}/Media:/media",
                    "/etc/localtime:/etc/localtime:ro",
                    "cache:/cache",
                    "/tmp-anonymous",
                ],
            }
        )
        == []
    )


def test__each_kind_of_host_access_is_named():
    assert access(
        {
            "privileged": True,
            "network_mode": "host",
            "pid": "host",
            "cap_add": ["NET_ADMIN", "CAP_SYS_MODULE"],
            "security_opt": ["seccomp=unconfined"],
            "devices": ["/dev/ttyUSB0:/dev/ttyUSB0", {"source": "/dev/dri"}],
            "gpus": "all",
            "volumes": [
                "/var/run/docker.sock:/var/run/docker.sock",
                {"type": "bind", "source": "/proc", "target": "/host/proc"},
                "../../../etc:/host/etc",
                f"{DATA}:/all",
            ],
        }
    ) == [
        "Capability NET_ADMIN",
        "Capability SYS_MODULE",
        "Device /dev/dri",
        "Device /dev/ttyUSB0",
        "Full control of Docker (/var/run/docker.sock)",
        "GPU",
        "Host folder /etc",
        "Host folder /proc",
        "Host network",
        "Host processes",
        "Privileged mode (all devices, can leave the container)",
        "Security profile off (seccomp=unconfined)",
        f"The data of all apps ({DATA})",
    ]
