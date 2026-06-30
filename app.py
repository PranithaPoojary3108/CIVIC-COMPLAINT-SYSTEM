from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response
from functools import wraps
import os
import json
import uuid
import smtplib
import ssl
import csv
import io
import difflib
import re
from datetime import datetime, timedelta
from postgrest.exceptions import APIError
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename
import hashlib

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-in-production')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=2)
app.config['WTF_CSRF_ENABLED'] = True

# ─── Config ───────────────────────────────────────────────────────────────────
UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5MB
app.config['EMAIL_FROM'] = os.environ.get('EMAIL_FROM', 'noreply@civicpulse.local')
app.config['EMAIL_HOST'] = os.environ.get('SMTP_HOST')
app.config['EMAIL_PORT'] = int(os.environ.get('SMTP_PORT', 465))
app.config['EMAIL_USER'] = os.environ.get('SMTP_USER')
app.config['EMAIL_PASS'] = os.environ.get('SMTP_PASS')
app.config['ADMIN_EMAILS'] = [e.strip() for e in os.environ.get('ADMIN_EMAILS', '').split(',') if e.strip()]
app.config['STATUS_FLOW'] = ['Pending', 'In Progress', 'Resolved']

# Simple in-memory fallback for local/dev use when Supabase is not configured.
APP_NOTIFICATIONS = {}

CATEGORIES = ['Roads', 'Water Supply', 'Electricity', 'Sanitation', 'Others',
               'Garbage', 'Pothole', 'Streetlight', 'Water Leakage', 'Road Damage',
               'Public Safety', 'Noise', 'Other']
PRIORITIES = ['Low', 'Medium', 'High']
DEPARTMENTS = ['Roads', 'Water Works', 'Electricity Board', 'Sanitation', 'Street Lighting',
                'Public Safety', 'Noise Control', 'General Services']

# ─── Supabase ─────────────────────────────────────────────────────────────────
def get_supabase():
    from supabase import create_client
    url = os.environ.get('SUPABASE_URL')
    key = os.environ.get('SUPABASE_KEY')
    if not url or not key:
        return None
    return create_client(url, key)

# ─── AI (Gemini) ──────────────────────────────────────────────────────────────
def analyze_complaint_with_ai(title, description):
    try:
        import google.generativeai as genai
        api_key = os.environ.get('GEMINI_API_KEY')
        if not api_key:
            return _fallback_analysis(title, description)
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = f"""Analyze this civic complaint and respond with ONLY valid JSON:

Title: {title}
Description: {description}

Return this exact JSON:
{{
  "category": "one of: Roads, Water Supply, Electricity, Sanitation, Others, Garbage, Pothole, Streetlight, Water Leakage, Road Damage, Public Safety, Noise, Other",
  "priority": "one of: Low, Medium, High",
  "summary": "one concise sentence (max 20 words)",
  "department": "one of: Roads, Water Works, Electricity Board, Sanitation, Street Lighting, Public Safety, Noise Control, General Services",
  "sentiment": "one of: Neutral, Frustrated, Urgent, Satisfied",
  "tags": ["array", "of", "relevant", "keywords"]
}}"""
        response = model.generate_content(prompt)
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text.strip())
        data.setdefault('department', _suggest_department(data.get('category', 'Other')))
        data.setdefault('sentiment', 'Neutral')
        data.setdefault('tags', [])
        return data
    except Exception as e:
        print(f"AI analysis error: {e}")
        return _fallback_analysis(title, description)


