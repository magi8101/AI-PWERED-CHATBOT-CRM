#!/usr/bin/env python3
"""
HubSpot integration module for handling API calls and error handling.
"""

import logging
import requests
import json
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(name)s: %(message)s")
logger = logging.getLogger("HubSpot_Integration")

# HubSpot API Configuration - no environment variables
HUBSPOT_ACCESS_TOKEN = "YOUR_HUBSPOT_ACCESS_TOKEN"
HUBSPOT_CLIENT_SECRET = "YOUR_HUBSPOT_CLIENT_SECRET"
HUBSPOT_BASE_URL = "https://api.hubapi.com"

class LeadActivity:
    def __init__(self, email: str, activity_type: str, details: Dict[str, Any] = None):
        self.email = email
        self.activity_type = activity_type
        self.details = details or {}

def get_hubspot_headers(api_key: str) -> Dict[str, str]:
    """Return headers required for HubSpot API calls"""
    return {
        "content-type": "application/json",
        "authorization": f"Bearer {api_key}"
    }

def find_contact_by_email(email: str, api_key: str, base_url: str) -> Optional[Dict[str, Any]]:
    """Find a contact in HubSpot by email address with error handling"""
    try:
        # First find the contact ID by email
        filter_url = f"{base_url}/crm/v3/objects/contacts/search"
        headers = get_hubspot_headers(api_key)
        
        payload = {
            "filterGroups": [{
                "filters": [{
                    "propertyName": "email",
                    "operator": "EQ",
                    "value": email
                }]
            }]
        }
        
        response = requests.post(filter_url, headers=headers, json=payload)
        
        if response.status_code != 200:
            logger.error(f"HubSpot API error: {response.status_code} - {response.text}")
            return None
            
        result = response.json()
        
        if result.get("total", 0) == 0 or not result.get("results"):
            # Contact not found
            return None
            
        return result["results"][0]
    
    except Exception as e:
        logger.error(f"Error finding contact by email: {str(e)}")
        return None

def create_contact_from_chat(email: str, message: str, api_key: str, base_url: str) -> Optional[Dict[str, Any]]:
    """Create or update contact in HubSpot from chat interaction"""
    try:
        # Check if contact exists
        contact = find_contact_by_email(email, api_key, base_url)
        
        # Prepare contact properties (only use standard HubSpot properties)
        properties = {
            "email": email,
            "lifecyclestage": "lead",  # Standard HubSpot property 
        }
        
        # Try to extract name from message if not already known
        if contact is None:
            import re
            name_match = re.search(r'(?:my name is|I am|I\'m) ([A-Z][a-z]+ [A-Z][a-z]+)', message)
            if name_match:
                full_name = name_match.group(1).split()
                if len(full_name) >= 2:
                    properties["firstname"] = full_name[0]
                    properties["lastname"] = " ".join(full_name[1:])
        
        url = f"{base_url}/crm/v3/objects/contacts"
        headers = get_hubspot_headers(api_key)
        
        # If contact exists, update it
        if contact:
            contact_id = contact["id"]
            url = f"{url}/{contact_id}"
            
            properties["notes"] = f"Chat interaction on {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            
            # Use PATCH to update
            response = requests.patch(url, headers=headers, json={"properties": properties})
        else:
            # Create new contact
            response = requests.post(url, headers=headers, json={"properties": properties})
        
        if response.status_code not in (200, 201):
            logger.error(f"Error creating contact: {response.status_code} - {response.text}")
            return None
            
        return response.json()
    
    except Exception as e:
        logger.error(f"Error creating contact from chat: {str(e)}")
        return None

def log_lead_activity(activity: LeadActivity, api_key: str, base_url: str) -> bool:
    """Log lead activity in HubSpot as a note or timeline event"""
    try:
        # Find the contact first
        contact = find_contact_by_email(activity.email, api_key, base_url)
        
        if not contact:
            logger.warning(f"Cannot log activity - contact not found: {activity.email}")
            return False
        
        contact_id = contact["id"]
        
        # Create a note on the contact
        url = f"{base_url}/crm/v3/objects/notes"
        headers = get_hubspot_headers(api_key)
        
        note_content = f"Activity: {activity.activity_type}\n"
        if activity.details:
            note_content += "Details:\n"
            for key, value in activity.details.items():
                note_content += f"- {key}: {value}\n"
        
        payload = {
            "properties": {
                "hs_note_body": note_content,
                "hs_timestamp": int(datetime.now().timestamp() * 1000)
            },
            "associations": [
                {
                    "to": {"id": contact_id},
                    "types": [{"category": "HUBSPOT_DEFINED", "typeId": 1}]
                }
            ]
        }
        
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code not in (200, 201):
            logger.error(f"Error logging activity: {response.status_code} - {response.text}")
            return False
        
        return True
    
    except Exception as e:
        logger.error(f"Error logging lead activity: {str(e)}")
        return False

