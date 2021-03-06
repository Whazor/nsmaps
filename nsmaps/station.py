from datetime import datetime
from enum import Enum
import json
import os
import requests

import ns_api

from nsmaps.local_settings import USERNAME, APIKEY
from nsmaps.logger import logger


class StationType(Enum):
    stoptreinstation = 1
    megastation = 2
    knooppuntIntercitystation = 3
    sneltreinstation = 4
    intercitystation = 5
    knooppuntStoptreinstation = 6
    facultatiefStation = 7
    knooppuntSneltreinstation = 8


class Station(object):
    def __init__(self, nsstation, data_dir, travel_time_min=None):
        self.nsstation = nsstation
        self.data_dir = data_dir
        self.travel_time_min = travel_time_min

    def get_name(self):
        return self.nsstation.names['long']

    def get_code(self):
        return self.nsstation.code

    def get_country_code(self):
        return self.nsstation.country

    def get_lat(self):
        return float(self.nsstation.lat)

    def get_lon(self):
        return float(self.nsstation.lon)

    def get_travel_time_filepath(self):
        return os.path.join(self.data_dir, 'traveltimes/traveltimes_from_' + self.get_code() + '.json')

    def has_travel_time_data(self):
        return os.path.exists(self.get_travel_time_filepath())

    def get_type(self):
        return self.nsstation.stationtype

    def __str__(self):
        return self.get_name() + ' (' +  self.get_code() + ')' + ', travel time: ' + str(self.travel_time_min)


class Stations(object):
    def __init__(self, data_dir, test=False):
        self.data_dir = data_dir
        self.stations = []
        nsapi = ns_api.NSAPI(USERNAME, APIKEY)
        nsapi_stations = nsapi.get_stations()
        for i, nsapi_station in enumerate(nsapi_stations):
            if test and i > 5 and nsapi_station.code != 'UT':
                continue
            if nsapi_station.country != 'NL':
                continue
            station = Station(nsapi_station, data_dir)
            self.stations.append(station)

    def __iter__(self):
        return self.stations.__iter__()

    def __len__(self):
        return self.stations.__len__()

    # def from_json(self, filename):
    #     stations_new = []
    #     with open(filename) as file:
    #         stations = json.load(file)['stations']
    #         for station in stations:
    #             self.find_station(self, station.name)
    #     return stations_new

    def find_station(self, name):
        for station in self.stations:
            if station.get_name() == name:
                return station
        return None

    def travel_times_from_json(self, filename):
        with open(filename) as file:
            travel_times = json.load(file)['stations']
            for travel_time in travel_times:
                station_name = travel_time['name']
                station = self.find_station(station_name)
                if station:
                    station.travel_time_min = int(travel_time['travel_time_min'])

    def update_station_data(self, filename_out):
        data = {'stations': []}
        for station in self.stations:
            # if station.country == "NL" and "Utrecht" in station.names['long']:
            travel_times_available = station.has_travel_time_data()
            contour_available = os.path.exists(os.path.join(self.data_dir, 'contours/' + station.get_code() + '.geojson'))
            data['stations'].append({'names': station.nsstation.names,
                                     'id': station.get_code(),
                                     'lon': station.get_lon(),
                                     'lat': station.get_lat(),
                                     'type': station.nsstation.stationtype,
                                     'travel_times_available': travel_times_available and contour_available})
        json_data = json.dumps(data, indent=4, sort_keys=True, ensure_ascii=False)
        with open(os.path.join(self.data_dir, filename_out), 'w') as fileout:
            fileout.write(json_data)

    def get_stations_for_types(self, station_types):
        selected_stations = []
        for station in self.stations:
            for station_type in station_types:
                if station.nsstation.stationtype == station_type.name:
                    selected_stations.append(station)
        return selected_stations

    def create_traveltimes_data(self, stations_from, timestamp):
        """ timestamp format: DD-MM-YYYY hh:mm """
        for station_from in stations_from:
            filename_out = station_from.get_travel_time_filepath()
            if os.path.exists(filename_out):
                logger.warning('File ' + filename_out + ' already exists. Will not overwrite. Return.')
                continue
            json_data = self.create_trip_data_from_station(station_from, timestamp)
            with open(filename_out, 'w') as fileout:
                fileout.write(json_data)

    def get_station_code(self, station_name):
        for station in self.stations:
            if station.get_name() == station_name:
                return station.get_code()
        return None

    def create_trip_data_from_station(self, station_from, timestamp):
        """ timestamp format: DD-MM-YYYY hh:mm """
        via = ""
        data = {'stations': []}
        data['stations'].append({'name': station_from.get_name(),
                                 'id': station_from.get_code(),
                                 'travel_time_min': 0,
                                 'travel_time_planned': "0:00"})
        nsapi = ns_api.NSAPI(USERNAME, APIKEY)
        for station in self.stations:
            if station.get_code() == station_from.get_code():
                continue
            trips = []
            try:
                trips = nsapi.get_trips(timestamp, station_from.get_code(), via, station.get_code())
            except TypeError as error:
                # this is a bug in ns-api, should return empty trips in case there are no results
                logger.error('Error while trying to get trips for destination: ' + station.get_name() + ', from: ' + station_from.get_name())
                continue
            except requests.exceptions.HTTPError as error:
                # 500: Internal Server Error does always happen for some stations (example are Eijs-Wittem and Kerkrade-West)
                logger.error('HTTP Error while trying to get trips for destination: ' + station.get_name() + ', from: ' + station_from.get_name())
                continue

            if not trips:
                continue

            shortest_trip = trips[0]
            for trip in trips:
                travel_time = datetime.strptime(trip.travel_time_planned, "%H:%M").time()
                trip.travel_time_min = travel_time.hour * 60 + travel_time.minute
                if trip.travel_time_min < shortest_trip.travel_time_min:
                    shortest_trip = trip

            logger.info(shortest_trip.departure + ' - ' + shortest_trip.destination)
            data['stations'].append({'name': shortest_trip.destination,
                                     'id': self.get_station_code(shortest_trip.destination),
                                     'travel_time_min': shortest_trip.travel_time_min,
                                     'travel_time_planned': shortest_trip.travel_time_planned})
            # time.sleep(0.3)  # balance load on the NS server
        json_data = json.dumps(data, indent=4, sort_keys=True, ensure_ascii=False)
        return json_data

    def get_missing_destinations(self, filename_json):
        self.travel_times_from_json(filename_json)
        missing_stations = []
        for station in self.stations:
            if station.travel_time_min is None:
                missing_stations.append(station)
        return missing_stations

    def recreate_missing_destinations(self, departure_timestamp, dry_run=False):
        ignore_station_ids = ['HRY', 'WTM', 'KRW', 'VMW', 'RTST', 'WIJ', 'SPV', 'SPH']
        for station in self.stations:
            if not station.has_travel_time_data():
                continue
            stations_missing = self.get_missing_destinations(station.get_travel_time_filepath())
            stations_missing_filtered = []
            for station_missing in stations_missing:
                if station_missing.get_code() not in ignore_station_ids:
                    stations_missing_filtered.append(stations_missing)
                    logger.info(station.get_name() + ' has missing station: ' + station_missing.get_name())
            if stations_missing_filtered and not dry_run:
                json_data = self.create_trip_data_from_station(station, departure_timestamp)
                with open(station.get_travel_time_filepath(), 'w') as fileout:
                    fileout.write(json_data)
            else:
                logger.info('No missing destinations for ' + station.get_name() + ' with ' + str(len(ignore_station_ids)) + ' ignored.')