def _get_faq_database():
    """Comprehensive FAQ knowledge base for the chatbot."""
    return {
        # About Application
        ('what is this application', 'what is civic pulse', 'tell me about this app'): 
            "CivicPulse is a smart civic complaint management system where citizens can report local issues (potholes, broken streetlights, garbage piles, water leaks, etc.) and track their resolution. Our AI automatically categorizes complaints, sets priority levels, and generates summaries. Admins review, prioritize, and assign complaints to relevant departments for swift resolution.",
        
        ('what is the complaint of my project', 'project complaint', 'project issues', 'what complaints does this handle'):
            "CivicPulse handles civic infrastructure and public service complaints including: 1) Roads (potholes, damage, pavement cracks), 2) Water Supply (leaks, low pressure, supply issues), 3) Electricity (outages, broken lines, voltage issues), 4) Sanitation (garbage piles, waste collection delays), 5) Streetlights (broken, flickering, non-functional), 6) Public Safety (hazards, accidents, security concerns), 7) Noise pollution (excessive noise, disturbances). Our goal is to empower citizens to report problems and enable quick resolution through AI-powered categorization and admin coordination with responsible departments.",
        
        ('how does this complaint portal work', 'how does this system work', 'explain the process'):
            "The process is simple: 1) You file a complaint with details and photos. 2) Our AI analyzes and categorizes it. 3) Admin team reviews and verifies. 4) Complaint is assigned to the relevant department. 5) Status updates as work progresses (Pending → In Progress → Resolved). 6) You receive notifications at each stage.",
        
        ('what services does this portal provide', 'what can i do here'):
            "CivicPulse provides: Filing civic complaints (roads, water, garbage, streetlights, public safety), AI-powered analysis and categorization, real-time status tracking, image upload for evidence, admin dashboard for complaint management, priority-based sorting, automated notifications, complaint timeline history, and an AI assistant for guidance.",
        
        ('how can you help me', 'what can you do'):
            "I can help you with: Filing new complaints (step-by-step guidance), understanding complaint statuses (Pending, In Progress, Resolved), uploading photos and locations, providing civic tips, answering FAQs about the system, explaining how AI works, guiding you through registration/login, and tracking your complaints. Just ask me anything!",
        
        # Filing Complaints
        ('what types of complaints can i register', 'what complaints can i file', 'complaint categories'):
            "You can report: 1) Roads (potholes, damage, cracks), 2) Water (leaks, supply issues), 3) Electricity (outages, broken lines), 4) Garbage/Sanitation (piles, uncollected waste), 5) Streetlights (broken, flickering), 6) Public Safety (hazards, accidents), 7) Noise pollution. Simply describe your issue and our AI categorizes it automatically.",
        
        ('why should i use this portal', 'benefits of this app', 'why register'):
            "Benefits: Your voice matters—complaints reach the right departments efficiently. AI prioritizes urgent issues. Real-time tracking keeps you informed. Multiple stakeholders see your complaint, ensuring accountability. Photo evidence speeds resolution. Admins coordinate across departments systematically. You contribute to community improvement. It's free and accessible anytime, anywhere.",
        
        ('how do i file a complaint', 'file a complaint', 'submit complaint'):
            "Filing is simple: 1) Click 'New Complaint' button. 2) Enter a clear title (e.g., 'Pothole on Oak Street'). 3) Write a detailed description of the issue. 4) Provide the exact location with landmarks. 5) Upload a photo if available (highly recommended). 6) Click Submit. You'll receive a confirmation and can track it on your dashboard. The more details you provide, the faster it gets resolved.",
        
        ('how can i report a pothole', 'reporting pothole'):
            "To report a pothole: 1) Click 'New Complaint'. 2) Title: 'Pothole on [Street Name]'. 3) Description: Size (small/large), depth if known, surface condition, if it's a hazard. 4) Location: Exact street address with nearby landmarks. 5) Photo: Take a clear picture showing the pothole and surrounding area. 6) Submit. Our AI will categorize it as 'Road Damage' and admins will route it to the Roads Department.",
        
        ('where do i submit a garbage complaint', 'garbage complaint', 'report garbage'):
            "Submit a garbage complaint: 1) Go to 'New Complaint'. 2) Title: 'Garbage pile on [Location]'. 3) Description: Type of garbage, quantity, duration (how long it's been there), health hazard level. 4) Location: Exact spot with landmarks. 5) Photo: Show the garbage pile clearly. 6) Submit. AI categorizes it as 'Sanitation' and admins assign it to the Sanitation Department immediately.",
        
        # Images
        ('can i upload an image with my complaint', 'upload image', 'add photo'):
            "Yes, absolutely! Upload supporting images when filing. 1) Click 'Choose File' in the complaint form. 2) Select your image (JPG, PNG format). 3) The image displays as a preview before submission. 4) Submit the complaint with the image attached. Photos provide visual evidence, significantly speed up admin review, and help departments understand the issue better. Highly recommended!",
        
        ('can i upload multiple images', 'multiple images', 'how many photos'):
            "Currently, you can upload one image per complaint submission. However, after filing, you can edit your complaint and update the image. For complex issues, describe all details in the description field, and the main photo should show the most critical aspect of the problem.",
        
        ('which image formats are supported', 'image format', 'what image types'):
            "Supported formats: JPG (.jpg, .jpeg) and PNG (.png). These formats compress well while maintaining quality, making uploads fast. If you have images in other formats (BMP, GIF, TIFF), convert them to JPG or PNG using free online tools before uploading.",
        
        ('why should i upload an image', 'importance of image', 'benefits of photo'):
            "Images are powerful because they: 1) Provide visual proof of the problem. 2) Help admins and departments understand the issue immediately. 3) Reduce back-and-forth clarification. 4) Speed up resolution significantly. 5) Deter false complaints. 6) Serve as documentation. Photo evidence can reduce resolution time by 50-70%!",
        
        ('what is the maximum image size', 'image size limit', 'file size'):
            "The recommended maximum image size is 5 MB. Most smartphone photos (2-4 MB) upload instantly. For faster uploads, use compressed images or reduce resolution slightly. Our system automatically optimizes images for storage.",
        
        ('is image upload mandatory', 'must i upload image', 'required image'):
            "Image upload is optional but highly recommended. You can file without an image, but including one dramatically improves the chance of quick resolution. Photos provide visual proof and help admins prioritize effectively. If possible, always include a clear, well-lit photo of the civic issue.",
        
        # Location
        ('is my location mandatory', 'location required', 'must provide location'):
            "Yes, location is mandatory and crucial. Without a precise location, admins and departments cannot find and fix the problem. Provide: Street name, building number, nearby landmarks, district, or recognizable features. Vague locations (like 'near the area') delay resolution significantly.",
        
        ('why should i provide my location', 'importance of location', 'location benefit'):
            "Location details are essential because: 1) Admins can verify the issue exists. 2) Departments know exactly where to send teams. 3) Prevents wasted time searching. 4) Enables quick resolution. 5) Serves as official record. Precise locations + photos = fastest resolution.",
        
        ('can i manually enter my location', 'manual location', 'type location'):
            "Yes, you can type your location manually in the complaint form. Provide: Street name, area, nearby landmarks, building numbers. For example: 'Oak Street between Main Market and Hospital, Building #45.' The more specific, the better.",
        
        ('does gps work in this application', 'gps location', 'automatic location'):
            "The current version requires manual location entry for accuracy. You can note GPS coordinates if you have them (e.g., Latitude: 28.7041, Longitude: 77.1025), but detailed street addresses work best. Future versions may include automatic GPS detection.",
        
        ('can i change my complaint location', 'update location', 'modify location'):
            "Yes! After filing, you can edit your complaint to correct or update the location. Click 'Edit' on your complaint details page, update the location information, and save changes. This helps if you provided incomplete details initially.",
        
        # Authentication
        ('how do i register', 'sign up', 'create account'):
            "Registration is quick: 1) Click 'Register' on the login page. 2) Enter your name. 3) Provide your email address. 4) Create a password (recommended: mix of letters, numbers, special characters). 5) Enter your phone number. 6) Click 'Sign Up'. You'll receive a confirmation and can immediately log in to file complaints.",
        
        ('i forgot my password', 'reset password', 'recover password'):
            "If you forgot your password: 1) Click 'Forgot Password?' on the login page. 2) Enter your registered email. 3) Check your email for a password reset link. 4) Click the link and create a new password. 5) Log in with your new password. If you don't receive an email within 5 minutes, check spam folder or contact admin support.",
        
        ('how do i login', 'sign in', 'log in'):
            "Logging in is simple: 1) Go to the login page. 2) Enter your registered email address. 3) Enter your password. 4) Click 'Login'. You'll be directed to your personal dashboard showing your complaints, notifications, and activity. Your session stays active for 2 hours.",
        
        ('can i change my email', 'update email', 'modify email'):
            "Yes, you can update your email: 1) Go to your profile/settings. 2) Click 'Edit Email'. 3) Enter your new email address. 4) Verify the new email (confirmation link sent). 5) Save changes. You'll log in with your new email from next time. Your old email won't be valid for login anymore.",
        
        ('how do i logout', 'sign out', 'exit'):
            "To log out: 1) Click your profile icon (top-right corner). 2) Select 'Logout'. You'll be redirected to the homepage. Your session ends, and you'll need to log in again to access your dashboard. For security, always log out on shared devices.",
        
        # Complaint Status
        ('how can i check my complaint status', 'track complaint', 'check progress'):
            "Check status easily: 1) Log in to your dashboard. 2) You'll see all your filed complaints in a table. 3) Click any complaint to view full details. 4) See the current status (Pending, In Progress, Resolved). 5) View the complete timeline showing all status changes. 6) Check notifications for updates. You can also filter by status.",
        
        ('what is the progress of my complaint', 'progress of my complaint', 'where is my complaint', 'my complaint progress', 'status of my complaint'):
            "To check your complaint's progress: 1) Log in to your dashboard with your email and password. 2) Find your complaint in the list (search by title or date). 3) Click on it to view detailed information. 4) You'll see: Current status (Pending/In Progress/Resolved), complete timeline of all status changes, when it was filed, last update timestamp, assigned department, priority level, and all admin notes/comments. 5) You'll also receive email notifications whenever the status changes. If your complaint hasn't moved in 3+ days, check if admin needs more information.",
        
        ('what does pending mean', 'pending status', 'pending complaint'):
            "Pending means your complaint has been received and is waiting for admin review. During this phase: The admin team verifies your details, checks photo/location, categorizes the issue, determines priority, and prepares it for assignment. Typical duration: 1-2 days. You'll be notified when status changes to 'In Progress.'",
        
        ('what does in progress mean', 'in progress status', 'being resolved'):
            "In Progress means your complaint is actively being worked on. The assigned department is investigating, coordinating resources, and taking corrective action. You may see physical teams visiting the location, fixing issues, or conducting assessments. Typical duration: 3-14 days depending on complexity. Admins keep this status updated as work progresses.",
        
        ('what does resolved mean', 'resolved status', 'completed complaint'):
            "Resolved means the issue has been fixed and the complaint is closed. The department has completed corrective action, verified the fix, and reported completion to admins. Your complaint is now part of the historical record. You can still view all details and timeline. Filing future related complaints is easy from your dashboard.",
        
        ('why is my complaint still pending', 'stuck pending', 'pending too long'):
            "If your complaint is pending longer than expected: 1) Admins may need clarification—check notifications. 2) High volume may cause delays. 3) Missing photo/location details slow verification. 4) Weekend/holiday delays are normal. 5) Urgent complaints are prioritized. Contact admin support if pending exceeds 3 days without updates. Providing complete details speeds up review significantly.",
        
        ('how long does it take to resolve complaints', 'resolution time', 'how long to fix'):
            "Resolution time varies: Simple issues (broken streetlight): 2-7 days. Moderate issues (pothole repair): 5-14 days. Complex issues (water line repair): 15-30+ days. Urgent/safety complaints: 1-2 days priority. Factors: Issue complexity, department workload, resource availability, weather conditions. Track your dashboard for status updates. Notifications alert you to all progress.",
        
        # AI Features
        ('how does ai categorize complaints', 'ai categorization', 'how ai works'):
            "Our AI analyzes your complaint's title, description, and keywords to automatically categorize it. It reads content like 'pothole,' 'streetlight,' 'water leak,' 'garbage,' and assigns to the correct category (Roads, Electricity, Water, Sanitation, etc.). This ensures complaints reach the right departments immediately without manual routing delays.",
        
        ('how is complaint priority decided', 'priority system', 'urgent complaints'):
            "AI and admins set priority based on: 1) Safety level—accidents, hazards = High. 2) Urgency—affecting many people = High. 3) Complexity—simple fix = Low, complex = Medium/High. 4) Severity—minor issue = Low, major = High. 5) Photo evidence—verified issues get priority. High-priority complaints get escalated immediately. Track priority level on your complaint details page.",
        
        ('what is complaint summarization', 'ai summary', 'auto summary'):
            "Complaint summarization is where AI reads your full description and generates a concise 1-2 sentence summary. This helps admins quickly understand the core issue without reading lengthy details. The summary appears on the complaint overview, saving time and improving response efficiency.",
        
        ('can ai detect urgent complaints', 'urgent detection', 'emergency complaints'):
            "Yes! Our AI identifies urgent/emergency complaints by detecting keywords like 'accident,' 'injury,' 'gas leak,' 'electrical hazard,' 'blocked road,' etc. Urgent complaints are automatically flagged and escalated to admins immediately, bypassing normal queues. Safety is prioritized above all else.",
        
        ('does ai read my complaint description', 'ai reading', 'text analysis'):
            "Yes, AI thoroughly reads your entire complaint—title, description, location details, and photo captions. It analyzes the text to extract: Issue type, severity, location precision, required department, priority level, and any urgent keywords. The more detailed your description, the better AI understands and categorizes your complaint.",
        
        ('how accurate is the ai', 'ai accuracy', 'ai mistakes'):
            "AI accuracy is typically 92-96%. It correctly categorizes most complaints on first analysis. Even if slightly off, admin review ensures correct categorization before department assignment. AI is trained continuously on real complaints, improving accuracy over time. Photo evidence and detailed descriptions boost accuracy significantly.",
        
        # Admin Features
        ('what can an admin do', 'admin powers', 'admin role'):
            "Admins can: 1) View all complaints in a dashboard. 2) Update complaint status (Pending → In Progress → Resolved). 3) Assign complaints to departments. 4) View analytics (stats, priority breakdown, timeline charts). 5) Manage user accounts. 6) Read all complaint details, photos, and timelines. 7) Add notes/comments to complaints. 8) Delete complaints if needed. 9) Export reports. 10) Send notifications to users.",
        
        ('how does the admin manage complaints', 'admin workflow', 'complaint management'):
            "Admin workflow: 1) Review incoming complaints on dashboard. 2) Verify details (photo, location, category). 3) Set/adjust priority based on urgency. 4) Assign to appropriate department (Roads, Water, Sanitation, etc.). 5) Update status as work progresses. 6) Monitor department progress. 7) Notify user of status changes. 8) Close complaint when resolved. 9) Maintain records. 10) Generate reports for analysis.",
        
        ('can the admin delete complaints', 'delete complaint', 'remove complaint'):
            "Yes, admins can delete complaints, but only in specific cases: Spam/false complaints, duplicate entries, or user requests. Deletion is logged for audit purposes. Once deleted, complaint data is permanently removed. Most complaints are kept as historical records for documentation and analysis, even after resolution.",
        
        ('how does the admin update complaint status', 'update status', 'status change'):
            "Admins update status via the admin dashboard: 1) Click on a complaint to open details. 2) See current status in the status section. 3) Click 'Update Status'. 4) Select new status (Pending → In Progress → Resolved). 5) Optionally add notes about the update. 6) Save changes. Users receive instant notification of the status change. Status change is logged with timestamp.",
        
        ('can the admin view reports', 'admin reports', 'analytics'):
            "Yes! Admins have access to comprehensive reports: 1) Total complaints filed. 2) Status breakdown (Pending, In Progress, Resolved counts). 3) Priority distribution (High, Medium, Low). 4) Category-wise breakdown (Roads, Water, Sanitation, etc.). 5) Timeline charts showing complaint trends. 6) Complaint resolution time statistics. 7) Department-wise workload. These insights help improve civic services.",
        
        # Privacy & General
        ('is registration compulsory', 'must i register', 'registration required'):
            "Yes, registration is required to file complaints. This ensures: 1) Accountability—each complaint linked to a real person. 2) Follow-up—admins can contact you for clarification. 3) Notifications—you receive updates on your complaint. 4) Security—your data is protected. Registration is quick (2 minutes) and free. You need one account per person, but one person can file multiple complaints.",
        
        ('is my complaint private', 'complaint privacy', 'who sees my complaint'):
            "Complaints are accessible to: 1) You (always). 2) Admin team (mandatory). 3) Relevant department staff (to fix the issue). 4) System administrators (technical support). Your personal details are never publicly displayed. Complaints are professional records, not social media. Privacy is strictly protected.",
        
        ('will i receive updates', 'notifications', 'status updates'):
            "Yes! You receive notifications when: 1) Complaint is filed (confirmation). 2) Status changes (Pending → In Progress → Resolved). 3) Admin adds notes/comments. 4) Department is assigned. 5) Complaint is resolved. Notifications are sent via email and shown in your dashboard. Opt-in to SMS alerts if available. Stay informed every step of the way.",
        
        ('can i track old complaints', 'history', 'past complaints'):
            "Yes, you can track all your historical complaints: 1) Log in to your dashboard. 2) Scroll through your complete complaint list. 3) Click any complaint (old or new) to view full details. 4) See the entire timeline with all status changes. 5) View photos, location, and comments. 6) Filter by status or date range. Your complaint history is permanently stored.",
        
        ('is this application free', 'cost', 'pricing'):
            "Yes, CivicPulse is completely free! Filing complaints, uploading images, tracking status, and all features cost nothing. It's a public service funded to improve civic infrastructure and citizen services. No hidden charges, no subscriptions, no premium features. Everyone is welcome.",
        
        ('who can view my complaint', 'complaint visibility', 'data access'):
            "Access to your complaint: 1) You—full access always. 2) Admin team—all details for management. 3) Assigned department—details needed to fix the issue. 4) CivicPulse system team—technical support if needed. General public—cannot see individual complaints. Your personal contact info is not exposed. Complaints are professional records, kept confidential.",
        
        # Civic Awareness
        ('what should i do if i see an accident', 'accident', 'emergency'):
            "If you witness an accident: 1) Ensure everyone's safety first. 2) Call emergency services (Police: 100, Ambulance: 102, Fire: 101). 3) Don't move injured persons unless critical. 4) Document scene if safe (photos, witness details). 5) After emergency services arrive, use CivicPulse to file a 'Public Safety Hazard' complaint if road/infrastructure was involved. 6) Provide photos and details to ensure infrastructure repairs. Citizen reports help prevent future accidents.",
        
        ('how can i keep my neighborhood clean', 'cleanliness', 'waste management'):
            "Keep your neighborhood clean: 1) Don't litter—use designated dustbins. 2) Segregate waste (wet/dry/hazardous). 3) Report accumulated garbage to CivicPulse. 4) Join community cleaning drives. 5) Encourage neighbors to dispose responsibly. 6) Report broken dustbins/sanitation issues to admins. 7) Compost organic waste at home. 8) Participate in 'Swachh Bharat' initiatives. Small actions create big changes!",
        
        ('why is waste segregation important', 'segregation', 'waste types'):
            "Waste segregation benefits: 1) Wet waste (food, leaves)—composts faster, reduces landfill. 2) Dry waste (paper, plastic, metal)—recycled, saves resources. 3) Hazardous waste (batteries, chemicals)—proper disposal prevents contamination. 4) Easier for sanitation workers—faster processing. 5) Reduces environmental pollution. 6) Saves money on processing. Always segregate and report issues via CivicPulse!",
        
        ('how can i conserve water', 'water conservation', 'save water'):
            "Conserve water: 1) Fix leaky taps/pipes immediately (report via CivicPulse). 2) Take shorter showers. 3) Turn off water while brushing/soaping. 4) Use buckets instead of continuous running. 5) Water plants early morning/evening (less evaporation). 6) Collect rainwater for gardening. 7) Reuse greywater for cleaning. 8) Report water leaks to authorities. Every drop saved counts!",
        
        ('what should i do during floods', 'flood safety', 'emergency'):
            "During floods: 1) Move to higher ground immediately. 2) Stay away from electrical lines and machinery. 3) Don't wade through flood water (hidden dangers). 4) Stay indoors unless evacuation ordered. 5) Listen to emergency broadcasts. 6) Keep emergency contact numbers handy. 7) After floods, report damaged roads/infrastructure via CivicPulse. 8) Help neighbors in need. 9) Document damage for insurance. Safety first!",
        
        ('what is rainwater harvesting', 'rainwater', 'water collection'):
            "Rainwater harvesting: 1) Collect rainfall from roofs/terraces. 2) Filter and store in tanks. 3) Use for gardening, cleaning, flushing toilets. 4) Reduces municipal water dependency. 5) Saves money on water bills. 6) Recharges groundwater. 7) Simple setup—gutters, filters, tank. 8) Legal in many cities—check local rules. 9) Environmentally responsible. Start at home, inspire community!",
        
        # Greetings & Casual
        ('hello', 'hi', 'hey'):
            "Hello! Welcome to CivicPulse! 👋 I'm your AI assistant here to help you file complaints, track progress, and learn about civic services. You can ask me: How to file a complaint? How to check status? What types of issues can I report? Or anything else. What can I help you with today?",
        
        ('good morning', 'good afternoon', 'good evening'):
            "Good to see you! 😊 I'm here to assist with your civic complaints and questions. Whether you want to file a new complaint, check your status, or learn how CivicPulse works, I'm ready to help. What would you like to do?",
        
        ('thank you', 'thanks', 'appreciate'):
            "You're very welcome! 😊 I'm glad I could help. If you have any more questions about filing complaints, tracking progress, or using CivicPulse, feel free to ask. Together, we're building a better community!",
        
        ('bye', 'goodbye', 'see you'):
            "Goodbye! Thank you for using CivicPulse. 👋 Your complaints help improve our city. If you need anything later, I'm always here. Have a great day, and keep making our community better!",
        
        ('who are you', 'your name', 'what is your name'):
            "I'm CivicPulse AI Assistant! 🤖 I'm an intelligent chatbot powered by multiple AI technologies (Google Gemini, OpenAI ChatGPT) with a comprehensive knowledge base. I'm here to: Help you file complaints, answer FAQs, provide guidance on tracking, explain civic services, and offer civic awareness tips. I'm available 24/7 to help!",
        
        ('what can you do', 'your capabilities', 'what are your features'):
            "I can help you with: 1) Filing complaints step-by-step. 2) Checking complaint status and progress. 3) Answering 100+ FAQs about the system. 4) Explaining how AI categorizes complaints. 5) Guidance on photos, location, details. 6) Civic awareness tips (water, waste, safety). 7) General questions and conversations. 8) Motivational quotes and information. 9) Connecting you to right resources. I'm your friendly civic assistant!",
        
        ('tell me a joke', 'joke'):
            "Why did the pothole go to school? Because it wanted to get filled! 😄 (Bad pun, I know!) 😄 But seriously, if you see an actual pothole, report it via CivicPulse! Let's get our roads fixed together. Any other questions?",
        
        ('tell me a civic awareness tip', 'civic tip', 'awareness'):
            "🌍 Civic Tip: Did you know? Reporting problems through CivicPulse is 10x more effective than complaining. Each complaint creates accountability and gets assigned to responsible departments. Your one report can prevent accidents, save resources, and improve community services. Speak up—your civic participation matters! Report issues, track progress, inspire change. Let's build a better city together! 💪",
        
        ('give me a motivational quote', 'motivational', 'quote', 'inspire'):
            "💡 Inspiring Quote: 'The only way to do great work is to care about the work.' – Steve Jobs. In civic terms: Every complaint you file, every detail you provide, every photo you upload—it all matters. You're not just reporting problems; you're driving positive change in your community. Your voice has power. Use it! 🌟",
    }