# ----- NEW FUNCTIONS TO ENABLE HUBSPOT TO CHATBOT COMMUNICATION -----

def process_hubspot_webhook(webhook_data: Dict[str, Any], chatbot_api_url: str) -> Tuple[bool, Optional[str]]:
    """
    Process a webhook received from HubSpot and forward relevant information to the chatbot
    
    Parameters:
    - webhook_data: The raw webhook payload from HubSpot
    - chatbot_api_url: URL of the chatbot API to communicate with
    
    Returns:
    - Success status and response from chatbot if any
    """
    try:
        # Extract relevant information from the webhook
        if not webhook_data or not isinstance(webhook_data, dict):
            logger.error("Invalid webhook data received")
            return False, "Invalid webhook data format"
            
        # Extract contact information from the webhook
        contact_id = None
        object_type = webhook_data.get("objectType", "").lower()
        
        if object_type == "contact":
            contact_id = webhook_data.get("objectId")
        else:
            associated_contacts = webhook_data.get("associatedObjectIds", {}).get("contact", [])
            if associated_contacts:
                contact_id = associated_contacts[0]
                
        if not contact_id:
            logger.error("No contact ID found in webhook data")
            return False, "No contact information found"
        
        # Get full contact details from HubSpot
        contact = get_contact_by_id(contact_id, HUBSPOT_ACCESS_TOKEN, HUBSPOT_BASE_URL)
        
        if not contact:
            return False, "Could not retrieve contact information"
            
        # Prepare data for the chatbot
        chatbot_payload = {
            "source": "hubspot",
            "webhook_type": webhook_data.get("subscriptionType", "unknown"),
            "contact": {
                "id": contact_id,
                "email": contact.get("properties", {}).get("email", ""),
                "firstname": contact.get("properties", {}).get("firstname", ""),
                "lastname": contact.get("properties", {}).get("lastname", ""),
                "company": contact.get("properties", {}).get("company", "")
            },
            "event_data": webhook_data.get("propertyValue", {})
        }
        
        # Forward to chatbot API
        chatbot_response = send_to_chatbot(chatbot_payload, chatbot_api_url)
        
        return True, chatbot_response
        
    except Exception as e:
        logger.error(f"Error processing HubSpot webhook: {str(e)}")
        return False, f"Error: {str(e)}"

def get_contact_by_id(contact_id: str, api_key: str, base_url: str) -> Optional[Dict[str, Any]]:
    """Retrieve contact details by ID"""
    try:
        url = f"{base_url}/crm/v3/objects/contacts/{contact_id}"
        headers = get_hubspot_headers(api_key)
        
        response = requests.get(url, headers=headers)
        
        if response.status_code != 200:
            logger.error(f"Error retrieving contact: {response.status_code} - {response.text}")
            return None
            
        return response.json()
    except Exception as e:
        logger.error(f"Error getting contact by ID: {str(e)}")
        return None

def send_to_chatbot(data: Dict[str, Any], chatbot_api_url: str) -> Optional[str]:
    """
    Send data to the chatbot API endpoint
    
    Parameters:
    - data: Payload to send to the chatbot
    - chatbot_api_url: URL of the chatbot API endpoint
    
    Returns:
    - Response from chatbot or None if failed
    """
    try:
        # If contact has email, prepare chat message
        if data.get("contact", {}).get("email"):
            email = data["contact"]["email"]
            name = f"{data['contact'].get('firstname', '')} {data['contact'].get('lastname', '')}".strip()
            
            # Create message for chatbot
            message = ""
            event_type = data.get("webhook_type", "").lower()
            
            # Customize message based on webhook type
            if "form_submission" in event_type:
                message = f"HubSpot form submission from {name} ({email})"
            elif "property_change" in event_type:
                message = f"HubSpot contact property update for {name} ({email})"
            elif "email_event" in event_type:
                message = f"HubSpot email interaction with {name} ({email})"
            else:
                message = f"HubSpot activity detected for {name} ({email})"
                
            # Send request to chatbot API
            chatbot_request = {
                "email": email,
                "user_id": f"hubspot_{data['contact'].get('id', 'unknown')}",
                "message": message,
                "history": [],
                "hubspot_data": data  # Pass the full HubSpot data for context
            }
            
            response = requests.post(
                chatbot_api_url, 
                json=chatbot_request,
                headers={"Content-Type": "application/json"}
            )
            
            if response.status_code != 200:
                logger.error(f"Error from chatbot API: {response.status_code} - {response.text}")
                return None
                
            result = response.json()
            return result.get("ai_reply")
        else:
            logger.error("No contact email found in data")
            return None
            
    except Exception as e:
        logger.error(f"Error sending to chatbot: {str(e)}")
        return None

