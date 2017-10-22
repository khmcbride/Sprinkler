#!/usr/bin/python3
import RPi.GPIO as GPIO
import threading
import os
import time as t
from datetime import date
from datetime import time
from datetime import datetime
from datetime import timedelta
import json
from enum import Enum
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


class Watcher:
    
	DIRECTORY_TO_WATCH = ""

	def __init__(self):
		if os.name == 'nt':
			Watcher.DIRECTORY_TO_WATCH = "." 
		else:
			Watcher.DIRECTORY_TO_WATCH = "/home/pi/Sprinkler"

		self.observer = Observer()

	def run(self):
		event_handler = Handler()
		self.observer.schedule(event_handler, Watcher.DIRECTORY_TO_WATCH, recursive=False)
		self.observer.start()
		#try:
		#	while True:
		#		time.sleep(5)
		#except:
		#	self.observer.stop()
		#	print("Error")

		#self.observer.join()


class Handler(FileSystemEventHandler):

	STATUS = None
	SCHED = None

	STATUS_FILE_NAME = "SprinklerStatus.json"
	SCHED_FILE_NAME = "SprinklerSched.json"

	@staticmethod
	def ProcessStatus(path):
		read = False
		while not read:
			try:
				with open(path, 'r') as file:
					Handler.STATUS = json.loads(file.read())
				read = True;
			except:
				print('Status read error\r\n')

	@staticmethod
	def ProcessSched(path):
		with open(path, 'r') as file:
			Handler.SCHED = json.loads(file.read())


	@staticmethod
	def on_modified(event):
		if event.is_directory:
			return None
		t.sleep(0.5)
		(path, filename) = os.path.split(event.src_path)

		if filename == Handler.STATUS_FILE_NAME:
			Handler.ProcessStatus(event.src_path)
		if filename == Handler.SCHED_FILE_NAME:
			Handler.ProcessSched(event.src_path)
		else:
			return None

	@staticmethod
	def WriteStatusFileChanges():
		
		with open(os.path.join(Watcher.DIRECTORY_TO_WATCH, Handler.STATUS_FILE_NAME), 'w+') as file:
			json.dump(Handler.STATUS, file, indent = 2)

	@staticmethod
	def GetDateAsString(dt_tm):
		return dt_tm.__str__()

	def GetDateFromString(dt_tm_str):
		return datetime.strptime(dt_tm_str, "%Y-%m-%d %X.%f")

