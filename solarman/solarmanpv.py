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

        inverter_data_list = ConstructData(inverter_data).device_current_data
        logger_data_list = ConstructData(logger_data).device_current_data
        if meter_data:
            meter_data_list = ConstructData(meter_data).device_current_data

        if config.get("debug", False):
            logging.info(json.dumps("STATION DATA"))
            logging.info(json.dumps(station_data, indent=4, sort_keys=True))
            logging.info(json.dumps("INVERTER DATA"))
            logging.info(json.dumps(inverter_data, indent=4, sort_keys=True))
            logging.info(json.dumps("INVERTER DATA LIST"))
            logging.info(json.dumps(inverter_data_list, indent=4, sort_keys=True))
            logging.info(json.dumps("LOGGER DATA"))
            logging.info(json.dumps(logger_data, indent=4, sort_keys=True))
            logging.info(json.dumps("LOGGER DATA LIST"))
            logging.info(json.dumps(logger_data_list, indent=4, sort_keys=True))
            if meter_data:
                logging.info(json.dumps("METER DATA"))
                logging.info(json.dumps(meter_data, indent=4, sort_keys=True))
                logging.info(json.dumps("METER DATA LIST"))
                logging.info(json.dumps(meter_data_list, indent=4, sort_keys=True))

        discard = ["code", "msg", "requestId", "success"]
        topic = config["mqtt"]["topic"]

        _t = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            inverter_device_state = inverter_data["deviceState"]
        except KeyError:
            inverter_device_state = 128

        meter_state = None
        if meter_data:
            try:
                meter_state = meter_data["deviceState"]
            except KeyError:
                meter_state = 128

        mqtt_connection = Mqtt(config["mqtt"])

        if meter_data and meter_state == 1:
            logging.info(
                "%s - Meter DeviceState: %s -> Publishing to MQTT ...", _t, meter_state
            )
            for i in meter_data:
                if meter_data[i]:
                    mqtt_connection.message(topic + "/meter/" + i, meter_data[i])
            mqtt_connection.message(
                topic + "/meter/attributes", json.dumps(meter_data_list)
            )

        if inverter_device_state == 1:
            logging.info(
                "%s - Inverter DeviceState: %s -> Publishing to MQTT ...",
                _t,
                inverter_device_state,
            )
            for i in station_data:
                if station_data[i] and i not in discard:
                    mqtt_connection.message(topic + "/station/" + i, station_data[i])

            for i in inverter_data:
                if inverter_data[i] and i not in discard:
                    mqtt_connection.message(topic + "/inverter/" + i, inverter_data[i])

            mqtt_connection.message(
                topic + "/inverter/attributes",
                json.dumps(inverter_data_list),
            )

            for i in logger_data:
                if logger_data[i] and i not in discard:
                    mqtt_connection.message(topic + "/logger/" + i, logger_data[i])

            mqtt_connection.message(
                topic + "/logger/attributes",
                json.dumps(logger_data_list),
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
