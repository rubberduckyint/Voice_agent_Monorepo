"""
MCP Calendar Server
Handles calendar availability and booking via Calendly API.
"""

import os
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta

app = FastAPI(
    title="MCP Calendar Server",
    description="Calendar MCP server for checking availability and booking meetings via Calendly",
    version="1.0.0"
)

# Calendly API configuration
CALENDLY_API_KEY = os.getenv("CALENDLY_API_KEY")
CALENDLY_API_URL = "https://api.calendly.com"
CALENDLY_EVENT_TYPE_URI = os.getenv("CALENDLY_EVENT_TYPE_URI", "")  # Your event type URI


# ============================================================
# MODELS
# ============================================================

class CheckAvailabilityRequest(BaseModel):
    date_range_start: str  # ISO format date
    date_range_end: str


class BookMeetingRequest(BaseModel):
    datetime: str  # ISO format datetime (must be an available slot)
    attendee_email: str
    attendee_name: str
    notes: Optional[str] = None


class CancelMeetingRequest(BaseModel):
    booking_id: str  # Calendly event UUID
    reason: Optional[str] = None


# ============================================================
# CALENDLY API HELPERS
# ============================================================

async def calendly_request(method: str, endpoint: str, data: dict = None, params: dict = None) -> dict:
    """Make authenticated request to Calendly API"""
    
    if not CALENDLY_API_KEY:
        # Return mock data if no API key (for development)
        return {"mock": True}
    
    async with httpx.AsyncClient() as client:
        url = f"{CALENDLY_API_URL}{endpoint}"
        headers = {
            "Authorization": f"Bearer {CALENDLY_API_KEY}",
            "Content-Type": "application/json"
        }
        
        if method == "GET":
            response = await client.get(url, headers=headers, params=params)
        elif method == "POST":
            response = await client.post(url, headers=headers, json=data)
        elif method == "DELETE":
            response = await client.delete(url, headers=headers)
        else:
            raise ValueError(f"Unsupported method: {method}")
        
        response.raise_for_status()
        return response.json()


async def get_current_user() -> dict:
    """Get the current Calendly user info"""
    response = await calendly_request("GET", "/users/me")
    return response.get("resource", {})


async def get_event_types(user_uri: str) -> list:
    """Get event types for the user"""
    response = await calendly_request(
        "GET", 
        "/event_types",
        params={"user": user_uri, "active": "true"}
    )
    return response.get("collection", [])


# ============================================================
# API ENDPOINTS
# ============================================================

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "mcp-calendar",
        "timestamp": datetime.utcnow().isoformat(),
        "calendly_configured": bool(CALENDLY_API_KEY)
    }


