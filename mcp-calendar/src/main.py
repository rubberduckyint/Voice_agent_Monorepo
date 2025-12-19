"""
MCP Calendar Server
Handles calendar availability and booking via Google Calendar API.
Uses Domain-Wide Delegation to impersonate a user for sending invites.
"""

import os
import json
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = FastAPI(
    title="MCP Calendar Server",
    description="Calendar MCP server for checking availability and booking meetings via Google Calendar",
    version="2.1.0"
)

# Google Calendar configuration
GOOGLE_CALENDAR_CREDENTIALS = os.getenv("GOOGLE_CALENDAR_CREDENTIALS")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID")
# Email of the user to impersonate (must be in your Google Workspace domain)
GOOGLE_IMPERSONATE_USER = os.getenv("GOOGLE_IMPERSONATE_USER")

# Initialize Google Calendar service
calendar_service = None


def get_calendar_service():
    """Get or create Google Calendar service with Domain-Wide Delegation."""
    global calendar_service
    
    if calendar_service:
        return calendar_service
    
    if not GOOGLE_CALENDAR_CREDENTIALS:
        return None
    
    try:
        # Parse credentials from environment variable
        creds_dict = json.loads(GOOGLE_CALENDAR_CREDENTIALS)
        credentials = service_account.Credentials.from_service_account_info(
            creds_dict,
            scopes=['https://www.googleapis.com/auth/calendar']
        )
        
        # Use Domain-Wide Delegation to impersonate a user
        if GOOGLE_IMPERSONATE_USER:
            credentials = credentials.with_subject(GOOGLE_IMPERSONATE_USER)
            print(f"✓ Impersonating user: {GOOGLE_IMPERSONATE_USER}")
        
        calendar_service = build('calendar', 'v3', credentials=credentials)
        return calendar_service
    except Exception as e:
        print(f"Failed to initialize Google Calendar service: {e}")
        return None


# ============================================================
# MODELS
# ============================================================

class CheckAvailabilityRequest(BaseModel):
    date_range_start: str  # ISO format date
    date_range_end: str


class BookMeetingRequest(BaseModel):
    datetime: str  # ISO format datetime
    attendee_email: str
    attendee_name: str
    notes: Optional[str] = None
    duration_minutes: Optional[int] = 30


class CancelMeetingRequest(BaseModel):
    booking_id: str  # Google Calendar event ID
    reason: Optional[str] = None


# ============================================================
# API ENDPOINTS
# ============================================================

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    service = get_calendar_service()
    return {
        "status": "healthy",
        "service": "mcp-calendar",
        "timestamp": datetime.utcnow().isoformat(),
        "google_calendar_configured": service is not None,
        "calendar_id": GOOGLE_CALENDAR_ID[:20] + "..." if GOOGLE_CALENDAR_ID else None,
        "impersonating": GOOGLE_IMPERSONATE_USER
    }


