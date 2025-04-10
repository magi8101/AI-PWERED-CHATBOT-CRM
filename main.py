from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List, Dict, Any, Union
from datetime import datetime
import requests
import time
import logging
import base64
import io
import json
import os
# Explicitly import python-multipart (package will be installed via requirements.txt)
import multipart
from supabase import create_client
import lead_manager
from lead_manager import Lead, LeadQualificationCriteria, LeadGenerationRequest, GeneratedLead, AILeadModel
import hubspot_integration
from hubspot_integration import LeadActivity
from lead_manager import generate_fake_ip_info, generate_location_based_recommendations, get_ip_info, calculate_distance
import socket
#import ipinfo

# Set up structured logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(name)s: %(message)s")
logger = logging.getLogger("ChatHub")

# Initialize FastAPI app and CORS
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Configuration - no environment variables
OPENAI_API_KEY = "your_openai_api_key"
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"

CLAUDE_API_KEY = "YOUR_CLAUDE_API_API_KEY"
CLAUDE_API_URL = "https://api.anthropic.com/v1/upload"

HUBSPOT_ACCESS_TOKEN = "_YOUR_HUBSPOT_ACCESS_TOKEN"
HUBSPOT_CLIENT_SECRET = "YOUR_HUBSPOT_CLIENT_SECERT"
HUBSPOT_BASE_URL = "https://api.hubapi.com"

# Supabase Config                                       
SUPABASE_URL = "YOUR_SUPABASE_URL"
SUPABASE_KEY = "YOUR_SUPABASE_KEY"

# Initialize Supabase client if credentials are available
supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ---------------------
# Models
# ---------------------
class ChatRequest(BaseModel):
    email: EmailStr = Field(..., description="User's email address.")
    user_id: Optional[str] = "anonymous"
    message: str = Field(..., description="User message. Supports lengthy input up to 15,000 lines.")
    history: Optional[List[Dict[str, Any]]] = Field(None, description="Conversation history from the client.")
    scraped_data: Optional[Dict[str, Any]] = Field(None, description="Scraped website data for additional context")

# HubSpot Models
class HubSpotContact(BaseModel):
    email: EmailStr
    firstname: Optional[str] = None
    lastname: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    website: Optional[str] = None
    jobtitle: Optional[str] = None
    lifecycle_stage: Optional[str] = None
    lead_source: Optional[str] = None
    
class HubSpotContactResponse(BaseModel):
    id: str
    properties: Dict[str, Any]
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    archived: bool = False

class UserSignup(BaseModel):
    full_name: str
    email: EmailStr
    password: str
    company: Optional[str] = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class FileUploadRequest(BaseModel):
    email: EmailStr = Field(..., description="User's email address.")
    user_id: Optional[str] = "anonymous"
    message: Optional[str] = Field(None, description="Optional text message accompanying the file.")
    history: Optional[List[Dict[str, Any]]] = Field(None, description="Conversation history from the client.")

# ---------------------
# Database Storage
# ---------------------
def store_chat_data(email: str, user_id: str, user_message: str, chatbot_reply: str, response_time: float, 
                    sentiment_label: Optional[str] = None, sentiment_score: Optional[float] = None, 
                    drop_off: bool = False, scraped_data: Optional[Dict[str, Any]] = None) -> None:
    """Store a new chat entry in the database."""
    try:
        data = {
            "email": email,
            "user_id": user_id,
            "user_message": user_message,
            "chatbot_reply": chatbot_reply,
            "response_time": response_time,
            "timestamp": datetime.now().isoformat(),
            "sentiment_label": sentiment_label,
            "sentiment_score": sentiment_score,
            "drop_off": drop_off,
            "message_length": len(user_message),
            "scraped_data": scraped_data  # Store scraped website data if provided
        }
        result = supabase.table("chat_logs").insert(data).execute()
        logger.info("Chat data stored in database", extra={"result": result.data})
    except Exception as e:
        logger.error(f"Error storing chat data in database: {e}")

# ---------------------
# Retrieve Conversation History from Database
# ---------------------
def get_conversation_history_from_db(email: str, limit: int = 10) -> List[Dict[str, str]]:
    """Retrieve the most recent conversation history from the database."""
    try:
        response = supabase.table("chat_logs").select("*").eq("email", email).order("timestamp", desc=True).limit(limit).execute()
        if response.data:
            messages = []
            # Reverse the results to maintain chronological order
            for entry in reversed(response.data):
                user_msg = entry.get("user_message", "")
                ai_reply = entry.get("chatbot_reply", "")
                if user_msg.strip():
                    messages.append({"role": "user", "content": user_msg})
                if ai_reply.strip():
                    messages.append({"role": "assistant", "content": ai_reply})
            return messages
        else:
            return []
    except Exception as e:
        logger.error(f"Error retrieving conversation history from database: {e}")
        return []