def _match_faq_question(user_message):
    """Match user message against FAQ database and return best answer."""
    if not user_message or len(user_message) < 2:
        return None
    
    message_lower = user_message.strip().lower()
    faq_db = _get_faq_database()
    
    # Try exact keyword matching
    for keywords, answer in faq_db.items():
        for keyword in keywords:
            if keyword in message_lower:
                return answer
    
    # Try fuzzy matching for close matches
    import difflib
    best_match = None
    best_ratio = 0
    for keywords in faq_db.keys():
        for keyword in keywords:
            ratio = difflib.SequenceMatcher(None, message_lower, keyword).ratio()
            if ratio > best_ratio and ratio > 0.6:
                best_ratio = ratio
                best_match = faq_db[keywords]
    
    return best_match


def _fallback_chatbot_response(user_message, complaint_context=None):
    """Enhanced rule-based fallback with professional, detailed responses."""
    import random
    message = (user_message or '').strip().lower()
    context = (complaint_context or '').strip()
    
    responses = {
        ('status', 'progress', 'update', 'track', 'where'): [
            "Your complaint status is managed by our admin team. They update complaints through these stages: Pending (initial submission) → In Progress (being reviewed and worked on) → Resolved (completed). You can check your dashboard anytime to see the current status and any updates. The admin team will also send you notifications about important changes.",
            "To track your complaint progress, check your dashboard where you'll see the current status and timeline. The admin team reviews and updates all complaints regularly based on urgency and complexity. You'll receive notifications whenever your complaint status changes, so you're always informed about the next steps.",
            "Your complaint is tracked from submission through resolution. Our admin team reviews each complaint, prioritizes it, assigns it to the relevant department, and updates the status as work progresses. Visit your dashboard to view the timeline and latest updates on your specific complaint."
        ],
        ('file', 'submit', 'report', 'new complaint', 'register complaint', 'how do', 'how to', 'create'): [
            "Filing a complaint is simple: Click the 'New Complaint' button, then provide a clear title (e.g., 'Pothole on Main Street'), a detailed description of the issue, the exact location with nearby landmarks, and optionally attach a photo. The more specific you are, the faster our admin team can address it. Once submitted, you can track the status on your dashboard.",
            "To submit a complaint, go to 'New Complaint' and fill in: the problem title, detailed description of what's wrong, the precise location (street name, building number, or landmarks), and upload a photo if possible. Photos really help because they show exactly what needs fixing. After submission, you'll be able to monitor progress in your dashboard.",
            "Filing is quick: navigate to 'New Complaint', describe the civic issue clearly (e.g., broken streetlight, garbage pile, water leak), pinpoint the location, and attach evidence like a photo. This information helps our admin team understand and prioritize the issue. Once filed, check back on your dashboard to track updates."
        ],
        ('photo', 'image', 'evidence', 'attachment', 'picture', 'upload'): [
            "Photos are very helpful! They give our admin team visual proof of the problem, which speeds up resolution. When filing a complaint, attach a clear photo that shows the civic issue. If you're adding it later, you can update the complaint with an image. Good photos show the exact location and nature of the problem, making it easier for the department to fix.",
            "Including a photo with your complaint significantly improves the chances of quick resolution. Take a clear picture that shows the problem area well—whether it's a pothole, broken light, or debris. The admin team uses photos to verify the issue and coordinate with the relevant department. You can attach photos when filing or edit your complaint to add them later.",
            "Supporting photos are crucial for effective complaint management. They provide visual evidence that the admin team can use to verify the issue and assign it to the right department. When uploading a photo, make sure it clearly shows the civic problem and its location. A good photo can reduce resolution time significantly."
        ],
        ('location', 'address', 'place', 'where', 'area'): [
            "Providing an exact location is essential for fast resolution. Include the street name, nearby landmarks, building numbers, or cross streets so the admin team and relevant departments can locate the problem immediately. The more precise you are, the quicker they can dispatch someone to investigate and fix the issue. Vague locations slow down the process.",
            "Location accuracy matters tremendously. Instead of 'near the park,' say 'Park Avenue near the green bench' or 'Corner of Main St and 5th Ave.' Include landmarks, building numbers, or recognizable features. This helps the department pinpoint the issue without wasting time searching. GPS coordinates or a photo location also helps.",
            "Always be specific about location. Use street names, nearby businesses or landmarks, building numbers, and district information. For example: 'Pothole on Oak Street between the hospital and shopping center.' The more details you provide, the easier it is for the admin team to coordinate with departments and get the issue fixed faster."
        ],
        ('help', 'what can', 'how can', 'guide', 'assist', 'support'): [
            "I'm here to help! I can guide you through: filing new civic complaints, understanding how to track complaint progress, explaining the status stages (Pending → In Progress → Resolved), providing tips on what details make complaints effective, and answering questions about the complaint system. What would you like to know?",
            "I can assist you with all aspects of the complaint system. Whether you're reporting a civic issue like a pothole or water leak, tracking an existing complaint, wondering about the resolution timeline, or learning how to upload photos and locations effectively—I'm here to help. Just ask me anything!",
            "I'm CivicPulse AI Assistant, and I'm here to support you with: filing complaints efficiently, checking complaint status and timeline, understanding how the admin team processes complaints, tips for providing effective complaint details, and general questions about civic services. How can I help you today?"
        ],
        ('time', 'how long', 'duration', 'resolve', 'quickly'): [
            "Resolution time depends on the complaint type and complexity. Simple issues like broken streetlights might be resolved within a few days, while complex problems could take weeks. The admin team prioritizes complaints by urgency and assigns them to the responsible department. You can check your dashboard anytime for the latest status. Most users see movement on their complaint within 3-7 days.",
            "Each complaint's timeline varies based on the issue type, department workload, and complexity. Our admin team prioritizes urgent complaints (like safety hazards) for faster resolution. You'll receive notifications whenever there's progress, and you can monitor everything on your dashboard. On average, complaints receive initial review within 2-3 days.",
            "The resolution timeline depends on what needs fixing. Emergency issues (safety hazards) get priority, while routine maintenance follows the queue. The admin team works efficiently to track, verify, and escalate each complaint to the appropriate department. Check your dashboard regularly for updates—you'll see status changes and can estimate resolution time based on priority level."
        ],
        ('admin', 'status update', 'department', 'authority'): [
            "The admin team has full control over complaint status updates. They review incoming complaints, verify the details, categorize the issue, set priority level, and assign it to the right department (Roads, Water Works, Sanitation, etc.). The admin team keeps your complaint updated throughout the process and communicates with relevant departments to ensure resolution.",
            "Our admin team is responsible for managing all complaint statuses. They receive your submission, validate the information, prioritize based on urgency and severity, and coordinate with relevant departments. Only admins can update complaint status—this ensures consistency and proper workflow. You'll see all updates reflected on your dashboard.",
            "Admins control the entire complaint lifecycle: receiving submissions, verifying details, assigning priority, routing to departments, monitoring progress, and closing resolved complaints. This centralized management ensures every complaint is tracked properly and resolved systematically. The admin team's updates are visible to you in real-time on your dashboard."
        ],
    }
    
    # Try to match user keywords to relevant responses
    for keywords, answers in responses.items():
        if any(k in message for k in keywords):
            return random.choice(answers)
    
    # Contextual fallback for specific complaints
    if context:
        detailed_fallback = [
            "I see you're asking about a specific complaint. Based on the context, I can help you understand the status, process, or next steps. What specific aspect would you like to know more about? I can explain how your complaint is being handled, what to expect next, or how to update your information.",
            "Looking at your complaint details, I can provide specific guidance. Our admin team reviews complaints like yours systematically. They verify all information, contact relevant departments, and monitor progress. If you'd like to know about your complaint's status, timeline, or how to add more information (like photos or updated location), just let me know!",
            "I can help with your complaint! Whether you want to understand the current status, learn what's happening next, or need to add more details to improve resolution speed, I'm here to assist. What would be most helpful for you right now?"
        ]
        return random.choice(detailed_fallback)
    
    # General greeting/fallback
    general = [
        "Hi! I'm CivicPulse AI Assistant, here to help you navigate the civic complaint system. You can ask me about: filing complaints, tracking progress, explaining statuses, uploading photos or locations, or anything else about our system. How can I assist you?",
        "Welcome! I'm your CivicPulse AI Assistant. I can guide you through the entire complaint process—from filing an effective complaint to tracking its progress. Feel free to ask me anything about civic services, complaint management, or how to make your voice heard. What's on your mind?",
        "I'm CivicPulse AI, your assistant for all things related to civic complaints. Whether you're new to the system or tracking an existing complaint, I'm here to help with clear, actionable guidance. What would you like to know?"
    ]
    return random.choice(general)


