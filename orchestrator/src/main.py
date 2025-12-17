"""
Voice Agent Orchestrator
Main FastAPI server that handles Vapi webhooks and coordinates with MCP servers.
"""

import os
import json
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from anthropic import Anthropic
from datetime import datetime

app = FastAPI(
    title="Voice Agent Orchestrator",
    description="Coordinates voice AI conversations with Claude and MCP tools",
    version="1.0.0"
)

# Initialize Anthropic client
anthropic = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# MCP Server URLs (internal Railway networking)
MCP_CALENDAR_URL = os.getenv("MCP_CALENDAR_URL", "http://localhost:8001")
MCP_CRM_URL = os.getenv("MCP_CRM_URL", "http://localhost:8002")
MCP_N8N_URL = os.getenv("MCP_N8N_URL", "http://localhost:8003")

# In-memory conversation state (replace with Redis in production)
conversations: Dict[str, List[Dict]] = {}


# ============================================================
# MODELS
# ============================================================

class CallInitiateRequest(BaseModel):
    """Request to initiate an outbound call"""
    lead_id: str
    phone_number: str
    lead_name: Optional[str] = None
    company_name: Optional[str] = None


class VapiWebhookPayload(BaseModel):
    """Payload from Vapi webhook"""
    type: str
    call_id: Optional[str] = None
    transcript: Optional[str] = None
    message: Optional[Dict[str, Any]] = None


# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM_PROMPT = """You are Alex, a friendly and professional sales development representative for Cloud Store, calling on behalf of Vehicle Price Evaluator.

## YOUR GOAL
Book a product demo with the lead. You're calling equipment dealers who have shown interest in pricing tools.

## ABOUT VEHICLE PRICE EVALUATOR
- Real-time equipment valuation tool for dealers
- Covers heavy equipment: excavators, skid steers, tractors, forklifts
- Integrates with dealer management systems
- Provides market-accurate pricing for trade-ins and inventory

## CONVERSATION FLOW
1. **Opening**: Introduce yourself, confirm you're speaking with the right person
2. **Discovery**: Ask about their current pricing process and pain points
3. **Pitch**: Briefly explain how Vehicle Price Evaluator helps
4. **Handle Questions**: Answer any questions they have
5. **Book Demo**: If interested, offer to schedule a 15-minute demo
6. **Close**: Confirm details and thank them

## AVAILABLE TOOLS
- `check_availability`: Check calendar for demo slots
- `book_meeting`: Book a demo appointment
- `get_lead`: Get information about the lead you're calling
- `update_lead`: Update lead information in CRM
- `log_activity`: Log call notes and outcome

## GUIDELINES
- Be conversational and natural, not scripted
- Keep responses concise (this is a phone call)
- If they're not interested, be respectful and ask if you can follow up later
- If they ask something you don't know, offer to have a specialist follow up
- Always confirm email before booking a meeting

