"""Package to facilitate communications."""

import settings
import micropython  # type: ignore
from collections import OrderedDict

from machine import Pin, Timer, I2C  # type: ignore
from network import WLAN, STA_IF  # type: ignore
from time import time_ns, time, sleep
from struct import unpack  # type: ignore

# mf = __import__("micropython-fusion")

# from constants import qmc5883l
from timing import TimerManager
from utils import bytes_to_int


class WIFIManager:
    """Singleton that manages the WIFI connection."""

    def __new__(cls, *args, **kwargs):
        """Create a singleton."""

        if not hasattr(cls, "instance"):
            cls.instance: WIFIManager = super(WIFIManager, cls).__new__(cls)

        return cls.instance

    def __init__(
        self,
        *,
        ssid: str = None,
        password: str = None,
        callback: callable = None,
        block: bool = False,
        timeout: float = 10,
    ):
        """Initialize the WIFI connection.

        Args:
            ssid: WiFi SSID (default from settings)
            password: WiFi password (default from settings)
            callback: callable to invoke when connected (optional)
            block: if True, block until connected or timeout (default False)
            timeout: max seconds to wait if block=True (default 10)
        """

        # Always update the callback even on subsequent singleton calls
        if callback:
            self.connect_callback = callback

        if not hasattr(self, "sta_if"):
            self.ssid: str = ssid or settings.WIFI["SSID"]
            self.password: str = password or settings.WIFI["Password"]
            # default callback storage if not set above
            if not hasattr(self, "connect_callback"):
                self.connect_callback = None

            self.sta_if: WLAN = WLAN(STA_IF)
            self.sta_if.active(True)

            if not self.ssid or not self.password:
                raise ValueError("SSID or password not provided.")

            # Resolve the exact broadcast SSID using a case-insensitive scan so
            # that stored credentials with different capitalisation still connect.
            resolved_ssid: str = self._resolve_ssid(self.ssid)
            self.sta_if.connect(resolved_ssid, self.password)

            # Always start the polling timer — needed to detect connection and fire callback
            TimerManager().get_timer(callback=self.check_connection_tick, periods=[1000], cycles=90)

            if settings.WIFI.get("Print_on_connect"):
                print(self)

            # Block until connected if requested
            if block:
                start = time()
                while not self.is_connected and (time() - start) < timeout:
                    sleep(0.1)
                if not self.is_connected:
                    print(f"WiFi: timeout after {timeout}s, not connected")
                else:
                    print(f"WiFi: connected in {time() - start:.1f}s")

    @property
    def is_connected(self):
        """Check if the WIFI is connected."""

        return self.sta_if.isconnected()

    def _resolve_ssid(self, target_ssid: str) -> str:
        """Scan visible networks and return the exact-case SSID that matches *target_ssid*.

        Comparison is case-insensitive.  If no match is found (e.g. the network
        is temporarily out of range or hidden), returns *target_ssid* unchanged
        so the connection attempt still proceeds.

        Args:
            target_ssid: The SSID string to match, as stored in settings.

        Returns:
            The exact SSID string as broadcast by the access point, or
            *target_ssid* if no case-insensitive match is found.
        """

        target_lower: str = target_ssid.lower()

        try:
            results = self.sta_if.scan()  # [(ssid, bssid, channel, rssi, authmode, hidden), ...]

            for entry in results:
                try:
                    ssid_raw = entry[0]

                    if isinstance(ssid_raw, bytes):
                        scanned_ssid: str = ssid_raw.decode("utf-8", "replace").strip()
                    else:
                        scanned_ssid = str(ssid_raw).strip()

                    if scanned_ssid.lower() == target_lower:
                        return scanned_ssid

                except Exception:
                    continue

        except Exception as error:
            print(f"WiFi: SSID scan failed ({error}), using stored value")

        return target_ssid

    @property
    def ip(self):
        """Return the IP address."""

        return self.sta_if.ifconfig()[0]

    def __str__(self):
        """Return the WIFI status."""

        return f"IP: {self.ip}, Connected: {self.is_connected}"

    def check_connection_tick(self, timer):
        """Check the WIFI connection repeatedly until it's up, or our counter reaches zero."""

        if self.is_connected:
            if settings.WIFI.get("Blink_on_connect"):
                LEDManager().blink(times=2)
            timer.stop(None)
            # invoke the provided callback (deferred via micropython.schedule)
            if hasattr(self, "connect_callback") and self.connect_callback:
                try:
                    micropython.schedule(self._run_connect_callback, 0)
                except Exception:
                    # fallback to direct call
                    try:
                        self.connect_callback()
                    except Exception:
                        pass

    def _run_connect_callback(self, _):
        try:
            self.connect_callback()
        except Exception:
            pass


