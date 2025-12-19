"""
Voice Agent Orchestrator
Main FastAPI server that handles Vapi webhooks and coordinates with MCP servers.
With proper tool calling support and availability checking.
"""

import os
import json
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from anthropic import Anthropic
from datetime import datetime, timedelta
import asyncio

app = FastAPI(
    title="Voice Agent Orchestrator",
    description="Coordinates voice AI conversations with Claude and MCP tools",
    version="1.1.0"
)

# Initialize Anthropic client
anthropic = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# MCP Server URLs (internal Railway networking)
MCP_CALENDAR_URL = os.getenv("MCP_CALENDAR_URL", "http://localhost:8001")
MCP_CRM_URL = os.getenv("MCP_CRM_URL", "http://localhost:8002")
MCP_N8N_URL = os.getenv("MCP_N8N_URL", "http://localhost:8003")


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

## CRITICAL - USING TOOL RESULTS
When you receive tool results, you MUST pay attention to them:

### For check_availability:
- If `available_slots` is empty or `total_available` is 0: Tell the caller that time is NOT available and suggest a different day
- If slots are returned: Offer those specific times to the caller
- NEVER say a time is available if the tool shows no availability

### For book_meeting:
- If `success` is true: Confirm the booking to the caller
- If `success` is false: Tell the caller there was an issue and try a different time
- NEVER confirm a booking if the tool returned success: false

## IMPORTANT - BOOKING MEETINGS
When the user agrees to book a meeting:
1. First use check_availability to verify the time slot is open
2. Collect their name and email
3. Use book_meeting to schedule it
4. Only confirm if the booking was successful

## GUIDELINES
- Be conversational and natural, not scripted
- Keep responses concise (this is a phone call, aim for 1-2 sentences)
- If they're not interested, be respectful and ask if you can follow up later
- Always get their email address before booking
- ALWAYS check tool results before responding

