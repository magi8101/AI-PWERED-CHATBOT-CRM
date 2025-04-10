"""
Lead Manager Module - Handles lead qualification, scoring, and automated responses
"""
import logging
import json
from datetime import datetime
import random
from typing import Dict, Any, List, Optional, Tuple
import requests
from pydantic import BaseModel, EmailStr, Field
import ipinfo  # New import for ipinfo package

# Configure logging
logger = logging.getLogger("LeadManager")

# Date format constants for HubSpot compatibility
HUBSPOT_DATE_FORMAT = "%m/%d/%Y"
HUBSPOT_DATETIME_FORMAT = "%m/%d/%Y %I:%M %p"  # Example: 06/15/2023 8:00 AM

# Add Chennai location data
CHENNAI_LOCATION = {
    "city": "Chennai",
    "region": "Tamil Nadu",
    "country": "India",
    "latitude": 13.0827,
    "longitude": 80.2707,
    "areas": [
        {"name": "Ambattur", "latitude": 13.1143, "longitude": 80.1548},
        {"name": "Anna Nagar", "latitude": 13.0891, "longitude": 80.2107},
        {"name": "T Nagar", "latitude": 13.0418, "longitude": 80.2341},
        {"name": "Velachery", "latitude": 12.9815, "longitude": 80.2180},
        {"name": "Adyar", "latitude": 13.0012, "longitude": 80.2565},
        {"name": "Porur", "latitude": 13.0359, "longitude": 80.1567},
        {"name": "Guindy", "latitude": 13.0070, "longitude": 80.2143}
    ]
}

# Sample product locations in Chennai (fictitious)
PRODUCT_LOCATIONS = [
    {"name": "Chennai Office Solutions", "type": "office_supplies", "latitude": 13.1133, "longitude": 80.1538, "address": "23 Ambattur Industrial Estate, Chennai"},
    {"name": "TechHub Chennai", "type": "electronics", "latitude": 13.0881, "longitude": 80.2117, "address": "45 Anna Nagar East, Chennai"},
    {"name": "Mega Retail Center", "type": "retail", "latitude": 13.0408, "longitude": 80.2351, "address": "78 T Nagar Main Road, Chennai"},
    {"name": "Chennai Business Park", "type": "office_space", "latitude": 13.0349, "longitude": 80.1577, "address": "120 Porur Highway, Chennai"},
    {"name": "IT Solutions Center", "type": "software", "latitude": 13.0060, "longitude": 80.2153, "address": "56 Guindy Industrial Area, Chennai"}
]

# Replace the simulated token with a placeholder for the real ipinfo API token
IPINFO_API_TOKEN = "your_ipinfo_api_token_here"  # Replace with your actual token

# Function to get real IP information using the ipinfo package
def get_ip_info(ip: Optional[str] = None) -> Dict[str, Any]:
    """
    Get IP information using the ipinfo Python package
    
    Parameters:
    - ip: Optional IP address. If None, gets information about the requestor's IP
    
    Returns:
    - Dictionary with IP geo-location data
    """
    try:
        # Initialize the ipinfo handler with your token
        handler = ipinfo.getHandler(IPINFO_API_TOKEN)
        
        # Get the IP details
        if ip:
            details = handler.getDetails(ip)
        else:
            details = handler.getDetails()  # Gets info for the requesting IP
        
        # Convert the details object to a dictionary
        ip_data = details.all
        
        # Add 'area' field to maintain compatibility with the rest of the code
        if "loc" in ip_data and "," in ip_data["loc"]:
            ip_data["area"] = get_nearest_area(ip_data["loc"])
        else:
            # Fallback to a default area in Chennai
            ip_data["area"] = "Ambattur"
            
        return ip_data
        
    except Exception as e:
        logger.error(f"Error fetching IP info from ipinfo: {str(e)}")
        # Fallback to Ambattur area in Chennai when API call fails
        return generate_fake_ip_info(True)

# Function to get the nearest area in Chennai based on coordinates
def get_nearest_area(loc_str: str) -> str:
    """Find the nearest area in Chennai based on coordinates"""
    try:
        latitude, longitude = map(float, loc_str.split(","))
        
        min_distance = float('inf')
        nearest_area = "Ambattur"  # Default area
        
        for area in CHENNAI_LOCATION["areas"]:
            dist = calculate_distance(
                latitude, longitude,
                area["latitude"], area["longitude"]
            )
            
            if dist < min_distance:
                min_distance = dist
                nearest_area = area["name"]
                
        return nearest_area
    except:
        # Default to Ambattur on any error
        return "Ambattur"

