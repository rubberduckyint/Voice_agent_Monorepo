"""
MCP CRM Server
Handles CRM operations via HubSpot API.
"""

import os
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from datetime import datetime

app = FastAPI(
    title="MCP CRM Server",
    description="CRM MCP server for lead management via HubSpot",
    version="1.0.0"
)

# HubSpot API configuration
HUBSPOT_ACCESS_TOKEN = os.getenv("HUBSPOT_ACCESS_TOKEN")
HUBSPOT_API_URL = "https://api.hubapi.com"


# ============================================================
# MODELS
# ============================================================

class GetLeadRequest(BaseModel):
    lead_id: str


class UpdateLeadRequest(BaseModel):
    lead_id: str
    properties: Dict[str, Any]


class LogActivityRequest(BaseModel):
    lead_id: str
    activity_type: str  # call_connected, voicemail, no_answer, demo_booked, not_interested, callback_requested
    notes: str


class CreateDealRequest(BaseModel):
    lead_id: str
    deal_name: str
    amount: Optional[float] = None
    stage: Optional[str] = "appointmentscheduled"


# ============================================================
# HUBSPOT API HELPERS
# ============================================================

async def hubspot_request(method: str, endpoint: str, data: dict = None) -> dict:
    """Make authenticated request to HubSpot API"""
    
    if not HUBSPOT_ACCESS_TOKEN:
        # Return mock data if no API key (for development)
        return {"mock": True}
    
    async with httpx.AsyncClient() as client:
        url = f"{HUBSPOT_API_URL}{endpoint}"
        headers = {
            "Authorization": f"Bearer {HUBSPOT_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        
        if method == "GET":
            response = await client.get(url, headers=headers)
        elif method == "POST":
            response = await client.post(url, headers=headers, json=data)
        elif method == "PATCH":
            response = await client.patch(url, headers=headers, json=data)
        elif method == "DELETE":
            response = await client.delete(url, headers=headers)
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
        "service": "mcp-crm",
        "timestamp": datetime.utcnow().isoformat(),
        "hubspot_configured": bool(HUBSPOT_ACCESS_TOKEN)
    }


