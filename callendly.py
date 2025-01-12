import os
from datetime import datetime, timedelta, timezone
import requests
from flask import Flask, redirect, request, session, jsonify, render_template
import json
from pathlib import Path
from typing import Dict, List
from zoneinfo import ZoneInfo

app = Flask(__name__)
app.secret_key = '0PfyNauUtDHwgMRrsOucs8PJQ7wZ5YmkN4Ot9LtXUEg'  # Using your webhook signing key as secret key

# Calendly API configuration
CALENDLY_AUTH_BASE_URL = "https://auth.calendly.com"
CALENDLY_API_BASE_URL = "https://api.calendly.com"
CLIENT_ID = "zzB2owvO8C_X4d_dz0bI7k5juare4iiEqzm0O_CAzbo"
CLIENT_SECRET = "d4I54GPBC9Aw0K9HiFIIP4-b66ev151zeJMdw65Y3Kk"
REDIRECT_URI = "http://localhost:5000/callback"

def save_user_availability(user_email: str, availability_data: Dict):
    """Save user availability data to a JSON file"""
    storage_dir = Path("user_data")
    storage_dir.mkdir(exist_ok=True)
    
    file_path = storage_dir / f"{user_email.replace('@', '_at_')}.json"
    with open(file_path, 'w') as f:
        json.dump(availability_data, f, indent=4)

def get_available_users() -> List[str]:
    """Get list of users with stored availability data"""
    storage_dir = Path("user_data")
    if not storage_dir.exists():
        return []
    
    return [f.stem.replace('_at_', '@') for f in storage_dir.glob('*.json')]