class LEDManager:
    """Singleton that manages the LED."""

    def __new__(cls, *args, **kwargs):
        """Create a singleton."""

        if not hasattr(cls, "instance"):
            cls.instance: LEDManager = super(LEDManager, cls).__new__(cls)

        return cls.instance

    def __init__(self, pin: int = None):
        """Initialize the LED."""

        if not hasattr(self, "led"):
            self.led: Pin = Pin(pin or settings.PINS["LED"], Pin.OUT)
            self.blinking: bool = False
            self.blink_count: int = 0
            self.timer: Timer = None

    def __str__(self):
        """Return the LED status."""

        return f"LED is {'on' if self.led.value() else 'off'}"

    @property
    def value(self):
        """Return the LED value."""

        return self.led.value()

    def on(self):
        """Turn the LED on."""

        self.led.on()

    def off(self):
        """Turn the LED off."""

        self.led.off()

    def toggle(self):
        """Toggle the LED."""

        self.led.value(not self.led.value())

    def blink(self, *, times: int = 5, on_period: int = 250, off_period: int = 125):
        """Blink the LED."""

        cycles = times * 2 - 1

        if self.blinking:
            return False

        self.blinking = True
        self.timer = TimerManager().get_timer(
            callback=self.blink_tick, periods=[on_period, off_period], cycles=cycles, end_callback=self.blink_end
        )

        return True

    def blink_tick(self, _):
        """Blink the LED."""

        self.toggle()

    def blink_end(self, _):
        """End the blinking."""

        self.blinking = False
        self.timer = None

    def blink_stop(self):
        """Stop the blinking."""

        self.off()

        if self.timer:
            self.timer.stop()


