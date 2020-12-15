from flask import Flask, request, session, render_template

from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from twilio.http.http_client import TwilioHttpClient
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

#  ================== Global Variables ==================
clients = []

#  ================== AWS ==================


def db_client():
 # Load AWS credentials
    ACCESS_ID = os.getenv("AWS_KEY")
    ACCESS_KEY = os.getenv("AWS_SECRET")

    dynamodb = boto3.resource('dynamodb',
                              region_name="us-west-1",
                              aws_access_key_id=ACCESS_ID,
                              aws_secret_access_key=ACCESS_KEY
                              )
    return dynamodb.Table('SunsetClients')


def get_clients():
    """Get clients from DynamoDB"""
    table = db_client()
    response = table.scan()
    clients = response['Items']
    while 'LastEvaluatedKey' in response:
        response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
        clients.extend(response['Items'])
    return clients


def client_exists(phone_number):
    """Check if phone number exists in DB"""
    all_clients = get_clients()
    for i in range(len(all_clients)):
        client = all_clients[i]
        if client["Phone"] == phone_number:
            return True
    return False


def create_client(phone_number, location):
    """Create new row in DB with client info"""
    table = db_client()
    response = table.put_item(
        Item={
            'Number': phone_number,
            'Role': 0,
            'Location': location,
        }
    )
    return response


def getPermissionsFromNumber(client_list, phone_number):
    """Get client permission level given phone number"""
    for i in range(len(client_list)):
        client = client_list[i]
        if client["Phone"] == phone_number:
            return client["Role"]
    return None


def get_location_from_number(client_list, phone_number):
    """
    Get location of client given phone number
    """
    for i in range(len(client_list)):
        client = client_list[i]
        if client["Phone"] == phone_number:
            return client["Location"]
    return None


def get_id_from_number(client_list, phone_number):
    """Get client Id given phone number"""
    for i in range(len(client_list)):
        client = client_list[i]
        if client["Phone"] == phone_number:
            return client["Id"]
    return None

#  ================== Twilio ==================


def validate_twilio_request(f):
    """Validates that incoming requests genuinely originated from Twilio"""
    @wraps(f)
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
    proxy_client = TwilioHttpClient(
        proxy={'http': os.environ['http_proxy'], 'https': os.environ['https_proxy']})

    client = Client(os.getenv("TWILIO_AUTH_SID"),
                    os.getenv("TWILIO_AUTH_TOKEN"), http_client=proxy_client)
    client.messages.create(
        body=msg,
        from_='++18057068922',
        to=phone_number
    )
    return '"{}" sent to {}'.format(msg, phone_number)


#  ================== Sunset ==================


def update_city(client_num, new_city):
    """
    Given name of city and client's number,
    update client's city in DB
    Return string with success or error
    """
    curr_city = get_location_from_number(clients, client_num)
    if curr_city.lower() == new_city.lower():
        return "Current city is already " + curr_city
    else:
        if (address_to_coord(new_city) == -1):
            return "Invalid city. Current city is " + curr_city

        table = boto3.resource('dynamodb').Table('SunsetClients')
        table.update_item(
            Key={'Id': get_id_from_number(clients, client_num)},
            UpdateExpression="set #KEY = :VALUE",
            ExpressionAttributeNames={
                '#KEY': 'Location',
            },
            ExpressionAttributeValues={
                ':VALUE': new_city
            },
            ReturnValues="UPDATED_NEW",

        )
        return "City has been updated to " + new_city + '\n' + get_sunset(new_city)


def address_to_coord(city_name):
    """Get coords of address"""
    geolocator = Nominatim(user_agent="sundown")
    location = geolocator.geocode(city_name)
    if location is None:
        return -1
    return (location.latitude, location.longitude)


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
    GEO_USERNAME = os.getnev("GEONAMES_USERNAME")
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
    message = day + ' at ' + address + '\n' + 'Sunset at {}pm'.format(sunset_time.strftime(
        '%H:%M')) + '\nQuality: ' + quality + " " + str(round(quality_percent, 2)) + "%"

    return message


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
        msg = "To begin, please respond with the name of your town or city:"
        send_msg(phone_number, msg)
    return("Success")


def finish_creation(phone_number, location):
    """Update user info and complete account creation"""
    update_city(phone_number, location)
    msg = "Set up complete! You will now receive daily sunset texts. Reply SUNDOWN to get your first sunset quality text. Reply HELP for more options"
    send_msg(phone_number, msg)


#  ================== Routes ==================
app = Flask(__name__)
app.config.from_object(__name__)


# Route that serves all requests
@app.route("/", methods=['GET', 'POST'])
def render_index():
    return render_template("index.html")


# Route that creates a new user
@app.route("/api/create", methods=['POST'])
def create_route():
    return begin_onboard(request.values.get("phone"))


# Route that handles incoming SMS
@app.route("/api/sms", methods=['POST'])
@validate_twilio_request
def incoming_text():

    # Fetch clients from DB
    clients = get_clients()

    # If this is a valid response
    if request.values.get("Body"):
        input_msg = request.values.get("Body")

        # Clean string
        input_msg = input_msg.replace('+', ' ').lower().lstrip().rstrip()

        # Get requestor details
        client_num = request.values.get('From')
        client_curr_city = get_location_from_number(clients, client_num)

        # Check if response is from account creation
        if client_curr_city == 'Pending':
            output_msg = finish_creation(input_msg)
        else:
            # Send response given input message
            if input_msg == 'refresh' or input_msg == 'update' or input_msg == 'sunset' or input_msg == "sundown":
                output_msg = get_sunset(client_curr_city, True)

            elif 'change city to' in input_msg:
                new_city = re.findall(
                    r'change city to (([a-zA-Z]*\s*)*)', input_msg)[0][0]
                output_msg = update_city(client_num, new_city)

            else:
                output_msg = 'Text REFRESH for the latest sunset prediction.\n Current City: ' + \
                    client_curr_city+'\n To change current city, text CHANGE CITY TO NEW YORK, NY'
    else:
        output_msg = "Invalid request"

    # Put it in a TwiML response
    resp = MessagingResponse()
    resp.message(output_msg)

    return str(resp)