def find_matching_slots(user1_email: str, user2_email: str):
    """Find matching free time slots between two users for the next 3 weeks"""
    print(f"\n🔍 Finding matching slots between {user1_email} and {user2_email}")
    
    storage_dir = Path("user_data")
    user1_file = storage_dir / f"{user1_email.replace('@', '_at_')}.json"
    user2_file = storage_dir / f"{user2_email.replace('@', '_at_')}.json"
    
    if not (user1_file.exists() and user2_file.exists()):
        print("❌ One or both user files not found")
        return None
    
    with open(user1_file) as f1, open(user2_file) as f2:
        user1_data = json.load(f1)
        user2_data = json.load(f2)

    print(f"\n👤 User 1: {user1_data['user']}")
    print(f"📅 Event types: {[et['name'] for et in user1_data['event_types']]}")
    print(f"🕒 Scheduled events: {len(user1_data['scheduled_events'])}")
    
    print(f"\n👤 User 2: {user2_data['user']}")
    print(f"📅 Event types: {[et['name'] for et in user2_data['event_types']]}")
    print(f"🕒 Scheduled events: {len(user2_data['scheduled_events'])}")

    # Set time range for 3 weeks
    start_time = datetime.utcnow().replace(tzinfo=timezone.utc)
    end_time = start_time + timedelta(weeks=3)
    print(f"\n📆 Checking availability from {start_time} to {end_time}")
    
    def parse_datetime(dt_str):
        return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
    
    # Get scheduled events (busy times)
    user1_busy = [(parse_datetime(event['start_time']), parse_datetime(event['end_time']))
                  for event in user1_data['scheduled_events']]
    user2_busy = [(parse_datetime(event['start_time']), parse_datetime(event['end_time']))
                  for event in user2_data['scheduled_events']]

    # Get minimum event duration
    user1_duration = min(event['duration'] for event in user1_data['event_types'])
    user2_duration = min(event['duration'] for event in user2_data['event_types'])
    min_duration = max(user1_duration, user2_duration)
    print(f"\n⏱️ Minimum meeting duration: {min_duration} minutes")
    
    def is_within_availability(time, user_data):
        """Check if a given time is within user's availability schedule"""
        weekday = time.strftime('%A').lower()
        print(f"\nChecking availability for {weekday} at {time}")
        
        if 'availability_schedules' not in user_data:
            print(f"No availability schedules found for {user_data['email']}")
            return False
        
        for schedule in user_data['availability_schedules']:
            print(f"Checking schedule: {schedule['name']} (Timezone: {schedule.get('timezone', 'UTC')})")
            
            # Get user's timezone
            user_tz = timezone(timedelta(hours=1) if schedule.get('timezone') == 'Europe/Berlin' else timedelta())
            
            for rule in schedule['rules']:
                if rule['type'] == 'wday' and rule['wday'] == weekday:
                    print(f"Found rule for {weekday}: {rule}")
                    for interval in rule['intervals']:
                        # Convert the time to check into user's timezone
                        local_time = time.astimezone(user_tz)
                        time_to_check = local_time.time()
                        
                        # Handle intervals that span midnight
                        if interval['from'] == '00:00' and interval['to'] != '00:00':
                            # Morning interval starting at midnight
                            from_time = datetime.strptime('00:00', '%H:%M').time()
                            to_time = datetime.strptime(interval['to'], '%H:%M').time()
                            
                            print(f"Checking midnight interval: {time_to_check} between {from_time}-{to_time} in {schedule.get('timezone', 'UTC')}")
                            if from_time <= time_to_check <= to_time:
                                print("Time is within midnight-morning availability!")
                                return True
                            
                        elif interval['to'] == '00:00':
                            # Evening interval ending at midnight
                            from_time = datetime.strptime(interval['from'], '%H:%M').time()
                            to_time = datetime.strptime('23:59', '%H:%M').time()
                            
                            print(f"Checking evening interval: {time_to_check} between {from_time}-{to_time} in {schedule.get('timezone', 'UTC')}")
                            if from_time <= time_to_check <= to_time:
                                print("Time is within evening-midnight availability!")
                                return True
                        else:
                            # Regular interval within the same day
                            from_time = datetime.strptime(interval['from'], '%H:%M').time()
                            to_time = datetime.strptime(interval['to'], '%H:%M').time()
                            
                            print(f"Checking regular interval: {time_to_check} between {from_time}-{to_time} in {schedule.get('timezone', 'UTC')}")
                            if from_time <= time_to_check <= to_time:
                                print("Time is within regular availability!")
                                return True
            
        print("Time is not within any availability window")
        return False
    
    # Find available slots day by day
    matching_slots = []
    current_date = start_time
    
    while current_date < end_time:
        print(f"\n📅 Checking {current_date.date()}")
        slot_start = current_date.replace(hour=0, minute=0)
        day_end = (current_date + timedelta(days=1)).replace(hour=0, minute=0)
        
        day_slots = 0
        while slot_start < day_end:
            slot_end = slot_start + timedelta(minutes=min_duration)
            
            # Check availability
            user1_available = is_within_availability(slot_start, user1_data)
            user2_available = is_within_availability(slot_start, user2_data)
            
            if not (user1_available and user2_available):
                slot_start += timedelta(minutes=30)
                continue
            
            # Check conflicts
            is_free = True
            for busy in user1_busy + user2_busy:
                if (slot_start < busy[1] and slot_end > busy[0]):
                    is_free = False
                    break
            
            if is_free:
                matching_slots.append((slot_start, slot_end))
                day_slots += 1
            
            slot_start += timedelta(minutes=30)
        
        print(f"✨ Found {day_slots} matching slots")
        current_date += timedelta(days=1)
    
    print(f"\n🎉 Total matching slots found: {len(matching_slots)}")
    
    # Format response
    response = {
        'user1': user1_data['user'],
        'user2': user2_data['user'],
        'matching_free_slots': [{
            'start': t1.isoformat(),
            'end': t2.isoformat(),
            'duration_minutes': min_duration
        } for t1, t2 in matching_slots],
        'start_time': start_time.isoformat(),
        'end_time': end_time.isoformat()
    }
    
    print("\n✅ Matching complete!")
    return response

def get_actual_availability(user_uri: str, date: datetime, headers: dict):
    """Get actual available time slots for a specific date"""
    # First get event types
    event_types_url = f"{CALENDLY_API_BASE_URL}/event_types"
    event_types_params = {
        "user": user_uri,
        "active": True
    }
    
    event_types_response = requests.get(event_types_url, headers=headers, params=event_types_params)
    if event_types_response.status_code != 200:
        print(f"Failed to get event types: {event_types_response.text}")
        return []
    
    event_types = event_types_response.json()['collection']
    if not event_types:
        print("No event types found")
        return []
    
    # Get availability for the first event type
    event_type = event_types[0]
    availability_url = f"{CALENDLY_API_BASE_URL}/event_types/{event_type['uri'].split('/')[-1]}/availability"
    params = {
        "start_time": date.replace(hour=0, minute=0).isoformat() + 'Z',
        "end_time": (date.replace(hour=23, minute=59) + timedelta(days=1)).isoformat() + 'Z'
    }
    
    response = requests.get(availability_url, headers=headers, params=params)
    if response.status_code != 200:
        print(f"Failed to get availability: {response.text}")
        return []
        
    print(f"\nAvailability Response for {date.date()}:", json.dumps(response.json(), indent=2))
    return response.json().get('slots', [])

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/login')
def login():
    # Construct the authorization URL
    auth_url = f"{CALENDLY_AUTH_BASE_URL}/oauth/authorize"
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
    }
    auth_url = f"{auth_url}?{'&'.join(f'{k}={v}' for k, v in params.items())}"
    return redirect(auth_url)

