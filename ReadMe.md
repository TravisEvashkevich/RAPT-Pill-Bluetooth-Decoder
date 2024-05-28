# Info
This repo should help people get the bluetooth low-energy data from their RAPT Pill devices

I've tested this with one Pill on a raspberry pi 4b+ as well as a windows 10 laptop, both worked fine for collecting the data

I also wanted to have my own graphs that I could look at while my fermentations are going so I added Grafana into the mix

## Setup Steps (raspberry pi):
- pip install requirements.txt so you get the same version of bleak (this might not work in future versions)
- get Grafana self hosted setup in your system - I followed this to get grafana running on my pi - https://pimylifeup.com/raspberry-pi-grafana/
- install influxdb - https://pimylifeup.com/raspberry-pi-influxdb/
- Set your influxdb up on grafana
- pip install influxdb

### Requirements:
- bleak
- Grafana - self hosted
- influxdb

Thanks to:
https://github.com/sairon/rapt-ble/blob/main/src/rapt_ble/parser.py#L14 - had a MUCH easier parsing method than I was using previously