# ---------------------
# User Management
# ---------------------
def store_user(user_data: UserSignup) -> Dict[str, Any]:
    """Store a new user in the database."""
    try:
        import bcrypt
        
        # Hash the password with bcrypt before storing
        hashed_password = bcrypt.hashpw(user_data.password.encode('utf-8'), bcrypt.gensalt())
        
        data = {
            "full_name": user_data.full_name,
            "email": user_data.email,
            "password": hashed_password.decode('utf-8'),  # Store the hashed password
            "company": user_data.company,
            "created_at": datetime.now().isoformat()
        }
        result = supabase.table("users").insert(data).execute()
        logger.info("User data stored in database", extra={"result": result.data})
        return result.data[0] if result.data else {}
    except Exception as e:
        logger.error(f"Error storing user data in database: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def authenticate_user(login_data: UserLogin) -> Dict[str, Any]:
    """Authenticate a user."""
    try:
        import bcrypt
        
        # Get the user by email
        response = supabase.table("users").select("*").eq("email", login_data.email).execute()
        
        if not response.data:
            raise HTTPException(status_code=401, detail="Invalid credentials")
            
        user = response.data[0]
        
        # Verify the password using bcrypt
        stored_password = user["password"].encode('utf-8')
        provided_password = login_data.password.encode('utf-8')
        
        if bcrypt.checkpw(provided_password, stored_password):
            return user
        else:
            raise HTTPException(status_code=401, detail="Invalid credentials")
    except Exception as e:
        logger.error(f"Error authenticating user: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ---------------------
# AI Response
# ---------------------

# Lead generation prompt templates
LEAD_GEN_PROMPT_TEMPLATE = """
You are a smart, efficient, and reliable AI assistant designed specifically for lead generation. Your key responsibilities and guidelines are as follows:

Greet & Assist Politely:

Start every interaction with a friendly greeting.

Offer clear assistance related to lead generation.

Lead Collection:

Ask directly for:

Name

Email address

Phone number (if available)

A brief description of their query or area of interest

Information Confirmation:

Confirm the details provided by summarizing the collected information.

If any required details are missing or unclear, politely request the necessary corrections.

Focused Responses:

Keep responses short, clear, and human-like.

Answer only questions related to lead automation.

If the conversation diverges (e.g., FAQs about pricing, services, etc.), provide a brief answer and then guide the conversation back to collecting lead information.

For any question outside lead automation, politely inform the user that your expertise is limited to lead automation topics.

Efficiency & Transparency:

Respond quickly without repetitive loops.

Ensure data is collected in a clean, consistent format.

Log key actions for transparency, such as confirmation of collected details.

Always maintain a professional and polite tone."""

def get_openai_response(chat_req: ChatRequest) -> str:
    """
    Call the OpenAI API using conversation history (with scraped website data if provided)
    and the current user message for context. Ensures previous conversation history is included.
    """
    try:
        conversation_history = get_conversation_history_from_db(chat_req.email, limit=10)
        
        # Add lead generation system prompt at the beginning
        conversation_history.insert(0, {
            "role": "system",
            "content": LEAD_GEN_PROMPT_TEMPLATE
        })
        
        # If scraped_data exists, add it as a system message
        if chat_req.scraped_data:
            conversation_history.insert(1, {
                "role": "system",
                "content": f"Scraped website details: {chat_req.scraped_data}"
            })
        
        # Append the current user message to the conversation history
        conversation_history.append({
            "role": "user",
            "content": chat_req.message
        })
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "messages": conversation_history,
            "model": "gpt-4-turbo"
        }
        logger.info("Sending request to OpenAI API", extra={"payload": payload})
        response = requests.post(OPENAI_API_URL, json=payload, headers=headers)
        if response.status_code != 200:
            logger.error("OpenAI API returned non-200 status", extra={"status_code": response.status_code, "response": response.text})
            return "I'm sorry, I'm having trouble processing your request right now."
        result = response.json()
        logger.info("Received OpenAI API result", extra={"result": result})
        if "choices" in result and isinstance(result["choices"], list) and result["choices"]:
            choice = result["choices"][0]
            if "message" in choice and "content" in choice["message"]:
                return choice["message"]["content"]
        if "error" in result:
            error_detail = result.get("error")
            logger.error("OpenAI API error", extra={"error": error_detail})
            return "I'm sorry, I'm having trouble processing your request right now."
        logger.error("Unexpected OpenAI API response structure", extra={"result": result})
        return "I'm sorry, I couldn't understand the response from the service."
    except Exception as e:
        logger.error("Error getting response from OpenAI", extra={"error": str(e)})
        return "I'm sorry, an unexpected error occurred while processing your request."

def process_file_with_claude(file_content: bytes, file_name: str, file_type: str, message: str) -> str:
    """
    Process a file (PDF or image) using Claude API for recognition/analysis
    """
    try:
        # Encode file to base64
        base64_file = base64.b64encode(file_content).decode("utf-8")
        
        headers = {
            "x-api-key": CLAUDE_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        
        # Prepare appropriate message based on file type
        file_prefix = "data:application/pdf;base64," if file_type.lower() == "pdf" else f"data:image/{file_type.lower()};base64,"
        
        # Create the payload for Claude API with lead gen prompt
        system_prompt = LEAD_GEN_PROMPT_TEMPLATE + f"\nYou are also analyzing a {file_type} file. After analysis, return to lead collection."
        
        payload = {
            "model": "claude-3-opus-20240229",
            "max_tokens": 4000,
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Analyze this {file_type} file. {message if message else 'Extract and summarize the key information.'}"
                        },
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": f"application/{file_type.lower()}" if file_type.lower() == "pdf" else f"image/{file_type.lower()}",
                                "data": base64_file
                            }
                        }
                    ]
                }
            ]
        }
        
        logger.info(f"Sending {file_type} file to Claude API: {file_name}")
        response = requests.post(CLAUDE_API_URL, headers=headers, json=payload)
        
        if response.status_code != 200:
            logger.error(f"Claude API error: {response.status_code} - {response.text}")
            return f"I had trouble analyzing this {file_type} file. Error: {response.status_code}"
        
        result = response.json()
        if "content" in result and len(result["content"]) > 0:
            # Extract the text content from Claude's response
            return result["content"][0]["text"]
        else:
            return f"I couldn't extract any meaningful information from this {file_type} file."
    
    except Exception as e:
        logger.error(f"Error processing file with Claude: {str(e)}")
        return f"An error occurred while processing your {file_type} file. Please try again later."