class ControllerThread(threading.Thread):

	relay_enable_pin = 26
	zone_to_gpio_mapping = dict([(1,5),
		(2,6),
		(3,13),
		(4,19),
		(5,12),
		(6,16),
		(7,20),
		(8,21)])
	last_start_time = None
	enabled_zones = []

	def __init__(self):
		super(ControllerThread,self).__init__()
		GPIO.setmode(GPIO.BCM)
		self.InitializeAllGpioPins()
		self.ZONE_ON = GPIO.LOW
		self.ZONE_OFF = GPIO.HIGH
		self.AllZonesOff()
		self.activeZone = 0
		self.mode = ControllerMode.IDLE
		self.next_stop_time = datetime.now()
		Handler.ProcessStatus(path=os.path.join(Watcher.DIRECTORY_TO_WATCH, Handler.STATUS_FILE_NAME))
		Handler.ProcessSched(path=os.path.join(Watcher.DIRECTORY_TO_WATCH, Handler.SCHED_FILE_NAME))
		
		
	

	def UpdateStatus(self):
		#overrides
		if Handler.STATUS != None and Handler.STATUS['override']['enabled']:
			print(Handler.STATUS['override']['zoneId'])
			
			#initial state of the zone override request
			if Handler.STATUS['override']['duration'] != 0:


				self.activeZone = Handler.STATUS['override']['zoneId']
				self.next_stop_time = datetime.now() + timedelta(seconds = Handler.STATUS['override']['duration'] * 60)
				#self.next_stop_time = datetime.now() + timedelta(minutes = Handler.STATUS['override']['duration'])
				Handler.STATUS['override']['stopTime'] = Handler.GetDateAsString(self.next_stop_time)#'{h}:{m}'.format(h = self.next_stop_time.hour, m = self.next_stop_time.minute)
				Handler.STATUS['override']['duration'] = 0
				Handler.STATUS['zones'][self.GetStatusIdxByZoneId(self.activeZone)]['state'] = 'on'
				Handler.STATUS['zones'][self.GetStatusIdxByZoneId(self.activeZone)]['lastRunTime'] = Handler.GetDateAsString(datetime.now())
				self.mode = ControllerMode.OVERRIDE
			
				Handler.WriteStatusFileChanges()

		#if Handler.STATUS != None and Handler.STATUS['override']['enabled']:

	def ControlZones(self):
		has_file_changes = False

		if self.mode == ControllerMode.OVERRIDE:
			#check for override expiration
			if datetime.now() > self.next_stop_time:
				self.AllZonesOff()
				self.mode = ControllerMode.IDLE
				
				Handler.STATUS['override']['enabled'] = False
				Handler.STATUS['override']['duration'] = 0
				Handler.STATUS['zones'][self.GetStatusIdxByZoneId(self.activeZone)]['state'] = 'off'
				self.activeZone = 0

				has_file_changes = True
			#override still active
			else:
				self.ZoneOn(self.activeZone)

		elif self.mode == ControllerMode.ACTIVE:
			#check for scheduled run expiration
			if datetime.now() > self.next_stop_time:
				self.AllZonesOff()
				self.StartNextZoneIfReady()
			
			else:
				self.ZoneOn(self.activeZone)
		# not doing anything presently.  check to see if it is time to start the next scheduled event
		elif self.mode == ControllerMode.IDLE and Handler.SCHED != None and Handler.SCHED["sched"]["enabled"] == True:
			present = datetime.now()
			now_dayOfWeek = present.isoweekday()
			now_timeOfDay = time(hour = present.hour, minute = present.minute)
			hr, mn = Handler.SCHED["sched"]["startTime"].split(":")
			next_start_time = datetime(year = present.year, month = present.month, day = present.day, hour = int(hr), minute = int(mn))
			#next_start_time = time(hour = int(hr), minute = int(mn))
			too_late_time = next_start_time + timedelta(seconds = 30)

			#make sure that it is the correct day of week, and that it is later than the next_start_date (but no more than 30 seconds over)
			if now_dayOfWeek in Handler.SCHED["sched"]["days"] and present > next_start_time and present < too_late_time:
				self.enabled_zones.clear()
				
				for z in Handler.SCHED["sched"]["zones"]:
					if z["enabled"] and int(z["durationMinutes"]) > 0:
						self.enabled_zones.append(z)

				if len(self.enabled_zones) > 0:
					self.enabled_zones.sort(key = lambda z : z["zoneId"])
					self.StartNextZoneIfReady(init = True)
				
			else:
				self.AllZonesOff()

		if has_file_changes:
			Handler.WriteStatusFileChanges()

	def StartNextZoneIfReady(self, init = False):
		#while there are still zones remaining to activate, take the first from the list, start it and remove it from the list
		if len(self.enabled_zones) > 0:
			if init:
				self.mode = ControllerMode.ACTIVE
				#firstZone = filter(lambda z : z['zoneId'] == self.activeZone ,self.enabled_zones)
			
			#get current active zone (if any) and update status before changing zones
			zoneStatusIdx = self.GetStatusIdxByZoneId(self.activeZone)
			if zoneStatusIdx >= 0:
				Handler.STATUS['zones'][zoneStatusIdx]['state'] = 'off'
				
			#set variables to point to the newly activated zone
			self.activeZone = self.enabled_zones[0]['zoneId']
			self.next_stop_time = datetime.now() + timedelta(minutes = int(self.enabled_zones[0]['durationMinutes']))
			self.enabled_zones.remove(self.enabled_zones[0])

			#get new active zone and update its status 
			zoneStatusIdx = self.GetStatusIdxByZoneId(self.activeZone)
			
			Handler.STATUS['zones'][zoneStatusIdx]['state'] = 'on'
			Handler.STATUS['zones'][zoneStatusIdx]['lastRunTime'] = Handler.GetDateAsString(datetime.now())
			
			Handler.WriteStatusFileChanges()
		
		#if no more zones remain to be watered, shut things down and make sure the next event time will fire	
		else:
			self.AllZonesOff()
			self.mode = ControllerMode.IDLE
			self.enabled_zones.clear()
			#get current active zone (if any) and update status before ending the last scheduled zone
			zoneStatusIdx = self.GetStatusIdxByZoneId(self.activeZone)
			if zoneStatusIdx >= 0:
				Handler.STATUS['zones'][zoneStatusIdx]['state'] = 'off'
			
			self.activeZone = 0
			
			Handler.WriteStatusFileChanges()

	def GetStatusIdxByZoneId(self, id = None):
		if id == None or id == 0:
			return -1

		for i,z in enumerate(Handler.STATUS['zones']):
			if z['zoneId'] == id:
				return i

	def InitializeAllGpioPins(self):
		GPIO.setup(self.relay_enable_pin, GPIO.OUT)
		for z, pin in self.zone_to_gpio_mapping.items():
			GPIO.setup(pin, GPIO.OUT)

	def PowerRelayOff(self):
		GPIO.output(self.relay_enable_pin, GPIO.LOW)

	def PowerRelayOn(self):
		GPIO.output(self.relay_enable_pin, GPIO.HIGH)

	def AllZonesOff(self):
		self.PowerRelayOff()
		#turn all zones off
		for z, pin in self.zone_to_gpio_mapping.items():
			GPIO.output(pin, self.ZONE_OFF)

	def ZoneOff(self, activeZone):
		#turn zone off
		if activeZone < 1:
			return
		GPIO.output(self.zone_to_gpio_mapping.get(activeZone), self.ZONE_OFF)

	def ZoneOn(self, activeZone):
		#turn zone on
		if activeZone < 1:
			return
		self.PowerRelayOn()
		GPIO.output(self.zone_to_gpio_mapping.get(activeZone), self.ZONE_ON)

	

	def run(self):
		while True:
			self.UpdateStatus()
			self.ControlZones()
			t.sleep(2)
			

class ControllerMode(Enum):	
	IDLE = 0
	ACTIVE = 1
	OVERRIDE = 2
			
		
	

if __name__ == '__main__':
	w = Watcher()
	
	controller = ControllerThread()
	controller.start()

	w.run()

	controller.join()
	w.observer.join()


