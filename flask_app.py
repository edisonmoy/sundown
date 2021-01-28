from flask import Flask, request, session, render_template

from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from twilio.request_validator import RequestValidator

from functools import wraps
import boto3
import re
import sys
import requests
import datetime
from suntime import Sun, SunTimeException
from dateutil import tz
from geopy.geocoders import Nominatim, GeoNames
import os
import phonenumbers
import uuid
import threading
import schedule
from dotenv import load_dotenv
import time


load_dotenv()

#  ================== Global Variables ==================
clients = []


#  ================== reCaptcha ==================
def validate_recaptcha(token):
    """Validate request using reCaptcha"""
    url = "https://www.google.com/recaptcha/api/siteverify"
    api_secret = os.getenv(
        "RECAPTCHA_SECRET")
    payload = {"secret": api_secret, "response": token}
    res = requests.post(url, params=payload)
    return res.json().get("success")

    #  ================== AWS ==================


def db_client():
    """Load AWS credentials"""
    ACCESS_ID = os.getenv("AWS_KEY")
    ACCESS_KEY = os.getenv("AWS_SECRET")

    dynamodb = boto3.resource('dynamodb',
                              region_name="us-west-1",
                              aws_access_key_id=ACCESS_ID,
                              aws_secret_access_key=ACCESS_KEY
                              )
    return dynamodb.Table('SunsetClients')


def refresh_clients():
    """Get clients from DynamoDB"""
    table = db_client()
    response = table.scan()
    all_clients = response['Items']
    while 'LastEvaluatedKey' in response:
        response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
        all_clients.extend(response['Items'])
    global clients
    clients = all_clients

    return all_clients


def client_exists(phone_number):
    """Check if phone number exists in DB"""
    for i in range(len(clients)):
        client = clients[i]
        if client["Phone"] == phone_number:
            return True
    return False


def create_client(phone_number, role='', location=''):
    """Create new row in DB with client info"""
    table = db_client()
    response = table.put_item(
        Item={
            'Id': str(uuid.uuid4()),
            'Phone': phone_number,
            'Role': role,
            'Location': location,
        }
    )
    return response


def update_row(client_id, key, value):
    """Edit item row in DB"""
    table = db_client()
    response = table.update_item(
        Key={'Id': client_id},
        UpdateExpression="set #KEY = :VALUE",
        ExpressionAttributeNames={
            '#KEY': key,
        },
        ExpressionAttributeValues={
            ':VALUE': value
        }, ReturnValues="UPDATED_NEW")
    return response


def get_client_role(phone_number):
    """Get client permission level given phone number"""
    for i in range(len(clients)):
        client = clients[i]
        if client["Phone"] == phone_number and "Role" in client:
            return client["Role"]
    return None


def get_client_location(phone_number):
    """Get location of client given phone number"""
    for i in range(len(clients)):
        client = clients[i]
        if client["Phone"] == phone_number and "Location" in client:
            return client["Location"]
    return None


def get_client_id(phone_number):
    """Get client Id given phone number"""
    for i in range(len(clients)):
        client = clients[i]
        if client["Phone"] == phone_number:
            return client["Id"]
    return None


def update_conversation(client_id, message):
    """Update dict of messages between server and client.
        Create new dictionary if does not exist"""
    table = db_client()
    response = table.get_item(Key={'Id': client_id})['Item']
    timestamp = str(datetime.datetime.now())
    if 'Conversation' in response:
        conversation = response['Conversation']
        conversation[timestamp] = message
    else:
        conversation = {timestamp: message}
    update_row(client_id, 'Conversation', conversation)
    return conversation


#  ================== Twilio ==================


def validate_twilio_request(f):
    """Validates that incoming requests genuinely originated from Twilio"""
    @ wraps(f)
    def decorated_function(*args, **kwargs):
        # Create an instance of the RequestValidator class
        validator = RequestValidator(os.environ.get('TWILIO_AUTH_TOKEN'))

        # Validate the request using its URL, POST data,
        # and X-TWILIO-SIGNATURE header
        request_valid = validator.validate(
            request.url,
            request.form,
            request.headers.get('X-TWILIO-SIGNATURE', ''))

        # Continue processing the request if it's valid, return a 403 error if
        # it's not
        if request_valid:
            return f(*args, **kwargs)
        else:
            return abort(403)
    return decorated_function


