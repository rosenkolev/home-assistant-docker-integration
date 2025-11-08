from homeassistant.helpers import device_registry as dr

from .const import _LOGGER, DOMAIN

# def property_with_default[T](name: str, fn: Callable[[], T], doc: str):
#     def _fget(obj):
#         if not hasattr(obj, name):
#             setattr(obj, name, fn())

#         return getattr(obj, name)

#     return property(
#         fget=_fget,
#         fset=lambda self, value: setattr(self, name, value),
#         doc=doc,
#     )


class AutoDiscoverDevicesMixin:
    _current_device_ids = set[str]()
    _new_device_ids = set[str]()

    @property
    def new_device_ids(self) -> set[str]:
        return self._new_device_ids

    # current_device_ids = property_with_default(
    #     "_current_device_ids",
    #     lambda: set[str](),
    #     doc="""
    #         The current container ids.
    #         """,
    # )
    # new_device_ids = property_with_default(
    #     "_new_device_ids",
    #     lambda: set[str](),
    #     doc="""
    #         The container ids of the new container found on the docker host.
    #         """,
    # )

    def clear_new_devices(self):
        self._new_device_ids.clear()

    def set_new_device_ids(self, ids: set[str], service_id: str) -> None:
        removed_device_ids = self._current_device_ids - ids
        self._new_device_ids = ids - self._current_device_ids
        self._current_device_ids = ids

        # Clean registries when removed devices found.
        if removed_device_ids:
            # get all devices in the service
            device_reg = dr.async_get(self.hass)
            device_list = dr.async_entries_for_config_entry(device_reg, service_id)

            # Find the container entities
            gateway_device = device_reg.async_get_device({(DOMAIN, service_id)})
            assert gateway_device is not None
            via_device_id = gateway_device.id

            # Then remove the connected orphaned device(s)
            for device_entry in device_list:
                for domain_name, entry_id in enumerate(device_entry.identifiers):
                    if (
                        domain_name == DOMAIN
                        and device_entry.via_device_id == via_device_id
                        and entry_id not in ids
                    ):
                        device_reg.async_update_device(
                            device_entry.id,
                            remove_config_entry_id=self.config_entry.entry_id,
                        )
                        _LOGGER.debug(
                            "Removed %s device %s %s from device_registry",
                            DOMAIN,
                            device_entry.model,
                            entry_id,
                        )