@app.post("/tools/check_availability")
async def check_availability(request: CheckAvailabilityRequest):
    """
    Check available time slots for booking.
    
    Returns list of available slots within the date range.
    """
    try:
        # Parse dates
        start_date = datetime.fromisoformat(request.date_range_start.replace('Z', '+00:00'))
        end_date = datetime.fromisoformat(request.date_range_end.replace('Z', '+00:00'))
        
        if not CALENDLY_API_KEY:
            # Return mock availability for development
            slots = []
            current = start_date.replace(hour=9, minute=0, second=0, microsecond=0)
            
            while current < end_date:
                # Skip weekends
                if current.weekday() < 5:
                    # Add morning and afternoon slots
                    for hour in [9, 10, 11, 14, 15, 16]:
                        slot_start = current.replace(hour=hour)
                        slot_end = slot_start + timedelta(minutes=30)
                        
                        if slot_start >= start_date and slot_end <= end_date:
                            slots.append({
                                "start": slot_start.isoformat(),
                                "end": slot_end.isoformat()
                            })
                
                current += timedelta(days=1)
            
            return {
                "success": True,
                "available_slots": slots[:10],  # Limit to 10 slots
                "mock": True
            }
        
        # Get user info to find event types
        user = await get_current_user()
        user_uri = user.get("uri")
        
        # Get event types
        event_types = await get_event_types(user_uri)
        
        if not event_types:
            return {
                "success": False,
                "error": "No active event types found in Calendly"
            }
        
        # Use the first event type or the configured one
        event_type_uri = CALENDLY_EVENT_TYPE_URI or event_types[0].get("uri")
        
        # Get available times
        response = await calendly_request(
            "GET",
            "/event_type_available_times",
            params={
                "event_type": event_type_uri,
                "start_time": request.date_range_start,
                "end_time": request.date_range_end
            }
        )
        
        slots = []
        for slot in response.get("collection", []):
            slots.append({
                "start": slot.get("start_time"),
                "end": None,  # Calendly doesn't return end time in availability
                "status": slot.get("status")
            })
        
        return {
            "success": True,
            "available_slots": slots,
            "event_type": event_type_uri
        }
        
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "error": f"Calendly API error: {e.response.status_code} - {e.response.text}"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/tools/book_meeting")
async def book_meeting(request: BookMeetingRequest):
    """
    Book a demo meeting.
    
    Note: Calendly doesn't support direct booking via API for most plans.
    This returns a scheduling link instead.
    """
    try:
        if not CALENDLY_API_KEY:
            # Return mock booking for development
            booking_id = f"mock_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
            return {
                "success": True,
                "booking_id": booking_id,
                "datetime": request.datetime,
                "attendee_email": request.attendee_email,
                "attendee_name": request.attendee_name,
                "confirmation_message": f"Demo booked for {request.datetime}. Confirmation sent to {request.attendee_email}.",
                "mock": True
            }
        
        # Get user info
        user = await get_current_user()
        user_uri = user.get("uri")
        
        # Get event types to find scheduling link
        event_types = await get_event_types(user_uri)
        
        if not event_types:
            return {
                "success": False,
                "error": "No active event types found"
            }
        
        event_type = event_types[0]
        scheduling_url = event_type.get("scheduling_url")
        
        # Calendly API doesn't support direct booking on most plans
        # Return the scheduling link with prefilled info
        prefilled_url = f"{scheduling_url}?name={request.attendee_name}&email={request.attendee_email}"
        
        if request.notes:
            prefilled_url += f"&a1={request.notes}"
        
        return {
            "success": True,
            "booking_method": "scheduling_link",
            "scheduling_url": prefilled_url,
            "attendee_email": request.attendee_email,
            "attendee_name": request.attendee_name,
            "message": f"Please use this link to book: {prefilled_url}",
            "note": "Direct API booking requires Calendly Enterprise plan"
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/tools/cancel_meeting")
async def cancel_meeting(request: CancelMeetingRequest):
    """Cancel an existing booking."""
    try:
        if not CALENDLY_API_KEY:
            return {
                "success": True,
                "message": f"Booking {request.booking_id} cancelled",
                "mock": True
            }
        
        # Cancel the scheduled event
        await calendly_request(
            "POST",
            f"/scheduled_events/{request.booking_id}/cancellation",
            data={"reason": request.reason or "Cancelled by agent"}
        )
        
        return {
            "success": True,
            "message": f"Booking {request.booking_id} has been cancelled"
        }
        
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "error": f"Failed to cancel: {e.response.status_code}"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.get("/tools/get_scheduled_events")
async def get_scheduled_events(
    min_start_time: Optional[str] = None,
    max_start_time: Optional[str] = None,
    status: str = "active"
):
    """Get list of scheduled events."""
    try:
        if not CALENDLY_API_KEY:
            return {
                "success": True,
                "events": [],
                "mock": True
            }
        
        user = await get_current_user()
        user_uri = user.get("uri")
        
        params = {
            "user": user_uri,
            "status": status
        }
        
        if min_start_time:
            params["min_start_time"] = min_start_time
        if max_start_time:
            params["max_start_time"] = max_start_time
        
        response = await calendly_request(
            "GET",
            "/scheduled_events",
            params=params
        )
        
        events = []
        for event in response.get("collection", []):
            events.append({
                "id": event.get("uri", "").split("/")[-1],
                "name": event.get("name"),
                "start_time": event.get("start_time"),
                "end_time": event.get("end_time"),
                "status": event.get("status"),
                "location": event.get("location", {}).get("location")
            })
        
        return {
            "success": True,
            "events": events
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.get("/tools/get_scheduling_link")
async def get_scheduling_link():
    """Get the scheduling link for the default event type."""
    try:
        if not CALENDLY_API_KEY:
            return {
                "success": True,
                "scheduling_url": "https://calendly.com/your-link",
                "mock": True
            }
        
        user = await get_current_user()
        user_uri = user.get("uri")
        
        event_types = await get_event_types(user_uri)
        
        if not event_types:
            return {
                "success": False,
                "error": "No active event types found"
            }
        
        # Return the first active event type's scheduling URL
        event_type = event_types[0]
        
        return {
            "success": True,
            "scheduling_url": event_type.get("scheduling_url"),
            "event_type_name": event_type.get("name"),
            "duration_minutes": event_type.get("duration")
        }
        
    except Exception as e:
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
    if CALENDLY_API_KEY:
        print("✓ Calendly API key configured")
        try:
            user = await get_current_user()
            print(f"✓ Connected to Calendly as: {user.get('name', 'Unknown')}")
        except Exception as e:
            print(f"⚠ Could not verify Calendly connection: {e}")
    else:
        print("⚠ Calendly API key not set - running in mock mode")
    
    print(f"✓ MCP Calendar Server started")