def process_scraped_data_with_claude(email: str, message: str, scraped_data: Dict[str, Any]) -> str:
    """
    Process scraped website data using Claude API for advanced analysis and chatting
    """
    try:
        # Format the scraped data into a structured text representation
        scraped_content = json.dumps(scraped_data, indent=2)
        
        headers = {
            "x-api-key": CLAUDE_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        
        # Create the payload for Claude API with combined prompt
        system_prompt = """
        You are an AI assistant that helps users understand and interact with web content.
        You've been given scraped data from a website the user is currently viewing.
        
        Your task:
        1. Analyze the scraped website data thoroughly
        2. Respond directly to the user's question about the website
        3. Provide accurate and helpful information based only on the scraped content
        4. If the scraped data doesn't contain enough information, acknowledge the limitations
        5. Be conversational but focused on the scraped content
        
        The scraped data includes essential website elements like title, meta description,
        headings, paragraphs, links, and other content elements.
        """
        
        payload = {
            "model": "claude-3-opus-20240229",
            "max_tokens": 4000,
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Here is scraped data from a website:\n\n{scraped_content}\n\nUser's question: {message}"
                        }
                    ]
                }
            ]
        }
        
        logger.info(f"Sending scraped data to Claude API for user {email}")
        response = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
        
        if response.status_code != 200:
            logger.error(f"Claude API error: {response.status_code} - {response.text}")
            return f"I had trouble analyzing this website data. Error: {response.status_code}"
        
        result = response.json()
        if "content" in result and len(result["content"]) > 0:
            # Extract the text content from Claude's response
            return result["content"][0]["text"]
        else:
            return "I couldn't extract any meaningful information from this website data."
    
    except Exception as e:
        logger.error(f"Error processing scraped data with Claude: {str(e)}")
        return f"An error occurred while processing the website data. Please try again later."

# ---------------------
# HubSpot Integration Functions
# ---------------------
def get_hubspot_headers():
    """Return the headers required for HubSpot API calls"""
    if not HUBSPOT_ACCESS_TOKEN:
        raise HTTPException(status_code=500, detail="HubSpot API access token not configured")
    return {
        "content-type": "application/json",
        "authorization": f"Bearer {HUBSPOT_ACCESS_TOKEN}"
    }

def format_hubspot_contact_properties(contact: HubSpotContact) -> Dict[str, str]:
    """Format contact properties for HubSpot API"""
    properties = {}
    
    # Add all non-None properties
    if contact.email:
        properties["email"] = contact.email
    if contact.firstname:
        properties["firstname"] = contact.firstname
    if contact.lastname:
        properties["lastname"] = contact.lastname
    if contact.phone:
        properties["phone"] = contact.phone
    if contact.company:
        properties["company"] = contact.company
    if contact.website:
        properties["website"] = contact.website
    if contact.jobtitle:
        properties["jobtitle"] = contact.jobtitle
    if contact.lifecycle_stage:
        properties["lifecyclestage"] = contact.lifecycle_stage
    if contact.lead_source:
        properties["lead_source"] = contact.lead_source
        
    return properties