## OBJECTION HANDLING
- "I'm busy": "I completely understand. Would a quick 15-minute call later this week work better?"
- "We have a solution": "That's great! Many of our dealers use us alongside their existing tools. What solution are you using?"
- "Not interested": "No problem at all. Would it be okay if I sent you some information to review when you have time?"
- "How much does it cost?": "Pricing depends on your dealership size. The demo will cover that - it's only 15 minutes."
"""


# ============================================================
# MCP TOOL DEFINITIONS
# ============================================================

TOOLS = [
    {
        "name": "check_availability",
        "description": "Check available time slots for booking a demo. Call this when the lead agrees to a demo.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_range_start": {
                    "type": "string",
                    "description": "Start date for availability check (ISO format, e.g., 2024-01-15)"
                },
                "date_range_end": {
                    "type": "string",
                    "description": "End date for availability check (ISO format)"
                }
            },
            "required": ["date_range_start", "date_range_end"]
        }
    },
    {
        "name": "book_meeting",
        "description": "Book a demo meeting with the lead. Call this after confirming a time slot.",
        "input_schema": {
            "type": "object",
            "properties": {
                "datetime": {
                    "type": "string",
                    "description": "Meeting datetime (ISO format)"
                },
                "attendee_email": {
                    "type": "string",
                    "description": "Lead's email address"
                },
                "attendee_name": {
                    "type": "string",
                    "description": "Lead's full name"
                },
                "notes": {
                    "type": "string",
                    "description": "Any notes about the lead or their needs"
                }
            },
            "required": ["datetime", "attendee_email", "attendee_name"]
        }
    },
    {
        "name": "get_lead",
        "description": "Get information about the lead you're calling.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id": {
                    "type": "string",
                    "description": "The lead's ID in the CRM"
                }
            },
            "required": ["lead_id"]
        }
    },
    {
        "name": "update_lead",
        "description": "Update lead information in the CRM.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id": {
                    "type": "string",
                    "description": "The lead's ID"
                },
                "properties": {
                    "type": "object",
                    "description": "Properties to update (e.g., email, notes, status)"
                }
            },
            "required": ["lead_id", "properties"]
        }
    },
    {
        "name": "log_activity",
        "description": "Log call activity and notes to the CRM.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id": {
                    "type": "string",
                    "description": "The lead's ID"
                },
                "activity_type": {
                    "type": "string",
                    "enum": ["call_connected", "voicemail", "no_answer", "demo_booked", "not_interested", "callback_requested"],
                    "description": "Type of activity"
                },
                "notes": {
                    "type": "string",
                    "description": "Call notes and summary"
                }
            },
            "required": ["lead_id", "activity_type", "notes"]
        }
    }
]


# ============================================================
# MCP TOOL EXECUTION
# ============================================================

async def execute_tool(tool_name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
    """Execute an MCP tool by calling the appropriate server."""
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "check_availability":
                response = await client.post(
                    f"{MCP_CALENDAR_URL}/tools/check_availability",
                    json=tool_input
                )
            elif tool_name == "book_meeting":
                response = await client.post(
                    f"{MCP_CALENDAR_URL}/tools/book_meeting",
                    json=tool_input
                )
            elif tool_name == "get_lead":
                response = await client.post(
                    f"{MCP_CRM_URL}/tools/get_lead",
                    json=tool_input
                )
            elif tool_name == "update_lead":
                response = await client.post(
                    f"{MCP_CRM_URL}/tools/update_lead",
                    json=tool_input
                )
            elif tool_name == "log_activity":
                response = await client.post(
                    f"{MCP_CRM_URL}/tools/log_activity",
                    json=tool_input
                )
            else:
                return {"error": f"Unknown tool: {tool_name}"}
            
            response.raise_for_status()
            return response.json()
            
        except httpx.HTTPError as e:
            return {"error": f"Tool execution failed: {str(e)}"}


# ============================================================
# CLAUDE CONVERSATION
# ============================================================

async def get_claude_response(
    call_id: str,
    user_message: str,
    lead_context: Optional[Dict] = None
) -> str:
    """Get response from Claude, handling tool use if needed."""
    
    # Initialize conversation history if needed
    if call_id not in conversations:
        conversations[call_id] = []
        
        # Add lead context to first message if available
        if lead_context:
            context_msg = f"[CONTEXT: Calling {lead_context.get('name', 'a lead')} at {lead_context.get('company', 'their company')}. Lead ID: {lead_context.get('id', 'unknown')}]"
            conversations[call_id].append({
                "role": "user",
                "content": context_msg
            })
            conversations[call_id].append({
                "role": "assistant", 
                "content": "Understood, I have the lead context. Ready to make the call."
            })
    
    # Add user message to history
    conversations[call_id].append({
        "role": "user",
        "content": user_message
    })
    
    # Call Claude
    response = anthropic.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,  # Keep responses short for voice
        system=SYSTEM_PROMPT,
        tools=TOOLS,
        messages=conversations[call_id]
    )
    
    # Handle tool use
    while response.stop_reason == "tool_use":
        # Extract tool calls
        tool_calls = [block for block in response.content if block.type == "tool_use"]
        
        # Add assistant response to history
        conversations[call_id].append({
            "role": "assistant",
            "content": response.content
        })
        
        # Execute tools and collect results
        tool_results = []
        for tool_call in tool_calls:
            result = await execute_tool(tool_call.name, tool_call.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_call.id,
                "content": json.dumps(result)
            })
        
        # Add tool results to history
        conversations[call_id].append({
            "role": "user",
            "content": tool_results
        })
        
        # Get next response from Claude
        response = anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=conversations[call_id]
        )
    
    # Extract text response
    text_response = ""
    for block in response.content:
        if hasattr(block, "text"):
            text_response += block.text
    
    # Add final response to history
    conversations[call_id].append({
        "role": "assistant",
        "content": text_response
    })
    
    return text_response


# ============================================================
# API ENDPOINTS
# ============================================================

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "orchestrator",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.post("/vapi/webhook")
async def vapi_webhook(request: Request):
    """
    Handle Vapi webhook events.
    
    Vapi sends events for:
    - assistant-request: Get assistant config
    - conversation-update: New transcript
    - end-of-call-report: Call completed
    """
    payload = await request.json()
    event_type = payload.get("message", {}).get("type", "")
    
    if event_type == "assistant-request":
        # Return assistant configuration
        return {
            "assistant": {
                "model": {
                    "provider": "custom-llm",
                    "url": f"{request.base_url}vapi/chat",
                    "model": "claude-sonnet-4-20250514"
                },
                "voice": {
                    "provider": "11labs",
                    "voiceId": "21m00Tcm4TlvDq8ikWAM"  # Rachel voice
                },
                "firstMessage": "Hi, this is Alex from Cloud Store. Am I speaking with the right person?",
                "transcriber": {
                    "provider": "deepgram",
                    "model": "nova-2"
                }
            }
        }
    
    elif event_type == "end-of-call-report":
        # Call ended - trigger post-call workflow
        call_id = payload.get("message", {}).get("call", {}).get("id")
        
        # Clean up conversation state
        if call_id in conversations:
            del conversations[call_id]
        
        # Trigger n8n workflow for post-call processing
        async with httpx.AsyncClient() as client:
            try:
                await client.post(
                    f"{MCP_N8N_URL}/tools/log_call_outcome",
                    json={
                        "call_id": call_id,
                        "payload": payload
                    }
                )
            except Exception as e:
                print(f"Failed to trigger post-call workflow: {e}")
        
        return {"status": "received"}
    
    return {"status": "received"}


@app.post("/vapi/chat")
async def vapi_chat(request: Request):
    """
    Handle Vapi chat requests (custom LLM endpoint).
    
    Vapi sends the conversation and expects a response.
    """
    payload = await request.json()
    
    call_id = payload.get("call", {}).get("id", "unknown")
    messages = payload.get("messages", [])
    
    # Get the latest user message
    user_message = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user_message = msg.get("content", "")
            break
    
    if not user_message:
        return {"content": "I didn't catch that. Could you repeat?"}
    
    # Get response from Claude
    response = await get_claude_response(call_id, user_message)
    
    return {"content": response}


@app.post("/call/initiate")
async def initiate_call(request: CallInitiateRequest):
    """
    Initiate an outbound call via Vapi.
    
    This endpoint is called to start a new call to a lead.
    """
    vapi_api_key = os.getenv("VAPI_API_KEY")
    if not vapi_api_key:
        raise HTTPException(status_code=500, detail="VAPI_API_KEY not configured")
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "https://api.vapi.ai/call/phone",
                headers={
                    "Authorization": f"Bearer {vapi_api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "phoneNumberId": os.getenv("VAPI_PHONE_NUMBER_ID"),
                    "customer": {
                        "number": request.phone_number,
                        "name": request.lead_name
                    },
                    "assistant": {
                        "model": {
                            "provider": "custom-llm",
                            "url": os.getenv("ORCHESTRATOR_URL", "http://localhost:8000") + "/vapi/chat"
                        },
                        "voice": {
                            "provider": "11labs",
                            "voiceId": "21m00Tcm4TlvDq8ikWAM"
                        },
                        "firstMessage": f"Hi, this is Alex from Cloud Store. Am I speaking with {request.lead_name or 'the right person'}?"
                    },
                    "metadata": {
                        "lead_id": request.lead_id,
                        "company_name": request.company_name
                    }
                }
            )
            response.raise_for_status()
            return response.json()
            
        except httpx.HTTPError as e:
            raise HTTPException(status_code=500, detail=f"Failed to initiate call: {str(e)}")


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup_event():
    """Verify configuration on startup"""
    required_vars = ["ANTHROPIC_API_KEY"]
    missing = [var for var in required_vars if not os.getenv(var)]
    
    if missing:
        print(f"WARNING: Missing environment variables: {missing}")
    else:
        print("✓ All required environment variables configured")
    
    print(f"✓ Orchestrator started")
    print(f"  - MCP Calendar: {MCP_CALENDAR_URL}")
    print(f"  - MCP CRM: {MCP_CRM_URL}")
    print(f"  - MCP n8n: {MCP_N8N_URL}")
