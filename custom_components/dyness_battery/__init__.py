import asyncio, hashlib, hmac, base64, json, time
from email.utils import formatdate
from datetime import timedelta
import aiohttp, async_timeout
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.const import Platform

DOMAIN = "dyness_battery"
PLATFORMS = [Platform.SENSOR]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    coordinator = DynessDataCoordinator(hass, entry.data["api_id"], entry.data["api_secret"], entry.data["api_base"], entry.data.get("device_sn"))
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok: hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok

class DynessDataCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, api_id, api_secret, api_base, device_sn):
        super().__init__(hass, None, name=DOMAIN, update_interval=timedelta(minutes=5))
        self.api_id, self.api_secret, self.api_base, self.device_sn = api_id, api_secret, api_base, device_sn
        self.module_data, self._bound_sns = {}, set()

    async def _async_update_data(self):
        async with aiohttp.ClientSession() as session:
            try:
                async with async_timeout.timeout(30):
                    if self.device_sn not in self._bound_sns:
                        body = json.dumps({"deviceSn": self.device_sn}, separators=(',', ':'))
                        headers = self._headers(body, "/v1/device/bindSn")
                        await session.post(f"{self.api_base}/openapi/ems-device/v1/device/bindSn", headers=headers, data=body)
                        self._bound_sns.add(self.device_sn)

                    # Main BDU Data
                    body = json.dumps({"deviceSn": self.device_sn}, separators=(',', ':'))
                    headers = self._headers(body, "/v1/device/realTime/data")
                    async with session.post(f"{self.api_base}/openapi/ems-device/v1/device/realTime/data", headers=headers, data=body) as res:
                        rt_raw = await res.json()
                        rt = {str(i["pointId"]): i["pointValue"] for i in rt_raw.get("data", [])}

                    data = {
                        "soc": rt.get("1400"), "power": rt.get("1300"), "current": rt.get("1200"),
                        "voltage": rt.get("1100"), "soh": rt.get("1500"), "cycles": rt.get("1800"),
                        "mode": rt.get("1000"), "alarm": str(rt.get("9999999")) == "1"
                    }

                    # Module discovery (01-04)
                    sub_sns = [s.strip() for s in str(rt.get("SUB", "")).split(",") if s.strip() and "-BDU" not in s]
                    for sn in sub_sns:
                        m_body = json.dumps({"deviceSn": sn}, separators=(',', ':'))
                        m_headers = self._headers(m_body, "/v1/device/realTime/data")
                        async with session.post(f"{self.api_base}/openapi/ems-device/v1/device/realTime/data", headers=m_headers, data=m_body) as m_res:
                            m_rt = {str(i["pointId"]): i["pointValue"] for i in (await m_res.json()).get("data", [])}
                            mid = sn[-2:]
                            self.module_data[mid] = {f"cell_{i:02d}": m_rt.get(str(11100 + i * 100)) for i in range(1, 31)}
                    
                    data["module_data"] = self.module_data
                    return data
            except Exception as e: raise UpdateFailed(f"API Error: {e}")

    def _headers(self, body, path):
        date = formatdate(timeval=None, localtime=False, usegmt=True)
        md5 = base64.b64encode(hashlib.md5(body.encode("utf-8")).digest()).decode("utf-8")
        sig_str = f"POST\n{md5}\napplication/json\n{date}\n{path}"
        sig = base64.b64encode(hmac.new(self.api_secret.encode("utf-8"), sig_str.encode("utf-8"), "sha1").digest()).decode("utf-8")
        return {"Content-Type": "application/json;charset=UTF-8", "Content-MD5": md5, "Date": date, "Authorization": f"API {self.api_id}:{sig}"}