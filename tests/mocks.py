class MockedBaseEntity:
    ha_state_update_scheduled = False
    ha_state_update_scheduled_force_refresh = False
    ha_state_write = False
    ha_added_to_hass = False
    hass = 1

    @property
    def name(self) -> str:
        return self._attr_name

    @property
    def unique_id(self) -> str:
        return self._attr_unique_id

    def async_write_ha_state(self):
        self.ha_state_write = True

    def async_schedule_update_ha_state(self, force_refresh=False):
        self.ha_state_update_scheduled = True
        self.ha_state_update_scheduled_force_refresh = force_refresh

    def schedule_update_ha_state(self, force_refresh=False):
        self.ha_state_update_scheduled = True
        self.ha_state_update_scheduled_force_refresh = False

    async def async_added_to_hass(self) -> None:
        self.ha_added_to_hass = True

    def async_on_remove(self, _=None):
        pass

    async def async_will_remove_from_hass(self) -> None:
        pass
