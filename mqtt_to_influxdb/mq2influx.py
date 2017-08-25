
from __future__ import unicode_literals
import re
import time
import json
import redis
from configparser import ConfigParser
import paho.mqtt.client as mqtt
from tsdb.worker import Worker
from frappe_api.device_db import DeviceDB


match_topic = re.compile(r'^([^/]+)/(.+)$')
match_data_path = re.compile(r'^([^/]+)/([^/]+)/(.+)$')

config = ConfigParser()
config.read('../config.ini')

redis_srv = config.get('redis', 'url', fallback='redis://127.0.0.1:6379')
redis_db = redis.Redis.from_url(redis_srv+"/8")

workers = {}
device_map = {}


def create_worker(db):
	worker = workers.get(db)
	if not worker:
		worker = Worker(db, config)
		worker.start()
		workers[db] = worker
	return worker

ddb = DeviceDB(redis_srv, device_map, create_worker, config)
ddb.start()


def get_worker(iot_device):
	worker = device_map.get(iot_device)
	if not worker:
		db = redis_db.get(iot_device)
		if not db:
			db = "example"
		else:
			db = db.decode('utf-8')
		worker = create_worker(db)
		device_map[iot_device] = worker
	return worker


def get_input_type(val):
	if isinstance(val, int):
		return "int", val
	elif isinstance(val, float):
		return "float", val
	else:
		return "string", str(val)


inputs_map = {}


def get_input_vt(iot_device, device, input, val):
	t, val = get_input_type(val)
	if t == "string":
		return "string", val

	key = iot_device + "/" + device + "/" + input
	vt = inputs_map.get(key)

	if vt:
		return vt, int(val)

	return None, float(val)


def make_input_map(iot_device, cfg):
	for dev in cfg:
		inputs = cfg[dev].get("inputs")
		if not inputs:
			return
		for it in inputs:
			vt = it.get("vt")
			if vt:
				key = iot_device + "/" + dev + "/" + it.get("name")
				inputs_map[key] = vt


# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, rc):
	print("Connected with result code "+str(rc))

	# Subscribing in on_connect() means that if we lose the connection and
	# reconnect then subscriptions will be renewed.
	#client.subscribe("$SYS/#")
	client.subscribe("+/data")
	client.subscribe("+/devices")
	client.subscribe("+/status")


def on_disconnect(client, userdata, rc):
	print("Disconnect with result code "+str(rc))


# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
	g = match_topic.match(msg.topic)
	if not g:
		return
	g = g.groups()
	if len(g) < 2:
		return

	devid = g[0]
	topic = g[1]

	if topic == 'data':
		payload = json.loads(msg.payload.decode('utf-8'))
		g = match_data_path.match(payload[0])
		if g and msg.retain == 0:
			g = g.groups()
			worker = get_worker(devid)
			prop = g[2]
			value=payload[2]
			if prop == "value":
				t, val = get_input_vt(devid, g[0], g[1], value)
				if t:
					prop = t + "_" + prop
				value = val
			else:
				value = str(value)
			worker.append_data(name=g[1], property=prop, device=g[0], iot=devid, timestamp=payload[1], value=value, quality=payload[3])
		return

	if topic == 'devices':
		print(devid, json.loads(msg.payload.decode('utf-8')))
		worker = get_worker(devid)
		worker.append_data(name="iot_device", property="cfg", device=devid, iot=devid, timestamp=time.time(),
							value=msg.payload.decode('utf-8'), quality=0)
		make_input_map(devid, json.loads(msg.payload.decode('utf-8')))
		return

	if topic == 'status':
		worker = get_worker(devid)
		#redis_sts.set(devid, msg.payload.decode('utf-8'))
		status = msg.payload.decode('utf-8')
		if status == "ONLINE" or status == "OFFLINE":
			val = status == "ONLINE"
			worker.append_data(name="device_status", property="online", device=devid, iot=devid, timestamp=time.time(),
								value=val, quality=0)
		return


client = mqtt.Client(client_id="SYS_MQTT_TO_INFLUXDB")
client.username_pw_set("root", "bXF0dF9pb3RfYWRtaW4K")
client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.on_message = on_message

mqtt_host = config.get('mqtt', 'host', fallback='127.0.0.1')
mqtt_port = config.getint('mqtt', 'port', fallback=1883)
mqtt_keepalive = config.getint('mqtt', 'port', fallback=60)
client.connect(mqtt_host, mqtt_port, mqtt_keepalive)


# Blocking call that processes network traffic, dispatches callbacks and
# handles reconnecting.
# Other loop*() functions are available that give a threaded interface and a
# manual interface.
client.loop_forever()