def send_msg(phone_number, msg):
    """Send text MSG to PHONE_NUM"""
    client = Client(os.getenv("TWILIO_AUTH_SID"),
                    os.getenv("TWILIO_AUTH_TOKEN"))
    client.messages.create(
        body=msg,
        from_='++18057068922',
        to=phone_number
    )
    update_conversation(get_client_id(phone_number), msg)
    return '"{}" sent to {}'.format(msg, phone_number)

#  ================== Sunset ==================


def address_to_coord(city_name):
    """Get coords of address"""
    geolocator = Nominatim(user_agent="sundown")
    location = geolocator.geocode(city_name)
    if location is None:
        return -1
    return (location.latitude, location.longitude)


def cleaned_address(address):
    """Get cleaned address"""
    geolocator = Nominatim(user_agent="sundown")
    location = geolocator.geocode(address)
    if location is None:
        return -1
    return (location.address)


def generate_grid(coord_tuple):
    """Given coord tuple, create 3x3 grid"""
    lat = coord_tuple[0]
    lng = coord_tuple[1]

    mod = .1375 * 2
    coords = []

    for dx in range(-1, 2):
        for dy in range(-1, 2):
            try:
                new_lat = lat + dx * mod
                new_lng = lng + dy * mod
                new_coord = str(new_lat) + "," + str(new_lng)
                coords.append(new_coord)
            except:
                pass
    return coords


def get_sunset(address, from_grid=True):
    """Get sunset quality and parse into message"""

    # Load Sunburst API credentials
    EMAIL = os.getenv("SUNBURST_EMAIL")
    PASSWORD = os.getenv("SUNBURST_PW")
    url = "https://sunburst.sunsetwx.com/v1/login"

    # Get Sunburst API token via POST
    res = requests.post(url, auth=(EMAIL, PASSWORD))

    # res = requests.post(url, data=payload)
    result = re.findall(r'token\":\"[0-9a-xA-Z-]*', res.text)
    token = "Bearer " + result[0][8:]

    # Get sunset quality via Sunburst GET
    headers = {"Authorization": token}
    url = "https://sunburst.sunsetwx.com/v1/quality"

    # Return if invalid coords
    coords = address_to_coord(address)
    if coords == -1:
        return "Invalid location. Please enter valid address."

    total = 0

    # Get coordinates and quality at each coord
    coords_list = []

    # If calculate quality from grid, false if calculate from single coord
    if from_grid:
        coords_list = generate_grid(coords)
        if len(coords_list) == 0:
            coords_list = [str(coords[0]) + "," + str(coords[1])]
        else:
            coords_list = [str(coords[0]) + "," + str(coords[1])]

    for coord in coords_list:
        data = {"geo": coord}
        res = requests.get(url, headers=headers, params=data)
        try:
            quality_percent = re.findall(
                r'quality_percent\":\d*\.\d*', res.text)[0][17:]
        except:
            return "Too many Sunburst requests. Try again later."

        total += float(quality_percent)

    quality_percent = total / float(len(coords_list))
    quality = ''

    if quality_percent < 25:
        quality = 'Poor'
    elif quality_percent < 50:
        quality = 'Fair'
    elif quality_percent < 75:
        quality = 'Good'
    else:
        quality = 'Great'

    # Get today's sunset in local time
    sun = Sun(coords[0], coords[1])
    today_ss = sun.get_sunset_time()

    # Convert time zone
    GEO_USERNAME = os.getenv("GEONAMES_USERNAME")
    geolocator = GeoNames(username=GEO_USERNAME)
    timezone = geolocator.reverse_timezone(coords)
    from_zone = tz.gettz('UTC')
    to_zone = tz.gettz(str(timezone))
    today_ss = today_ss.replace(tzinfo=from_zone)
    sunset_time = today_ss.astimezone(to_zone)

    # Get day of week
    day_list = ["Monday", "Tuesday", "Wednesday",
                "Thursday", "Friday", "Saturday", "Sunday"]
    day = day_list[datetime.datetime.today().weekday()]

    # Create message
    message = day + ' at ' + address + '\n\nSunset at {}pm'.format(sunset_time.strftime(
        '%H:%M')) + '\nQuality: ' + quality + " " + str(round(quality_percent, 2)) + "%"

    return message


#  ================== Schedule Send ==================
def schedule_send():
    '''
    Send update to each client
    '''
    refresh_clients()
    for client in clients:
        location = client['Location']
        phone = client['Phone']
        msg = get_sunset(location)
        send_msg(phone, msg)
    return len(clients)


