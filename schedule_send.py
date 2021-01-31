import sys
from flask_app import refresh_clients, get_sunset, send_msg


def schedule_send():
    print("scheduler send", file=sys.stderr)

    '''
    Send update to each client
    '''
    clients = refresh_clients()
    for client in clients:
        location = client['Location']
        phone = client['Phone']
        msg = get_sunset(location)
        # send_msg(phone, msg)
    send_msg("+19739759395", "hello")
    return len(clients)


schedule_send()