# Function to generate fake IP info (fallback when simulated IP info fails)
def generate_fake_ip_info(use_default_location: bool = True) -> Dict[str, Any]:
    """
    Generate fake IP information, optionally using a default Chennai location
    
    Parameters:
    - use_default_location: Whether to use the default Chennai location
    
    Returns:
    - Dictionary with IP geo-location data
    """
    if use_default_location:
        area = random.choice(CHENNAI_LOCATION["areas"])
        
        # Add slight randomization to coordinates for variety
        lat_offset = random.uniform(-0.01, 0.01)
        long_offset = random.uniform(-0.01, 0.01)
        
        return {
            "ip": f"103.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(0, 255)}",
            "hostname": f"host-{random.randint(100, 999)}.airtel.net.in",
            "city": CHENNAI_LOCATION["city"],
            "region": CHENNAI_LOCATION["region"],
            "country": CHENNAI_LOCATION["country"],
            "loc": f"{area['latitude'] + lat_offset},{area['longitude'] + long_offset}",
            "org": f"AS{random.randint(10000, 99999)} Bharti Airtel Ltd.",
            "postal": f"6000{random.randint(10, 99)}",
            "timezone": "Asia/Kolkata",
            "area": area["name"]
        }
    else:
        # Generate completely random location (not used in this implementation)
        return {
            "ip": f"{random.randint(1, 255)}.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(0, 255)}",
            "hostname": f"host-{random.randint(100, 999)}.example.com",
            "city": "Random City",
            "region": "Random Region",
            "country": "Random Country",
            "loc": f"{random.uniform(-90, 90)},{random.uniform(-180, 180)}",
            "org": f"AS{random.randint(10000, 99999)} Example ISP",
            "postal": f"{random.randint(10000, 99999)}",
            "timezone": "UTC"
        }