@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code:
        return "Authorization failed", 400

    # Exchange the authorization code for an access token
    token_url = f"{CALENDLY_AUTH_BASE_URL}/oauth/token"
    token_data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code"
    }

    response = requests.post(token_url, data=token_data)
    if response.status_code != 200:
        return "Failed to get access token", 400

    token_info = response.json()
    session['access_token'] = token_info['access_token']
    return redirect('/availability')

@app.route('/availability')
def get_availability():
    if 'access_token' not in session:
        return redirect('/login')

    headers = {
        "Authorization": f"Bearer {session['access_token']}",
        "Content-Type": "application/json"
    }

    # Get user info first to get timezone
    user_response = requests.get(f"{CALENDLY_API_BASE_URL}/users/me", headers=headers)
    if user_response.status_code != 200:
        return f"Failed to get user info: {user_response.text}", 400
        
    user_data = user_response.json()
    user_uri = user_data['resource']['uri']
    user_timezone = user_data['resource']['timezone']
    
    # Get event types
    event_types_response = requests.get(
        f"{CALENDLY_API_BASE_URL}/event_types",
        params={"user": user_uri},
        headers=headers
    )
    
    if event_types_response.status_code != 200:
        return f"Failed to get event types: {event_types_response.text}", 400
        
    event_types = event_types_response.json()
    
    # Get available times for each event type using the working endpoint
    availability = {}
    for event_type in event_types['collection']:
        # Get next 7 days of availability
        start_time = datetime.utcnow()
        end_time = start_time + timedelta(days=7)
        
        available_times_url = f"{CALENDLY_API_BASE_URL}/event_type_available_times"
        params = {
            "event_type": event_type['uri'],
            "start_time": start_time.isoformat() + 'Z',
            "end_time": end_time.isoformat() + 'Z'
        }
        
        times_response = requests.get(available_times_url, headers=headers, params=params)
        
        if times_response.status_code == 200:
            times_data = times_response.json()
            # Convert times to user's timezone
            for slot in times_data.get('collection', []):
                utc_time = datetime.fromisoformat(slot['start_time'].replace('Z', '+00:00'))
                local_time = utc_time.astimezone(ZoneInfo(user_timezone))
                slot['local_time'] = local_time.strftime('%Y-%m-%d %H:%M %Z')
            availability[event_type['name']] = times_data

    # Save to user file
    email = user_data['resource']['email']
    user_file = f"user_data/{email.replace('@', '_at_')}.json"
    os.makedirs('user_data', exist_ok=True)
    
    user_info = {
        'email': email,
        'name': user_data['resource']['name'],
        'timezone': user_timezone,
        'event_types': event_types['collection'],
        'availability': availability
    }
    
    with open(user_file, 'w', encoding='utf-8') as f:
        json.dump(user_info, f, indent=2)
    
    return jsonify(user_info)

# Add new routes for matching
@app.route('/users')
def list_users():
    """List all users with stored availability"""
    storage_dir = Path("user_data")
    if not storage_dir.exists():
        return jsonify({'users': []})
    
    # Get all JSON files and convert filenames back to email addresses
    users = []
    for file in storage_dir.glob('*.json'):
        email = file.stem.replace('_at_', '@')
        users.append(email)
    
    print(f"Found users: {users}")  # Debug print
    return jsonify({'users': users})

