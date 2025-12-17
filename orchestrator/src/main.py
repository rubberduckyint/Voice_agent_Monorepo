"""
Voice Agent Orchestrator
Main FastAPI server that handles Vapi webhooks and coordinates with MCP servers.
"""

import os
import json
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from anthropic import Anthropic
from datetime import datetime
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

# In-memory conversation state (replace with Redis in production)
conversations: Dict[str, List[Dict]] = {}


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

## GUIDELINES
- Be conversational and natural, not scripted
- Keep responses concise (this is a phone call, aim for 1-2 sentences)
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
# MODELS FOR OPENAI-COMPATIBLE API
# ============================================================

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: Optional[str] = "claude-sonnet-4-20250514"
    messages: List[ChatMessage]
    stream: Optional[bool] = False
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 300


# ============================================================
# CLAUDE CONVERSATION
# ============================================================

def get_claude_response_sync(messages: List[Dict]) -> str:
    """Get response from Claude (synchronous for simplicity)."""
    
    try:
        # Convert messages to Claude format
        claude_messages = []
        for msg in messages:
            if msg.get("role") in ["user", "assistant"]:
                claude_messages.append({
                    "role": msg["role"],
                    "content": msg.get("content", "")
                })
        
        # Ensure we have at least one message
        if not claude_messages:
            claude_messages = [{"role": "user", "content": "Hello"}]
        
        response = anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            system=SYSTEM_PROMPT,
            messages=claude_messages
        )
        
        # Extract text response
        text_response = ""
        for block in response.content:
            if hasattr(block, "text"):
                text_response += block.text
        
        return text_response or "I'm sorry, I didn't catch that. Could you repeat?"
        
    except Exception as e:
        print(f"Claude API error: {e}")
        return "I'm having a brief technical issue. Could you give me just a moment?"


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
        
        # Get response from Claude
        response_text = get_claude_response_sync(messages)
        
        print(f"Claude response: {response_text[:100]}...")
        
        if stream:
            # Return streaming response
            async def generate():
                # Send the response as a single chunk
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
                
                # Send finish chunk
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
            
            return StreamingResponse(
                generate(),
                media_type="text/event-stream"
            )
        else:
            # Return non-streaming response
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
    """
    Handle Vapi webhook events.
    """
    try:
        payload = await request.json()
        message = payload.get("message", {})
        message_type = message.get("type", "")
        
        print(f"Webhook received: {message_type}")
        
        # Handle different message types
        if message_type == "assistant-request":
            # Vapi is asking for assistant configuration
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
            # Handle function/tool calls
            function_call = message.get("functionCall", {})
            function_name = function_call.get("name", "")
            parameters = function_call.get("parameters", {})
            
            print(f"Function call: {function_name} with params: {parameters}")
            
            result = await handle_function_call(function_name, parameters)
            return {"result": result}
        
        elif message_type == "end-of-call-report":
            # Call ended
            call = message.get("call", {})
            call_id = call.get("id", "unknown")
            print(f"Call ended: {call_id}")
            
            # Trigger post-call workflow
            try:
                async with httpx.AsyncClient() as client:
                    await client.post(
                        f"{MCP_N8N_URL}/tools/log_call_outcome",
                        json={
                            "call_id": call_id,
                            "outcome": "completed",
                            "payload": payload
                        },
                        timeout=10
                    )
            except Exception as e:
                print(f"Failed to log call outcome: {e}")
            
            return {"status": "received"}
        
        elif message_type == "hang":
            return {"status": "received"}
        
        elif message_type == "speech-update":
            return {"status": "received"}
        
        elif message_type == "transcript":
            return {"status": "received"}
        
        elif message_type == "status-update":
            return {"status": "received"}
            
        elif message_type == "assistant.started":
            print("Assistant started")
            return {"status": "received"}
            
        elif message_type == "conversation-update":
            return {"status": "received"}
        
        else:
            print(f"Unhandled message type: {message_type}")
            return {"status": "received"}
            
    except Exception as e:
        print(f"Webhook error: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}


async def handle_function_call(function_name: str, parameters: Dict) -> Dict:
    """Handle function calls from Vapi."""
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if function_name == "check_availability":
                response = await client.post(
                    f"{MCP_CALENDAR_URL}/tools/check_availability",
                    json=parameters
                )
                return response.json()
            
            elif function_name == "book_meeting":
                response = await client.post(
                    f"{MCP_CALENDAR_URL}/tools/book_meeting",
                    json=parameters
                )
                return response.json()
            
            elif function_name == "get_lead":
                response = await client.post(
                    f"{MCP_CRM_URL}/tools/get_lead",
                    json=parameters
                )
                return response.json()
            
            elif function_name == "update_lead":
                response = await client.post(
                    f"{MCP_CRM_URL}/tools/update_lead",
                    json=parameters
                )
                return response.json()
            
            elif function_name == "log_activity":
                response = await client.post(
                    f"{MCP_CRM_URL}/tools/log_activity",
                    json=parameters
                )
                return response.json()
            
            else:
                return {"error": f"Unknown function: {function_name}"}
                
        except Exception as e:
            print(f"Function call error: {e}")
            return {"error": str(e)}


# ============================================================
# CALL INITIATION
# ============================================================

@app.post("/call/initiate")
async def initiate_call(
    lead_id: str,
    phone_number: str,
    lead_name: Optional[str] = None,
    company_name: Optional[str] = None
):
    """
    Initiate an outbound call via Vapi.
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
                        "number": phone_number,
                        "name": lead_name
                    },
                    "assistantId": os.getenv("VAPI_ASSISTANT_ID"),
                    "metadata": {
                        "lead_id": lead_id,
                        "company_name": company_name
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
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        print(f"✓ Anthropic API key configured (starts with {api_key[:10]}...)")
    else:
        print("⚠ WARNING: ANTHROPIC_API_KEY not set!")
    
    print(f"✓ Orchestrator started")
    print(f"  - MCP Calendar: {MCP_CALENDAR_URL}")
    print(f"  - MCP CRM: {MCP_CRM_URL}")
    print(f"  - MCP n8n: {MCP_N8N_URL}")
