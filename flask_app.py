from flask import Flask, request, session
from twilio.twiml.messaging_response import MessagingResponse
import boto3
import re
import sys
import requests
import datetime
from suntime import Sun, SunTimeException
from dateutil import tz
from geopy.geocoders import Nominatim, GeoNames


# The session object makes use of a secret key.
SECRET_KEY = 'a secret key'
app = Flask(__name__)
app.config.from_object(__name__)

callers = {
    "+19739759395": "Edison",
}


def get_clients():
    """
    Get clients from DynamoDB
    """
    dynamodb = boto3.resource('dynamodb',
                          region_name="us-west-1"
                          )
    table = dynamodb.Table('SunsetClients')
    response = table.scan()
    clients = response['Items']
    while 'LastEvaluatedKey' in response:
        response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
        clients.extend(response['Items'])
    return clients

def getPermissionsFromNumber(clients, phone_number):
    """
    Get permission of client given phone number
    """
    for i in range(len(clients)):
        client = clients[i]
        if client["Phone"] == phone_number:
	        return client["Role"]
    return None

def get_location_from_number(clients, phone_number):
    """
    Get location of client given phone number
    """
    for i in range(len(clients)):
        client = clients[i]
        if client["Phone"] == phone_number:
            return client["Location"]
    return None

def get_id_from_number(clients, phone_number):
    """
    Get Id of client given phone number
    """
    for i in range(len(clients)):
        client = clients[i]
        if client["Phone"] == phone_number:
            return client["Id"]
    return None

#
def update_city(client_num, new_city):
    """
    Given name of city and client's number,
    update client's city in DB
    Return string with success or error
    """
    curr_city = get_location_from_number(client_num)
    if curr_city.lower() == new_city.lower():
        return "Current city is already " + curr_city
    else:
        if (address_to_coord(new_city) == -1):
            return "Invalid city. Current city is " + curr_city

        table = boto3.resource('dynamodb').Table('SunsetClients')
        table.update_item(
            Key={'Id': get_id_from_number(client_num)},
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
    """
    Get coords of address
    """
    geolocator = Nominatim(user_agent="sundown")
    location = geolocator.geocode(city_name)
    if location is None:
        return -1
    return (location.latitude, location.longitude)


def generate_grid(coord_tuple):
    """
    Given coord tuple, create 3x3 grid
    """
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
    """
    Get sunset quality and parse into message
    """

    # Sunburst API login
    email = "moyedison@gmail.com"
    password = "saywhat??"

    url = "https://sunburst.sunsetwx.com/v1/login"
    payload = {"email": email, "password": password}

    # Get Sunburst API token via POST
    res = requests.post(url, data=payload)
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

    #If calculate quality from grid, false if calculate from single coord
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
    geolocator = GeoNames(username='moysauce18')
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

def create_user(msg):
    """
    Create new user.
    If requestor is admin, update DB with new record
    """
    return "Currently unavailable"


# Route that serves all requests
@app.route("/", methods=['GET', 'POST'])
def hello():
    print(request.values, file=sys.stderr)

    # If this is a valid response
    if request.values.get("Body"):
        input_msg = request.values.get("Body")

        #Clean string
        input_msg = input_msg.replace('+', ' ').lower().lstrip().rstrip()

        # Get requestor details
        client_num = request.values.get('From')
        client_curr_city = get_location_from_number(client_num)

        # Send response given input message
        if input_msg == 'refresh' or input_msg == 'update':
            output_msg = get_sunset(client_curr_city, True)

        elif 'change city to' in input_msg:
            new_city = re.findall(
                r'change city to (([a-zA-Z]*\s*)*)', input_msg)[0][0]
            output_msg = update_city(client_num, new_city)

        elif 'create' in input_msg:
            output_msg = create_user(input_msg)

        else:
            output_msg = 'Text REFRESH for the latest sunset prediction.\n Current City: ' + \
                client_curr_city+'\n To change current city, text CHANGE CITY TO NEW YORK, NY'


    """
    # Respond with the number of text messages sent between two parties.
    # Increment the counter
    counter = session.get('counter', 0)
    counter += 1

    # Save the new counter value in the session
    session['counter'] = counter

    from_number = request.values.get('From')
    if from_number in callers:
        name = callers[from_number]
    else:
        name = "Friend"

    # Build our reply
    message = '{} has messaged {} {} times.' \
        .format(name, request.values.get('To'), counter)
    """

    # Put it in a TwiML response
    resp = MessagingResponse()
    resp.message(output_msg)

    return str(resp)