@app.post("/tools/check_availability")
async def check_availability(request: CheckAvailabilityRequest):
    """
    Check available time slots for booking.
    Returns list of available 30-minute slots within the date range.
    """
    try:
        service = get_calendar_service()
        
        # Parse dates
        try:
            start_date = datetime.fromisoformat(request.date_range_start.replace('Z', '+00:00'))
        except:
            start_date = datetime.strptime(request.date_range_start[:10], '%Y-%m-%d')
        
        try:
            end_date = datetime.fromisoformat(request.date_range_end.replace('Z', '+00:00'))
        except:
            end_date = datetime.strptime(request.date_range_end[:10], '%Y-%m-%d')
            end_date = end_date.replace(hour=23, minute=59)
        
        if not service:
            # Return mock availability for development
            slots = []
            current = start_date.replace(hour=9, minute=0, second=0, microsecond=0)
            
            while current < end_date:
                if current.weekday() < 5:  # Skip weekends
                    for hour in [9, 10, 11, 14, 15, 16]:
                        slot_start = current.replace(hour=hour)
                        slot_end = slot_start + timedelta(minutes=30)
                        
                        if slot_start >= start_date and slot_end <= end_date:
                            slots.append({
                                "start": slot_start.isoformat(),
                                "end": slot_end.isoformat(),
                                "available": True
                            })
                
                current += timedelta(days=1)
            
            return {
                "success": True,
                "available_slots": slots[:10],
                "mock": True
            }
        
        # Get existing events from Google Calendar
        events_result = service.events().list(
            calendarId=GOOGLE_CALENDAR_ID,
            timeMin=start_date.isoformat() + 'Z' if start_date.tzinfo is None else start_date.isoformat(),
            timeMax=end_date.isoformat() + 'Z' if end_date.tzinfo is None else end_date.isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        existing_events = events_result.get('items', [])
        
        # Build list of busy times
        busy_times = []
        for event in existing_events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))
            busy_times.append({
                'start': datetime.fromisoformat(start.replace('Z', '+00:00')),
                'end': datetime.fromisoformat(end.replace('Z', '+00:00'))
            })
        
        # Generate available slots (business hours: 9 AM - 5 PM, weekdays)
        available_slots = []
        current = start_date.replace(hour=9, minute=0, second=0, microsecond=0)
        
        while current < end_date:
            if current.weekday() < 5:  # Weekdays only
                for hour in [9, 10, 11, 13, 14, 15, 16]:  # Business hours with lunch break
                    for minute in [0, 30]:  # 30-minute slots
                        slot_start = current.replace(hour=hour, minute=minute)
                        slot_end = slot_start + timedelta(minutes=30)
                        
                        if slot_start < start_date or slot_end > end_date:
                            continue
                        
                        # Check if slot conflicts with any existing event
                        is_available = True
                        for busy in busy_times:
                            if (slot_start < busy['end'] and slot_end > busy['start']):
                                is_available = False
                                break
                        
                        if is_available:
                            available_slots.append({
                                "start": slot_start.isoformat(),
                                "end": slot_end.isoformat(),
                                "available": True
                            })
            
            current += timedelta(days=1)
        
        return {
            "success": True,
            "available_slots": available_slots[:15],  # Limit to 15 slots
            "total_available": len(available_slots)
        }
        
    except Exception as e:
        print(f"Check availability error: {e}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/tools/book_meeting")
async def book_meeting(request: BookMeetingRequest):
    """
    Book a demo meeting on Google Calendar.
    Checks availability first to prevent double-booking.
    Creates a Google Meet link and sends invitations.
    """
    try:
        service = get_calendar_service()
        
        # Parse the requested datetime
        try:
            meeting_start = datetime.fromisoformat(request.datetime.replace('Z', '+00:00'))
        except:
            # Try parsing without timezone
            meeting_start = datetime.strptime(request.datetime[:16], '%Y-%m-%dT%H:%M')
        
        meeting_end = meeting_start + timedelta(minutes=request.duration_minutes or 30)
        
        if not service:
            # Return mock booking for development
            booking_id = f"mock_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
            return {
                "success": True,
                "booking_id": booking_id,
                "datetime": request.datetime,
                "attendee_email": request.attendee_email,
                "attendee_name": request.attendee_name,
                "confirmation_message": f"Demo booked for {meeting_start.strftime('%B %d at %I:%M %p')}. Confirmation sent to {request.attendee_email}.",
                "mock": True
            }
        
        # Check for conflicts first
        events_result = service.events().list(
            calendarId=GOOGLE_CALENDAR_ID,
            timeMin=meeting_start.isoformat() + 'Z' if meeting_start.tzinfo is None else meeting_start.isoformat(),
            timeMax=meeting_end.isoformat() + 'Z' if meeting_end.tzinfo is None else meeting_end.isoformat(),
            singleEvents=True
        ).execute()
        
        existing_events = events_result.get('items', [])
        
        if existing_events:
            # Time slot is not available
            return {
                "success": False,
                "error": "This time slot is no longer available. Please choose another time.",
                "conflicts": len(existing_events)
            }
        
        # Create the calendar event with Google Meet
        event = {
            'summary': f'Vehicle Price Evaluator Demo - {request.attendee_name}',
            'description': f'''Demo meeting with {request.attendee_name}
            
Email: {request.attendee_email}
Notes: {request.notes or 'N/A'}

Booked via Voice Agent''',
            'start': {
                'dateTime': meeting_start.isoformat(),
                'timeZone': 'America/Chicago',  # Adjust timezone as needed
            },
            'end': {
                'dateTime': meeting_end.isoformat(),
                'timeZone': 'America/Chicago',
            },
            'attendees': [
                {'email': request.attendee_email, 'displayName': request.attendee_name},
            ],
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'email', 'minutes': 24 * 60},  # 1 day before
                    {'method': 'popup', 'minutes': 30},  # 30 minutes before
                ],
            },
            # Add Google Meet conference
            'conferenceData': {
                'createRequest': {
                    'requestId': f"meet-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
                    'conferenceSolutionKey': {'type': 'hangoutsMeet'}
                }
            },
        }
        
        # Insert the event with conference data
        created_event = service.events().insert(
            calendarId=GOOGLE_CALENDAR_ID,
            body=event,
            sendUpdates='all',  # Send email invitations to attendees
            conferenceDataVersion=1  # Required for Google Meet
        ).execute()
        
        # Get the Google Meet link
        meet_link = None
        if 'conferenceData' in created_event:
            for entry_point in created_event['conferenceData'].get('entryPoints', []):
                if entry_point.get('entryPointType') == 'video':
                    meet_link = entry_point.get('uri')
                    break
        
        return {
            "success": True,
            "booking_id": created_event.get('id'),
            "datetime": request.datetime,
            "attendee_email": request.attendee_email,
            "attendee_name": request.attendee_name,
            "calendar_link": created_event.get('htmlLink'),
            "meet_link": meet_link,
            "confirmation_message": f"Demo successfully booked for {meeting_start.strftime('%B %d at %I:%M %p')}. A calendar invitation with Google Meet link has been sent to {request.attendee_email}."
        }
        
    except Exception as e:
        print(f"Book meeting error: {e}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/tools/cancel_meeting")
