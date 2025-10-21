# IOT Weather Station
- End-to-end IoT weather station using distributed Raspberry Pi 4 nodes to simulate real-time weather monitoring
- Collects real-time environmental data (temperature, humidity, soil moisture, and wind speed) from three Adafruit sensors interfaced with the Blinka python library
- Implemented robust token-ring and polling-based communication protocols with collision handling and retransmissions to improve both the reliability and scalability of the network topology
- Developed a full-stack data pipeline by integrating MySQL and Flask to store the local IoT sensor readings and provide real-time visual comparisons against external weather data via the Open-Meteo API
