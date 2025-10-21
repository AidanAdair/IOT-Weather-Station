from flask import Flask, render_template, send_file
import mysql.connector
from datetime import datetime, timedelta
import matplotlib
# prevents Mac OS GUI pop-up error
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import io
import requests

app = Flask(__name__)

def fetch_sensor_data():
    db = mysql.connector.connect(user='root', password='', host='127.0.0.1', database='piSenseDB')
    cursor = db.cursor()
    since = datetime.utcnow() - timedelta(days=1)
    all_rows = []
    for i in (1, 2, 3):
        cursor.execute(f"""
            SELECT PID, temperature, humidity, wind_speed, soil_moisture, timestamp
            FROM sensor_readings{i} WHERE timestamp >= %s
        """, (since,))
        all_rows += cursor.fetchall()
    cursor.close()
    db.close()
    return all_rows

def fetch_forecast():
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        'latitude': 36.97,
        'longitude': -122.03,
        'hourly': 'temperature_2m,relativehumidity_2m,wind_speed_10m'
    }
    r = requests.get(url, params=params)
    data = r.json()['hourly']
    return {
        'temperature': data['temperature_2m'][0],
        'humidity': data['relativehumidity_2m'][0],
        'wind_speed': data['wind_speed_10m'][0]
    }

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/plot/<measure>')
def plot_measure(measure):
    rows = fetch_sensor_data()
    df = pd.DataFrame(rows, columns=['PID', 'temperature', 'humidity', 'wind_speed', 'soil_moisture', 'time'])
    df['time'] = pd.to_datetime(df['time'])
    # ***alter this line for sampling duration
    pivot = df.pivot_table(index='time', columns='PID', values=measure).resample('15s').mean()
    avg = pivot.mean(axis=1)
    forecast = fetch_forecast().get(measure, None)

    fig, ax = plt.subplots()
    for col in pivot.columns:
        ax.plot(pivot.index, pivot[col], alpha=0.3, label=f'Pi {col}')
    ax.plot(avg.index, avg, linewidth=2, label='Average')
    if forecast is not None:
        ax.axhline(forecast, linestyle='--', color='red', label='Forecast')
    # ax.set_title(measure.replace('_', ' ').title())
    ax.legend()
    ax.set_xlabel("Time")
    ax.set_ylabel(measure.title())
    fig.autofmt_xdate(rotation=90)

    buf = io.BytesIO()
    fig.savefig(buf, format='png')
    plt.close(fig)
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=6500)

