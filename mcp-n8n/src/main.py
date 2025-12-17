"""
MCP n8n Server
Bridge between voice agent and n8n workflows.
Triggers post-call automations via webhooks.
"""

import os
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime

app = FastAPI(
    title="MCP n8n Server",
    description="n8n webhook bridge for triggering post-call workflows",
    version="1.0.0"
)

# n8n webhook configuration
N8N_WEBHOOK_BASE_URL = os.getenv("N8N_WEBHOOK_BASE_URL", "")
N8N_WEBHOOK_SECRET = os.getenv("N8N_WEBHOOK_SECRET", "")

# Workflow webhook paths (configured in n8n)
WEBHOOKS = {
    "post_call_summary": os.getenv("N8N_WEBHOOK_POST_CALL", "/webhook/post-call-summary"),
    "demo_booked": os.getenv("N8N_WEBHOOK_DEMO_BOOKED", "/webhook/demo-booked"),
    "no_answer": os.getenv("N8N_WEBHOOK_NO_ANSWER", "/webhook/no-answer"),
    "not_interested": os.getenv("N8N_WEBHOOK_NOT_INTERESTED", "/webhook/not-interested"),
    "callback_requested": os.getenv("N8N_WEBHOOK_CALLBACK", "/webhook/callback-requested"),
}


# ============================================================
# MODELS
# ============================================================

class TriggerWorkflowRequest(BaseModel):
    workflow_id: str
    payload: Dict[str, Any]


class LogCallOutcomeRequest(BaseModel):
    call_id: str
    lead_id: Optional[str] = None
    outcome: str  # demo_booked, not_interested, callback_requested, no_answer, voicemail
    duration_seconds: Optional[int] = None
    transcript: Optional[str] = None
    summary: Optional[str] = None
    demo_datetime: Optional[str] = None
    attendee_email: Optional[str] = None
    notes: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None  # Full Vapi payload


# ============================================================
# N8N WEBHOOK HELPERS
# ============================================================

async def trigger_n8n_webhook(webhook_path: str, data: dict) -> dict:
    """Trigger an n8n webhook"""
    
    if not N8N_WEBHOOK_BASE_URL:
        # Return mock response if not configured
        return {
            "success": True,
            "mock": True,
            "message": f"Would trigger webhook: {webhook_path}"
        }
    
    url = f"{N8N_WEBHOOK_BASE_URL}{webhook_path}"
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        headers = {
            "Content-Type": "application/json"
        }
        
        # Add auth if configured
        if N8N_WEBHOOK_SECRET:
            headers["X-Webhook-Secret"] = N8N_WEBHOOK_SECRET
        
        response = await client.post(url, headers=headers, json=data)
        response.raise_for_status()
        
        return {
            "success": True,
            "status_code": response.status_code,
            "response": response.json() if response.text else {}
        }


# ============================================================
# API ENDPOINTS
# ============================================================

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "mcp-n8n",
        "timestamp": datetime.utcnow().isoformat(),
        "n8n_configured": bool(N8N_WEBHOOK_BASE_URL)
    }


@app.post("/tools/trigger_workflow")
async def trigger_workflow(request: TriggerWorkflowRequest):
    """
    Trigger a specific n8n workflow by ID/name.
    
    Generic endpoint for triggering any workflow.
    """
    try:
        webhook_path = WEBHOOKS.get(request.workflow_id)
        
        if not webhook_path:
            # Try using workflow_id as direct path
            webhook_path = f"/webhook/{request.workflow_id}"
        
        result = await trigger_n8n_webhook(webhook_path, request.payload)
        
        return {
            "success": True,
            "workflow_id": request.workflow_id,
            "result": result
        }
        
    except httpx.HTTPError as e:
        return {
            "success": False,
            "error": f"Failed to trigger workflow: {str(e)}"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/tools/log_call_outcome")
async def log_call_outcome(request: LogCallOutcomeRequest):
    """
    Log call outcome and trigger appropriate n8n workflow.
    
    This is the main endpoint called after each call ends.
    Routes to different workflows based on outcome.
    """
    try:
        # Build payload
        payload = {
            "call_id": request.call_id,
            "lead_id": request.lead_id,
            "outcome": request.outcome,
            "duration_seconds": request.duration_seconds,
            "transcript": request.transcript,
            "summary": request.summary,
            "notes": request.notes,
            "timestamp": datetime.utcnow().isoformat(),
        }
        
        # Add outcome-specific data
        if request.demo_datetime:
            payload["demo_datetime"] = request.demo_datetime
        if request.attendee_email:
            payload["attendee_email"] = request.attendee_email
        
        # Include full Vapi payload if provided
        if request.payload:
            payload["vapi_payload"] = request.payload
        
        # Determine which workflow to trigger based on outcome
        workflow_map = {
            "demo_booked": "demo_booked",
            "not_interested": "not_interested",
            "callback_requested": "callback_requested",
            "no_answer": "no_answer",
            "voicemail": "no_answer",
        }
        
        workflow_id = workflow_map.get(request.outcome, "post_call_summary")
        webhook_path = WEBHOOKS.get(workflow_id, WEBHOOKS["post_call_summary"])
        
        # Always trigger post-call summary (logging)
        summary_result = await trigger_n8n_webhook(
            WEBHOOKS["post_call_summary"],
            payload
        )
        
        # Also trigger outcome-specific workflow if different
        outcome_result = None
        if workflow_id != "post_call_summary":
            outcome_result = await trigger_n8n_webhook(webhook_path, payload)
        
        return {
            "success": True,
            "call_id": request.call_id,
            "outcome": request.outcome,
            "workflows_triggered": {
                "post_call_summary": summary_result,
                workflow_id: outcome_result
            }
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/tools/send_notification")
async def send_notification(
    channel: str,
    message: str,
    priority: str = "normal"
):
    """
    Send a notification via n8n (Slack, email, etc.)
    
    Useful for alerts about high-value bookings, issues, etc.
    """
    try:
        payload = {
            "channel": channel,  # e.g., "slack", "email"
            "message": message,
            "priority": priority,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        result = await trigger_n8n_webhook("/webhook/notification", payload)
        
        return {
            "success": True,
            "channel": channel,
            "result": result
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
    if N8N_WEBHOOK_BASE_URL:
        print(f"✓ n8n webhook base URL configured: {N8N_WEBHOOK_BASE_URL}")
    else:
        print("⚠ n8n webhook base URL not set - running in mock mode")
    
    print(f"✓ MCP n8n Server started")
    print(f"  Configured webhooks:")
    for name, path in WEBHOOKS.items():
        print(f"    - {name}: {path}")
