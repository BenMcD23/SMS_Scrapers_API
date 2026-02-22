import requests
import json
import os

APPS_SCRIPT = "https://script.google.com/macros/s/AKfycbxCZG-4ZudtBRL-TGRIsxD2QDWBa1rxZuZVZ_1L8YwI3Yb2hlkkuZNnof0y-Y7f_2S9/exec"
NOTIFIED_FILE = "notifications.json"

def load_notified():
    if os.path.exists(NOTIFIED_FILE):
        with open(NOTIFIED_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_notified(notified_set):
    with open(NOTIFIED_FILE, "w") as f:
        json.dump(sorted(list(notified_set)), f)

def get_event_bans(event_data):
    already_notified = load_notified()

    try:
        response = requests.get(APPS_SCRIPT)

        # Check if the request was successful
        if response.status_code == 200:
            # Parse the JSON response
            banned_names = response.json()
        else:
            print(f"Error: Received status code {response.status_code}")
    except:
        # didnt work, so just leave it for now
        return 404

    # Normalize the banned names into a set for efficient lookup
    banned_names_set = set(
        f"{name[0]} {name[1]}" for name in banned_names
    )

    # Dict to store who is on event when banned
    banned_and_bidding = {}

    new_notifications = set()

    # Loop through each event
    for event in event_data:
        event_name = event.get("event_name")

        # Check if there are attendees
        if isinstance(event["attendees"], list):  # Attendees exist as a list
            for attendee in event["attendees"]:
                if len(attendee) < 3:
                    continue

                first_name = attendee[1]
                last_name = attendee[2]
                full_name = f"{first_name} {last_name}"
                status = attendee[3]

                # Check if the attendee is banned
                if status in ["Attending", "Selected", "Reserve", "Bidding"] and full_name in banned_names_set:
                    key = f"{full_name}::{event_name}"

                    if key not in already_notified:
                        banned_and_bidding.setdefault(full_name, []).append([status, event_name])
                        new_notifications.add(key)

    if new_notifications:
        already_notified.update(new_notifications)
        save_notified(already_notified)

    return banned_and_bidding