def _is_unhelpful_chatbot_response(reply):
    """Check if response is unhelpful or evasive."""
    if not reply:
        return True
    lower = reply.lower()
    # Only filter truly unhelpful responses
    unhelpful_phrases = [
        "i cannot help",
        "cannot answer",
        "unable to assist",
        "i'm unable to",
        "unable to help",
        "i cannot assist",
    ]
    return any(phrase in lower for phrase in unhelpful_phrases)


def ai_generate_chatbot_response_gemini(message, complaint_context):
    """Generate response using Google Gemini API with professional-grade prompts."""
    try:
        import google.generativeai as genai
        api_key = os.environ.get('GEMINI_API_KEY')
        if not api_key:
            return None
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        context_info = ""
        if complaint_context:
            context_info = f"""
Current Complaint Context:
{complaint_context}

If the user is asking about this specific complaint, provide relevant insights about their issue based on the context above."""
        
        system_prompt = f"""You are CivicPulse AI Assistant, an expert civic complaint management system.

Your Role:
- Help citizens file and track civic complaints (potholes, water leaks, garbage, streetlights, etc.)
- Provide clear, actionable guidance on using the complaint system
- Explain complaint status changes and what to expect at each stage
- Answer questions about civic services and complaint resolution

Personality & Tone:
- Professional yet friendly and approachable
- Empathetic to citizen concerns about civic issues
- Clear and concise, but detailed enough to be genuinely helpful
- Proactive in suggesting next steps

Key Guidelines:
1. Always provide actionable advice tailored to what the user is asking
2. Explain the complaint process: File → Admin Reviews → In Progress → Resolved
3. Encourage users to include photos and precise locations for faster resolution
4. If explaining status, mention that admins manage all status updates
5. For file/upload questions, guide them step-by-step
6. Never claim limitations or say "I cannot help" - always provide helpful direction
7. Keep responses natural (1-3 paragraphs, 40-150 words)
8. Use simple language while maintaining professionalism
{context_info}"""

        response = model.generate_content(
            f"{system_prompt}\n\nUser Question: {message}",
            generation_config={"temperature": 0.8, "top_p": 0.9, "max_output_tokens": 200}
        )
        reply = response.text.strip() if response else ''
        if reply and not _is_unhelpful_chatbot_response(reply) and len(reply) > 10:
            return reply
        return None
    except Exception as e:
        print(f"Gemini error: {e}")
        return None


