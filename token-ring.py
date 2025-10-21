import socket
import sys
import types
import random
import selectors
import matplotlib.pyplot as plt
import numpy as np
import logging
import time
import json

#------ for sensor readings ------------
import board
import busio
import time
from datetime import datetime
import simpleio
import adafruit_ads1x15.ads1015 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
from adafruit_seesaw.seesaw import Seesaw
import adafruit_sht31d
import argparse
# ---------------------------------------

# import mysql DB driver
import mysql.connector

#setup logging format style 
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',)

class TokenRing:
    def __init__(self, MY_HOST, MY_PORT, NEXT_HOST, NEXT_PORT, PREV_HOST, PREV_PORT, ID_NUMBER, timeout = 30):

        # initialize the selector to detect events
        self.sel = selectors.DefaultSelector()

        # initialize hosts and ports and IDs
        self.my_host = MY_HOST
        self.my_port = MY_PORT
        self.next_host = NEXT_HOST
        self.next_port = NEXT_PORT
        self.prev_host = PREV_HOST
        self.prev_port = PREV_PORT
        self.id_number = ID_NUMBER

        self.BACKUP_INCREMENTER = False
        


        #failure detection vars
        # creates staggered 5 sec intervals 
        self.timeout = timeout + (ID_NUMBER-1)*5
        self.WAITING = False
        self.START = 0


        #LOGIC to swap round incrementer ROLE
        # this works since it happens at startup before any reconfigs
        if self.id_number == 1:
            self.p3_port = self.prev_port
        
        elif self.id_number == 2:
            self.p3_port = self.next_port
        
        else:
            self.p3_port = self.my_port
        

        # hard mapping next host and port 
        self.next_id = (self.id_number % 3) + 1 # keep in bounds of 3
        self.prev_id = ( (self.id_number + 1) % 3) + 1
        # this isn't totally neccesary but covers weird edge cases with reconfigs
        #mapping port to id number for failure logic where we need to remove keys from token
        self.local_area = {self.next_port: f"P{self.next_id}",
                           self.prev_port: f"P{self.prev_id}"}
        


        #set up pi specific logger will now mention pi in log not (srv)
        self.logger = logging.getLogger(f"(P{ID_NUMBER})")
        self.logger.setLevel(level=logging.INFO)

        #log message 
        self.logger.info(f"Token Ring initialized.")

    
    #main function 
    def run(self):

        self.logger.debug("Starting Token Ring Server...")

        #startup the listening socket
        self.listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listen_sock.bind((self.my_host, self.my_port))
        self.listen_sock.listen()
        self.logger.info(f"Listening from port {self.my_port}.")

        #Make NON-BLOCKING
        self.listen_sock.setblocking(False)

        """
        As mentioned before, we use selectors to monitor for new events 
        by monitoring the socket for changes.
        """
        self.sel.register(self.listen_sock, selectors.EVENT_READ, data=None)
        self.logger.debug("Monitoring set.")

        #if pi #1 start the token ring only happens if id nubnmer 1 
        #this need to happen to start the ring and trigger other pis events
        if(self.id_number == 1):
            # waits 12 seconds before attempting startup
            # this waits for other PIS to enter the ring
            start_time = 12
            self.logger.info(f"Starting Round in ({start_time}...)")
            for i in range(start_time-1,0,-1):
                time.sleep(1)
                self.logger.info(f"Starting Round in ({i}...)")
            self.logger.info("GO")
            self.create_token()

        #this means we are either P2 or P3 
        else:
            self.WAITING = True
            #started waiting at this time, need to track in case of PI FAILURES
            self.START = time.time()
            self.logger.info("waiting to be passed token...")
        

        # last_token_activity = time.time()
        
        #Event Loop. 

        try:
            
        
            while True:
                # self.logger.info(f"HELLO time:{self.timeout}")
                events = self.sel.select(timeout=self.timeout)
                # list is empty timeout raised need to do something here
                if not events:
                    self.logger.info("CREATING NEW TOKEN AFTER CRASH")
                    self.create_token()
                    continue
         
                for key, mask in events:
                    if key.data is None:
                        #accepts NEW connections
                        self.accept_wrapper(key.fileobj)
                    else:
                        #will service ALREADY existing connections
                        self.service_connection(key,mask)


        except KeyboardInterrupt:
            self.logger.info("Caught keyboard interrupt, exiting...")
        
        finally:
            self.sel.close()
    
    def accept_wrapper(self, sock):

        #returns a tuple
        conn, addr = sock.accept()

        self.logger.debug(f"Accepted connection from {addr}.")
        
        # non-blocking 
        conn.setblocking(False)

        #creates a dasta object
        data = types.SimpleNamespace(addr=addr, inb=b"", outb=b"")

        #since this is the listening socket
        events = selectors.EVENT_READ
        self.sel.register(conn,events,data=data)

    def service_connection(self, key, mask):

        self.logger.debug(f"Servicing connection from: {key}, {mask}")
        sock = key.fileobj
        data = key.data

        #only will read from the listening socket since we're not sending out the same sokcet we 
        #read from
        if mask & selectors.EVENT_READ:
            #read data from socket, increasing buffer size for JSON 
            recv_data = sock.recv(4096)

            # if theres no data received
            if not recv_data:
                self.sel.unregister(sock)
                sock.close()
                # skip rest of func
                return 
            #append to input buffer (inb)
            # data.inb += recv_data
            #will decode byte string and then load back the reg dict not string dict
            token = json.loads(recv_data.decode())
            self.process_token(token)
            # self.WAITING = False
            #clear buffer don't need data anymore
            # data.inb=b""
            self.sel.unregister(sock)
            sock.close()


    # ------------ WRITING TO DB ----------------------

    def insert_to_db(self,token):

        conn = None 

        try:
            # establishing connection
            conn = mysql.connector.connect(
                host = '192.168.0.184', #my ip addr
                port = 3306, 
                database = 'piSenseDB',
                user = 'test_user',
                password = 'password'
            )
            if conn.is_connected():
                self.logger.info("talking to MySQL database")
            
            table_map = {
                "P1": "sensor_readings1",
                "P2": "sensor_readings2",
                "P3": "sensor_readings3",
            }


            # inserting data
            with conn.cursor() as cursor:
                # iterates through PI keys
                for pi_id, table in table_map.items():
                    # grab value from key in token
                    data = token.get(pi_id)
                    if not data:
                        continue #this will skip DEAD pis, needed for db tables

                    # will skip ROUND_NUMBER and INCREMENTER keys 
                    # if pi_id not in table_map:
                    #     continue

                    # table = table_map[pi_id]
                    # construct the SQL query
                    sql = f"""
                        INSERT INTO {table} (PID, temperature, humidity, wind_speed, soil_moisture, timestamp)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """
                    cursor.execute(sql, (
                        pi_id[1], 
                        data['SHT30 Temp'],
                        data['SHT30 Hum'],
                        data['WIND Speed'],
                        data['SEESAW Hum'],
                        data['timestamp']
                    ))
                conn.commit()
                self.logger.info("inserted data to DB")

            
        except mysql.connector.Error as e:
            self.logger.error(f"data base ERROR: {e}")
        
        finally:
            if conn is not None and conn.is_connected():
                conn.close()

    # ------------------------------------------------


    def read_sensor_data(self):

        # timestamping for each PI, will automatically be appended to every token
        curr_time = datetime.now()
        timestamp = curr_time.strftime("%Y-%m-%d %H:%M:%S")

        try:

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
            soil_temp = soil_sensor.get_temp()

            self.logger.debug("Successfully read from sensors")

            sensor_data_dict = {
                'SHT30 Temp': SHT30_temp,
                'SHT30 Hum': SHT30_hum,
                'SEESAW Temp': soil_temp,
                'SEESAW Hum': soil_moist,
                'WIND Speed': wind_speed,
                'timestamp': timestamp
            }

            return sensor_data_dict
        
        except Exception as e:
            self.logger.error(f"ERROR reading sensor data{e}")
            fake_data = {
                'SHT30 Temp': 0,
                'SHT30 Hum': 0,
                'SEESAW Temp': 0,
                'SEESAW Hum': 0,
                'WIND Speed': 0,
                'timestamp': timestamp
            }
            return fake_data

    
    def create_token(self):


        #need to reset last round now since we're starting back at round 1 
        self.last_round = 0
        sensor_data = self.read_sensor_data()

        #token that will hold all relevant information as we pass arond ring
        token = {f"P{self.id_number}": sensor_data, 
                 "ROUND_NUMBER": 1,
                 "INCREMENTER": False
                 }

        self.logger.info("creating new token to pass ROUND 1")

        # send token helper function
        self.send_token_processor(token)



    
    #have read data from service connections now we can process
    def process_token(self, token):

        #handles main control logic depending on the PI
        self.logger.info("I HAVE THE TOKEN")

        # debug line
        # self.logger.info(f"token contents: {token.items()}")
        time.sleep(1)

        # grab round num
        round_num = token.get("ROUND_NUMBER",1)


        # handle duplicate rounds
        # start a last_round tracker
        # if not hasattr(self, 'last_round'):
        #     self.last_round = 0

        # # round_num is less than last round means that we restarted ring
        # if round_num < self.last_round:
        #     self.last_round = 0

        # self.logger.info(f"round_num: {round_num}   last_round: {self.last_round}")
        
        # if round_num<=self.last_round:
        #     self.logger.info("already processed round")
        #     return
        
        self.last_round = round_num

        # self.logger.info(f"processing token in ROUND{round_num}")

        #****CRITICAL append/overwrite pi data to token dict
        token[f"P{self.id_number}"]= self.read_sensor_data()

        self.logger.info(f"P{self.id_number} added to token!")

        # only if we are pi3 do we need to plot
        if self.id_number == 3 or self.BACKUP_INCREMENTER:
            self.logger.info(f"plotting data for ROUND{round_num}")

            # self.plotter(token)

            # inserting to DB now since END of round 
            # no need to change logic will happen when ID3 or backup incrementer
            self.insert_to_db(token)

            token["ROUND_NUMBER"]+=1
            #issues with plots if no sleep
            time.sleep(1)

        # after we have added data we read to token dict we now sned via next_host and next_port
        self.send_token_processor(token)




    #custom sender function so we can control who sends and when


    def send_token_processor(self, token):
        # if we failed to send token
        if(not self.send_token(token, self.next_host, self.next_port)):

            #** check to see if the port that refused is P3 so we can then swap roles accordingly
            failed_port = self.next_port
            time.sleep(3)
            self.logger.warning(f"PI at {self.next_host}:{self.next_port} is down! :(")
            self.logger.info("RECONFIGURING topology...")

            # ** NEED to remove that key from token now ------
            failed_pi = self.local_area.get(self.next_port)
            self.logger.warning(f"Removed {failed_pi} at {self.next_host}:{self.next_port} from token")
            token.pop(failed_pi,None)
            # --------------------------

            time.sleep(2)
            self.logger.info(f"linking P{self.id_number} to previous PI...")

            # if it failed to send AGAIN will enter this conditional
            if(not self.send_token(token, self.prev_host, self.prev_port)):
                time.sleep(1)
                self.logger.error("BOTH MY NEIGHBORS ARE DOWN MAYDAY!!")
                time.sleep(1)

                # ONLY ONE WITH TOKEN
                # ****self.logger.error("closing all operations...")
                
                # while loop that just reads the sensor data, connects to db, and plots 
                # until a disconnect, not handling reconnections** 
                self.logger.info("CONTINUING OPERATIONS ... (SOLO)")
                self.logger.info("closing socket/selector...")
                time.sleep(1)
                #**critical
                self.sel.unregister(self.listen_sock)
                self.listen_sock.close()
                self.sel.close()
                #enter solo loop
                return self.solo_loop(token)
                
                    
                # time.sleep(1.25)
                # sys.exit(1)

            # successfully sent to new receiver can return
            else:
                self.logger.info("UPDATED TOPOLOGY")
                self.logger.info(f"Now I point to {self.prev_host}:{self.prev_port}")
                self.next_host = self.prev_host
                self.next_port = self.prev_port

                if(failed_port == self.p3_port):
                    # now its guaranteed P2 is incrementer after fail since P1 will never send to P3 and fail
                    # if it does its the last node and will crash sys or go solo mode
                    self.logger.info("TRANSFERRING P3's DUTIES")
                    self.BACKUP_INCREMENTER = True
                    # self.logger.info(f"P{self.id_number} INCREMENTING ROUND NUMBER")
                    # token["ROUND_NUMBER"]+=1
                if hasattr(self, 'last_round'):
                    self.last_round = 0 

    def solo_loop(self,token):
        # restarting the round num
        self.logger.info("entering solo loop")
        # token["ROUND_NUMBER"] +=1 
        while True:
            try:
                self.logger.info("reading sensor data...")
                data = self.read_sensor_data()
                token[f"P{self.id_number}"] = data 
                time.sleep(1)

                self.logger.info("plotting data")
                self.plotter(token)
                time.sleep(1)

                token["ROUND_NUMBER"] +=1 

                self.logger.info("inserting to DB")
                # chekc if we removed bad keys here
                self.logger.info(f"token: {token.keys()}")
                self.insert_to_db(token)
                time.sleep(1)

                
            except KeyboardInterrupt:
                self.logger.info("closing all operations... no one in ring")
                break
            except Exception as e:
                self.logger.info(f"failed with {e}, closing all operations... no one in ring")
                break
            finally:
                time.sleep(1.25)
        
        sys.exit(0)



    def send_token(self,token,host,port):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                #set a timeout of 30 seconds for the socket object
                s.settimeout(10)
                #use the cli args for ip and port info *** might need connect_ex()
                s.connect((host,port))
                #takes the token dict makes it string and converts to utf-8 to send over TCP
                token_network_ready = json.dumps(token).encode()

                #now we can actually send token over network
                s.sendall(token_network_ready)

                self.logger.info(f"sent token to {host}:{port}")
                return True
            
        except socket.timeout:
            self.logger.error("Socket timed out!")
            return False
        #will get this if you start PI1 first nothing to connect
        except ConnectionRefusedError:
            self.logger.error(f"connection refused from pi at {host}:{port}")
            return False
    


    def plotter(self, token):
        round_num = token["ROUND_NUMBER"]
        data = {}
        for key,value in token.items():
            if key.startswith("P"):
                data[key] = value

        fig, ax = plt.subplots(2, 2)
        keys = ["SHT30 Temp", "SHT30 Hum", "SEESAW Hum", "WIND Speed"]
        titles = ["Temp (C)", "Humidity (%)", "Soil Moisture", "Wind Speed"]
        axes = [ax[0][0], ax[0][1], ax[1][0], ax[1][1]]

        #should be P1, P2, P3, AVG
        xlabels = list(data.keys()) + ["AVG"]
        # self.logger.info(f"X-AXIS {xlabels}")
        x_position = np.arange(len(xlabels))

        colors = ['blue', 'red', 'green']
        avg_color = 'black'

        for i, key in enumerate(keys):
            sensor_data = []
            for pi in data:
                #will extract sensor data. data looks like => {'P1': {'SHT30': 24.3C}, ...}
                sensor_value = data[pi][key]
                #this list is only for specific pis sensor vlas
                sensor_data.append(sensor_value)
            
            avg = np.mean(sensor_data)
            sensor_data.append(avg)

            #loop through sensor_data list 
            for j, val in enumerate(sensor_data):

                # check to see if sensor val or avg
                if j < len(data):
                    color = colors[j]
                
                else:
                    color = avg_color
                #plot sensor value with assigned color
                axes[i].scatter(x_position[j], val, color=color)

            axes[i].set_title(titles[i])
            axes[i].set_xticks(x_position, xlabels)

        #make everything fit 
        plt.tight_layout()

        #save to file will be in cwd unless specifed otherwise
        filename = f"token-plot-{round_num}.png"
        plt.savefig(filename)
        plt.close()
        self.logger.info(f"saved  plot as {filename}")


if __name__ == '__main__':
    #added command line args with argparser lib
    parser = argparse.ArgumentParser(description="Token Ring")
    parser.add_argument('--my-host', default='127.0.0.1', help ='host address for pi')
    parser.add_argument('--my-port', type=int, required=True, help="port for pi")
    parser.add_argument('--next-host', default='127.0.0.1', help ='host address for next pi')
    parser.add_argument('--next-port', type=int, required=True, help="port for next pi")
    parser.add_argument('--prev-host', default = '127.0.0.1',help='host address for the previous pi')
    parser.add_argument('--prev-port', type=int, required=True ,help='port for the previous pi')
    parser.add_argument('--id', type=int, required=True, help ='pi ID')
    parser.add_argument('--timeout', type=int, default=30, help='timeout to detect offline pi')
    args = parser.parse_args()

    token_ring = TokenRing(MY_HOST=args.my_host, MY_PORT=args.my_port, NEXT_HOST=args.next_host, NEXT_PORT =args.next_port,
                           PREV_HOST=args.prev_host, PREV_PORT=args.prev_port, ID_NUMBER=args.id,timeout=args.timeout)

    token_ring.run()