## OBJECTION HANDLING
- "I'm busy": "I completely understand. Would a quick 15-minute call later this week work better?"
- "We have a solution": "That's great! Many of our dealers use us alongside their existing tools."
- "Not interested": "No problem at all. Would it be okay if I sent you some information?"
- "How much does it cost?": "Pricing depends on your dealership size. The demo will cover that."
"""


# ============================================================
# TOOL DEFINITIONS FOR CLAUDE
# ============================================================

TOOLS = [
    {
        "name": "check_availability",
        "description": "Check available time slots for booking. ALWAYS use this before booking to verify the requested time is available. Returns available_slots array - if empty, that time is NOT available.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_range_start": {
                    "type": "string",
                    "description": "Start date in ISO format (e.g., 2024-12-18)"
                },
                "date_range_end": {
                    "type": "string",
                    "description": "End date in ISO format (e.g., 2024-12-20)"
                }
            },
            "required": ["date_range_start", "date_range_end"]
        }
    },
    {
        "name": "book_meeting",
        "description": "Book a demo meeting with the lead. Only use AFTER confirming availability. Check the 'success' field in the response - only confirm booking if success is true.",
        "input_schema": {
            "type": "object",
            "properties": {
                "datetime": {
                    "type": "string",
                    "description": "The meeting datetime in ISO format (e.g., 2024-12-18T10:00:00). Convert relative times like 'tomorrow at 10am' to actual dates."
                },
                "attendee_email": {
                    "type": "string",
                    "description": "The lead's email address"
                },
                "attendee_name": {
                    "type": "string",
                    "description": "The lead's full name"
                }
            },
            "required": ["datetime", "attendee_email", "attendee_name"]
        }
    }
]


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def get_current_datetime_context():
    """Get current date/time for context"""
    now = datetime.utcnow()
    tomorrow = now + timedelta(days=1)
    return f"Current date/time is {now.strftime('%Y-%m-%d %H:%M')} UTC. Tomorrow is {tomorrow.strftime('%Y-%m-%d')}."


async def execute_tool(tool_name: str, tool_input: Dict[str, Any]) -> str:
    """Execute a tool and return the result as a string."""
    
    print(f"Executing tool: {tool_name} with input: {tool_input}")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "check_availability":
                response = await client.post(
                    f"{MCP_CALENDAR_URL}/tools/check_availability",
                    json=tool_input
                )
                result = response.json()
                print(f"check_availability result: {result}")
                
                if result.get("success"):
                    available_slots = result.get("available_slots", [])
                    total_available = result.get("total_available", len(available_slots))
                    
                    if total_available == 0 or len(available_slots) == 0:
                        # NO AVAILABILITY - be explicit about this
                        return "NO_AVAILABILITY: There are no available time slots for the requested date. Please ask the caller to try a different day or time."
                    else:
                        # Format available slots nicely
                        slots = available_slots[:5]  # Limit to 5 slots
                        slot_strings = []
                        for s in slots:
                            start = s.get("start", "")[:16]
                            # Parse and format nicely
                            try:
                                dt = datetime.fromisoformat(start)
                                slot_strings.append(dt.strftime("%I:%M %p"))
                            except:
                                slot_strings.append(start.replace("T", " at "))
                        return f"AVAILABLE_SLOTS: The following times are available: {', '.join(slot_strings)}. Total available slots: {total_available}"
                else:
                    error = result.get("error", "Unknown error")
                    return f"ERROR checking availability: {error}. Ask the caller to try again or suggest a different time."
                    
            elif tool_name == "book_meeting":
                response = await client.post(
                    f"{MCP_CALENDAR_URL}/tools/book_meeting",
                    json=tool_input
                )
                result = response.json()
                print(f"book_meeting result: {result}")
                
                # CRITICAL: Check success field
                if result.get("success") == True:
                    booking_id = result.get("booking_id", "confirmed")
                    meet_link = result.get("meet_link", "")
                    return f"BOOKING_SUCCESS: Meeting successfully booked! Booking ID: {booking_id}. A calendar invitation with Google Meet link has been sent to {tool_input.get('attendee_email')}."
                else:
                    # BOOKING FAILED - be explicit
                    error = result.get("error", "Unknown error")
                    return f"BOOKING_FAILED: Could not book the meeting. Reason: {error}. Please apologize to the caller and try a different time."
                    
            else:
                return f"ERROR: Tool {tool_name} not recognized."
                
        except Exception as e:
            print(f"Tool execution error: {e}")
            import traceback
            traceback.print_exc()
            return f"ERROR: Failed to execute {tool_name}: {str(e)}. Please apologize and offer to have someone follow up."


# ============================================================
# CLAUDE CONVERSATION WITH TOOLS
# ============================================================

async def get_claude_response(messages: List[Dict]) -> Dict:
    """
    Get response from Claude with tool support.
    Returns either a text response or a tool call.
    """
    
    try:
        # Add datetime context to help Claude with relative dates
        datetime_context = get_current_datetime_context()
        enhanced_system = f"{SYSTEM_PROMPT}\n\n## CURRENT TIME\n{datetime_context}"
        
        # Convert messages to Claude format
        claude_messages = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            
            if role == "user" and content:
                claude_messages.append({"role": "user", "content": content})
            elif role == "assistant" and content:
                claude_messages.append({"role": "assistant", "content": content})
            elif role == "tool":
                # Handle tool results
                claude_messages.append({
                    "role": "user",
                    "content": f"Tool result: {content}"
                })
        
        if not claude_messages:
            claude_messages = [{"role": "user", "content": "Hello"}]
        
        print(f"Calling Claude with {len(claude_messages)} messages")
        
        # Call Claude with tools
        response = anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            system=enhanced_system,
            tools=TOOLS,
            messages=claude_messages
        )
        
        print(f"Claude response stop_reason: {response.stop_reason}")
        
        # Check if Claude wants to use a tool
        if response.stop_reason == "tool_use":
            for block in response.content:
                if block.type == "tool_use":
                    tool_name = block.name
                    tool_input = block.input
                    
                    print(f"Claude wants to use tool: {tool_name}")
                    
                    # Execute the tool
                    tool_result = await execute_tool(tool_name, tool_input)
                    
                    print(f"Tool result: {tool_result}")
                    
                    # Add the tool use and result to messages and get final response
                    claude_messages.append({
                        "role": "assistant",
                        "content": response.content
                    })
                    claude_messages.append({
                        "role": "user",
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": tool_result
                        }]
                    })
                    
                    # Get Claude's response after tool use
                    final_response = anthropic.messages.create(
                        model="claude-sonnet-4-20250514",
                        max_tokens=300,
                        system=enhanced_system,
                        tools=TOOLS,
                        messages=claude_messages
                    )
                    
                    # Extract text from final response
                    text = ""
                    for block in final_response.content:
                        if hasattr(block, "text"):
                            text += block.text
                    
                    return {"type": "text", "content": text or "I apologize, there was an issue. Let me try to help you another way."}
        
        # Extract text response
        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text += block.text
        
        return {"type": "text", "content": text or "I'm sorry, could you repeat that?"}
        
    except Exception as e:
        print(f"Claude API error: {e}")
        import traceback
        traceback.print_exc()
        return {"type": "text", "content": "I'm having a brief technical issue. Could you give me just a moment?"}


# ============================================================
# API ENDPOINTS
# ============================================================

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "orchestrator",
        "version": "1.1.0",
        "timestamp": datetime.utcnow().isoformat()
    }


# ============================================================
# OPENAI-COMPATIBLE CHAT COMPLETIONS ENDPOINT
# ============================================================

@app.post("/vapi/chat/completions")
@app.post("/chat/completions")
@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """
    OpenAI-compatible chat completions endpoint for Vapi.
    """
    try:
        body = await request.json()
        messages = body.get("messages", [])
        stream = body.get("stream", False)
        
        print(f"Received chat completion request with {len(messages)} messages")
        
        # Get response from Claude (with tool support)
        response = await get_claude_response(messages)
        response_text = response.get("content", "")
        
        print(f"Final response: {response_text[:100]}...")
        
        if stream:
            async def generate():
                chunk = {
                    "id": f"chatcmpl-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
                    "object": "chat.completion.chunk",
                    "created": int(datetime.utcnow().timestamp()),
                    "model": "claude-sonnet-4-20250514",
                    "choices": [{
                        "index": 0,
                        "delta": {"content": response_text},
                        "finish_reason": None
                    }]
                }
                yield f"data: {json.dumps(chunk)}\n\n"
                
                finish_chunk = {
                    "id": f"chatcmpl-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
                    "object": "chat.completion.chunk",
                    "created": int(datetime.utcnow().timestamp()),
                    "model": "claude-sonnet-4-20250514",
                    "choices": [{
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop"
                    }]
                }
                yield f"data: {json.dumps(finish_chunk)}\n\n"
                yield "data: [DONE]\n\n"
            
            return StreamingResponse(generate(), media_type="text/event-stream")
        else:
            return {
                "id": f"chatcmpl-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
                "object": "chat.completion",
                "created": int(datetime.utcnow().timestamp()),
                "model": "claude-sonnet-4-20250514",
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": response_text
                    },
                    "finish_reason": "stop"
                }],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150
                }
            }
            
    except Exception as e:
        print(f"Chat completion error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# VAPI WEBHOOK HANDLERS
# ============================================================

@app.post("/")
@app.post("/vapi/webhook")
async def vapi_webhook(request: Request):
    """Handle Vapi webhook events."""
    try:
        payload = await request.json()
        message = payload.get("message", {})
        message_type = message.get("type", "")
        
        print(f"Webhook received: {message_type}")
        
        if message_type == "assistant-request":
            return {
                "assistant": {
                    "firstMessage": "Hi, this is Alex from Cloud Store. How are you doing today?",
                    "model": {
                        "provider": "custom-llm",
                        "url": os.getenv("ORCHESTRATOR_URL", "https://orchestrator-production-24c4.up.railway.app"),
                        "model": "claude-sonnet-4-20250514"
                    },
                    "voice": {
                        "provider": "11labs",
                        "voiceId": "21m00Tcm4TlvDq8ikWAM"
                    }
                }
            }
        
        elif message_type == "function-call":
            function_call = message.get("functionCall", {})
            function_name = function_call.get("name", "")
            parameters = function_call.get("parameters", {})
            
            print(f"Function call from Vapi: {function_name} with params: {parameters}")
            
            # Execute the tool
            result = await execute_tool(function_name, parameters)
            return {"result": result}
        
        elif message_type == "end-of-call-report":
            call = message.get("call", {})
            call_id = call.get("id", "unknown")
            print(f"Call ended: {call_id}")
            
            # Try to log to n8n
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(
                        f"{MCP_N8N_URL}/tools/log_call_outcome",
                        json={
                            "call_id": call_id,
                            "outcome": "completed",
                            "payload": payload
                        }
                    )
            except Exception as e:
                print(f"Failed to log call outcome: {e}")
            
            return {"status": "received"}
        
        else:
            return {"status": "received"}
            
    except Exception as e:
        print(f"Webhook error: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup_event():
    """Verify configuration on startup"""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        print(f"✓ Anthropic API key configured")
    else:
        print("⚠ WARNING: ANTHROPIC_API_KEY not set!")
    
    print(f"✓ Orchestrator started (v1.1.0)")
    print(f"  - MCP Calendar: {MCP_CALENDAR_URL}")
    print(f"  - MCP CRM: {MCP_CRM_URL}")
    print(f"  - MCP n8n: {MCP_N8N_URL}")