class I2CManager:
    """Singleton that manages the I2C bus."""

    def __new__(cls, *args, **kwargs):
        """Create a singleton."""

        if not hasattr(cls, "instance"):
            cls.instance: I2CManager = super(I2CManager, cls).__new__(cls)

        return cls.instance

    def __init__(self) -> None:
        """Initialize the I2C bus."""

        if not hasattr(self, "i2c"):
            self.i2c: I2C = I2C(
                0, scl=Pin(settings.PINS["SCL"]), sda=Pin(settings.PINS["SDA"]), freq=settings.I2C["Freq"]
            )

            self.devices: dict = {}
            self.scan: list = self.i2c.scan()

            for id, device in settings.I2C["IDs"].items():
                if id in self.scan:
                    if device == "AccellGyro" and settings.ACCEL_GYRO["Type"] == "MPU6050":
                        self.devices[device] = self.MPU6050(name=device, address=id)

                    if device == "Compass" and settings.COMPASS["Type"] == "QMC5883L":
                        self.devices[device] = self.QMC5883L(name=device, address=id)

                    else:
                        self.devices[device] = self.Device(name=device, address=id)

            if settings.I2C.get("Blink_on_connect"):
                LEDManager().blink(times=3)

            if settings.I2C.get("Print_on_connect"):
                print(self)

    class Device:
        """Device on the I2C bus."""

        def __init__(self, name: str, address: int) -> None:
            """Initialize the device."""

            self.name: str = name
            self.address: int = address
            self.i2c = I2CManager().i2c

    class MPU6050(Device):
        """Accelerometer/Gyroscope.
        Based on https://github.com/adamjezek98/MPU6050-ESP8266-MicroPython, with thanks."""

        def __init__(self, name: str, address: int) -> None:
            """Initialize the device."""

            super().__init__(name=name, address=address)

            self.i2c.writeto(self.address, bytearray([107, 0]))

            self.values: OrderedDict[str:int] = OrderedDict(
                [
                    ("time_ns", None),
                    ("accel_x", None),
                    ("accel_y", None),
                    ("accel_z", None),
                    ("gyro_x", None),
                    ("gyro_y", None),
                    ("gyro_z", None),
                    ("temp", None),
                ]
            )

        def get_values(self) -> OrderedDict[str:int]:
            """Load and return the raw values."""

            bytes: bytearray = self.i2c.readfrom_mem(self.address, 0x3B, 14)
            self.values["time_ns"] = time_ns()
            self.values["accel_x"] = bytes_to_int(bytes[0], bytes[1])
            self.values["accel_y"] = bytes_to_int(bytes[2], bytes[3])
            self.values["accel_z"] = bytes_to_int(bytes[4], bytes[5])
            self.values["temp"] = bytes_to_int(bytes[6], bytes[7]) / 340.00 + 36.53
            self.values["gyro_x"] = bytes_to_int(bytes[8], bytes[9]) / settings.GYRO["Scale_factor"]
            self.values["gyro_y"] = bytes_to_int(bytes[10], bytes[11]) / settings.GYRO["Scale_factor"]
            self.values["gyro_z"] = bytes_to_int(bytes[12], bytes[13]) / settings.GYRO["Scale_factor"]

            return self.values

    class QMC5883L(Device):
        """Compass.
        Based on https://github.com/robert-hh/QMC5883, with thanks."""

        def __init__(
            self,
            name: str,
            address: int,
            temp_offset: float = 50.0,
            oversampling=None,
            gauss=None,
            rate=None,
            mode=None,
        ) -> None:
            """Initialize the device."""

            super().__init__(name=name, address=address)

            self.command = bytearray(1)
            self.data = bytearray(9)
            self.temp_offset = temp_offset

            self.oversampling = oversampling if oversampling is not None else qmc5883l.CONFIG_OS64
            self.gauss = gauss if gauss is not None else qmc5883l.CONFIG_2GAUSS
            self.rate = rate if rate is not None else qmc5883l.CONFIG_100HZ
            self.mode = mode if mode is not None else qmc5883l.CONFIG_CONT

            # Reset the device.
            self.command[0] = 1
            self.i2c.writeto_mem(self.address, qmc5883l.RESET, self.command)

            # Set the oversampling rate | range | rate | mode.
            self.command[0] = self.oversampling | self.gauss | self.rate | self.mode
            self.i2c.writeto_mem(self.address, qmc5883l.CONFIG, self.command)

            # Set the config 2 register?
            self.command[0] = qmc5883l.CONFIG2_INT_DISABLE
            self.i2c.writeto_mem(self.address, qmc5883l.CONFIG2, self.command)

        def get_values(self, no_temp=False) -> tuple(int, int, int, int) | tuple(int, int, int):
            """Load and return the raw values."""

            self.i2c.readfrom_mem_into(self.address, qmc5883l.X_LSB, self.data)
            x, y, z, _, temp = unpack("<hhhBh", self.data)
            if no_temp:
                return (x, y, z)

            return (x, y, z, temp)

        def get_values_no_temp(self) -> tuple(int, int, int):
            """Load and return the raw values."""

            return self.get_values(no_temp=True)

        def get_scaled_values(self):
            x, y, z, temp = self.get_values()
            scale = 12000 if self.gauss == qmc5883l.CONFIG_2GAUSS else 3000

            return (x / scale, y / scale, z / scale, (temp / 100 + self.temp_offset))

        def calibrate(self):
            """Calibrate the compass."""

        def calibrate_count_callback(self):
            """Callback for the calibration count."""

            for count in range(1, 100):
                yield count

            yield False

    def __str__(self):
        """Return the I2C status."""

        return f"I2C devices: " + ", ".join([f"{device.name} at {device.address}" for device in self.devices.values()])
