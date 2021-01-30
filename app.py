# Copyright 2018 Socialmetrix
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import docker
from pushover import init, Client
import os
import sys
import time
import signal
import sqlite3
import traceback

event_filters = ["create","update","destroy","die","kill","pause","unpause","start","stop"]
ignore_names = []
ignore_label = "docker-events.ignore"

BUILD_VERSION=os.getenv('BUILD_VERSION')
APP_NAME = 'Docker Events Pushover (v{})'.format(BUILD_VERSION)

def get_config(env_key, optional=False):
    value = os.getenv(env_key)
    if not value and not optional:
        print('Environment variable {} is missing. Can\'t continue'.format(env_key))
        sys.exit(1)
    return value

def create_connection(db_file):
    conn = None
    try:
        conn = sqlite3.connect(db_file)
        print("Limits db created.")
        return conn
    except sqlite3.Error as error:
        print("create_connection error: {}".format(error))

    return conn

def create_table():
    try:
        cursor.execute(""" -- create limits table
                                    CREATE TABLE IF NOT EXISTS limits (
                                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                                        name text NOT NULL,
                                        count integer,
                                        last_update_ts DATETIME DEFAULT CURRENT_TIMESTAMP
                                    );""")
        cursor.execute(""" -- create trigger  
                                    CREATE TRIGGER IF NOT EXISTS [UpdateLastTime]  
                                    AFTER   
                                    UPDATE  
                                    ON limits
                                    FOR EACH ROW   
                                    WHEN NEW.last_update_ts <= OLD.last_update_ts  
                                    BEGIN  
                                        update limits set last_update_ts=CURRENT_TIMESTAMP where id=OLD.id;  
                                    END""")
        print("Limits table & trigger created.")
    except sqlite3.Error as er:
        print("create_table:")
        print('SQLite error: %s' % (' '.join(er.args)))
        print("Exception class is: ", er.__class__)
        print('SQLite traceback: ')
        exc_type, exc_value, exc_tb = sys.exc_info()
        print(traceback.format_exception(exc_type, exc_value, exc_tb))

def update_limit_count(limit_name):
    try:
        cursor.execute("INSERT OR IGNORE into limits(name, count) VALUES('{}', 1)".format(limit_name))
        cursor.execute("UPDATE limits set count = count + 1 WHERE name = '{}'".format(limit_name))
        cursor.execute("SELECT count FROM limits WHERE name = '{}'".format(limit_name))
        row = cursor.fetchone()
        if row == None:
            if bool(DEBUG): print("no limit found for {}".format(limit_name))
            return 0
        else:
            if bool(DEBUG): print("limit found for {} = {}".format(limit_name, row[0]))
            return row[0]


    except sqlite3.Error as er:
        print("update_limit_count:")
        print('SQLite error: %s' % (' '.join(er.args)))
        print("Exception class is: ", er.__class__)
        print('SQLite traceback: ')
        exc_type, exc_value, exc_tb = sys.exc_info()
        print(traceback.format_exception(exc_type, exc_value, exc_tb))

def flush_limits():
    try:
        if bool(DEBUG): print("DELETE FROM limits WHERE last_update_ts < DATETIME('now', '{}')".format(LIMIT_FLUSH))
        cursor.execute("DELETE FROM limits WHERE last_update_ts <= DATETIME('now', '{}')".format(LIMIT_FLUSH))
        if bool(DEBUG): print("Limits Flushed.")
     
    except sqlite3.Error as er:
        print("Failed to flush records in sqlite table.")
        print('SQLite error: %s' % (' '.join(er.args)))
        print("Exception class is: ", er.__class__)
        print('SQLite traceback: ')
        exc_type, exc_value, exc_tb = sys.exc_info()
        print(traceback.format_exception(exc_type, exc_value, exc_tb))

def watch_and_notify_events(client):
    global event_filters

    event_filters = {"event": event_filters}

    for event in client.events(filters=event_filters, decode=True):
        if bool(DEBUG): print(event)
        container_id = event['Actor']['ID'][:12]
        attributes = event['Actor']['Attributes']
        when = time.strftime('%Y-%m-%d %H:%M:%S %Z', time.localtime(event['time']))
        event['status'] = event['status']+'d'
        flush_limits()
        x = 'no'

        try:
            x = event['Actor']['Attributes']['docker-events.ignore']
        except KeyError:
            pass

        if x != 'no':
            if bool(DEBUG): print('docker-events.ignore specified')
            continue
		
        if bool(DEBUG): print('docker-events.ignore NOT specified')
			
        if attributes['name'] in ignore_names:
            continue

        if LIMIT_PER or LIMIT_ALL:
            if LIMIT_ALL:
                this_count = update_limit_count("ALL")
                if this_count >= LIMIT_ALL:
                    print("LIMIT_ALL({}) limit hit({}). Not sending to pushover.".format(LIMIT_ALL, this_count))
                    continue
            if LIMIT_PER:
                limit_name = "{}.{}".format(attributes['name'], event['status'])
                this_count = update_limit_count(limit_name)
                if this_count >= LIMIT_PER:
                    print("LIMIT_PER{}) limit hit({}). Not sending to pushover.".format(LIMIT_PER, this_count))
                    continue

        message = "The container {} ({}) {} at {}" \
                .format(attributes['name'],
                        attributes['image'],
                        event['status'],
                        when)
        send_message(message)


def send_message(message):
    client = Client(po_key, api_token=po_token)
    client.send_message(message,title="Docker Event")
##    global pb_key
##    pb = Pushbullet(pb_key)
##    pb.push_note("Docker Event", message)
    pass


def exit_handler(_signo, _stack_frame):
    send_message('{} received SIGTERM on {}. Goodbye!'.format(APP_NAME, host))
    sys.exit(0)


def host_server(client):
    return client.info()['Name']


if __name__ == '__main__':
##    pb_key = get_config("PB_API_KEY")
    po_token = get_config("PUSHOVER_TOKEN")
    po_key = get_config("PUSHOVER_KEY")
    # declare limit stuff
    LIMIT_PER = int(os.getenv("LIMIT_PER"))
    LIMIT_ALL= int(os.getenv("LIMIT_ALL"))
    LIMIT_FLUSH = os.getenv("LIMIT_FLUSH")
    DEBUG = os.getenv("DEBUG")
    database = "/limits.db"

    if LIMIT_PER or LIMIT_ALL:
        conn = create_connection(database)
        cursor = conn.cursor()
        if conn is not None:
            create_table()
        else:
            print("Error! cannot create the database connection.")
            sys.exit(1)

    events_string = get_config("EVENTS", True)
    if events_string:
        event_filters = events_string.split(',')

    ignore_strings = get_config("IGNORE_NAMES", True)
    if ignore_strings:
        ignore_names = ignore_strings.split(',')

    signal.signal(signal.SIGTERM, exit_handler)
    signal.signal(signal.SIGINT, exit_handler)

    client = docker.DockerClient(base_url='unix://var/run/docker.sock')
    host = host_server(client)

#    message = '{} reporting for duty on {}'.format(APP_NAME, host)
#    send_message(message)

    watch_and_notify_events(client)

    pass
