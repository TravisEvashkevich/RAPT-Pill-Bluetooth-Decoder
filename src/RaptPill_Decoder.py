from __future__ import annotations
import asyncio
from pathlib import Path
import json
from typing import Optional
from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from struct import unpack
from collections import namedtuple
from datetime import datetime, timezone
import traceback


# Taken from rapt_ble on github (https://github.com/sairon/rapt-ble/blob/main/src/rapt_ble/parser.py#L14) as well as the decode_rapt_data
RAPTPillMetricsV1 = namedtuple("RAPTPillMetrics", "version, mac, temperature, gravity, x, y, z, battery")
RAPTPillMetricsV2 = namedtuple(
    "RAPTPillMetrics",
    "hasGravityVel, gravityVel, temperature, gravity, x, y, z, battery",
)


class InfluxDbWrapper(object):
    def __init__(self, db_name: str, db_address: str, db_port: int, db_username: str, db_pwd: str):
        from influxdb.resultset import ResultSet

        self.db_name = db_name
        self.db_host = db_address
        self.db_port = db_port
        self.db_username = db_username
        self.db_pwd = db_pwd
        try:
            from influxdb import InfluxDBClient

            self.client = InfluxDBClient(
                host=self.db_host,
                port=self.db_port,
                username=self.db_username,
                password=self.db_pwd,
                database=self.db_name,
            )

        except:
            print(traceback.format_exc())
            self.client = None

    def add_datapoint(self, pill: RaptPill):
        """Add a datapoint to the database if connected

        Args:
            pill (RaptPill): pill data to add
        """
        if not self.client:
            print("!!!! Couldn't connect to DB !!!!")
            return
        # Define the data
        now = datetime.now(timezone.utc)
        inf_data = [
            {
                "measurement": "rapt_pill_metrics",
                "tags": {"version": pill.version},
                "fields": {
                    "sessionName": pill.session_name,
                    "gravity_velocity": pill.gravity_velocity,
                    "curr_gravity": float(pill.curr_gravity),
                    "abv": float(pill.abv),
                    "temperature": float(pill.temperature),
                    "battery": float(pill.battery),
                    "x": float(pill.x_accel),
                    "y": float(pill.y_accel),
                    "z": float(pill.z_accel),
                    "timestamp": int(now.timestamp() * 1e9),
                },
            }
        ]

        # Write the data to the database
        self.client.write_points(inf_data)

    def run_query(self, query: str):
        """Run a query on the database and get the results

        Args:
            query (str): query to run

        Returns:
            None | asyncio.Generator[ResultSet, asyncio.Any, None] | ResultSet | list[ResultSet]: results or None
        """
        if not self.client:
            print("!!!! Couldn't connect to DB !!!!")
            return

        return self.client.query(query)


class InfluxDbWrapperV2(object):
    def __init__(self, db_bucket: str, db_address: str, db_port: int, db_org: str, db_token: str):
        from influxdb_client import InfluxDBClient, Point, WritePrecision
        from influxdb_client.client.write_api import SYNCHRONOUS

        self.db_bucket = db_bucket
        self.db_host = f"http://{db_address}"
        self.db_port = db_port
        self.db_org = db_org
        self.db_token = db_token
        try:

            self.client = InfluxDBClient(
                url=f"{self.db_host}:{self.db_port}",
                token=self.db_token,
                org=self.db_org,
            )
            self.point = Point
            self.write_pres = WritePrecision
            self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
            print("======== Connected to DbV2!")
        except:
            print(traceback.format_exc())
            self.client = None

    def add_datapoint(self, pill: RaptPill):
        """Add a datapoint to the database if connected

        Args:
            pill (RaptPill): pill data to add
        """
        if not self.client:
            print("!!!! Couldn't connect to DB !!!!")
            return
        # Define the data
        now = datetime.now(timezone.utc)
        point = (
            self.point("rapt_pill_metrics")
            .tag("version", pill.version)
            .tag("sessionName", pill.session_name)
            .field("sessionName", pill.session_name)
            .field("gravity_velocity", pill.gravity_velocity)
            .field("curr_gravity", pill.curr_gravity)
            .field("abv", pill.abv)
            .field("temperature", pill.temperature)
            .field("battery", pill.battery)
            .field("x", pill.x_accel)
            .field("y", pill.y_accel)
            .field("z", pill.z_accel)
            .field("timestamp", int(now.timestamp() * 1e9))
        )

        # Write the data to the database
        self.write_api.write(bucket=self.db_bucket, org=self.db_org, record=point)
        # print("======= wrote Data to V2 Db")

    def run_query(self, query: str):
        """Run a query on the database and get the results

        Args:
            query (str): query to run

        Returns:
            None | asyncio.Generator[ResultSet, asyncio.Any, None] | ResultSet | list[ResultSet]: results or None
        """
        if not self.client:
            print("!!!! Couldn't connect to DB !!!!")
            return

        return self.client.query_api().query(query=query)