def ai_generate_chatbot_response_openai(message, complaint_context):
    """Generate response using OpenAI ChatGPT API with professional-grade prompts."""
    try:
        from openai import OpenAI
        api_key = os.environ.get('OPENAI_API_KEY')
        if not api_key:
            return None
        client = OpenAI(api_key=api_key)
        
        context_info = ""
        if complaint_context:
            context_info = f"""
Current Complaint Context:
{complaint_context}

If the user is asking about this specific complaint, provide relevant insights about their issue based on the context above."""
        
        system_prompt = f"""You are CivicPulse AI Assistant, an expert civic complaint management system.

Your Role:
- Help citizens file and track civic complaints (potholes, water leaks, garbage, streetlights, etc.)
- Provide clear, actionable guidance on using the complaint system
- Explain complaint status changes and what to expect at each stage
- Answer questions about civic services and complaint resolution

Personality & Tone:
- Professional yet friendly and approachable
- Empathetic to citizen concerns about civic issues
- Clear and concise, but detailed enough to be genuinely helpful
- Proactive in suggesting next steps

Key Guidelines:
1. Always provide actionable advice tailored to what the user is asking
2. Explain the complaint process: File → Admin Reviews → In Progress → Resolved
3. Encourage users to include photos and precise locations for faster resolution
4. If explaining status, mention that admins manage all status updates
5. For file/upload questions, guide them step-by-step
6. Never claim limitations or say "I cannot help" - always provide helpful direction
7. Keep responses natural (1-3 paragraphs, 40-150 words)
8. Use simple language while maintaining professionalism
{context_info}"""
        
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message}
            ],
            temperature=0.8,
            top_p=0.9,
            max_tokens=200,
            presence_penalty=0.1,
            frequency_penalty=0.1
        )
        reply = response.choices[0].message.content.strip() if response.choices else ''
        if reply and not _is_unhelpful_chatbot_response(reply) and len(reply) > 10:
            return reply
        return None
    except Exception as e:
        print(f"OpenAI error: {e}")
        return None


def ai_generate_chatbot_response(user_message, complaint_context=None):
    """AI chatbot with multi-provider support: Gemini → OpenAI → Enhanced Fallback."""
    message = (user_message or '').strip()
    if not message:
        return "Hi! I'm CivicPulse AI Assistant. How can I help you today? You can ask me about filing complaints, tracking progress, or any questions about civic services."
    
    # Try Gemini first
    reply = ai_generate_chatbot_response_gemini(message, complaint_context)
    if reply:
        return reply
    
    # Try OpenAI second
    reply = ai_generate_chatbot_response_openai(message, complaint_context)
    if reply:
        return reply
    
    # Try comprehensive FAQ database third
    faq_answer = _match_faq_question(message)
    if faq_answer:
        return faq_answer
    
    # Fall back to enhanced rule-based responses
    return _fallback_chatbot_response(message, complaint_context)



def _suggest_department(category):
    mapping = {
        'Roads': 'Roads', 'Road Damage': 'Roads', 'Pothole': 'Roads',
        'Water Supply': 'Water Works', 'Water Leakage': 'Water Works',
        'Electricity': 'Electricity Board',
        'Sanitation': 'Sanitation', 'Garbage': 'Sanitation',
        'Streetlight': 'Street Lighting',
        'Public Safety': 'Public Safety',
        'Noise': 'Noise Control',
    }
    return mapping.get(category, 'General Services')

def _normalize_text(text):
    return re.sub(r'[^a-z0-9]+', ' ', text.lower()).strip()

def _fallback_analysis(title, description):
    text = (title + " " + description).lower()
    category = "Other"
    for cat, keywords in {
        "Roads": ["road", "pothole", "pavement", "tar", "crack", "speed breaker"],
        "Water Supply": ["water", "leak", "pipe", "flood", "drain", "sewage", "supply"],
        "Electricity": ["electricity", "power", "electric", "current", "voltage", "wire"],
        "Sanitation": ["garbage", "trash", "waste", "litter", "dump", "clean"],
        "Streetlight": ["light", "lamp", "dark", "streetlight", "bulb"],
        "Public Safety": ["safety", "danger", "hazard", "accident", "crime"],
        "Noise": ["noise", "loud", "sound", "music", "disturbance"],
    }.items():
        if any(k in text for k in keywords):
            category = cat
            break
    word_count = len(description.split())
    priority = "High" if word_count > 30 else ("Medium" if word_count > 15 else "Low")
    summary = f"{category} issue reported: {description[:80].rstrip()}."
    return {
        "category": category, "priority": priority, "summary": summary,
        "department": _suggest_department(category),
        "sentiment": "Neutral", "tags": []
    }

# ─── Helpers ──────────────────────────────────────────────────────────────────
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def _format_complaint_ref(sequence, created_at=None):
    created_at = created_at or datetime.utcnow()
    return f"CMP{created_at.year}{sequence:04d}"

def _get_yearly_complaint_count():
    sb = get_supabase()
    if not sb:
        return 0
    data = sb.table('complaints').select('created_at').execute().data or []
    current_year = str(datetime.utcnow().year)
    return sum(1 for c in data if str(c.get('created_at', '')).startswith(current_year))


def _safe_select_complaints(select_fields=None):
    sb = get_supabase()
    if not sb:
        return []
    try:
        if select_fields:
            return sb.table('complaints').select(select_fields).execute().data or []
        return sb.table('complaints').select('*').execute().data or []
    except APIError as exc:
        msg = str(exc)
        if 'column' in msg and 'complaints.' in msg and 'does not exist' in msg:
            if select_fields:
                parts = [p.strip() for p in select_fields.split(',') if p.strip()]
                cleaned = [p for p in parts if p != 'ref_id' and not p.endswith('.ref_id')]
                if cleaned and len(cleaned) != len(parts):
                    return sb.table('complaints').select(', '.join(cleaned)).execute().data or []
            return sb.table('complaints').select('id, title, description, location').execute().data or []
        raise


def db_find_duplicate(title, description, location, exclude_id=None):
    all_c = _safe_select_complaints('id, title, description, location, ref_id')
    new_text = _normalize_text(title + ' ' + description + ' ' + location)
    best, best_score = None, 0.0
    for c in all_c:
        if exclude_id and c.get('id') == exclude_id:
            continue
        compare = _normalize_text(c.get('title','') + ' ' + c.get('description','') + ' ' + c.get('location',''))
        score = difflib.SequenceMatcher(None, new_text, compare).ratio()
        if score > best_score:
            best_score, best = score, c
    return best if best and best_score > 0.7 else None

def send_email(subject, body, recipients):
    if not app.config['EMAIL_HOST'] or not app.config['EMAIL_USER'] or not app.config['EMAIL_PASS']:
        print('Email config incomplete. Skipping.')
        return False
    message = f"Subject: {subject}\nFrom: {app.config['EMAIL_FROM']}\nTo: {', '.join(recipients)}\n\n{body}"
    context = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL(app.config['EMAIL_HOST'], app.config['EMAIL_PORT'], context=context) as server:
            server.login(app.config['EMAIL_USER'], app.config['EMAIL_PASS'])
            server.sendmail(app.config['EMAIL_FROM'], recipients, message)
        return True
    except Exception as e:
        print(f"send_email error: {e}")
        return False

def notify_user(subject, body, user_email):
    if not user_email:
        return False
    recipients = [user_email]
    if app.config['ADMIN_EMAILS']:
        recipients.extend(app.config['ADMIN_EMAILS'])
    return send_email(subject, body, recipients)

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def generate_csrf_token():
    if '_csrf_token' not in session:
        session['_csrf_token'] = str(uuid.uuid4())
    return session['_csrf_token']