# ---------------------
# Endpoints
# ---------------------
@app.post("/api/chat/extension/")
async def chatbot(chat: ChatRequest, background_tasks: BackgroundTasks):
    """
    Extended chat endpoint which supports scraped_data.
    It retrieves conversation history from the database and passes it (with scraped data) for the AI to generate a reply.
    """
    logger.info("Received chat request", extra={"chat": chat.dict()})
    line_count = len(chat.message.splitlines())
    logger.info("User message line count", extra={"lines": line_count})
    start_time = time.time()
    try:
        bot_reply = get_openai_response(chat)
        response_time = round(time.time() - start_time, 2)
        # Store chat data in the database including scraped_data if provided
        background_tasks.add_task(
            store_chat_data, 
            chat.email, 
            chat.user_id, 
            chat.message, 
            bot_reply, 
            response_time,
            scraped_data=chat.scraped_data
        )
        logger.info("Sending response to client", extra={"ai_reply_preview": bot_reply[:50]})
        return JSONResponse(content={"ai_reply": bot_reply})
    except Exception as e:
        logger.error("Error in chatbot endpoint", extra={"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat/")
async def chatbot_plain(chat: ChatRequest, background_tasks: BackgroundTasks):
    """
    Plain chat endpoint which does not include scraped_data.
    Conversation history is still retrieved and stored in the DB.
    """
    logger.info("Received chat request", extra={"chat": chat.dict()})
    line_count = len(chat.message.splitlines())
    logger.info("User message line count", extra={"lines": line_count})
    start_time = time.time()
    try:
        bot_reply = get_openai_response(chat)
        response_time = round(time.time() - start_time, 2)
        
        # Store chat data in the database
        background_tasks.add_task(
            store_chat_data, 
            chat.email, 
            chat.user_id, 
            chat.message, 
            bot_reply, 
            response_time
        )
        
        # Track interaction in HubSpot
        try:
            # Create or update the contact in HubSpot
            background_tasks.add_task(
                hubspot_integration.create_contact_from_chat,
                chat.email,
                chat.message,
                HUBSPOT_ACCESS_TOKEN,
                HUBSPOT_BASE_URL
            )
            
            # Log the activity in HubSpot
            activity = LeadActivity(
                email=chat.email,
                activity_type="chat_message",
                details={
                    "message_length": len(chat.message),
                    "response_time": response_time
                }
            )
            background_tasks.add_task(
                hubspot_integration.log_lead_activity, 
                activity, 
                HUBSPOT_ACCESS_TOKEN,
                HUBSPOT_BASE_URL
            )
            
        except Exception as hubspot_error:
            logger.error(f"Error integrating with HubSpot: {hubspot_error}")
            # Continue processing even if HubSpot integration fails
        
        logger.info("Sending response to client", extra={"ai_reply_preview": bot_reply[:50]})
        return JSONResponse(content={"ai_reply": bot_reply})
    except Exception as e:
        logger.error("Error in chatbot endpoint", extra={"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat/signup")
async def signup(user_data: UserSignup):
    """Register a new user."""
    try:
        user = store_user(user_data)
        return JSONResponse(content={"message": "User registered successfully", "user_id": user.get("id")})
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error in signup endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat/login")
async def login(login_data: UserLogin):
    """Authenticate a user and return user data."""
    try:
        user = authenticate_user(login_data)
        return JSONResponse(content={"message": "Login successful", "user": {
            "id": user.get("id"),
            "email": user.get("email"),
            "full_name": user.get("full_name"),
            "company": user.get("company")
        }})
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error in login endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat/file-upload/")
async def chat_with_file(
    email: str = Form(...),
    user_id: str = Form("anonymous"),
    message: str = Form(None),
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None
):
    """
    Process file uploads (PDF and images) using Claude API for recognition/analysis
    """
    logger.info(f"Received file upload request: {file.filename} from {email}")
    start_time = time.time()
    
    # Validate file type
    file_extension = file.filename.split(".")[-1].lower()
    supported_extensions = {"pdf", "png", "jpg", "jpeg", "gif", "webp"}
    
    if file_extension not in supported_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format. Supported formats: {', '.join(supported_extensions)}"
        )
    
    try:
        # Read file content
        file_content = await file.read()
        
        # Process file with Claude
        bot_reply = process_file_with_claude(
            file_content=file_content,
            file_name=file.filename,
            file_type=file_extension,
            message=message or ""
        )
        
        response_time = round(time.time() - start_time, 2)
        
        # Store in database (optional)
        if background_tasks:
            background_tasks.add_task(
                store_chat_data,
                email,
                user_id,
                f"[File Upload: {file.filename}] {message or ''}",
                bot_reply,
                response_time
            )
        
        return JSONResponse(content={"ai_reply": bot_reply})
    
    except Exception as e:
        logger.error(f"Error processing file upload: {str(e)}")
        raise HTTPException(status_code=500, detail="An internal error occurred while processing the file. Please try again later.")

@app.get("/api/hubspot/contacts/")
async def get_hubspot_contacts(
    after: Optional[str] = None
):
    """Get all contacts from HubSpot with pagination support"""
    try:
        url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts"
        params = {"limit": limit}
        if after:
            params["after"] = after
            
        headers = hubspot_integration.get_hubspot_headers(HUBSPOT_ACCESS_TOKEN)
        response = requests.get(url, headers=headers, params=params)
        
        if response.status_code != 200:
            logger.error(f"HubSpot API error: {response.status_code} - {response.text}")
            raise HTTPException(
                status_code=response.status_code,
                detail=f"HubSpot API error: {response.text}"
            )
            
        return response.json()
    except Exception as e:
        logger.error(f"Error retrieving HubSpot contacts: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/hubspot/contacts/", response_model=HubSpotContactResponse)
async def create_hubspot_contact(contact: HubSpotContact):
    """Create a new contact in HubSpot"""
    try:
        url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts"
        properties = format_hubspot_contact_properties(contact)
        
        payload = {"properties": properties}
        
        headers = hubspot_integration.get_hubspot_headers(HUBSPOT_ACCESS_TOKEN)
        response = requests.post(
            url,
            headers=headers,
            json=payload
        )
        
        if response.status_code not in (200, 201):
            logger.error(f"HubSpot API error: {response.status_code} - {response.text}")
            raise HTTPException(
                status_code=response.status_code,
                detail=f"HubSpot API error: {response.text}"
            )
            
        return response.json()
    except Exception as e:
        logger.error(f"Error creating HubSpot contact: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/hubspot/contacts/{contact_id}")
async def get_hubspot_contact(contact_id: str):
    """Get a specific contact by ID from HubSpot"""
    try:
        url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts/{contact_id}"
        
        headers = hubspot_integration.get_hubspot_headers(HUBSPOT_ACCESS_TOKEN)
        response = requests.get(
            url,
            headers=headers
        )
        
        if response.status_code != 200:
            logger.error(f"HubSpot API error: {response.status_code} - {response.text}")
            raise HTTPException(
                status_code=response.status_code,
                detail=f"HubSpot API error: {response.text}"
            )
            
        return response.json()
    except Exception as e:
        logger.error(f"Error retrieving HubSpot contact: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/hubspot/contacts/{contact_id}")
async def update_hubspot_contact(contact_id: str, contact: HubSpotContact):
    """Update an existing contact in HubSpot"""
    try:
        url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts/{contact_id}"
        properties = format_hubspot_contact_properties(contact)
        
        payload = {"properties": properties}
        
        headers = hubspot_integration.get_hubspot_headers(HUBSPOT_ACCESS_TOKEN)
        response = requests.patch(
            url,
            headers=headers,
            json=payload
        )
        
        if response.status_code != 200:
            logger.error(f"HubSpot API error: {response.status_code} - {response.text}")
            raise HTTPException(
                status_code=response.status_code,
                detail=f"HubSpot API error: {response.text}"
            )
            
        return response.json()
    except Exception as e:
        logger.error(f"Error updating HubSpot contact: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "Chatbot API is running"}

# ---------------------
# Lead Management Endpoints
# ---------------------
@app.post("/api/leads/qualify")
async def qualify_incoming_lead(lead: lead_manager.Lead):
    """
    Qualify an incoming lead based on preset criteria and return qualification score
    """
    try:
        # Process the lead
        result = lead_manager.process_lead(lead)
        
        # Store lead details in database for tracking
        background_tasks = BackgroundTasks()
        background_tasks.add_task(
            store_lead_data,
            lead.email,
            result
        )
        
        return JSONResponse(content=result)
    except Exception as e:
        logger.error(f"Error qualifying lead: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/leads/create-and-qualify")
async def create_and_qualify_lead(lead: lead_manager.Lead):
    """
    Create a lead in HubSpot and qualify it in one operation
    """
    try:
        # First qualify the lead
        qualification_result = lead_manager.qualify_lead(lead)
        
        # Convert to HubSpot format
        hubspot_data = lead_manager.convert_lead_to_hubspot(lead)
        
        # Create in HubSpot
        url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts"
        headers = hubspot_integration.get_hubspot_headers(HUBSPOT_ACCESS_TOKEN)
        response = requests.post(
            url,
            headers=headers,
            json=hubspot_data
        )
        
        if response.status_code not in (200, 201):
            logger.error(f"HubSpot API error: {response.status_code} - {response.text}")
            raise HTTPException(
                status_code=response.status_code,
                detail=f"HubSpot API error: {response.text}"
            )
            
        hubspot_result = response.json()
        
        # Return combined results
        return {
            "hubspot_contact": hubspot_result,
            "qualification": qualification_result.dict()
        }
    except Exception as e:
        logger.error(f"Error creating and qualifying lead: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/leads/qualification-criteria")
async def get_qualification_criteria():
    """Get current lead qualification criteria"""
    return lead_manager.DEFAULT_QUALIFICATION

@app.post("/api/leads/chatbot-to-lead")
async def convert_chat_to_lead(chat_request: ChatRequest):
    """
    Convert a chat interaction to a lead and qualify it
    """
    try:
        # Extract potential lead information from the chat message
        lead_info = extract_lead_info_from_chat(chat_request.message)
        
        # If we have an email, create a lead
        if lead_info.get("email"):
            lead = lead_manager.Lead(
                email=lead_info.get("email"),
                first_name=lead_info.get("first_name"),
                last_name=lead_info.get("last_name"),
                company=lead_info.get("company"),
                phone=lead_info.get("phone"),
                message=chat_request.message
            )
            
            # Process the lead
            result = lead_manager.process_lead(lead)
            
            # If qualified, store in HubSpot
            if result["qualification"]["qualified"]:
                hubspot_data = lead_manager.convert_lead_to_hubspot(lead)
                
                # Create in HubSpot (in background task)
                background_tasks = BackgroundTasks()
                background_tasks.add_task(
                    create_hubspot_contact_from_lead,
                    hubspot_data
                )
                
            return JSONResponse(content={
                "lead_extracted": True,
                "lead_data": result
            })
        else:
            return JSONResponse(content={
                "lead_extracted": False,
                "reason": "No email address found in the message"
            })
    except Exception as e:
        logger.error(f"Error converting chat to lead: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# ---------------------
# AI Lead Generation Endpoints
# ---------------------
@app.post("/api/leads/generate")
async def generate_leads(request: LeadGenerationRequest):
    """
    Generate potential leads using AI based on specified industry and criteria
    """
    try:
        if not OPENAI_API_KEY:
            raise HTTPException(status_code=500, detail="OpenAI API key not configured")
            
        # Generate leads using the AI
        generated_leads = lead_manager.generate_leads_with_ai(request, OPENAI_API_KEY)
        
        if not generated_leads:
            return JSONResponse(content={
                "success": False,
                "error": "Failed to generate leads"
            })
        
        # Store generated leads in database
        background_tasks = BackgroundTasks()
        for lead in generated_leads:
            background_tasks.add_task(
                store_generated_lead,
                lead
            )
        
        return JSONResponse(content={
            "success": True,
            "leads": [lead.dict() for lead in generated_leads]
        })
    except Exception as e:
        logger.error(f"Error generating leads: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/leads/enrich")
async def enrich_lead(lead: lead_manager.Lead):
    """
    Enrich lead data with AI-generated insights and additional information
    """
    try:
        if not OPENAI_API_KEY:
            raise HTTPException(status_code=500, detail="OpenAI API key not configured")
            
        # Enrich lead data using AI
        enriched_data = lead_manager.enrich_lead_data(lead, OPENAI_API_KEY)
        
        return JSONResponse(content=enriched_data)
    except Exception as e:
        logger.error(f"Error enriching lead data: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/leads/personalized-outreach")
async def create_personalized_outreach(
    lead: lead_manager.Lead,
    campaign_type: str = "cold_email"
):
    """
    Generate personalized outreach content for a lead using AI
    """
    try:
        if not OPENAI_API_KEY:
            raise HTTPException(status_code=500, detail="OpenAI API key not configured")
            
        # Generate personalized outreach content
        outreach_content = lead_manager.generate_personalized_outreach(
            lead, campaign_type, OPENAI_API_KEY
        )
        
        return JSONResponse(content={
            "success": True,
            "outreach": outreach_content
        })
    except Exception as e:
        logger.error(f"Error generating personalized outreach: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/leads/generate-and-qualify")
async def generate_and_qualify_leads(request: LeadGenerationRequest):
    """
    Generate leads with AI and then qualify them based on standard criteria
    """
    try:
        if not OPENAI_API_KEY:
            raise HTTPException(status_code=500, detail="OpenAI API key not configured")
            
        # Generate leads using AI
        generated_leads = lead_manager.generate_leads_with_ai(request, OPENAI_API_KEY)
        
        if not generated_leads:
            return JSONResponse(content={
                "success": False,
                "error": "Failed to generate leads"
            })
        
        # Convert and qualify each generated lead
        qualified_leads = []
        for gen_lead in generated_leads:
            # Create a lead object from generated lead
            lead = lead_manager.Lead(
                email=f"contact@{gen_lead.website}" if gen_lead.website else "unknown@example.com",
                company=gen_lead.company_name,
                industry=gen_lead.industry,
                company_size=estimate_company_size_to_number(gen_lead.estimated_company_size),
                job_title=gen_lead.potential_contact_role
            )
            
            # Qualify the lead
            qualification = lead_manager.qualify_lead(lead)
            
            qualified_leads.append({
                "generated_lead": gen_lead.dict(),
                "qualification": qualification.dict(),
                "qualified": qualification.qualified
            })
            
        return JSONResponse(content={
            "success": True,
            "leads": qualified_leads
        })
    except Exception as e:
        logger.error(f"Error generating and qualifying leads: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# ---------------------
# Helper Functions for Lead Management
# ---------------------
def store_lead_data(email: str, lead_data: Dict[str, Any]) -> None:
    """Store lead data in the database."""
    try:
        data = {
            "email": email,
            "lead_data": json.dumps(lead_data),
            "score": lead_data.get("qualification", {}).get("score", 0),
            "qualified": lead_data.get("qualification", {}).get("qualified", False),
            "timestamp": datetime.now().isoformat()
        }
        result = supabase.table("leads").insert(data).execute()
        logger.info("Lead data stored in database", extra={"result": result.data})
    except Exception as e:
        logger.error(f"Error storing lead data in database: {e}")

def create_hubspot_contact_from_lead(hubspot_data: Dict[str, Any]) -> None:
    """Create a contact in HubSpot from lead data."""
    try:
        url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts"
        headers = hubspot_integration.get_hubspot_headers(HUBSPOT_ACCESS_TOKEN)
        response = requests.post(
            url,
            headers=headers,
            json=hubspot_data
        )
        
        if response.status_code not in (200, 201):
            logger.error(f"Error creating HubSpot contact: {response.status_code} - {response.text}")
        else:
            logger.info("Successfully created HubSpot contact from lead")
    except Exception as e:
        logger.error(f"Error creating HubSpot contact from lead: {str(e)}")

def extract_lead_info_from_chat(message: str) -> Dict[str, Any]:
    """
    Extract potential lead information from a chat message using simple pattern matching
    For production, consider using a more sophisticated NLP approach
    """
    import re
    
    lead_info = {}
    
    # Extract email with regex
    email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', message)
    if email_match:
        lead_info["email"] = email_match.group(0)
    
    # Extract phone numbers
    phone_match = re.search(r'\b(?:\+\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b', message)
    if phone_match:
        lead_info["phone"] = phone_match.group(0)
    
    # Extract name patterns (very basic)
    name_match = re.search(r'(?:I am|my name is|name[:\s]+)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)', message)
    if name_match:
        full_name = name_match.group(1).split()
        if full_name:
            lead_info["first_name"] = full_name[0]
            if len(full_name) > 1:
                lead_info["last_name"] = full_name[-1]
    
    # Extract company
    company_match = re.search(r'(?:company[:\s]+|work(?:ing)?\s+(?:at|for)|from)\s+([A-Z][A-Za-z0-9\s&]+?)(?:\.|\s|$)', message)
    if company_match:
        lead_info["company"] = company_match.group(1).strip()
    
    return lead_info

# ---------------------
# Helper Functions for AI Lead Generation
# ---------------------
def store_generated_lead(lead: GeneratedLead) -> None:
    """Store AI-generated lead in the database."""
    try:
        data = {
            "company_name": lead.company_name,
            "website": lead.website,
            "industry": lead.industry,
            "company_size": lead.estimated_company_size,
            "contact_role": lead.potential_contact_role,
            "region": lead.region,
            "relevance_score": lead.relevance_score,
            "generation_method": lead.generation_method,
            "timestamp": datetime.now().isoformat()
        }
        result = supabase.table("ai_generated_leads").insert(data).execute()
        logger.info("AI-generated lead stored in database", extra={"result": result.data})
    except Exception as e:
        logger.error(f"Error storing AI-generated lead in database: {e}")

def estimate_company_size_to_number(size_description: Optional[str]) -> Optional[int]:
    """Convert company size description to approximate employee count."""
    if not size_description:
        return None
        
    size_mapping = {
        "small": 25,
        "medium": 100,
        "large": 500,
        "enterprise": 1000
    }
    
    size_lower = size_description.lower()
    
    for key, value in size_mapping.items():
        if key in size_lower:
            return value
            
    return None

# ---------------------
# Additional API Endpoints
# ---------------------

@app.get("/api/analytics/chat-metrics")
async def get_chat_metrics():
    """Get analytics on chat usage and metrics"""
    try:
        # Query for chat metrics from the database
        total_chats = supabase.table("chat_logs").select("count", count="exact").execute()
        avg_response_time = supabase.table("chat_logs").select("avg(response_time)").execute()
        
        # Get chat counts by day for the last 7 days
        from datetime import datetime, timedelta
        
        end_date = datetime.now().isoformat()
        start_date = (datetime.now() - timedelta(days=7)).isoformat()
        
        daily_counts = supabase.table("chat_logs") \
            .select("date_trunc('day', timestamp)", "count") \
            .gte("timestamp", start_date) \
            .lte("timestamp", end_date) \
            .group_by("date_trunc('day', timestamp)") \
            .execute()
        
        return {
            "total_chats": total_chats.count if hasattr(total_chats, 'count') else 0,
            "avg_response_time": avg_response_time.data[0]["avg"] if avg_response_time.data else 0,
            "daily_counts": daily_counts.data
        }
    except Exception as e:
        logger.error(f"Error retrieving chat metrics: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/analytics/user-metrics")
async def get_user_metrics():
    """Get analytics on user engagement and metrics"""
    try:
        # Query for user metrics from the database
        total_users = supabase.table("users").select("count", count="exact").execute()
        
        # Get new users by day for the last 7 days
        from datetime import datetime, timedelta
        
        end_date = datetime.now().isoformat()
        start_date = (datetime.now() - timedelta(days=7)).isoformat()
        
        new_users = supabase.table("users") \
            .select("date_trunc('day', created_at)", "count") \
            .gte("created_at", start_date) \
            .lte("created_at", end_date) \
            .group_by("date_trunc('day', created_at)") \
            .execute()
        
        return {
            "total_users": total_users.count if hasattr(total_users, 'count') else 0,
            "new_users_last_7_days": new_users.data
        }
    except Exception as e:
        logger.error(f"Error retrieving user metrics: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/users/{user_id}/chat-history")
async def get_user_chat_history(user_id: str, limit: int = 50):
    """Get chat history for a specific user"""
    try:
        response = supabase.table("chat_logs") \
            .select("*") \
            .eq("user_id", user_id) \
            .order("timestamp", desc=True) \
            .limit(limit) \
            .execute()
        
        if not response.data:
            return {"messages": []}
        
        # Format the response for the client
        formatted_messages = []
        for msg in response.data:
            formatted_messages.append({
                "id": msg.get("id"),
                "user_message": msg.get("user_message"),
                "chatbot_reply": msg.get("chatbot_reply"),
                "timestamp": msg.get("timestamp")
            })
        
        return {"messages": formatted_messages}
    except Exception as e:
        logger.error(f"Error retrieving user chat history: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/faq")
async def get_faq_list():
    """Get list of frequently asked questions"""
    try:
        # These could be stored in a database, but for simplicity we'll define them here
        faqs = [
            {
                "question": "How does the chat system work?",
                "answer": "Our chat system uses AI to understand and respond to your questions. It leverages GPT-4 to provide helpful, accurate information on a wide range of topics."
            },
            {
                "question": "Is my conversation data secure?",
                "answer": "Yes, all conversations are encrypted and stored securely. We do not share your data with third parties, and you can request deletion of your data at any time."
            },
            {
                "question": "Can I use the chatbot without registering?",
                "answer": "Yes, you can use the chatbot as a guest by providing just an email address. However, registering gives you access to additional features like saving chat history and customizing preferences."
            },
            {
                "question": "What programming languages does the chatbot support?",
                "answer": "The chatbot can help with many programming languages including JavaScript, Python, Java, C++, Ruby, Go, PHP, and more."
            },
            {
                "question": "How can I report an issue with the chatbot?",
                "answer": "You can report issues by sending an email to support@chathub.pro with details about the problem you encountered."
            }
        ]
        
        return {"faqs": faqs}
    except Exception as e:
        logger.error(f"Error retrieving FAQs: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/feedback")
async def submit_feedback(feedback: dict):
    """Submit user feedback about the chat experience"""
    try:
        required_fields = ["email", "rating", "feedback_text"]
        for field in required_fields:
            if field not in feedback:
                raise HTTPException(status_code=400, detail=f"Missing required field: {field}")
        
        # Store feedback in database
        data = {
            "email": feedback["email"],
            "rating": feedback["rating"],
            "feedback_text": feedback["feedback_text"],
            "timestamp": datetime.now().isoformat()
        }
        
        if "user_id" in feedback:
            data["user_id"] = feedback["user_id"]
            
        result = supabase.table("user_feedback").insert(data).execute()
        logger.info("Feedback stored in database", extra={"result": result.data})
        
        return {"message": "Feedback submitted successfully", "id": result.data[0]["id"] if result.data else None}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error submitting feedback: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat/integrations/jira")
async def create_jira_issue(issue_data: dict):
    """Create a Jira issue from chat conversation"""
    try:
        # Validate required fields
        required_fields = ["email", "summary", "description", "issue_type"]
        for field in required_fields:
            if field not in issue_data:
                raise HTTPException(status_code=400, detail=f"Missing required field: {field}")
        
        # In a real implementation, this would connect to Jira API
        # For now, we'll simulate the response
        
        # Log the attempt
        logger.info(f"Jira issue creation requested", extra={"issue_data": issue_data})
        
        # Return a mock response
        return {
            "success": True,
            "message": "Jira issue created successfully",
            "issue": {
                "id": f"CHAT-{int(time.time())}",
                "key": f"CHAT-{int(time.time())}",
                "summary": issue_data["summary"],
                "status": "Open",
                "created_at": datetime.now().isoformat()
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating Jira issue: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat/integrations/slack")
async def send_to_slack(slack_data: dict):
    """Send a message to a Slack channel"""
    try:
        # Validate required fields
        required_fields = ["email", "message", "channel"]
        for field in required_fields:
            if field not in slack_data:
                raise HTTPException(status_code=400, detail=f"Missing required field: {field}")
        
        # In a real implementation, this would connect to Slack API
        # For now, we'll simulate the response
        
        # Log the attempt
        logger.info(f"Slack message requested", extra={"slack_data": slack_data})
        
        # Return a mock response
        return {
            "success": True,
            "message": f"Message sent to Slack channel #{slack_data['channel']}",
            "timestamp": datetime.now().isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sending to Slack: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/users/export-data/{user_id}")
async def export_user_data(user_id: str):
    """Export all user data (GDPR compliance)"""
    try:
        # Get user profile
        user = supabase.table("users").select("*").eq("id", user_id).execute()
        
        # Get user's chat history
        chat_history = supabase.table("chat_logs").select("*").eq("user_id", user_id).execute()
        
        # Get user's feedback submissions
        feedback = supabase.table("user_feedback").select("*").eq("user_id", user_id).execute()
        
        # Compile all data
        user_data = {
            "profile": user.data[0] if user.data else None,
            "chat_history": chat_history.data if chat_history.data else [],
            "feedback": feedback.data if feedback.data else [],
            "export_date": datetime.now().isoformat()
        }
        
        # In a production environment, this would be sent as a download
        # or emailed to the user after processing
        
        return user_data
    except Exception as e:
        logger.error(f"Error exporting user data: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/users/{user_id}")
async def delete_user_account(user_id: str):
    """Delete a user account and all associated data (GDPR compliance)"""
    try:
        # Delete chat logs
        supabase.table("chat_logs").delete().eq("user_id", user_id).execute()
        
        # Delete feedback
        supabase.table("user_feedback").delete().eq("user_id", user_id).execute()
        
        # Delete user profile (do this last)
        supabase.table("users").delete().eq("id", user_id).execute()
        
        return {"message": "User account and all associated data have been deleted"}
    except Exception as e:
        logger.error(f"Error deleting user account: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# ---------------------
# Location-Based Recommendations Endpoints
# ---------------------
@app.get("/api/products/nearby")
async def get_nearby_products(request: Request, user_email: Optional[str] = None):
    """
    Get recommendations for products near the user's location.
    Uses ipinfo to get real location data with fallback to Chennai/Ambattur area.
    """
    try:
        # Get client's IP address from the request
        client_ip = request.client.host
        
        # For local development, if IP is localhost, use a fallback IP
        if client_ip in ('127.0.0.1', 'localhost', '::1'):
            # Use a fallback IP that will resolve to somewhere in Chennai
            client_ip = '103.48.198.141'  # Example Chennai IP
        
        # Generate nearby product recommendations based on IP
        recommendations = generate_location_based_recommendations(client_ip)
        
        # Check if we got user location
        user_location = recommendations.get("user_location", {})
        
        logger.info("Generated location-based product recommendations", 
                   extra={"location": user_location.get("area", "Unknown")})
        
        return JSONResponse(content={
            "user_location": user_location,
            "recommendations": recommendations.get("recommendations", [])
        })
    except Exception as e:
        logger.error(f"Error getting nearby products: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat/product-recommendations")
async def product_recommendation_chat(chat: ChatRequest, request: Request, background_tasks: BackgroundTasks):
    """
    Specialized chat endpoint that includes location-based product recommendations
    in the response regardless of the query
    """
    logger.info("Received product recommendation chat request", extra={"email": chat.email})
    start_time = time.time()
    
    try:
        # Get client's IP address from the request
        client_ip = request.client.host
        
        # For local development, if IP is localhost, use a fallback IP
        if client_ip in ('127.0.0.1', 'localhost', '::1'):
            client_ip = '103.48.198.141'  # Example Chennai IP
        
        # Get regular chatbot response
        bot_reply = get_openai_response(chat)
        
        # Generate nearby product recommendations
        recommendations = generate_location_based_recommendations(client_ip)
        
        # Get location information
        user_location = recommendations.get("user_location", {})
        city = user_location.get("city", "Chennai")
        area = user_location.get("area", "Ambattur")
        
        # Format the recommendations into a readable string
        recommendation_text = f"\n\nBased on your location in {city} (near {area}), here are some product options nearby:\n\n"
        
        for idx, rec in enumerate(recommendations.get("recommendations", [])[:3]):
            recommendation_text += f"{idx+1}. {rec['name']} ({rec['type']})\n"
            recommendation_text += f"   Distance: {rec['distance']}\n"
            recommendation_text += f"   Address: {rec['address']}\n"
            recommendation_text += f"   Est. travel time: {rec['estimated_travel_time']}\n\n"
            
        # Combine the regular reply with recommendations
        combined_reply = bot_reply + recommendation_text
        
        response_time = round(time.time() - start_time, 2)
        
        # Store chat data in the database
        background_tasks.add_task(
            store_chat_data, 
            chat.email, 
            chat.user_id, 
            chat.message, 
            combined_reply, 
            response_time
        )
        
        logger.info("Sending product recommendations response", 
                   extra={"location": user_location.get("area", "Ambattur")})
        
        return JSONResponse(content={
            "ai_reply": combined_reply,
            "user_location": {
                "city": city,
                "area": area,
                "ip": user_location.get("ip", "Unknown")
            }
        })
    except Exception as e:
        logger.error(f"Error in product recommendation chat: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/user/ip-info")
async def get_user_ip_info(request: Request):
    """Get information about the user's IP address using ipinfo"""
    try:
        # Get client's IP address
        client_ip = request.client.host
        
        # For local development, if IP is localhost, use a fallback IP
        if client_ip in ('127.0.0.1', 'localhost', '::1'):
            client_ip = '103.48.198.141'  # Example Chennai IP
            
        # Get IP info using our module
        ip_info = get_ip_info(client_ip)
        
        return JSONResponse(content={
            "ip_info": ip_info,
            "detected_client_ip": client_ip
        })
    except Exception as e:
        logger.error(f"Error getting IP info: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Add HubSpot webhook endpoints
@app.post("/api/hubspot/webhook")
async def hubspot_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Endpoint to receive webhooks from HubSpot.
    This allows HubSpot to communicate with the chatbot when events occur.
    """
    try:
        # Read webhook data from request
        webhook_data = await request.json()
        logger.info("Received HubSpot webhook", extra={"webhook_type": webhook_data.get("subscriptionType", "unknown")})
        
        # Process the webhook data using hubspot_integration module
        # Use our own API endpoint URL for the chatbot
        chatbot_api_url = f"http://{request.url.netloc}/api/chat"
        
        # Process webhook in the background to return response quickly
        background_tasks.add_task(
            hubspot_integration.process_hubspot_webhook,
            webhook_data,
            chatbot_api_url
        )
        
        return {"status": "success", "message": "Webhook received and processing started"}
    
    except Exception as e:
        logger.error(f"Error processing HubSpot webhook: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/hubspot/configure-webhook")
async def configure_hubspot_webhook(webhook_url: str):
    """
    Configure HubSpot to send webhooks to the specified URL
    """
    try:
        # Configure the webhook in HubSpot
        result = hubspot_integration.configure_hubspot_webhook(
            webhook_url, 
            HUBSPOT_ACCESS_TOKEN,
            HUBSPOT_BASE_URL
        )
        
        if not result:
            raise HTTPException(status_code=500, detail="Failed to configure HubSpot webhook")
            
        return {"status": "success", "message": f"HubSpot webhook configured for {webhook_url}"}
        
    except Exception as e:
        logger.error(f"Error configuring HubSpot webhook: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/hubspot/conversation-history/{email}")
async def get_conversation_history(email: str, request: Request):
    """
    Get combined conversation history for a contact from both HubSpot and chatbot
    """
    try:
        # Get conversation history using the hubspot_integration module
        chatbot_api_url = f"http://{request.url.netloc}"
        
        conversations = hubspot_integration.get_contact_conversation_history(
            email,
            HUBSPOT_ACCESS_TOKEN,
            HUBSPOT_BASE_URL,
            chatbot_api_url
        )
        
        return {"email": email, "conversations": conversations}
        
    except Exception as e:
        logger.error(f"Error getting conversation history: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
