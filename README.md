# Voice Agent Monorepo

AI-powered voice booking agent for Vehicle Price Evaluator demos.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              VOICE AI PLATFORM (Vapi)                       │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTP/WebSocket
                            ▼
┌─────────────────────────────────────────────────────────────┐
│            ORCHESTRATOR (FastAPI)                           │
│     Conversation State • Claude API • MCP Client            │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTP (internal)
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                      MCP SERVERS                            │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │ Calendar │ │   CRM    │ │Knowledge │ │   n8n    │       │
│  │(Cal.com) │ │(HubSpot) │ │(Qdrant)  │ │(Webhook) │       │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘       │
└─────────────────────────────────────────────────────────────┘
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| `orchestrator` | 8000 | Main FastAPI server, handles Vapi webhooks, calls Claude |
| `mcp-calendar` | 8001 | Calendar MCP server (Cal.com/Calendly integration) |
| `mcp-crm` | 8002 | CRM MCP server (HubSpot integration) |
| `mcp-n8n` | 8003 | n8n webhook bridge for post-call workflows |

## Quick Start (Local Development)

### Prerequisites

- Python 3.11+
- Docker (optional, for containerized development)
- API Keys: Anthropic, Vapi, HubSpot, Cal.com

### 1. Clone and Setup

```bash
git clone https://github.com/YOUR_USERNAME/Voice_agent_Monorepo.git
cd Voice_agent_Monorepo
```

### 2. Start Each Service

**Orchestrator:**
```bash
cd orchestrator
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env      # Edit with your API keys
uvicorn src.main:app --reload --port 8000
```

**MCP Calendar:**
```bash
cd mcp-calendar
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn src.main:app --reload --port 8001
```

**MCP CRM:**
```bash
cd mcp-crm
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn src.main:app --reload --port 8002
```

**MCP n8n:**
```bash
cd mcp-n8n
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn src.main:app --reload --port 8003
```

## Railway Deployment

### 1. Connect Repository

1. In Railway project, click **"Add a Service"** → **"GitHub Repo"**
2. Select `Voice_agent_Monorepo`
3. Set **Root Directory** to `orchestrator`
4. Repeat for each service folder

### 2. Add Databases

1. Click **"Add a Service"** → **"Database"** → **"Redis"**
2. Click **"Add a Service"** → **"Database"** → **"PostgreSQL"**

### 3. Configure Environment Variables

Set these in Railway for each service (Settings → Variables):

**Orchestrator:**
```
ANTHROPIC_API_KEY=sk-ant-...
VAPI_API_KEY=...
REDIS_URL=${{Redis.REDIS_URL}}
DATABASE_URL=${{Postgres.DATABASE_URL}}
MCP_CALENDAR_URL=http://mcp-calendar.railway.internal:8001
MCP_CRM_URL=http://mcp-crm.railway.internal:8002
MCP_N8N_URL=http://mcp-n8n.railway.internal:8003
```

**MCP Calendar:**
```
CAL_COM_API_KEY=...
```

**MCP CRM:**
```
HUBSPOT_ACCESS_TOKEN=...
```

**MCP n8n:**
```
N8N_WEBHOOK_URL=https://your-n8n-instance.com/webhook/...
```

### 4. Generate Domain

For the orchestrator only (it needs a public URL for Vapi webhooks):
1. Go to orchestrator service → **Settings** → **Networking**
2. Click **"Generate Domain"**
3. Use this URL in your Vapi assistant config

## Environment Variables Reference

| Variable | Service | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | orchestrator | Claude API key |
| `VAPI_API_KEY` | orchestrator | Vapi API key |
| `REDIS_URL` | orchestrator | Redis connection string |
| `DATABASE_URL` | orchestrator | PostgreSQL connection string |
| `MCP_CALENDAR_URL` | orchestrator | Internal URL to calendar service |
| `MCP_CRM_URL` | orchestrator | Internal URL to CRM service |
| `MCP_N8N_URL` | orchestrator | Internal URL to n8n service |
| `CAL_COM_API_KEY` | mcp-calendar | Cal.com API key |
| `HUBSPOT_ACCESS_TOKEN` | mcp-crm | HubSpot private app token |
| `N8N_WEBHOOK_URL` | mcp-n8n | n8n webhook endpoint |

## API Endpoints

### Orchestrator (port 8000)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| POST | `/vapi/webhook` | Vapi webhook handler |
| POST | `/call/initiate` | Start outbound call |

### MCP Calendar (port 8001)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| POST | `/tools/check_availability` | Get available time slots |
| POST | `/tools/book_meeting` | Book a demo |
| POST | `/tools/cancel_meeting` | Cancel booking |

### MCP CRM (port 8002)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| POST | `/tools/get_lead` | Get lead details |
| POST | `/tools/update_lead` | Update lead properties |
| POST | `/tools/log_activity` | Log call activity |

### MCP n8n (port 8003)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| POST | `/tools/trigger_workflow` | Trigger n8n workflow |
| POST | `/tools/log_call_outcome` | Send call summary to n8n |

## License

Proprietary - Cloud Store