@app.route('/match/<user1_email>/<user2_email>')
def match_users(user1_email: str, user2_email: str):
    """Find matching availability between two users"""
    storage_dir = Path("user_data")
    user1_file = storage_dir / f"{user1_email.replace('@', '_at_')}.json"
    user2_file = storage_dir / f"{user2_email.replace('@', '_at_')}.json"
    
    if not (user1_file.exists() and user2_file.exists()):
        return jsonify({'error': 'One or both users not found'}), 404
    
    with open(user1_file, 'r') as f1, open(user2_file, 'r') as f2:
        user1_data = json.load(f1)
        user2_data = json.load(f2)
    
    # Get available slots for both users
    user1_slots = []
    user2_slots = []
    
    # Extract slots from user1 with date information
    for event_type in user1_data['availability'].values():
        for slot in event_type['collection']:
            slot_time = datetime.fromisoformat(slot['start_time'].replace('Z', '+00:00'))
            slot_date = slot_time.date()
            user1_slots.append({
                'start': slot['start_time'],
                'date': slot_date,
                'time': slot_time.time(),
                'datetime': slot_time,
                'end': slot_time + timedelta(minutes=15),
                'local_time': slot['local_time']
            })
    
    # Extract slots from user2 with date information
    for event_type in user2_data['availability'].values():
        for slot in event_type['collection']:
            slot_time = datetime.fromisoformat(slot['start_time'].replace('Z', '+00:00'))
            slot_date = slot_time.date()
            user2_slots.append({
                'start': slot['start_time'],
                'date': slot_date,
                'time': slot_time.time(),
                'datetime': slot_time,
                'end': slot_time + timedelta(minutes=15),
                'local_time': slot['local_time']
            })
    
    # Sort slots by datetime
    user1_slots.sort(key=lambda x: x['datetime'])
    user2_slots.sort(key=lambda x: x['datetime'])
    
    # Find exact matching slots
    matching_slots = []
    for slot1 in user1_slots:
        for slot2 in user2_slots:
            if slot1['start'] == slot2['start']:
                matching_slots.append({
                    'start': slot1['start'],
                    'end': slot1['end'].isoformat().replace('+00:00', 'Z'),
                    'duration_minutes': 15,
                    'local_time': slot1['local_time']
                })
    
    # If no exact matches, find closest alternatives
    closest_alternatives = []
    if not matching_slots:
        # Group slots by date for better organization
        user1_slots_by_date = {}
        user2_slots_by_date = {}
        
        # Group user1 slots by date
        for slot in user1_slots:
            date_str = slot['date'].strftime('%Y-%m-%d')
            if date_str not in user1_slots_by_date:
                user1_slots_by_date[date_str] = []
            user1_slots_by_date[date_str].append(slot)
            
        # Group user2 slots by date
        for slot in user2_slots:
            date_str = slot['date'].strftime('%Y-%m-%d')
            if date_str not in user2_slots_by_date:
                user2_slots_by_date[date_str] = []
            user2_slots_by_date[date_str].append(slot)
        
        # Find closest slots for each day where both users have availability
        all_dates = sorted(set(user1_slots_by_date.keys()) | set(user2_slots_by_date.keys()))
        
        for date_str in all_dates:
            day_alternatives = []
            
            if date_str in user1_slots_by_date and date_str in user2_slots_by_date:
                user1_day_slots = user1_slots_by_date[date_str]
                user2_day_slots = user2_slots_by_date[date_str]
                
                # Find closest pairs for this day
                for slot1 in user1_day_slots:
                    closest_slot = min(user2_day_slots, 
                                    key=lambda x: abs((x['datetime'] - slot1['datetime']).total_seconds()))
                    time_diff = abs((closest_slot['datetime'] - slot1['datetime']).total_seconds() / 3600)  # in hours
                    
                    if time_diff <= 24:  # Only include if within 24 hours
                        day_alternatives.append({
                            'user1_slot': {
                                'time': slot1['local_time'],
                                'start': slot1['start']
                            },
                            'user2_slot': {
                                'time': closest_slot['local_time'],
                                'start': closest_slot['start']
                            },
                            'time_difference_hours': round(time_diff, 1),
                            'date': date_str
                        })
                
                # Sort by time difference and take top 2 for each day
                day_alternatives.sort(key=lambda x: x['time_difference_hours'])
                closest_alternatives.extend(day_alternatives[:2])
    
    # Sort all alternatives by date and time difference
    closest_alternatives.sort(key=lambda x: (x['date'], x['time_difference_hours']))
    
    response = {
        'user1': user1_email,
        'user2': user2_email,
        'has_matching_slots': len(matching_slots) > 0,
        'matching_free_slots': matching_slots,
        'closest_alternatives': closest_alternatives if not matching_slots else [],
        'timezone': user1_data['timezone']
    }
    
    return jsonify(response)

@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle Calendly webhook notifications"""
    data = request.json
    
    if data['event'] == 'invitee.created':
        # New event scheduled
        event = data['payload']
        user_email = event['event']['user']  # Get user email from event
        
        # Update stored availability
        storage_dir = Path("user_data")
        user_file = storage_dir / f"{user_email.replace('@', '_at_')}.json"
        
        if user_file.exists():
            with open(user_file) as f:
                user_data = json.load(f)
            
            # Add new event to scheduled_events
            user_data['scheduled_events'].append({
                'start_time': event['event']['start_time'],
                'end_time': event['event']['end_time'],
                'event_type': event['event']['event_type']
            })
            
            # Save updated data
            save_user_availability(user_email, user_data)
    
    return '', 200

if __name__ == '__main__':
    app.run(debug=True) 
