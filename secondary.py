# Implement a secondary peer for a polling based approach of
# data collection

# Library imports
import sys
import socket
import selectors
import logging
import types
import argparse
from time import sleep
from datetime import datetime
import json

# TODO: Change to false when using real sensors
TESTING = False

if not TESTING:
    import board
    import busio
    import simpleio
    import adafruit_ads1x15.ads1015 as ADS
    from adafruit_ads1x15.analog_in import AnalogIn
    from adafruit_seesaw.seesaw import Seesaw
    import adafruit_sht31d
else:
    import random


# TODO: Improve logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',)
slogger = logging.getLogger(f"(srv)")
slogger.setLevel(level=logging.INFO)

def parse_args():
    parser = argparse.ArgumentParser(
        prog="secondary",
        description="Secondary is a peer in an Adhoc network when contacted provides sensor data.",
        epilog="Example usage:\n python secondary.py --ip X.X.X.X --port N"
    )
    parser.add_argument("--ip", default="127.0.0.1", type=str, help="Enter IPv4 Address to bind to.", required=False)
    parser.add_argument("--port", type=int, help="Enter port number to listen on.", required=True)

    args = parser.parse_args()

    try:
        socket.inet_aton(args.ip)
    except OSError as e:
        slogger.error(f"--ip: Enter a valid IPv4 Address\n{e}")
        raise

    if (args.port < 1024 or args.port > 65535):
        slogger.error("--port: Enter a valid port in the range 1024-65535")
        raise RuntimeError("--port: Enter a valid port in the range 1024-65535")

    return args.ip, args.port

class Secondary:
    def __init__(self, host, port):
        slogger.debug("Initializing server...")

        # Set up selector.
        self.sel = selectors.DefaultSelector()

        # Set host and port and timeout duration.
        self.host = host
        self.port = port
        self.timeout = 60

        self.sht30 = {'temp': -1, 'hum': -1}
        self.seesaw = {'temp': -1, 'hum': -1}
        self.wind = {'speed': -1}
        self.timestamp = {'time': -1}
        self.sensors = [self.sht30, self.seesaw, self.wind]

        slogger.info("Server initialized.")


    def genData(self):
        if not TESTING:
            # create the I2C bus
            i2c = busio.I2C(board.SCL, board.SDA)

            # create the ADS object
            ads = ADS.ADS1015(i2c)

            # create the analog input channel for the anemometer on ADC pin(2)
            chan = AnalogIn(ads, ADS.P2)

            # create the Seesaw object for the soil moisture sensor
            soil_sensor = Seesaw(i2c, addr=0x36)

            # create the SHT31D object for the temperature and humidity sensor
            temp_sensor = adafruit_sht31d.SHT31D(i2c)

            # maps the voltage to wind speed via simpleio
            voltage = chan.voltage
            if voltage < 0.41:
                wind_speed = 0
            else:
                wind_speed = simpleio.map_range(voltage, 0.4, 2, 0, 32.4)

            SHT30_temp = temp_sensor.temperature
            SHT30_hum = temp_sensor.relative_humidity

            soil_moist = soil_sensor.moisture_read()

            data = {
                'temperature': SHT30_temp,
                'humidity': SHT30_hum,
                'wind_speed': wind_speed,
                'soil_moisture': soil_moist,
                'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        else:
            data = {
                'temperature': random.randrange(0, 20),
                'humidity': random.randrange(0, 20),
                'wind_speed': random.randrange(0, 20),
                'soil_moisture': random.randrange(0, 20),
                'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

        return data

    def genMsg(self):
        msg = self.genData()

        if msg is None:
            return b"Error: No data received\n"
        else:
            return json.dumps(msg).encode()

    def valid_request(self, data):
        return True if data == b'Requesting data\n' else False

    # Run function.
    def run(self):

        slogger.debug("Starting server...")
        # Set, bind, and set to listen ports.
        slogger.debug("\tSetting socket...")

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))

        sock.listen()
        slogger.info(f"Listening from port {self.port}.")
        sock.setblocking(False)

        # Register the socket to be monitored.
        self.sel.register(sock, selectors.EVENT_READ, data=None)
        slogger.debug("Monitoring set.")

        # Event loop.
        try:
            while True:
                events = self.sel.select(timeout=self.timeout)

                if not events:
                    self.sel.close()
                    raise RuntimeError(f"No connections recevied in last {self.timeout} seconds. Shutting Down.")

                for key, mask in events:
                    if key.data is None:
                        self.accept_wrapper(key.fileobj)
                    else:
                        self.service_connection(key, mask)

        except KeyboardInterrupt:
            slogger.info("Caught keyboard interrupt, exiting...")
        finally:
            self.sel.close()

    # Helper functions for accepting wrappers, servicing connections, and closing.
    def accept_wrapper(self, sock):
        """
        Accepts and registers new connections.
        """
        conn, addr = sock.accept()
        slogger.debug(f"Accepted connection from {addr}.")
        # Disable blocking.
        conn.setblocking(False)
        # Create data object to monitor for read and write availability.
        data = types.SimpleNamespace(addr=addr, inb=b"", outb=b"")
        events = selectors.EVENT_READ | selectors.EVENT_WRITE
        # Register connection with selector.
        self.sel.register(conn, events, data=data)

    def service_connection(self, key:selectors.SelectorKey, mask):
        slogger.debug(f"Servicing connection from: {key}, {mask}")
        sock = key.fileobj
        data = key.data
        # Check for reads or writes.

        if mask & selectors.EVENT_READ:
            recv_data = sock.recv(1024)
            if recv_data:
                data.outb += recv_data
            else:
                slogger.debug(f"Closing connection to {data.addr}")
                self.sel.unregister(sock)
                sock.close()

        if mask & selectors.EVENT_WRITE:
            if data.outb:
                # NOTE Event handling code below



                if self.valid_request(data.outb):
                    print("Valid request")
                    resp = self.genMsg()

                    sock.sendall(resp)
                else:
                    print("Invalid request")




                # Unregister and close socket.
                self.unregister_and_close(sock)

    def unregister_and_close(self, sock:socket.socket):
        slogger.debug("Closing connection...")
        # Unregister the connection.
        try:
            self.sel.unregister(sock)
        except Exception as e:
            slogger.error(f"Socket could not be unregistered:\n{e}")
        # Close the connection.
        try:
            sock.close()
        except OSError as e:
            slogger.error(f"Socket could not close:\n{e}")

if __name__ == "__main__":
    host, port = parse_args()

    secondary = Secondary(host, port)
    secondary.run()



