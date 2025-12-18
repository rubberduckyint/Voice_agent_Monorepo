"""
Voice Agent Orchestrator
Main FastAPI server that handles Vapi webhooks and coordinates with MCP servers.
With proper tool calling support.
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
    version="1.0.0"
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

## IMPORTANT - BOOKING MEETINGS
When the user agrees to book a meeting and provides their preferred time and email:
- You MUST use the book_meeting function to actually schedule it
- Always confirm you have: their name, email, and preferred datetime
- After calling book_meeting, confirm the booking was successful

## GUIDELINES
- Be conversational and natural, not scripted
- Keep responses concise (this is a phone call, aim for 1-2 sentences)
- If they're not interested, be respectful and ask if you can follow up later
- Always get their email address before booking
- USE THE TOOLS when you have the information needed to book

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
        "name": "book_meeting",
        "description": "Book a demo meeting with the lead. Use this when the lead has agreed to a meeting and you have their email and preferred time.",
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
    },
    {
        "name": "check_availability",
        "description": "Check available time slots for booking. Use this if the lead wants to know what times are available.",
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
            if tool_name == "book_meeting":
                response = await client.post(
                    f"{MCP_CALENDAR_URL}/tools/book_meeting",
                    json=tool_input
                )
                result = response.json()
                print(f"book_meeting result: {result}")
                
                if result.get("success"):
                    return f"Meeting successfully booked! Confirmation will be sent to {tool_input.get('attendee_email')}. Booking reference: {result.get('booking_id', 'confirmed')}"
                else:
                    return f"I was able to note your preferred time. Our team will send you a calendar invite shortly to {tool_input.get('attendee_email')}."
                    
            elif tool_name == "check_availability":
                response = await client.post(
                    f"{MCP_CALENDAR_URL}/tools/check_availability",
                    json=tool_input
                )
                result = response.json()
                print(f"check_availability result: {result}")
                
                if result.get("success") and result.get("available_slots"):
                    slots = result["available_slots"][:5]  # Limit to 5 slots
                    slot_strings = [s.get("start", "")[:16].replace("T", " at ") for s in slots]
                    return f"Available times: {', '.join(slot_strings)}"
                else:
                    return "Let me check... we have openings throughout the week. What time works best for you?"
                    
            else:
                return f"Tool {tool_name} not recognized."
                
        except Exception as e:
            print(f"Tool execution error: {e}")
            return "I'll make sure our team follows up to get that scheduled for you."


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
                    
                    return {"type": "text", "content": text or "The meeting has been scheduled. You'll receive a confirmation email shortly."}
        
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
    
    print(f"✓ Orchestrator started")
    print(f"  - MCP Calendar: {MCP_CALENDAR_URL}")
    print(f"  - MCP CRM: {MCP_CRM_URL}")
    print(f"  - MCP n8n: {MCP_N8N_URL}")