# Calculate distance between two geo coordinates
def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the approximate distance between two points (in km)
    using the Haversine formula
    
    Parameters:
    - lat1, lon1: Coordinates of first point
    - lat2, lon2: Coordinates of second point
    
    Returns:
    - Distance in kilometers
    """
    from math import radians, sin, cos, sqrt, atan2
    
    # Earth radius in kilometers
    R = 6371.0
    
    # Convert degrees to radians
    lat1_rad = radians(lat1)
    lon1_rad = radians(lon1)
    lat2_rad = radians(lat2)
    lon2_rad = radians(lon2)
    
    # Difference in coordinates
    dlon = lon2_rad - lon1_rad
    dlat = lat2_rad - lat1_rad
    
    # Haversine formula
    a = sin(dlat / 2)**2 + cos(lat1_rad) * cos(lat2_rad) * sin(dlon / 2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    
    distance = R * c
    
    return distance

# Find nearby product locations
def find_nearby_products(lat: float, lon: float, max_distance: float = 10.0) -> List[Dict[str, Any]]:
    """
    Find product locations within specified distance of coordinates
    
    Parameters:
    - lat, lon: User coordinates
    - max_distance: Maximum distance in kilometers (default 10 km)
    
    Returns:
    - List of nearby product locations with distance added
    """
    nearby = []
    
    for location in PRODUCT_LOCATIONS:
        distance = calculate_distance(
            lat, lon, 
            location["latitude"], location["longitude"]
        )
        
        if distance <= max_distance:
            location_copy = location.copy()
            location_copy["distance_km"] = round(distance, 2)
            nearby.append(location_copy)
    
    # Sort by distance
    nearby.sort(key=lambda x: x["distance_km"])
    
    return nearby

# Generate location-based product recommendations using simulated IP info
def generate_location_based_recommendations(client_ip: Optional[str] = None) -> Dict[str, Any]:
    """
    Generate product recommendations based on location
    
    Parameters:
    - client_ip: Optional client IP address. If None, generates simulated IP info
    
    Returns:
    - Dictionary with location data and recommended products
    """
    try:
        # Get simulated IP info
        ip_info = get_ip_info(client_ip)
        
        # Parse location coordinates
        if "loc" in ip_info and "," in ip_info["loc"]:
            lat, lon = map(float, ip_info["loc"].split(","))
        else:
            # Use Ambattur coordinates as fallback
            area = next((a for a in CHENNAI_LOCATION["areas"] if a["name"] == "Ambattur"), 
                        CHENNAI_LOCATION["areas"][0])
            lat, lon = area["latitude"], area["longitude"]
        
        # Find nearby product locations
        nearby_products = find_nearby_products(lat, lon, 15.0)
        
        # Format recommendations
        recommendations = []
        for product in nearby_products:
            recommendations.append({
                "name": product["name"],
                "type": product["type"],
                "distance": f"{product['distance_km']} km",
                "address": product["address"],
                "estimated_travel_time": f"{int(product['distance_km'] * 3)} minutes"  # Rough estimate
            })
        
        return {
            "user_location": {
                "ip": ip_info.get("ip", "Unknown"),
                "city": ip_info.get("city", "Chennai"),
                "area": ip_info.get("area", "Ambattur"),
                "coordinates": f"{lat},{lon}"
            },
            "recommendations": recommendations,
            "recommendation_time": datetime.now().isoformat()
        }
    
    except Exception as e:
        logger.error(f"Error generating location-based recommendations: {str(e)}")
        # Fallback to fake data on error
        return generate_fake_recommendations()

# Fallback function to generate fake recommendations
def generate_fake_recommendations() -> Dict[str, Any]:
    """Generate fake product recommendations near Chennai as fallback"""
    # Use a fixed area in Chennai (Ambattur)
    area = next((a for a in CHENNAI_LOCATION["areas"] if a["name"] == "Ambattur"), 
                CHENNAI_LOCATION["areas"][0])
    lat, lon = area["latitude"], area["longitude"]
    
    # Find nearby products
    nearby_products = find_nearby_products(lat, lon, 15.0)
    
    # Format recommendations
    recommendations = []
    for product in nearby_products:
        recommendations.append({
            "name": product["name"],
            "type": product["type"],
            "distance": f"{product['distance_km']} km",
            "address": product["address"],
            "estimated_travel_time": f"{int(product['distance_km'] * 3)} minutes"
        })
    
    return {
        "user_location": {
            "ip": f"103.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(0, 255)}",
            "city": "Chennai",
            "area": "Ambattur",
            "coordinates": f"{lat},{lon}"
        },
        "recommendations": recommendations,
        "recommendation_time": datetime.now().isoformat()
    }

# Enhanced lead enrichment that includes location data
def enrich_lead_data_with_location(lead: 'Lead', api_key: str, client_ip: Optional[str] = None) -> Dict[str, Any]:
    """
    Enrich lead data with location info and nearby product recommendations
    
    Parameters:
    - lead: Existing lead data
    - api_key: OpenAI API key
    - client_ip: Optional client IP address
    
    Returns:
    - Dictionary with enriched lead data including location-based recommendations
    """
    try:
        # Get simulated IP info
        ip_info = get_ip_info(client_ip)
        
        # Get basic lead enrichment (assuming this function exists elsewhere in the code)
        try:
            basic_enrichment = enrich_lead_data(lead, api_key)
        except:
            basic_enrichment = {}
        
        # Generate location-based recommendations
        location_recommendations = generate_location_based_recommendations(client_ip)
        
        # Combine the results
        result = basic_enrichment.copy() if isinstance(basic_enrichment, dict) else {}
        result["location_info"] = ip_info
        result["nearby_recommendations"] = location_recommendations
        
        return result
    except Exception as e:
        logger.error(f"Error enriching lead data with location: {str(e)}")
        # Fallback to fake data on error
        ip_info = generate_fake_ip_info(True)
        
        result = {}
        if lead:
            result["lead"] = {
                "email": lead.email,
                "name": f"{lead.first_name or ''} {lead.last_name or ''}".strip() or None
            }
        
        result["location_info"] = ip_info
        result["nearby_recommendations"] = generate_fake_recommendations()
        
        return result

def format_date_for_hubspot(date_obj: datetime) -> str:
    """Format a datetime object to mm/dd/yyyy for HubSpot"""
    if not date_obj:
        return ""
    return date_obj.strftime(HUBSPOT_DATE_FORMAT)

def format_datetime_for_hubspot(date_obj: datetime) -> str:
    """Format a datetime object to mm/dd/yyyy h:MM AM/PM for HubSpot"""
    if not date_obj:
        return ""
    # Return the date with time set to 8:00 AM
    formatted_date = date_obj.strftime("%m/%d/%Y")
    return f"{formatted_date} 8:00 AM"

class LeadQualificationCriteria(BaseModel):
    """Model for lead qualification criteria"""
    min_company_size: Optional[int] = None
    target_industries: Optional[List[str]] = None
    budget_threshold: Optional[float] = None
    decision_maker_titles: Optional[List[str]] = None
    buying_timeframe: Optional[str] = None
    required_fields: List[str] = ["email"]

class Lead(BaseModel):
    """Model for lead data"""
    email: EmailStr
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company: Optional[str] = None
    company_size: Optional[int] = None
    industry: Optional[str] = None
    job_title: Optional[str] = None
    phone: Optional[str] = None
    budget: Optional[float] = None
    buying_timeframe: Optional[str] = None
    website: Optional[str] = None
    source: Optional[str] = None
    message: Optional[str] = None
    location: Optional[Dict[str, Any]] = None
    created_at: datetime = Field(default_factory=datetime.now)
    last_contact_date: Optional[datetime] = None
    
    def get_hubspot_formatted_dates(self) -> Dict[str, str]:
        """Return all dates formatted for HubSpot"""
        return {
            "created_at": format_datetime_for_hubspot(self.created_at),
            "last_contact_date": format_datetime_for_hubspot(self.last_contact_date) if self.last_contact_date else ""
        }

class GeneratedLead(BaseModel):
    """Model for leads generated by AI"""
    company_name: str
    website: Optional[str] = None
    industry: Optional[str] = None
    estimated_company_size: Optional[str] = None
    potential_contact_role: Optional[str] = None
    region: Optional[str] = None
    relevance_score: Optional[float] = None
    generation_method: str = "ai_suggested"

class LeadGenerationRequest(BaseModel):
    """Model for lead generation request parameters"""
    industry: str
    region: Optional[str] = None
    company_size: Optional[str] = None
    product_interest: Optional[str] = None
    count: int = 5
    min_relevance_score: float = 0.7

class AILeadModel(BaseModel):
    """Model for the AI Lead Generation capabilities"""
    industry_focus: Optional[List[str]] = None
    region_focus: Optional[List[str]] = None
    custom_prompt: Optional[str] = None
    min_confidence: float = 0.7
    max_results: int = 10