async def cancel_meeting(request: CancelMeetingRequest):
    """Cancel an existing booking."""
    try:
        service = get_calendar_service()
        
        if not service:
            return {
                "success": True,
                "message": f"Booking {request.booking_id} cancelled",
                "mock": True
            }
        
        # Delete the event
        service.events().delete(
            calendarId=GOOGLE_CALENDAR_ID,
            eventId=request.booking_id,
            sendUpdates='all'  # Notify attendees
        ).execute()
        
        return {
            "success": True,
            "message": f"Booking {request.booking_id} has been cancelled. Attendees have been notified."
        }
        
    except Exception as e:
        print(f"Cancel meeting error: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@app.get("/tools/get_upcoming_meetings")
async def get_upcoming_meetings(days: int = 7):
    """Get upcoming meetings for the next N days."""
    try:
        service = get_calendar_service()
        
        if not service:
            return {
                "success": True,
                "meetings": [],
                "mock": True
            }
        
        now = datetime.utcnow()
        end_date = now + timedelta(days=days)
        
        events_result = service.events().list(
            calendarId=GOOGLE_CALENDAR_ID,
            timeMin=now.isoformat() + 'Z',
            timeMax=end_date.isoformat() + 'Z',
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        
        meetings = []
        for event in events:
            meetings.append({
                "id": event.get('id'),
                "summary": event.get('summary'),
                "start": event['start'].get('dateTime', event['start'].get('date')),
                "end": event['end'].get('dateTime', event['end'].get('date')),
                "attendees": [a.get('email') for a in event.get('attendees', [])]
            })
        
        return {
            "success": True,
            "meetings": meetings,
            "count": len(meetings)
        }
        
    except Exception as e:
        print(f"Get upcoming meetings error: {e}")
        return {
            "success": False,
            "error": str(e)
        }


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup_event():
    """Verify configuration on startup"""
    service = get_calendar_service()
    
    if service:
        print("✓ Google Calendar service initialized")
        print(f"✓ Calendar ID: {GOOGLE_CALENDAR_ID}")
        if GOOGLE_IMPERSONATE_USER:
            print(f"✓ Impersonating: {GOOGLE_IMPERSONATE_USER}")
        
        # Test the connection
        try:
            calendar = service.calendars().get(calendarId=GOOGLE_CALENDAR_ID).execute()
            print(f"✓ Connected to calendar: {calendar.get('summary', 'Unknown')}")
        except Exception as e:
            print(f"⚠ Could not verify calendar access: {e}")
    else:
        print("⚠ Google Calendar not configured - running in mock mode")
    
    print("✓ MCP Calendar Server started")
