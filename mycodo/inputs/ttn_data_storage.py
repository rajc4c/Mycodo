# coding=utf-8
import datetime
import logging
import requests
from flask_babel import lazy_gettext

from mycodo.databases.models import Conversion
from mycodo.databases.models import DeviceMeasurements
from mycodo.inputs.base_input import AbstractInput
from mycodo.inputs.sensorutils import calculate_altitude
from mycodo.inputs.sensorutils import calculate_dewpoint
from mycodo.inputs.sensorutils import calculate_vapor_pressure_deficit
from mycodo.utils.database import db_retrieve_table_daemon
from mycodo.utils.influx import parse_measurement
from mycodo.utils.influx import write_influxdb_value

# Measurements
measurements_dict = {
    0: {
        'measurement': 'humidity',
        'unit': 'percent'
    },
    1: {
        'measurement': 'temperature',
        'unit': 'C'
    },
    2: {
        'measurement': 'pressure',
        'unit': 'Pa'
    },
    3: {
        'measurement': 'dewpoint',
        'unit': 'C'
    },
    4: {
        'measurement': 'altitude',
        'unit': 'm'
    },
    5: {
        'measurement': 'vapor_pressure_deficit',
        'unit': 'Pa'
    }
}

# Input information
INPUT_INFORMATION = {
    'input_name_unique': 'TTN_DATA_STORAGE',
    'input_manufacturer': 'The Things Network',
    'input_name': 'TTN Integration: Data Storage',
    'measurements_name': 'Temperature/Humidity',
    'measurements_dict': measurements_dict,

    'options_enabled': [
        'custom_options',
        'measurements_select',
        'period',
        'pre_output'
    ],
    'options_disabled': ['interface'],

    'interfaces': ['Mycodo'],

    'custom_options': [
        {
            'id': 'application_id',
            'type': 'text',
            'default_value': '',
            'name': lazy_gettext('Application ID'),
            'phrase': lazy_gettext('The Things Network Application ID')
        },
        {
            'id': 'app_api_key',
            'type': 'text',
            'default_value': '',
            'name': lazy_gettext('App API Key'),
            'phrase': lazy_gettext('The Things Network Application API Key')
        },
        {
            'id': 'device_id',
            'type': 'text',
            'default_value': '',
            'name': lazy_gettext('Device ID'),
            'phrase': lazy_gettext('The Things Network Device ID')
        }
    ]
}


class InputModule(AbstractInput):
    """ A sensor support class that retrieves stored data from The Things Network """

    def __init__(self, input_dev, testing=False):
        super(InputModule, self).__init__()
        self.logger = logging.getLogger("mycodo.inputs.ttn_data_storage")



        if not testing:
            self.logger = logging.getLogger(
                "mycodo.ttn_data_storage_{id}".format(id=input_dev.unique_id.split('-')[0]))
            
            self.unique_id = input_dev.unique_id
            self.interface = input_dev.interface
            self.period = input_dev.period
            self.first_run = True
            
            self.device_measurements = db_retrieve_table_daemon(
                DeviceMeasurements).filter(
                DeviceMeasurements.device_id == input_dev.unique_id)

            if input_dev.custom_options:
                for each_option in input_dev.custom_options.split(';'):
                    option = each_option.split(',')[0]
                    value = each_option.split(',')[1]
                    if option == 'application_id':
                        self.application_id = value
                    elif option == 'app_api_key':
                        self.app_api_key = value
                    elif option == 'device_id':
                        self.device_id = value

            # Get all the data the past 7 days when first started
            self.get_new_data(604800)  # 604800 seconds = 7 days (longest Data Storage Integration stores for)

    def get_new_data(self, past_seconds):
        if self.first_run:
            self.first_run = False
            return

        endpoint = "https://{app}.data.thethingsnetwork.org/api/v2/query/{dev}?last={time}".format(
            app=self.application_id, dev=self.device_id, time="{}s".format(past_seconds))
        headers = {"Authorization": "key {k}".format(k=self.app_api_key)}
        response = requests.get(endpoint, headers=headers)
        try:
            for each_resp in response.json():
                datetime_ts = datetime.datetime.strptime(each_resp['time'][:-7], '%Y-%m-%dT%H:%M:%S.%f')
                measurements = {
                    0: {
                        'measurement': 'humidity',
                        'unit': 'percent',
                        'value': each_resp['humidity']
                    },
                    1: {
                        'measurement': 'temperature',
                        'unit': 'C',
                        'value': each_resp['temperature']
                    },
                    2: {
                        'measurement': 'pressure',
                        'unit': 'Pa',
                        'value': each_resp['pressure']
                    },
                    3: {
                        'measurement': 'dewpoint',
                        'unit': 'C',
                        'value': calculate_dewpoint(
                            each_resp['temperature'], each_resp['humidity'])
                    },
                    4: {
                        'measurement': 'altitude',
                        'unit': 'm',
                        'value': calculate_altitude(each_resp['pressure'])
                    },
                    5: {
                        'measurement': 'vapor_pressure_deficit',
                        'unit': 'Pa',
                        'value': calculate_vapor_pressure_deficit(
                            each_resp['temperature'], each_resp['humidity'])
                    }
                }

                for channel in measurements:
                    if self.is_enabled(channel):
                        measurement = self.device_measurements.filter(
                            DeviceMeasurements.channel == channel).first()
                        conversion = db_retrieve_table_daemon(
                            Conversion, unique_id=measurement.conversion_id)
                        measurement_single = parse_measurement(
                            conversion,
                            measurement,
                            measurements,
                            measurement.channel,
                            measurements[channel])
                        write_influxdb_value(
                            self.unique_id,
                            measurement_single[channel]['unit'],
                            value=measurement_single[channel]['value'],
                            measure=measurement_single[channel]['measurement'],
                            channel=channel,
                            timestamp=datetime_ts)
        except:
            self.logger.exception("Error acquiring and/or storing measurements")

    def get_measurement(self):
        """ Gets the data """
        self.get_new_data(int(self.period))
