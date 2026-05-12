"""
SolarmanPV - Collect PV data from the SolarmanPV API and send Power+Energy data (W+kWh) to MQTT
"""

import json
import logging
import sys
import time

from datetime import datetime, timedelta
from astral import LocationInfo
from astral.sun import sun

from .api import SolarmanApi, ConstructData
from .helpers import ConfigCheck, HashPassword
from .mqtt import Mqtt

logging.basicConfig(level=logging.INFO)


class SolarmanPV:
    """
    SolarmanPV data collection and MQTT publishing
    """

    def __init__(self, file):
        self.config = self.load_config(file)

    def load_config(self, file):
        """
        Load configuration
        :return:
        """
        with open(file, "r", encoding="utf-8") as config_file:
            config = json.load(config_file)

        if not isinstance(config, list):
            config = [config]

        return config

    def validate_config(self, config):
        """
        Validate config file
        :param file: Config file
        :return:
        """
        config = self.load_config(config)
        for conf in config:
            print(
                f"## CONFIG INSTANCE NAME: {conf['name']} [{config.index(conf) + 1}/{len(config)}]"
            )
            ConfigCheck(conf)

    DISCARD = ["code", "msg", "requestId", "success"]

    @staticmethod
    def _publish_section(mqtt_connection, topic_prefix, data, data_list, discard):
        """
        Publish a device data dict (and, when available, its attributes list) to MQTT.
        Does nothing when ``data`` is missing (e.g. the ID was omitted from the config).
        """
        if not data:
            return
        for key in data:
            if data[key] and key not in discard:
                mqtt_connection.message(f"{topic_prefix}/{key}", data[key])
        if data_list is not None:
            mqtt_connection.message(
                f"{topic_prefix}/attributes", json.dumps(data_list)
            )

    @staticmethod
    def _log_debug(sections):
        """
        Dump the raw and restructured data for every fetched section.
        :param sections: mapping of label -> (data, data_list)
        """
        for label, (data, data_list) in sections.items():
            if data:
                logging.info(json.dumps(f"{label} DATA"))
                logging.info(json.dumps(data, indent=4, sort_keys=True))
                if data_list is not None:
                    logging.info(json.dumps(f"{label} DATA LIST"))
                    logging.info(json.dumps(data_list, indent=4, sort_keys=True))

    def single_run(self, config):
        """
        Output current watts and kilowatts
        :return:
        """
        pvdata = SolarmanApi(config)

        station_data = pvdata.station_realtime
        inverter_data = pvdata.device_current_data_inverter
        logger_data = pvdata.device_current_data_logger
        meter_data = pvdata.device_current_data_meter

        inverter_data_list = (
            ConstructData(inverter_data).device_current_data if inverter_data else None
        )
        logger_data_list = (
            ConstructData(logger_data).device_current_data if logger_data else None
        )
        meter_data_list = (
            ConstructData(meter_data).device_current_data if meter_data else None
        )

        if config.get("debug", False):
            self._log_debug(
                {
                    "STATION": (station_data, None),
                    "INVERTER": (inverter_data, inverter_data_list),
                    "LOGGER": (logger_data, logger_data_list),
                    "METER": (meter_data, meter_data_list),
                }
            )

        topic = config["mqtt"]["topic"]
        _t = time.strftime("%Y-%m-%d %H:%M:%S")

        try:
            inverter_device_state = inverter_data["deviceState"]
        except (KeyError, TypeError):
            inverter_device_state = 128

        meter_state = meter_data.get("deviceState", 128) if meter_data else None

        mqtt_connection = Mqtt(config["mqtt"])

        if meter_data and meter_state == 1:
            logging.info(
                "%s - Meter DeviceState: %s -> Publishing to MQTT ...", _t, meter_state
            )
            self._publish_section(
                mqtt_connection, topic + "/meter", meter_data, meter_data_list, []
            )

        if inverter_device_state == 1:
            logging.info(
                "%s - Inverter DeviceState: %s -> Publishing to MQTT ...",
                _t,
                inverter_device_state,
            )
            self._publish_section(
                mqtt_connection, topic + "/station", station_data, None, self.DISCARD
            )
            self._publish_section(
                mqtt_connection,
                topic + "/inverter",
                inverter_data,
                inverter_data_list,
                self.DISCARD,
            )
            self._publish_section(
                mqtt_connection,
                topic + "/logger",
                logger_data,
                logger_data_list,
                self.DISCARD,
            )

        elif inverter_device_state == 128:
            logging.info(
                "%s - Inverter DeviceState: %s"
                "-> No valid inverter status data available",
                _t,
                inverter_device_state,
            )
        else:
            mqtt_connection.message(
                topic + "/inverter/deviceState", inverter_data.get("deviceState")
            )
            if logger_data:
                mqtt_connection.message(
                    topic + "/logger/deviceState", logger_data.get("deviceState")
                )
            logging.info(
                "%s - Inverter DeviceState: %s"
                "-> Only status MQTT publish (probably offline due to nighttime shutdown)",
                _t,
                inverter_device_state,
            )

    def single_run_loop(self, file):
        """
        Perform single runs for all config instances
        """
        config = self.load_config(file)
        for conf in config:
            self.single_run(conf)

    def daemon(self, file, interval):
        """
        Run as a daemon process — only between sunrise and sunset.
        :param file: Config file
        :param interval: Run interval in seconds
        :return:
        """
        interval = int(interval)
        logging.info(
            "Starting daemonized with a %s seconds run interval (daylight only)", str(interval)
        )
        while True:
            try:
                config = self.load_config(file)
                # Sun window is taken from the first config entry (all installations
                # are typically at the same location); if yours are in different
                # geographic locations, see the note below.
                sunrise, sunset = self._get_sun_window(config[0])
                now = datetime.now(sunrise.tzinfo)

                if sunrise <= now <= sunset:
                    logging.info(
                        "Daylight window %s - %s, running poll",
                        sunrise.strftime("%H:%M"), sunset.strftime("%H:%M"),
                    )
                    SolarmanPV.single_run_loop(self, file)
                    time.sleep(interval)
                else:
                    sleep_for = self._seconds_until_next_sunrise(config[0])
                    logging.info(
                        "Outside daylight window (now %s, sunrise %s, sunset %s). "
                        "Sleeping %d s until next sunrise.",
                        now.strftime("%Y-%m-%d %H:%M"),
                        sunrise.strftime("%H:%M"), sunset.strftime("%H:%M"),
                        sleep_for,
                    )
                    time.sleep(sleep_for)

            except Exception as error:  # pylint: disable=broad-except
                logging.error("Error on start: %s", str(error))
                sys.exit(1)
            except KeyboardInterrupt:
                logging.info("Exiting on keyboard interrupt")
                sys.exit(0)

    def create_passhash(self, password):
        """
        Create passhash from password
        :param password: Password
        :return:
        """
        pwstring = HashPassword(password)
        print(pwstring.hashed)

    def _get_sun_window(self, conf):
        """
        Return (sunrise, sunset) as aware datetimes in the configured timezone.
        Requires the following in config:
        "location": {
            "latitude": 51.7592,
            "longitude": 19.4560,
            "timezone": "Europe/Warsaw",      # optional, defaults to UTC
            "name": "Lodz",                   # optional, cosmetic only
            "twilight_offset_minutes": 15     # optional, defaults to 0
        }
        """
        loc_cfg = conf.get("location", {})
        lat = loc_cfg["latitude"]
        lon = loc_cfg["longitude"]
        tz_name = loc_cfg.get("timezone", "UTC")
        offset = timedelta(minutes=loc_cfg.get("twilight_offset_minutes", 0))

        location = LocationInfo(
            name=loc_cfg.get("name", "site"),
            region=loc_cfg.get("region", ""),
            timezone=tz_name,
            latitude=lat,
            longitude=lon,
        )
        today = datetime.now(location.tzinfo).date()
        s = sun(location.observer, date=today, tzinfo=location.tzinfo)
        return s["sunrise"] - offset, s["sunset"] + offset

    def _seconds_until_next_sunrise(self, conf):
        """
        How many seconds to sleep until the next sunrise
        (if already past sunset, until tomorrow's sunrise).
        """
        loc_cfg = conf.get("location", {})
        tz_name = loc_cfg.get("timezone", "UTC")
        offset = timedelta(minutes=loc_cfg.get("twilight_offset_minutes", 0))

        location = LocationInfo(
            name=loc_cfg.get("name", "site"),
            region=loc_cfg.get("region", ""),
            timezone=tz_name,
            latitude=loc_cfg["latitude"],
            longitude=loc_cfg["longitude"],
        )
        now = datetime.now(location.tzinfo)
        today_sun = sun(location.observer, date=now.date(), tzinfo=location.tzinfo)
        today_sunrise = today_sun["sunrise"] - offset

        if now < today_sunrise:
            target = today_sunrise
        else:
            tomorrow = now.date() + timedelta(days=1)
            target_sun = sun(location.observer, date=tomorrow, tzinfo=location.tzinfo)
            target = target_sun["sunrise"] - offset

        return max(60, int((target - now).total_seconds()))