class RaptPill(object):
    active_pollers = []

    def __init__(
        self,
        session_name: str,
        mac_address: str,
        poll_interval: int,
        influx_database_details: Optional[InfluxDbWrapper] = None,
        temp_as_celsius: bool = True,
    ):
        """Create a Pill object to actively poll for data

        Args:
            session_name (str): name of the session we are tracking
            mac_address (str): address of the pill we are tracking so we know which bluetooth device to watch for
            poll_interval (int): how often should we poll for data in seconds. This ideally will be slightly longer than what is set in the Pill firmware
            influx_database_details(InfluxDbWrapper): details for database to log data to - If None, no data is logged and is just printed to output.
            temp_as_celsius(bool): set False if you want temp as F instead
        """
        self.__db_details = influx_database_details
        self.__log_to_db = True if self.__db_details is not None else False
        # how often should we actively poll for data. This should ideally be slightly longer
        # than the send rate of the PILL so we make sure we are looking while it will be sending
        self.__polling_interval = poll_interval
        # macaddress of pill
        self.__mac_address = mac_address
        # session that will be logged with data
        self.__session_name = session_name
        # should be 1 or 2
        self.__api_version = -1
        # temperature value (kelvin)
        self.__temperature = 0
        # C or F
        self.__is_celsius = True
        self.__gravity_velocity = -1
        # this should be set True on the first value we retrieve so we can then calculate abv actively
        self.__starting_gravity_set = False
        # Starting gravity so we can actively know how much abv we have
        self.__starting_gravity = 1.000
        # Current Gravity
        self.__curr_gravity = 1.000
        # abv we have calculated off the start/curr gravity difference
        self.__abv = -1
        # accelerometer data
        self.__x = -100
        self.__y = -100
        self.__z = -100
        # battery life
        self.__battery = -1
        # When was the last event
        self.__last_event = None

        # polling variables
        self.__polling_task = None
        self.active_pollers.append(self)
        self.bt_scanner = None

    @property
    def starting_gravity(self) -> float:
        """get the starting gravity as set on first data retrieval
            This is get/set so we can't overwrite it once we're going
        Returns:
            float: gravity value
        """
        return self.__starting_gravity

    @starting_gravity.setter
    def starting_gravity(self, gravity: float):
        """set the starting gravity. This should only be allowed once

        Args:
            gravity (float): value to set as starting gravity
        """
        if self.__starting_gravity_set:
            return
        self.__starting_gravity_set = True
        self.__starting_gravity = gravity

    @property
    def session_name(self) -> str:
        return self.__session_name

    @property
    def gravity_velocity(self) -> float:
        return self.__gravity_velocity

    @property
    def curr_gravity(self):
        return self.__curr_gravity

    @property
    def abv(self):
        return self.__abv

    @property
    def temperature(self):
        return self.__temperature

    @property
    def battery(self):
        return self.__battery

    @property
    def version(self):
        return self.__api_version

    @property
    def x_accel(self):
        return self.__x

    @property
    def y_accel(self):
        return self.__y

    @property
    def z_accel(self):
        return self.__z

    @property
    def last_event(self):
        return self.__last_event

    def start_session(self):
        print(f"Starting Session: {self.session_name}")
        self.bt_scanner = BleakScanner(detection_callback=self.device_found)
        if self.__polling_task is None:
            self.__polling_task = asyncio.create_task(self.__poll_for_device())

    def end_session(self):
        if self.__polling_task is not None:
            self.__polling_task.cancel()
            self.__polling_task = None
            self.active_pollers.remove(self)
            print(f"Ended Session: {self.session_name}")

    async def __poll_for_device(self):
        """poll for data from the Pill"""
        while True:
            await self.bt_scanner.start()
            await asyncio.sleep(self.__polling_interval)
            await self.bt_scanner.stop()

    def device_found(self, device: BLEDevice, advertisement_data: AdvertisementData):
        """This is fired everytime the bleakScanner finds a bluetooth device so we check if it is the macaddress of the pill we are tracking
        if it is not, then we ignore it

        Args:
            device (BLEDevice): bluetooth device that was found
            advertisement_data (AdvertisementData): advertisment data from the found bluetooth device
        """
        if device.address != self.__mac_address:
            return
        # Assuming the custom data is under manufacturer specific data
        raw_data = advertisement_data.manufacturer_data.get(16722, None)
        if raw_data == b"PTdPillG1":
            return
        if raw_data is None:
            return
        self.decode_rapt_data(raw_data)
        print(self)

    def calculate_abv(self, current_gravity: float) -> float:
        """calculate the alchol by volume given the current gravity (we estimate it by calculating against the start gravity we have stored)

        Args:
            current_gravity (float): current gravity value

        Returns:
            float: estimated abv
        """
        return round((self.starting_gravity - current_gravity) * 131.25, 4)

    def calculate_temp(self, kelvin: float) -> float:
        """calculate the temperature from the given kelvin value, return in C or F depending on what we have set as our default

        Args:
            kelvin (float): kelvin temp value

        Returns:
            float: temperature in F or C
        """
        # return in c
        if self.__is_celsius:
            return round(kelvin - 273.15, 2)
        # return in f
        return (kelvin - 273.15) * (9 / 5) + 32

    def decode_rapt_data(self, data: bytes):
        """Given bytes from a bluetooth advertisement, decode it into the RAPTPillMetrics tuple and return it so it can be used.
        Updates class values
        Args:
            data (bytes): advertisement data as bytes

        Raises:
            ValueError: length of data isn't correct

        """
        if len(data) != 23:
            raise ValueError("advertisment data must have length 23")

        # print(f"===> raw_data: {data}")

        # Extract and check the version
        prefix, version = unpack(">2sB", data[:3])
        # Validate the prefix
        if prefix != b"PT":
            raise ValueError("Unexpected prefix")
        # get "raw" data, drop second part of the prefix ("PT"), start with the version
        if version == 1:
            metrics_raw = RAPTPillMetricsV1._make(unpack(">B6sHfhhhh", data[2:]))
        else:
            # metrics_raw = RAPTPillMetricsV2._make( unpack(">BfHfhhhH", data[4:]))
            metrics_raw = RAPTPillMetricsV2._make(unpack(">BfHfhhhH", data[4:]))

        now = datetime.now(timezone.utc)
        dt_string = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        # print("date and time =", dt_string)
        if not self.__starting_gravity_set:
            self.starting_gravity = round(metrics_raw.gravity / 1000, 4)
        self.__api_version = version
        self.__gravity_velocity = metrics_raw.gravityVel
        self.__curr_gravity = round(metrics_raw.gravity / 1000, 4)
        self.__abv = self.calculate_abv(self.__curr_gravity)
        self.__temperature = self.calculate_temp(metrics_raw.temperature / 128)
        self.__battery = round(metrics_raw.battery / 256)
        self.__last_event = dt_string
        self.__x = metrics_raw.x / 16
        self.__y = metrics_raw.y / 16
        self.__z = metrics_raw.z / 16

        if self.__log_to_db:
            self.__db_details.add_datapoint(self)
        else:
            print(self)

    def __repr__(self):
        return (
            "Current Data: \n"
            f"Session Name: {self.__session_name} , "
            "\n"
            f"Firmware Version: {self.version}, "
            "\n"
            f"MacAddr: {self.__mac_address} , "
            "\n"
            f"Start Gravity: {self.__starting_gravity} , "
            "\n"
            f"CurrGravity: {self.__curr_gravity} , "
            "\n"
            f"ABV: {self.__abv} , "
            "\n"
            f"Last Event TimeStamp:{self.__last_event}"
            "\n"
            f"Temp: {self.__temperature} {'f' if not self.__is_celsius else 'c'}, "
            "\n"
            f"X-Accel : {self.__x} , "
            "\n"
            f"Y-Accel : {self.__y} , "
            "\n"
            f"Z-Accel : {self.__z} , "
            "\n"
            f"Battery : {self.__battery} , "
        )