app.jinja_env.globals['csrf_token'] = generate_csrf_token

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to continue.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            flash('Admin access required.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated

# ─── DB helpers ───────────────────────────────────────────────────────────────
def db_get_user_by_email(email):
    sb = get_supabase()
    if not sb: return None
    r = sb.table('users').select('*').eq('email', email).execute()
    return r.data[0] if r.data else None

def db_get_user_by_id(user_id):
    sb = get_supabase()
    if not sb: return None
    r = sb.table('users').select('*').eq('id', user_id).execute()
    return r.data[0] if r.data else None


def _is_missing_users_column_error(exc, column_name):
    if not isinstance(exc, APIError):
        return False
    msg = str(exc).lower()
    return (
        f"column users.{column_name} does not exist" in msg
        or f"could not find the '{column_name}' column of 'users'" in msg
        or f"could not find the \"{column_name}\" column of 'users'" in msg
    )


def db_create_user(name, email, password, role='user', phone=''):
    sb = get_supabase()
    if not sb: return None
    data = {'id': str(uuid.uuid4()), 'name': name, 'email': email,
            'password': hash_password(password), 'role': role,
            'phone': phone}
    try:
        r = sb.table('users').insert(data).execute()
        return r.data[0] if r.data else None
    except APIError as exc:
        if _is_missing_users_column_error(exc, 'phone'):
            data = {k: v for k, v in data.items() if k != 'phone'}
            r = sb.table('users').insert(data).execute()
            return r.data[0] if r.data else None
        msg = str(exc)
        if 'is_active' in msg or 'created_at' in msg:
            data = {k: v for k, v in data.items() if k not in {'is_active', 'created_at'}}
            r = sb.table('users').insert(data).execute()
            return r.data[0] if r.data else None
        raise


def db_get_all_users():
    sb = get_supabase()
    if not sb: return []
    try:
        users = sb.table('users').select('id,name,email,role,phone').execute().data or []
    except APIError as exc:
        if _is_missing_users_column_error(exc, 'phone'):
            users = sb.table('users').select('id,name,email,role').execute().data or []
            for user in users:
                user['phone'] = ''
        else:
            msg = str(exc)
            if 'is_active' in msg or 'created_at' in msg:
                users = sb.table('users').select('id,name,email,role,phone').execute().data or []
            else:
                raise

    for user in users:
        user.setdefault('is_active', True)
        user.setdefault('created_at', '')
        user.setdefault('phone', '')
    return users

def db_get_complaints(user_id=None, status=None, category=None, priority=None,
                      start_date=None, end_date=None, q_text=None, department=None):
    sb = get_supabase()
    if not sb: return []
    query = sb.table('complaints').select('*, users(name, email)').order('created_at', desc=True)
    if user_id:
        query = query.eq('user_id', user_id)
    if status:
        query = query.eq('status', status)
    if category:
        query = query.eq('category', category)
    if priority:
        query = query.eq('priority', priority)
    if department:
        query = query.eq('department', department)
    if start_date:
        query = query.gte('created_at', f"{start_date}T00:00:00")
    if end_date:
        query = query.lte('created_at', f"{end_date}T23:59:59")
    results = query.execute().data or []
    if q_text and q_text.strip():
        term = q_text.strip().lower()
        results = [c for c in results if term in ' '.join([
            str(c.get('title','')), str(c.get('description','')),
            str(c.get('location','')), str(c.get('ref_id', c.get('id','')))
        ]).lower()]
    return results

def db_get_complaint(complaint_id):
    sb = get_supabase()
    if not sb: return None
    r = sb.table('complaints').select('*, users(name, email)').eq('id', complaint_id).execute()
    return r.data[0] if r.data else None

def db_get_complaint_history(complaint_id):
    sb = get_supabase()
    if not sb: return []
    try:
        r = sb.table('complaint_history').select('*').eq('complaint_id', complaint_id).order('created_at').execute()
        return r.data or []
    except Exception:
        return []

def db_add_complaint_history(complaint_id, action, old_value='', new_value='', user_name='System'):
    sb = get_supabase()
    if not sb: return
    try:
        sb.table('complaint_history').insert({
            'id': str(uuid.uuid4()),
            'complaint_id': complaint_id,
            'action': action,
            'old_value': old_value,
            'new_value': new_value,
            'changed_by': user_name,
            'created_at': datetime.utcnow().isoformat()
        }).execute()
    except Exception as e:
        print(f"History log error: {e}")

def db_get_comments(complaint_id):
    sb = get_supabase()
    if not sb: return []
    try:
        r = sb.table('complaint_comments').select('*, users(name, role)').eq('complaint_id', complaint_id).order('created_at').execute()
        return r.data or []
    except Exception:
        return []

def db_add_comment(complaint_id, user_id, content):
    sb = get_supabase()
    if not sb: return None
    try:
        r = sb.table('complaint_comments').insert({
            'id': str(uuid.uuid4()),
            'complaint_id': complaint_id,
            'user_id': user_id,
            'content': content,
            'created_at': datetime.utcnow().isoformat()
        }).execute()
        return r.data[0] if r.data else None
    except Exception as e:
        print(f"Comment error: {e}")
        return None

def _notification_key(user_id):
    return str(user_id or 'anonymous')


def db_get_notifications(user_id, limit=20):
    sb = get_supabase()
    if sb:
        try:
            r = sb.table('notifications').select('*').eq('user_id', user_id).order('created_at', desc=True).limit(limit).execute()
            return r.data or []
        except Exception:
            pass

    key = _notification_key(user_id)
    items = APP_NOTIFICATIONS.get(key, [])
    items = sorted(items, key=lambda n: n.get('created_at', ''), reverse=True)
    return items[:limit]


def db_create_notification(user_id, title, message, complaint_id=None):
    sb = get_supabase()
    if sb:
        try:
            sb.table('notifications').insert({
                'id': str(uuid.uuid4()),
                'user_id': user_id,
                'title': title,
                'message': message,
                'complaint_id': complaint_id,
                'is_read': False,
                'created_at': datetime.utcnow().isoformat()
            }).execute()
            return
        except Exception as e:
            print(f"Notification error: {e}")

    key = _notification_key(user_id)
    notif = {
        'id': str(uuid.uuid4()),
        'user_id': str(user_id),
        'title': title,
        'message': message,
        'complaint_id': complaint_id,
        'is_read': False,
        'created_at': datetime.utcnow().isoformat()
    }
    APP_NOTIFICATIONS.setdefault(key, []).append(notif)
    return notif


def db_get_unread_notification_count(user_id):
    sb = get_supabase()
    if sb:
        try:
            r = sb.table('notifications').select('id').eq('user_id', user_id).eq('is_read', False).execute()
            return len(r.data or [])
        except Exception:
            pass

    key = _notification_key(user_id)
    return sum(1 for n in APP_NOTIFICATIONS.get(key, []) if not n.get('is_read', False))


def db_mark_notifications_read(user_id):
    sb = get_supabase()
    if sb:
        try:
            sb.table('notifications').update({'is_read': True}).eq('user_id', user_id).eq('is_read', False).execute()
            return
        except Exception:
            pass

    key = _notification_key(user_id)
    for notif in APP_NOTIFICATIONS.get(key, []):
        notif['is_read'] = True

def db_log_activity(action, entity_type, entity_id, user_id, details=''):
    sb = get_supabase()
    if not sb: return
    try:
        sb.table('activity_logs').insert({
            'id': str(uuid.uuid4()),
            'action': action,
            'entity_type': entity_type,
            'entity_id': entity_id,
            'user_id': user_id,
            'details': details,
            'created_at': datetime.utcnow().isoformat()
        }).execute()
    except Exception as e:
        print(f"Activity log error: {e}")

def _retry_postgrest_insert(table_name, data):
    sb = get_supabase()
    if not sb: return None
    try:
        r = sb.table(table_name).insert(data).execute()
        return r.data[0] if r.data else None
    except APIError as exc:
        msg = str(exc)
        match = re.search(r"Could not find the '(.+?)' column", msg)
        if match:
            missing = match.group(1)
            data = {k: v for k, v in data.items() if k != missing}
            return _retry_postgrest_insert(table_name, data)
        raise

def _retry_postgrest_update(table_name, updates, record_id):
    sb = get_supabase()
    if not sb: return None
    try:
        r = sb.table(table_name).update(updates).eq('id', record_id).execute()
        return r.data[0] if r.data else None
    except APIError as exc:
        msg = str(exc)
        match = re.search(r"Could not find the '(.+?)' column", msg)
        if match:
            missing = match.group(1)
            updates = {k: v for k, v in updates.items() if k != missing}
            return _retry_postgrest_update(table_name, updates, record_id)
        raise

def db_create_complaint(data):
    return _retry_postgrest_insert('complaints', data)

def db_update_complaint(complaint_id, updates):
    return _retry_postgrest_update('complaints', updates, complaint_id)

def db_get_stats():
    sb = get_supabase()
    if not sb: return {}
    all_c = sb.table('complaints').select('*').execute().data or []
    all_users = sb.table('users').select('id,role').execute().data or []
    now = datetime.utcnow()
    stats = {
        'total': len(all_c),
        'pending': sum(1 for c in all_c if c.get('status') == 'Pending'),
        'in_progress': sum(1 for c in all_c if c.get('status') == 'In Progress'),
        'resolved': sum(1 for c in all_c if c.get('status') == 'Resolved'),
        'high': sum(1 for c in all_c if c.get('priority') == 'High'),
        'medium': sum(1 for c in all_c if c.get('priority') == 'Medium'),
        'low': sum(1 for c in all_c if c.get('priority') == 'Low'),
        'today': 0, 'this_month': 0,
        'total_users': len(all_users),
        'active_users': sum(1 for u in all_users if u.get('role') != 'admin'),
        'average_resolution_days': 0,
    }
    cats, monthly = {}, {}
    resolution_secs = []
    for c in all_c:
        cats[c.get('category','Other')] = cats.get(c.get('category','Other'), 0) + 1
        created = c.get('created_at', '')
        if created.startswith(now.strftime('%Y-%m-%d')):
            stats['today'] += 1
        if created.startswith(now.strftime('%Y-%m')):
            stats['this_month'] += 1
        try:
            month_key = created[:7]
            monthly[month_key] = monthly.get(month_key, 0) + 1
        except Exception:
            pass
        if c.get('resolved_at'):
            try:
                created_dt = datetime.fromisoformat(created.replace('Z',''))
                resolved_dt = datetime.fromisoformat(c.get('resolved_at','').replace('Z',''))
                resolution_secs.append((resolved_dt - created_dt).total_seconds())
            except Exception:
                pass
    stats['by_category'] = cats
    stats['monthly_trend'] = dict(sorted(monthly.items())[-6:])
    stats['average_resolution_days'] = round(sum(resolution_secs)/len(resolution_secs)/86400, 1) if resolution_secs else 0
    resolution_rate = round((stats['resolved'] / stats['total'] * 100), 1) if stats['total'] > 0 else 0
    stats['resolution_rate'] = resolution_rate
    return stats

def upload_image(file):
    if not file or not allowed_file(file.filename):
        return None
    filename = f"{uuid.uuid4()}_{secure_filename(file.filename)}"
    upload_dir = os.path.join(app.root_path, 'static', 'uploads')
    os.makedirs(upload_dir, exist_ok=True)
    filepath = os.path.join(upload_dir, filename)

    sb = get_supabase()
    if sb:
        try:
            data = file.read()
            sb.storage.from_("complaint-images").upload(
                filename,
                data,
                file_options={"content-type": file.content_type}
            )
            result = sb.storage.from_("complaint-images").get_public_url(filename)
            if isinstance(result, dict):
                return result.get("publicUrl")
            if hasattr(result, "get"):
                return result.get("publicUrl")
            return str(result)
        except Exception as e:
            print(f"Supabase storage error: {e}")
            try:
                file.seek(0)
            except Exception:
                pass

    try:
        if not hasattr(file, 'stream'):
            with open(filepath, 'wb') as f:
                f.write(file.read())
        else:
            try:
                file.stream.seek(0)
            except Exception:
                pass
            with open(filepath, 'wb') as out_file:
                file.stream.seek(0)
                out_file.write(file.read())
    except Exception as e:
        print(f"Local image save error: {e}")
        return None

    return url_for('static', filename=f'uploads/{filename}', _external=True)

@app.errorhandler(RequestEntityTooLarge)
def handle_large_upload(error):
    flash('The uploaded file is too large. Please use an image smaller than 5MB.', 'danger')
    return redirect(request.referrer or url_for('new_complaint'))

# ─── Context processor ────────────────────────────────────────────────────────
@app.context_processor
def inject_notifications():
    unread = 0
    if 'user_id' in session:
        unread = db_get_unread_notification_count(session['user_id'])
    return dict(unread_notifications=unread)

# ─── Routes: Auth ─────────────────────────────────────────────────────────────
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        phone = request.form.get('phone', '').strip()
        if not all([name, email, password, confirm]):
            flash('All required fields must be filled.', 'danger')
        elif not re.match(r'^[^\@\s]+@[^\@\s]+\.[^\@\s]+$', email):
            flash('Please enter a valid email address.', 'danger')
        elif password != confirm:
            flash('Passwords do not match.', 'danger')
        elif len(password) < 6:
            flash('Password must be at least 6 characters.', 'danger')
        elif db_get_user_by_email(email):
            flash('An account with this email already exists.', 'danger')
        else:
            user = db_create_user(name, email, password, phone=phone)
            if user:
                session['user_id'] = user['id']
                session['user_name'] = user['name']
                session['user_email'] = email
                session['role'] = user['role']
                db_log_activity('register', 'user', user['id'], user['id'], f"New user: {email}")
                flash(f'Welcome, {name}! Your account has been created.', 'success')
                return redirect(url_for('dashboard'))
            flash('Registration failed. Please try again.', 'danger')
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        user = db_get_user_by_email(email)
        if user and user.get('is_active') == False:
            flash('Your account has been disabled. Please contact admin.', 'danger')
        elif user and user['password'] == hash_password(password):
            session.permanent = True
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            session['user_email'] = user['email']
            session['role'] = user['role']
            db_log_activity('login', 'user', user['id'], user['id'], f"Login: {email}")
            flash(f'Welcome back, {user["name"]}!', 'success')
            return redirect(url_for('admin_dashboard') if user['role'] == 'admin' else url_for('dashboard'))
        else:
            flash('Invalid email or password.', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    if 'user_id' in session:
        db_log_activity('logout', 'user', session['user_id'], session['user_id'])
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

# ─── Routes: Profile ──────────────────────────────────────────────────────────
@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user = db_get_user_by_id(session['user_id'])
    if request.method == 'POST':
        sb = get_supabase()
        action = request.form.get('action')
        if action == 'update_profile' and sb:
            name = request.form.get('name', '').strip()
            phone = request.form.get('phone', '').strip()
            if name:
                update_data = {'name': name, 'phone': phone}
                try:
                    sb.table('users').update(update_data).eq('id', session['user_id']).execute()
                except APIError as exc:
                    if _is_missing_users_column_error(exc, 'phone'):
                        sb.table('users').update({'name': name}).eq('id', session['user_id']).execute()
                    else:
                        raise
                session['user_name'] = name
                flash('Profile updated successfully.', 'success')
            else:
                flash('Name cannot be empty.', 'danger')
        elif action == 'change_password' and sb:
            current = request.form.get('current_password', '')
            new_pw = request.form.get('new_password', '')
            confirm = request.form.get('confirm_password', '')
            if user and user['password'] != hash_password(current):
                flash('Current password is incorrect.', 'danger')
            elif new_pw != confirm:
                flash('New passwords do not match.', 'danger')
            elif len(new_pw) < 6:
                flash('New password must be at least 6 characters.', 'danger')
            else:
                sb.table('users').update({'password': hash_password(new_pw)}).eq('id', session['user_id']).execute()
                flash('Password changed successfully.', 'success')
        return redirect(url_for('profile'))
    complaints = db_get_complaints(user_id=session['user_id'])
    stats = {
        'total': len(complaints),
        'pending': sum(1 for c in complaints if c['status'] == 'Pending'),
        'in_progress': sum(1 for c in complaints if c['status'] == 'In Progress'),
        'resolved': sum(1 for c in complaints if c['status'] == 'Resolved'),
    }
    return render_template('profile.html', user=user, stats=stats)

# ─── Routes: Dashboard ────────────────────────────────────────────────────────
@app.route('/dashboard')
@login_required
def dashboard():
    status = request.args.get('status')
    category = request.args.get('category')
    priority = request.args.get('priority')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    q = request.args.get('q')
    complaints = db_get_complaints(
        user_id=session['user_id'], status=status, category=category,
        priority=priority, start_date=start_date, end_date=end_date, q_text=q
    )
    counts = {
        'total': len(complaints),
        'pending': sum(1 for c in complaints if c['status'] == 'Pending'),
        'in_progress': sum(1 for c in complaints if c['status'] == 'In Progress'),
        'resolved': sum(1 for c in complaints if c['status'] == 'Resolved'),
    }
    return render_template('dashboard.html', complaints=complaints, counts=counts,
                           filters={'status': status, 'category': category, 'priority': priority,
                                    'start_date': start_date, 'end_date': end_date, 'q': q},
                           categories=CATEGORIES)

# ─── Routes: Complaints ───────────────────────────────────────────────────────
@app.route('/complaint/new', methods=['GET', 'POST'])
@login_required
def new_complaint():
    duplicate_warning = None
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        location = request.form.get('location', '').strip()
        if not all([title, description, location]):
            flash('Title, description, and location are required.', 'danger')
            return render_template('new_complaint.html', categories=CATEGORIES)
        existing = db_find_duplicate(title, description, location)
        if existing:
            duplicate_warning = f"Similar complaint exists ({existing.get('ref_id', existing['id'])}). Please review before submitting."
        image_url = None
        if 'image' in request.files:
            f = request.files['image']
            if f and f.filename:
                if not allowed_file(f.filename):
                    flash('Only JPG, PNG, GIF, and WEBP images are allowed.', 'danger')
                    return render_template('new_complaint.html', duplicate_warning=duplicate_warning, categories=CATEGORIES)
                image_url = upload_image(f)
        ai = analyze_complaint_with_ai(title, description)
        sequence = _get_yearly_complaint_count() + 1
        created_at = datetime.utcnow()
        data = {
            'id': str(uuid.uuid4()),
            'ref_id': _format_complaint_ref(sequence, created_at),
            'user_id': session['user_id'],
            'title': title, 'description': description, 'location': location,
            'image_url': image_url,
            'category': ai.get('category', 'Other'),
            'priority': ai.get('priority', 'Medium'),
            'department': ai.get('department', 'General Services'),
            'summary': ai.get('summary', description[:100]),
            'sentiment': ai.get('sentiment', 'Neutral'),
            'tags': json.dumps(ai.get('tags', [])),
            'status': 'Pending',
            'is_duplicate': bool(existing),
            'duplicate_of': existing['id'] if existing else None,
            'created_at': created_at.isoformat(),
        }
        complaint = db_create_complaint(data)
        if complaint:
            db_add_complaint_history(data['id'], 'submitted', '', 'Pending', session.get('user_name', 'User'))
            db_log_activity('submit_complaint', 'complaint', data['id'], session['user_id'], f"Submitted: {title}")
            user = db_get_user_by_id(session['user_id'])
            user_email = user.get('email') if user else session.get('user_email')
            subject = f"Complaint Registered: {data['ref_id']}"
            body = (f"Your complaint has been received.\n\nID: {data['ref_id']}\n"
                    f"Category: {data['category']}\nPriority: {data['priority']}\n"
                    f"Department: {data['department']}\n\nThank you for reporting.")
            notify_user(subject, body, user_email)
            db_create_notification(session['user_id'], 'Complaint Submitted',
                                   f"Your complaint {data['ref_id']} has been submitted successfully.", data['id'])
            flash('Complaint submitted successfully! AI has categorized it.', 'success')
            return redirect(url_for('view_complaint', complaint_id=complaint['id']))
        flash('Failed to submit complaint. Please try again.', 'danger')
    return render_template('new_complaint.html', duplicate_warning=duplicate_warning, categories=CATEGORIES)

@app.route('/complaint/<complaint_id>')
@login_required
def view_complaint(complaint_id):
    complaint = db_get_complaint(complaint_id)
    if not complaint:
        flash('Complaint not found.', 'danger')
        return redirect(url_for('dashboard'))
    if session.get('role') != 'admin' and complaint['user_id'] != session['user_id']:
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
    history = db_get_complaint_history(complaint_id)
    comments = db_get_comments(complaint_id)
    can_edit = (complaint['user_id'] == session['user_id'] and
                complaint['status'] == 'Pending' and session.get('role') != 'admin')
    return render_template('view_complaint.html', complaint=complaint,
                           history=history, comments=comments, can_edit=can_edit)

@app.route('/complaint/<complaint_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_complaint(complaint_id):
    complaint = db_get_complaint(complaint_id)
    if not complaint:
        flash('Complaint not found.', 'danger')
        return redirect(url_for('dashboard'))
    if complaint['user_id'] != session['user_id']:
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
    if complaint['status'] != 'Pending':
        flash('Only pending complaints can be edited.', 'warning')
        return redirect(url_for('view_complaint', complaint_id=complaint_id))
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        location = request.form.get('location', '').strip()
        if not all([title, description, location]):
            flash('All fields are required.', 'danger')
            return render_template('edit_complaint.html', complaint=complaint, categories=CATEGORIES)
        ai = analyze_complaint_with_ai(title, description)
        updates = {
            'title': title, 'description': description, 'location': location,
            'category': ai.get('category', complaint.get('category')),
            'priority': ai.get('priority', complaint.get('priority')),
            'summary': ai.get('summary', description[:100]),
            'department': ai.get('department', complaint.get('department')),
        }
        if 'image' in request.files:
            f = request.files['image']
            if f and f.filename and allowed_file(f.filename):
                updates['image_url'] = upload_image(f)
        db_update_complaint(complaint_id, updates)
        db_add_complaint_history(complaint_id, 'edited', 'Previous content', title, session.get('user_name'))
        flash('Complaint updated successfully.', 'success')
        return redirect(url_for('view_complaint', complaint_id=complaint_id))
    return render_template('edit_complaint.html', complaint=complaint, categories=CATEGORIES)

@app.route('/complaint/<complaint_id>/withdraw', methods=['POST'])
@login_required
def withdraw_complaint(complaint_id):
    complaint = db_get_complaint(complaint_id)
    if not complaint or complaint['user_id'] != session['user_id']:
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
    if complaint['status'] != 'Pending':
        flash('Only pending complaints can be withdrawn.', 'warning')
        return redirect(url_for('view_complaint', complaint_id=complaint_id))
    db_update_complaint(complaint_id, {'status': 'Withdrawn'})
    db_add_complaint_history(complaint_id, 'withdrawn', 'Pending', 'Withdrawn', session.get('user_name'))
    flash('Complaint withdrawn successfully.', 'info')
    return redirect(url_for('dashboard'))

@app.route('/complaint/<complaint_id>/comment', methods=['POST'])
@login_required
def add_comment(complaint_id):
    complaint = db_get_complaint(complaint_id)
    if not complaint:
        flash('Complaint not found.', 'danger')
        return redirect(url_for('dashboard'))
    if session.get('role') != 'admin' and complaint['user_id'] != session['user_id']:
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
    content = request.form.get('content', '').strip()
    if not content:
        flash('Comment cannot be empty.', 'danger')
        return redirect(url_for('view_complaint', complaint_id=complaint_id))
    if len(content) > 500:
        flash('Comment must be under 500 characters.', 'danger')
        return redirect(url_for('view_complaint', complaint_id=complaint_id))
    db_add_comment(complaint_id, session['user_id'], content)
    if session.get('role') == 'admin' and complaint['user_id'] != session['user_id']:
        db_create_notification(complaint['user_id'], 'New Remark on Your Complaint',
                               f"Admin added a remark on {complaint.get('ref_id',complaint_id)}.", complaint_id)
    flash('Comment added.', 'success')
    return redirect(url_for('view_complaint', complaint_id=complaint_id))

# ─── Routes: Notifications ────────────────────────────────────────────────────
@app.route('/notifications')
@login_required
def notifications():
    notifs = db_get_notifications(session['user_id'], limit=50)
    db_mark_notifications_read(session['user_id'])
    return render_template('notifications.html', notifications=notifs)

@app.route('/api/notifications/count')
@login_required
def api_notification_count():
    count = db_get_unread_notification_count(session['user_id'])
    return jsonify({'count': count})

# ─── Routes: Admin ────────────────────────────────────────────────────────────
@app.route('/admin')
@admin_required
def admin_dashboard():
    status = request.args.get('status')
    category = request.args.get('category')
    priority = request.args.get('priority')
    department = request.args.get('department')
    q_text = request.args.get('q')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    stats = db_get_stats()
    complaints = db_get_complaints(status=status, category=category, priority=priority,
                                   department=department, start_date=start_date,
                                   end_date=end_date, q_text=q_text)
    return render_template('admin_dashboard.html', stats=stats, complaints=complaints,
                           filters={'status': status, 'category': category, 'priority': priority,
                                    'department': department, 'start_date': start_date,
                                    'end_date': end_date, 'q': q_text},
                           categories=CATEGORIES, departments=DEPARTMENTS)

@app.route('/admin/reports')
@admin_required
def admin_reports():
    stats = db_get_stats()
    complaints = db_get_complaints()
    return render_template('admin_reports.html', stats=stats, complaints=complaints)

@app.route('/admin/users')
@admin_required
def admin_users():
    users = db_get_all_users()
    return render_template('admin_users.html', users=users)

@app.route('/admin/users/<user_id>/toggle', methods=['POST'])
@admin_required
def toggle_user(user_id):
    sb = get_supabase()
    if not sb:
        flash('Database unavailable.', 'danger')
        return redirect(url_for('admin_users'))
    user = db_get_user_by_id(user_id)
    if not user:
        flash('User not found.', 'danger')
        return redirect(url_for('admin_users'))
    try:
        new_status = not user.get('is_active', True)
        sb.table('users').update({'is_active': new_status}).eq('id', user_id).execute()
        action = 'enabled' if new_status else 'disabled'
        db_log_activity(f'user_{action}', 'user', user_id, session['user_id'], f"User {user['email']} {action}")
        flash(f"User account {action}.", 'success')
    except APIError as exc:
        if 'is_active' in str(exc):
            flash('This database does not support account enable/disable toggling.', 'warning')
        else:
            raise
    return redirect(url_for('admin_users'))

@app.route('/admin/users/<user_id>/promote', methods=['POST'])
@admin_required
def promote_user(user_id):
    sb = get_supabase()
    if not sb:
        flash('Database unavailable.', 'danger')
        return redirect(url_for('admin_users'))
    user = db_get_user_by_id(user_id)
    if not user:
        flash('User not found.', 'danger')
        return redirect(url_for('admin_users'))
    new_role = 'admin' if user.get('role') == 'user' else 'user'
    sb.table('users').update({'role': new_role}).eq('id', user_id).execute()
    db_log_activity('role_change', 'user', user_id, session['user_id'], f"Role changed to {new_role}")
    flash(f"User role changed to {new_role}.", 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/complaint/<complaint_id>/status', methods=['POST'])
@admin_required
def update_status(complaint_id):
    new_status = request.form.get('status')
    if new_status not in app.config['STATUS_FLOW']:
        flash('Invalid status.', 'danger')
        return redirect(url_for('admin_dashboard'))
    complaint = db_get_complaint(complaint_id)
    old_status = complaint.get('status', '') if complaint else ''
    updates = {'status': new_status}
    if new_status == 'Resolved':
        updates['resolved_at'] = datetime.utcnow().isoformat()
    db_update_complaint(complaint_id, updates)
    if complaint:
        db_add_complaint_history(complaint_id, 'status_changed', old_status, new_status, session.get('user_name', 'Admin'))
        db_log_activity('status_update', 'complaint', complaint_id, session['user_id'],
                        f"Status: {old_status} → {new_status}")
        if complaint.get('users') and complaint['users'].get('email'):
            db_create_notification(complaint['user_id'], f"Complaint Status Updated",
                                   f"Your complaint {complaint.get('ref_id',complaint_id)} is now {new_status}.", complaint_id)
            subject = f"Complaint {complaint.get('ref_id', complaint_id)} status updated"
            body = (f"Status changed to {new_status}.\n\nID: {complaint.get('ref_id',complaint_id)}\n"
                    f"Title: {complaint.get('title')}\nNew Status: {new_status}")
            notify_user(subject, body, complaint['users']['email'])
        flash(f'Status updated to {new_status}.', 'success')
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route('/admin/complaint/<complaint_id>/assign', methods=['POST'])
@admin_required
def assign_department(complaint_id):
    department = request.form.get('department')
    if department not in DEPARTMENTS:
        flash('Invalid department.', 'danger')
        return redirect(url_for('admin_dashboard'))
    db_update_complaint(complaint_id, {'department': department})
    db_add_complaint_history(complaint_id, 'department_assigned', '', department, session.get('user_name', 'Admin'))
    flash(f'Complaint assigned to {department}.', 'success')
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route('/admin/complaint/<complaint_id>/delete', methods=['POST'])
@admin_required
def delete_complaint(complaint_id):
    sb = get_supabase()
    if sb:
        complaint = db_get_complaint(complaint_id)
        sb.table('complaints').delete().eq('id', complaint_id).execute()
        if complaint:
            db_log_activity('delete_complaint', 'complaint', complaint_id, session['user_id'],
                            f"Deleted: {complaint.get('title','')}")
    flash('Complaint deleted.', 'info')
    return redirect(url_for('admin_dashboard'))

# ─── Export routes ────────────────────────────────────────────────────────────
@app.route('/admin/export/csv')
@admin_required
def export_csv():
    complaints = db_get_complaints(
        status=request.args.get('status'),
        category=request.args.get('category'),
        priority=request.args.get('priority'),
    )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Ref ID', 'Title', 'Category', 'Priority', 'Status', 'Department',
                     'Location', 'Submitted By', 'Date', 'Resolved At'])
    for c in complaints:
        writer.writerow([
            c.get('ref_id', c.get('id','')),
            c.get('title',''),
            c.get('category',''),
            c.get('priority',''),
            c.get('status',''),
            c.get('department',''),
            c.get('location',''),
            c.get('users',{}).get('name','') if c.get('users') else '',
            c.get('created_at','')[:10],
            c.get('resolved_at','')[:10] if c.get('resolved_at') else '',
        ])
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment;filename=complaints_{datetime.utcnow().strftime("%Y%m%d")}.csv'}
    )

# ─── API endpoints ─────────────────────────────────────────────────────────────
@app.route('/api/analyze', methods=['POST'])
@login_required
def api_analyze():
    data = request.get_json()
    result = analyze_complaint_with_ai(data.get('title',''), data.get('description',''))
    return jsonify(result)

@app.route('/api/chatbot', methods=['POST'])
@login_required
def api_chatbot():
    data = request.get_json()
    message = data.get('message', '').strip()
    context = data.get('context', '')
    if not message:
        return jsonify({'reply': 'Please type a message.'})
    reply = ai_generate_chatbot_response(message, context)
    return jsonify({'reply': reply})

@app.route('/api/stats')
@admin_required
def api_stats():
    return jsonify(db_get_stats())

if __name__ == '__main__':
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)