def run_scheduler():
    '''
    Continuously run to send messages at same time each day
    '''
    time_to_send = "14:00"
    schedule.every().day.at(time_to_send).do(schedule_send)
    while True:
        schedule.run_pending()
        time.sleep(1)


scheduler = threading.Thread(target=run_scheduler)
scheduler.start()


#  ================== Account Creation ==================

def begin_onboard(phone_number):
    """Send onboarding messages"""
    if client_exists(phone_number):
        msg = "Account with this phone number already exists. For more information, reply HELP."
        send_msg(phone_number, msg)
    else:
        create_client(phone_number, "Pending")
        msg = "Welcome to Sundown, the simple way to get daily notifications of the sunset quality."
        send_msg(phone_number, msg)
        msg = "To begin, please respond with your location. You can reply with a street address, city and state or zipcode."
        send_msg(phone_number, msg)
    return ("Success")


def validate_location(phone_number, location):
    """Update client location and verify that it is correct"""
    location = cleaned_address(location)
    update_row(get_client_id(phone_number), "Location", location)
    return "(Yes/No) Is this the correct location? \n\n" + str(location)


def finish_creation(phone_number):
    """Update user info and complete account creation"""
    # Timestamp of account creation finished
    client_id = get_client_id(phone_number)
    update_row(client_id,
               "Account Created", str(datetime.datetime.now()))
    update_row(client_id, "Role", "User")

    return "Set up complete! You will now receive daily sunset texts. Reply SUNDOWN to get your first sunset quality text.\n\nReply HELP for more options."


#  ================== Routes ==================
app = Flask(__name__)
app.config.from_object(__name__)


# Route that serves all requests
@ app.route("/", methods=['GET', 'POST'])
def render_index():
    # Fetch clients from DB
    refresh_clients()
    return render_template("index.html")


# Route that creates a new user
@ app.route("/api/create", methods=['POST'])
def create_route():
    # Fetch clients from DB
    refresh_clients()
    # Validate request
    if not validate_recaptcha(request.values.get("recaptcha_token")):
        return "Invalid request", 401

    # Validate phone number
    phone_number = request.values.get("phone")
    phone_number_obj = phonenumbers.parse(phone_number, None)

    if phonenumbers.is_valid_number(phone_number_obj):
        return begin_onboard(phone_number)
    else:
        return "Invalid Number", 400


# Route that handles incoming SMS
@ app.route("/api/sms", methods=['POST'])
@ validate_twilio_request
def incoming_text():

    # Fetch clients from DB
    refresh_clients()

    # If this is a valid response
    if request.values.get("Body"):

        input_msg = request.values.get("Body")
        # Clean string
        input_msg = input_msg.replace('+', ' ').lower().lstrip().rstrip()

        # Get requestor details
        client_num = request.values.get('From')
        client_curr_location = get_client_location(client_num)
        client_role = get_client_role(client_num)
        client_id = get_client_id(client_num)

        # Update conversation dict with request
        update_conversation(client_id, input_msg)

        # Check if response is from account creation
        if client_role == 'Pending':
            if input_msg == 'yes':
                output_msg = finish_creation(client_num)
            elif input_msg == 'no':
                output_msg = "Please input your location again. Add more specificity like street address, city, zip code, state and country."
            else:
                output_msg = validate_location(client_num, input_msg)
        # Check if response is from location update
        elif client_role == 'Updating':
            if input_msg == 'yes':
                update_row(client_id, "Role", "User")
                output_msg = "Your location has been updated to:\n" + client_curr_location
            elif input_msg == 'no':
                output_msg = "Please input your location again. Add more specificity like street address, city, zip code, state or country."
            else:
                output_msg = validate_location(client_num, input_msg)
        else:
            # Refresh
            if input_msg == 'refresh' or input_msg == 'update' or input_msg == 'sunset' or input_msg == "sundown":
                output_msg = get_sunset(client_curr_location, True)

            # Update Location
            elif 'change location to' in input_msg or 'change city to' in input_msg:
                location = input_msg.split(' ', 3)[3]
                update_row(client_id, "Role", "Updating")
                output_msg = validate_location(client_num, location)

                # Get Help
            elif input_msg == "help" or input_msg == 'info':
                return
            else:
                output_msg = "Sorry, we can't process your message. Reply HELP for more options."
    else:
        output_msg = "Sorry, we can't process your message. Reply HELP for more options."

    # Update conversation dict with response
    update_conversation(client_id, output_msg)

    # Put it in a TwiML response
    resp = MessagingResponse()
    resp.message(output_msg)

    return str(resp)