async def main() -> None:
    # Handle setup of database and pill(s)
    data_path = Path(__file__).parent.joinpath("data.json")

    # if data is filled in data.json file use it and start sessions and database (if set)
    if data_path.exists():
        # Read data.json and spin up processes
        data = json.loads(data_path.read_text())
        influx_details = None
        if data.get("InfluxDb Details", None):
            db_details = data.get("InfluxDb Details", None)
            if db_details.get("Database Version", None) == 1:
                print("======== Using influxdb V1")
                influx_details = InfluxDbWrapper(
                    db_details.get("DatabaseV1 Name", None),
                    db_details.get("Database Address", ""),
                    db_details.get("Database Port", 8086),
                    db_details.get("DatabaseV1 Username", ""),
                    db_details.get("DatabaseV1 Password", ""),
                )
            elif db_details.get("Database Version", None) == 2:
                print("======== Using Influxdb V2")
                influx_details = InfluxDbWrapperV2(
                    db_details.get("DatabaseV2 Bucket", None),
                    db_details.get("Database Address", ""),
                    db_details.get("Database Port", 8086),
                    db_details.get("DatabaseV2 Org", ""),
                    db_details.get("DatabaseV2 Token", ""),
                )
        for pill_details in data.get("Sessions", []):
            # MAC addresses of your RAPT Pill(s) - in case you have more (This hasn't been actually tested but it should in theory work.)
            pill = RaptPill(
                pill_details.get("Session Name", "NoSessionNameSet"),
                pill_details.get("Mac Address", None),
                pill_details.get("Poll Interval", ""),
                influx_database_details=influx_details,
                temp_as_celsius=pill_details.get("Temp in C", True),
            )

            # We set the Starting Gravity here if we had a disconnect or something like that and we need to set it again (and we don't want the abv to be wrong)
            # In general this shouldn't be set - only if a session has been lost and needs to be restarted mid session
            # TODO: maybe check the database for the first gravity of this session instead?
            if pill_details.get("Get Start Gravity From Db", False):
                # Check if this session is in the database already over the the last 10 days and check the first gravity we logged if it is
                starting_gravity_in_db = 0
                if db_details.get("Database Version", None) == 1:
                    query = f"""
                    SELECT MAX("curr_gravity")
                    FROM "rapt_pill_metrics"
                    WHERE "sessionName" = '{pill_details.get('Session Name', '')}' AND time > now() - 10d
                    ORDER BY time ASC
                    LIMIT 1
                    """
                    results = influx_details.run_query(query)
                    points = list(results.get_points())
                    starting_gravity_in_db = points[0].get("curr_gravity", 0)

                else:
                    bucket = db_details.get("DatabaseV2 Bucket")
                    session_name = pill_details.get("Session Name", "")
                    query = f"""
                    from(bucket: "{bucket}")
                    |> range(start: -10d)
                    |> filter(fn: (r) => r["_measurement"] == "rapt_pill_metrics")
                    |> filter(fn: (r) => r["sessionName"] == "{session_name}")
                    |> filter(fn: (r) => r["_field"] == "curr_gravity")
                    |> sort(columns: ["_time"], desc: true)
                    |> limit(n: 1)
                    """
                    results = influx_details.run_query(query)
                    for table in results:
                        for record in table.records:
                            starting_gravity_in_db = record.get_value()
                    print(f"Got {starting_gravity_in_db} as current/starting gravity in db")
                # Only set the gravity if we found something. Else leave it up to the PILL to set it.
                if starting_gravity_in_db != 0:
                    pill.starting_gravity = starting_gravity_in_db
            elif pill_details.get("Starting Gravity", 0) != 0 and not pill_details.get(
                "Get Start Gravity From Db", False
            ):
                pill.starting_gravity = pill_details.get("Starting Gravity", 0)
                print(f"Setting starting gravity: {pill.starting_gravity} - from data.json")

            pill.start_session()
    else:
        # fill in all the details yourself here if you don't want to use the data.json
        influx_details = InfluxDbWrapper("Database Name", "localhost", 8086, "user", "somePassword")
        # MAC addresses of your RAPT Pill(s) - in case you have more (This hasn't been actually tested but it should in theory work.)
        pill = RaptPill(
            "Exmple Session Name",
            "78:E3:6D:29:19:16",
            120,
            influx_database_details=influx_details,
        )
        pill.start_session()


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(main())
    loop.run_forever()
