# Library imports
import socket
import sys
import matplotlib.pyplot as plt
import numpy as np
import argparse
import time
from datetime import datetime
import json
import mysql.connector

#Change to false when using real sensors
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

def parse_args():
    parser = argparse.ArgumentParser(
        prog="Primary",
        description="Primary is a peer in an Adhoc network that contacts two other peers for sensor data.",
        epilog="Example usage:\n python primary.py --sec1_ip X.X.X.X --sec1_port N --sec2_ip Y.Y.Y.Y --sec2_port M --db_ip Z.Z.Z.Z --db_port V"
    )

    parser.add_argument("--sec1_ip", default="127.0.0.1", type=str, help="Enter IPv4 Address for peer 1 (Sec1).", required=False)
    parser.add_argument("--sec1_port", type=int, help="Enter port number for peer 1 (Sec1).", required=True)
    parser.add_argument("--sec2_ip", default="127.0.0.1", type=str, help="Enter IPv4 Address for peer 2 (Sec2).", required=False)
    parser.add_argument("--sec2_port", type=int, help="Enter port number for peer 2 (Sec2).", required=True)
    parser.add_argument("--db_ip", default="127.0.0.1", type=str, help="Enter IPv4 Address for database.", required=False)
    parser.add_argument("--db_port", default=3306, type=int, help="Enter port number for database.", required=False)
    parser.add_argument("--db_user", default="root", type=str, help="Enter user for database.", required=False)
    parser.add_argument("--db_pass", default="", type=str, help="Enter password for user of database.", required=False)

    args = parser.parse_args()

    try:
        socket.inet_aton(args.sec1_ip)
    except OSError:
        print("--sec1_ip: Enter a valid IPv4 Address")
        raise

    try:
        socket.inet_aton(args.sec2_ip)
    except OSError:
        print("--sec2_ip: Enter a valid IPv4 Address")
        raise

    try:
        socket.inet_aton(args.db_ip)
    except OSError:
        print("--db_ip: Enter a valid IPv4 Address")
        raise

    if (args.sec1_port < 1024 or args.sec1_port > 65535):
        raise RuntimeError("--sec1_port: Enter a valid port in the range 1024-65535")

    if (args.sec2_port < 1024 or args.sec2_port > 65535):
        raise RuntimeError("--sec2_port: Enter a valid port in the range 1024-65535")

    if (args.db_port < 1024 or args.db_port > 65535):
        raise RuntimeError("--db_port: Enter a valid port in the range 1024-65535")


    return args.sec1_ip, args.sec1_port, args.sec2_ip, args.sec2_port, args.db_ip, args.db_port, args.db_user, args.db_pass


class Primary():
    def __init__(self, sec1_ip: int, sec1_port: int, sec2_ip: int, sec2_port: int, db_ip: int, db_port: int, db_user: str, db_pass: str):
        self.sec1_ip = sec1_ip
        self.sec1_port = sec1_port
        self.sec2_ip = sec2_ip
        self.sec2_port = sec2_port
        self.db_ip = db_ip
        self.db_port = db_port
        self.db_user = db_user
        self.db_pass = db_pass

        self.timeout = 5
        self.invalid = np.nan

        self.p1 = []
        self.p2 = []
        self.p3 = []

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
                # 'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S") 
            }
        else:
            data = {
                'temperature': random.randrange(0, 20),
                'humidity': random.randrange(0, 20),
                'wind_speed': random.randrange(0, 20),
                'soil_moisture': random.randrange(0, 20),
                # 'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

        return data

    def parse_data(self, data: dict, peer: str):
            temp = []

            # Parse data into temporary list
            for key, value in data.items():
                if key == 'temperature':
                    temp.append(value)
                if key == 'humidity':
                    temp.append(value)
                if key == 'wind_speed':
                    temp.append(value)
                if key == 'soil_moisture':
                    temp.append(value)
                if key == 'timestamp':
                    temp.append(value)

            # Assign to correct peer
            if peer == 'Sec1':
                self.p1 = temp
            if peer == 'Sec2':
                self.p2 = temp
            if peer == 'Primary':
                self.p3 = temp

    def local(self):
        localData = self.genData()
        self.parse_data(localData, 'Primary')


    def network(self):
        # Make two calls into the Network
        # Request to Host1
        # Request to Host2

        # Host1
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(self.timeout)

            try:
                s.connect((self.sec1_ip, self.sec1_port))

                print("Requesting data from Sec1")
                request = b'Requesting data\n'
                s.sendall(request)

                response = json.loads(s.recv(1024).decode())
                self.parse_data(response, 'Sec1')

            except Exception as e:
                print(f"Communication failed w/ Sec1: {e}.")
                self.p1 = []

        # Host2
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(self.timeout)

            try:
                s.connect((self.sec2_ip, self.sec2_port))

                print("Requesting data from Sec2")
                request = b'Requesting data\n'
                s.sendall(request)

                response = json.loads(s.recv(1024).decode())
                self.parse_data(response, 'Sec2')

            except Exception as e:
                print(f"Communication failed w/ Sec2: {e}.")
                self.p2 = []

    def upload(self):
        print("Attempting connection to piSenseDB")

        with mysql.connector.connect(host=self.db_ip, port=self.db_port, database='piSenseDB', user=self.db_user, password=self.db_pass) as conn:
            if conn.is_connected():
                print("Successful connection to piSenseDB")

                cursor = conn.cursor()

                for peer in range(3):
                    query = (f"INSERT INTO sensor_readings{peer+1} (PID, temperature, humidity, wind_speed, soil_moisture) " "VALUES(%s, %s, %s, %s, %s)")

                    data = []
                    if peer == 0: data = self.p1
                    elif peer == 1: data = self.p2
                    elif peer == 2: data = self.p3
                    if (len(data)):
                        data = [peer+1] + data
                    
                    if (data != []):
                        cursor.execute(query, data)
                        conn.commit()
                        print(f"Commiting data: {data} into sensor_readings{peer+1} table")


    def run(self):
        round_number = 1

        while True:
            print(f"Round {round_number}")
            self.network()
            self.local()
            self.upload()
            round_number += 1
            time.sleep(5)

if __name__ == "__main__":
    host1, port1, host2, port2, db_ip, db_port, db_user, db_pass = parse_args()

    primary = Primary(host1, port1, host2, port2, db_ip, db_port, db_user, db_pass)
    primary.run()