@app.post("/tools/get_lead")
async def get_lead(request: GetLeadRequest):
    """
    Get lead/contact details from HubSpot.
    
    Returns contact properties including name, email, phone, company.
    """
    try:
        if not HUBSPOT_ACCESS_TOKEN:
            # Return mock data for development
            return {
                "success": True,
                "lead": {
                    "id": request.lead_id,
                    "firstname": "John",
                    "lastname": "Smith",
                    "email": "john.smith@example.com",
                    "phone": "+1-555-123-4567",
                    "company": "Smith Equipment Co",
                    "jobtitle": "Owner",
                    "lifecyclestage": "lead",
                    "notes": "Interested in pricing tools for their dealership"
                },
                "mock": True
            }
        
        # Real HubSpot API call
        response = await hubspot_request(
            "GET",
            f"/crm/v3/objects/contacts/{request.lead_id}",
        )
        
        return {
            "success": True,
            "lead": response.get("properties", {})
        }
        
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return {
                "success": False,
                "error": "Lead not found"
            }
        return {
            "success": False,
            "error": str(e)
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/tools/update_lead")
async def update_lead(request: UpdateLeadRequest):
    """
    Update lead/contact properties in HubSpot.
    
    Can update any standard or custom properties.
    """
    try:
        if not HUBSPOT_ACCESS_TOKEN:
            return {
                "success": True,
                "message": f"Lead {request.lead_id} updated",
                "updated_properties": request.properties,
                "mock": True
            }
        
        # Real HubSpot API call
        response = await hubspot_request(
            "PATCH",
            f"/crm/v3/objects/contacts/{request.lead_id}",
            {"properties": request.properties}
        )
        
        return {
            "success": True,
            "message": f"Lead {request.lead_id} updated successfully",
            "updated_properties": request.properties
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/tools/log_activity")
async def log_activity(request: LogActivityRequest):
    """
    Log a call activity to HubSpot.
    
    Creates an engagement/activity record associated with the contact.
    """
    try:
        # Map activity types to HubSpot call dispositions
        disposition_map = {
            "call_connected": "CONNECTED",
            "voicemail": "LEFT_VOICEMAIL",
            "no_answer": "NO_ANSWER",
            "demo_booked": "CONNECTED",
            "not_interested": "CONNECTED",
            "callback_requested": "CONNECTED"
        }
        
        disposition = disposition_map.get(request.activity_type, "CONNECTED")
        
        if not HUBSPOT_ACCESS_TOKEN:
            return {
                "success": True,
                "message": f"Activity logged for lead {request.lead_id}",
                "activity_type": request.activity_type,
                "notes": request.notes,
                "mock": True
            }
        
        # Create call engagement in HubSpot
        engagement_data = {
            "properties": {
                "hs_timestamp": datetime.utcnow().isoformat() + "Z",
                "hs_call_body": request.notes,
                "hs_call_disposition": disposition,
                "hs_call_direction": "OUTBOUND",
                "hs_call_status": "COMPLETED",
                "hs_call_title": f"Voice Agent Call - {request.activity_type}"
            },
            "associations": [
                {
                    "to": {"id": request.lead_id},
                    "types": [
                        {
                            "associationCategory": "HUBSPOT_DEFINED",
                            "associationTypeId": 194  # Call to Contact
                        }
                    ]
                }
            ]
        }
        
        response = await hubspot_request(
            "POST",
            "/crm/v3/objects/calls",
            engagement_data
        )
        
        # Also update contact lifecycle stage if demo booked
        if request.activity_type == "demo_booked":
            await hubspot_request(
                "PATCH",
                f"/crm/v3/objects/contacts/{request.lead_id}",
                {"properties": {"lifecyclestage": "salesqualifiedlead"}}
            )
        
        return {
            "success": True,
            "message": f"Activity logged for lead {request.lead_id}",
            "activity_type": request.activity_type,
            "engagement_id": response.get("id")
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/tools/create_deal")
async def create_deal(request: CreateDealRequest):
    """
    Create a deal/opportunity in HubSpot associated with the contact.
    """
    try:
        if not HUBSPOT_ACCESS_TOKEN:
            return {
                "success": True,
                "deal_id": f"mock_deal_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
                "deal_name": request.deal_name,
                "mock": True
            }
        
        # Create deal
        deal_data = {
            "properties": {
                "dealname": request.deal_name,
                "dealstage": request.stage or "appointmentscheduled",
                "pipeline": "default",
                "amount": str(request.amount) if request.amount else None
            },
            "associations": [
                {
                    "to": {"id": request.lead_id},
                    "types": [
                        {
                            "associationCategory": "HUBSPOT_DEFINED",
                            "associationTypeId": 3  # Deal to Contact
                        }
                    ]
                }
            ]
        }
        
        response = await hubspot_request(
            "POST",
            "/crm/v3/objects/deals",
            deal_data
        )
        
        return {
            "success": True,
            "deal_id": response.get("id"),
            "deal_name": request.deal_name
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/tools/get_lead_history")
async def get_lead_history(request: GetLeadRequest):
    """
    Get activity history for a lead.
    
    Returns recent engagements (calls, emails, meetings).
    """
    try:
        if not HUBSPOT_ACCESS_TOKEN:
            return {
                "success": True,
                "lead_id": request.lead_id,
                "activities": [
                    {
                        "type": "call",
                        "date": "2024-01-10T14:30:00Z",
                        "notes": "Initial outreach - left voicemail"
                    },
                    {
                        "type": "email",
                        "date": "2024-01-08T10:00:00Z",
                        "notes": "Sent intro email"
                    }
                ],
                "mock": True
            }
        
        # Get engagements associated with contact
        response = await hubspot_request(
            "GET",
            f"/crm/v3/objects/contacts/{request.lead_id}/associations/calls"
        )
        
        return {
            "success": True,
            "lead_id": request.lead_id,
            "activities": response.get("results", [])
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
    if HUBSPOT_ACCESS_TOKEN:
        print("✓ HubSpot access token configured")
    else:
        print("⚠ HubSpot access token not set - running in mock mode")
    
    print(f"✓ MCP CRM Server started")
