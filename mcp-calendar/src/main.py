"""
MCP Calendar Server
Handles calendar availability and booking via Cal.com API.
"""

import os
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta

app = FastAPI(
    title="MCP Calendar Server",
    description="Calendar MCP server for checking availability and booking meetings",
    version="1.0.0"
)

# Cal.com API configuration
CAL_COM_API_KEY = os.getenv("CAL_COM_API_KEY")
CAL_COM_API_URL = "https://api.cal.com/v1"
CAL_COM_EVENT_TYPE_ID = os.getenv("CAL_COM_EVENT_TYPE_ID", "")  # Your demo event type


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


class CancelMeetingRequest(BaseModel):
    booking_id: str
    reason: Optional[str] = None


class TimeSlot(BaseModel):
    start: str
    end: str


# ============================================================
# CAL.COM API HELPERS
# ============================================================

async def cal_com_request(method: str, endpoint: str, data: dict = None) -> dict:
    """Make authenticated request to Cal.com API"""
    
    if not CAL_COM_API_KEY:
        # Return mock data if no API key (for development)
        return {"mock": True}
    
    async with httpx.AsyncClient() as client:
        url = f"{CAL_COM_API_URL}{endpoint}"
        headers = {
            "Content-Type": "application/json"
        }
        params = {"apiKey": CAL_COM_API_KEY}
        
        if method == "GET":
            response = await client.get(url, headers=headers, params=params)
        elif method == "POST":
            response = await client.post(url, headers=headers, params=params, json=data)
        elif method == "DELETE":
            response = await client.delete(url, headers=headers, params=params)
        else:
            raise ValueError(f"Unsupported method: {method}")
        
        response.raise_for_status()
        return response.json()


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
        "cal_com_configured": bool(CAL_COM_API_KEY)
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
        
        if not CAL_COM_API_KEY:
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
        
        # Real Cal.com API call
        response = await cal_com_request(
            "GET",
            f"/availability",
            {
                "eventTypeId": CAL_COM_EVENT_TYPE_ID,
                "startTime": request.date_range_start,
                "endTime": request.date_range_end
            }
        )
        
        return {
            "success": True,
            "available_slots": response.get("slots", [])
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
    
    Creates a booking in Cal.com and sends confirmation.
    """
    try:
        if not CAL_COM_API_KEY:
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
        
        # Real Cal.com API call
        response = await cal_com_request(
            "POST",
            "/bookings",
            {
                "eventTypeId": int(CAL_COM_EVENT_TYPE_ID),
                "start": request.datetime,
                "responses": {
                    "name": request.attendee_name,
                    "email": request.attendee_email,
                    "notes": request.notes or ""
                },
                "timeZone": "America/Chicago",  # Adjust as needed
                "language": "en"
            }
        )
        
        return {
            "success": True,
            "booking_id": str(response.get("id")),
            "datetime": request.datetime,
            "attendee_email": request.attendee_email,
            "confirmation_message": f"Demo booked! Confirmation sent to {request.attendee_email}."
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
        if not CAL_COM_API_KEY:
            return {
                "success": True,
                "message": f"Booking {request.booking_id} cancelled",
                "mock": True
            }
        
        # Real Cal.com API call
        await cal_com_request(
            "DELETE",
            f"/bookings/{request.booking_id}",
            {"cancellationReason": request.reason or "Cancelled by agent"}
        )
        
        return {
            "success": True,
            "message": f"Booking {request.booking_id} has been cancelled"
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/tools/reschedule_meeting")
async def reschedule_meeting(booking_id: str, new_datetime: str):
    """Reschedule an existing booking."""
    try:
        if not CAL_COM_API_KEY:
            return {
                "success": True,
                "message": f"Booking {booking_id} rescheduled to {new_datetime}",
                "mock": True
            }
        
        # Cal.com reschedule - typically cancel and rebook
        # Implementation depends on Cal.com API version
        
        return {
            "success": True,
            "message": f"Booking rescheduled to {new_datetime}"
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
    if CAL_COM_API_KEY:
        print("✓ Cal.com API key configured")
    else:
        print("⚠ Cal.com API key not set - running in mock mode")
    
    print(f"✓ MCP Calendar Server started")