def configure_hubspot_webhook(webhook_url: str, api_key: str, base_url: str) -> bool:
    """
    Configure a webhook subscription in HubSpot
    
    Parameters:
    - webhook_url: The URL where HubSpot should send webhook data
    - api_key: HubSpot API key
    - base_url: HubSpot API base URL
    
    Returns:
    - True if successful, False otherwise
    """
    try:
        url = f"{base_url}/webhooks/v3/app/subscriptions"
        headers = get_hubspot_headers(api_key)
        
        # Define events to trigger webhooks
        payload = {
            "eventType": "contact.propertyChange",
            "propertyName": "*",  # Listen for changes to any property
            "active": True,
            "webhookUrl": webhook_url
        }
        
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code not in (200, 201):
            logger.error(f"Error configuring webhook: {response.status_code} - {response.text}")
            return False
            
        logger.info(f"Successfully configured HubSpot webhook: {webhook_url}")
        return True
        
    except Exception as e:
        logger.error(f"Error configuring HubSpot webhook: {str(e)}")
        return False

def get_contact_conversation_history(email: str, api_key: str, base_url: str, chatbot_api_url: str) -> List[Dict[str, Any]]:
    """
    Get conversation history for a contact from both HubSpot and chatbot system
    
    Parameters:
    - email: Contact email address
    - api_key: HubSpot API key
    - base_url: HubSpot API base URL
    - chatbot_api_url: URL for chatbot API
    
    Returns:
    - List of conversation entries from both systems
    """
    try:
        conversations = []
        
        # Get contact from HubSpot
        contact = find_contact_by_email(email, api_key, base_url)
        
        if not contact:
            logger.warning(f"Contact not found: {email}")
            return []
            
        contact_id = contact["id"]
        
        # Get HubSpot engagement history (notes, emails, meetings)
        url = f"{base_url}/crm/v3/objects/contacts/{contact_id}/associations/notes"
        headers = get_hubspot_headers(api_key)
        
        response = requests.get(url, headers=headers)
        
        if response.status_code != 200:
            logger.error(f"Error getting contact notes: {response.status_code} - {response.text}")
        else:
            notes_data = response.json()
            note_ids = [result["id"] for result in notes_data.get("results", [])]
            
            # Get details for each note
            for note_id in note_ids:
                note_url = f"{base_url}/crm/v3/objects/notes/{note_id}"
                note_response = requests.get(note_url, headers=headers)
                
                if note_response.status_code == 200:
                    note = note_response.json()
                    
                    # Add to conversation history
                    conversations.append({
                        "source": "hubspot",
                        "type": "note", 
                        "content": note.get("properties", {}).get("hs_note_body", ""),
                        "timestamp": note.get("properties", {}).get("hs_createdate", ""),
                        "system": "HubSpot"
                    })
        
        # Get chatbot conversation history (needs API endpoint in chatbot system)
        try:
            chatbot_history_url = f"{chatbot_api_url}/api/chat/history"
            chatbot_response = requests.get(
                chatbot_history_url,
                params={"email": email},
                headers={"Content-Type": "application/json"}
            )
            
            if chatbot_response.status_code == 200:
                chatbot_history = chatbot_response.json().get("history", [])
                
                for entry in chatbot_history:
                    conversations.append({
                        "source": "chatbot",
                        "type": "message",
                        "user_message": entry.get("user_message", ""),
                        "bot_reply": entry.get("chatbot_reply", ""),
                        "timestamp": entry.get("timestamp", ""),
                        "system": "Chatbot"
                    })
                    
        except Exception as chat_err:
            logger.error(f"Error getting chatbot history: {str(chat_err)}")
        
        # Sort all conversations by timestamp if available
        try:
            conversations.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        except:
            # If sorting fails, just return the unsorted list
            pass
            
        return conversations
        
    except Exception as e:
        logger.error(f"Error getting contact conversation history: {str(e)}")
        return []

# Usage examples:
if __name__ == "__main__":
    # Example: Create or update a contact
    email = "test@example.com"
    message = "Hello, my name is John Doe and I am interested in your services."
    contact_result = create_contact_from_chat(email, message, HUBSPOT_ACCESS_TOKEN, HUBSPOT_BASE_URL)
    print(f"Contact operation result: {contact_result}")
    
    # Example: Log an activity
    activity = LeadActivity(
        email="test@example.com",
        activity_type="chat_session",
        details={"message_count": 5, "duration": "10m"}
    )
    activity_result = log_lead_activity(activity, HUBSPOT_ACCESS_TOKEN, HUBSPOT_BASE_URL)
    print(f"Activity logging result: {activity_result}")
