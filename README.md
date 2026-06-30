# CivicPulse 🏙️
### Smart Civic Complaint Management System
*Flask · Supabase · Gemini AI + ChatGPT*

A full-stack web application where citizens report civic issues (potholes, garbage, broken streetlights, water leakage) and AI automatically categorizes, prioritizes, and summarizes each complaint in real time. Features **multi-provider AI** with intelligent fallback.

---

## Features

### Citizen Side
- **Sign up / Login** with email and password
- **Submit complaints** with title, description, location, and photo upload
- **AI analysis** — Gemini/ChatGPT categorizes, sets priority, and writes a summary live as you type
- **Track status** — Pending → In Progress → Resolved with a visual timeline
- **Smart Chatbot** — AI-powered assistant using Gemini, ChatGPT, or rule-based fallback

### Admin Side
- **Admin dashboard** with live stats (total, pending, in-progress, resolved)
- **Change complaint status** directly from the table
- **Priority breakdown** and category analytics charts
- **Delete complaints**
- **User management** with role-based access

### AI Features
- **Multi-Provider Support**: 
  - Primary: Google Gemini 1.5 Flash
  - Secondary: OpenAI ChatGPT (gpt-3.5-turbo)
  - Fallback: Smart rule-based system
- **Complaint Analysis**: Auto-categorizes and prioritizes issues
- **AI Chatbot**: Friendly assistant for filing complaints and tracking progress
- **Intelligent Fallback**: Seamlessly switches between providers if one fails

---

## Tech Stack

| Layer     | Technology                        |
|-----------|-----------------------------------|
| Backend   | Flask (Python)                    |
| Database  | Supabase (PostgreSQL)             |
| Storage   | Supabase Storage (images)         |
| AI        | Google Gemini 1.5 + OpenAI ChatGPT|
| Frontend  | Vanilla HTML/CSS/JS (no framework)|
| Fonts     | Inter + DM Serif Display          |

---

## Setup (8 steps)

### 1. Clone and install
```bash
git clone <your-repo>
cd civic-complaint
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Create a Supabase project
- Go to [supabase.com](https://supabase.com) → New Project
- Copy your **Project URL** and **anon public key**

### 3. Run the schema
- Supabase Dashboard → SQL Editor
- Paste and run the contents of `schema.sql`
- This creates the `users` and `complaints` tables and seeds an admin account

### 4. Create Supabase Storage bucket
- Supabase Dashboard → Storage → New bucket
- Name: `complaint-images`, set to **Public**

### 5. Get API keys (at least one for AI)
- **Gemini** (free): [aistudio.google.com/app/apikeys](https://aistudio.google.com/app/apikeys)
- **OpenAI** (paid, but includes free trial credits): [platform.openai.com/api-keys](https://platform.openai.com/api-keys)

### 6. Create `.env`
```bash
cp .env.example .env
```
Fill in (at minimum: Supabase + one AI API key):
```
SECRET_KEY=any-random-string-here
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_KEY=eyJ...
GEMINI_API_KEY=AIza...          # (optional if using OpenAI)
OPENAI_API_KEY=sk-proj-...      # (optional if using Gemini)
```

### 7. Run
```bash
python app.py
```
Open [http://localhost:5000](http://localhost:5000)

### 8. Default admin login
```
Email:    admin@civic.gov
Password: admin123
```

---

## How AI Chatbot Works

The chatbot uses a **multi-provider fallback chain**:

1. **Tries Gemini** → If API key exists and responds well
2. **Tries OpenAI** → If Gemini fails and API key exists
3. **Uses Rule-Based Fallback** → Smart keyword matching for instant responses

Each response is personalized to help with civic complaints, filing complaints, or tracking progress. The system automatically selects the best provider based on availability.

---

## Project Structure

```
civic-complaint/
├── app.py                  # Main Flask app (routes, AI, DB)
├── requirements.txt
├── schema.sql              # Supabase table definitions + seed
├── .env.example
├── templates/
│   ├── base.html           # Navbar, flash messages, layout
│   ├── index.html          # Landing page
│   ├── login.html
│   ├── register.html
│   ├── dashboard.html      # User complaint list
│   ├── new_complaint.html  # Submit form with live AI preview
│   ├── view_complaint.html # Complaint detail + timeline
│   └── admin_dashboard.html
└── static/
    ├── css/style.css       # Complete design system
    ├── js/main.js
    └── uploads/            # Local fallback (if Supabase storage not configured)
```

---

## Deployment (Render — free tier)

1. Push to GitHub
2. Render → New Web Service → connect repo
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app`
5. Add environment variables in Render dashboard
6. Add at least one API key (GEMINI_API_KEY or OPENAI_API_KEY)

---

## How to explain this in your internship interview

> "I built a full-stack civic complaint management system using Flask and Supabase. The unique part is the **multi-provider AI integration**. For complaint analysis and the chatbot, I implemented a fallback system that tries Google Gemini first, then falls back to OpenAI's ChatGPT if needed, and finally uses smart rule-based logic if neither API is available. This ensures the app works reliably even if one AI provider is down or the API key isn't configured. The system analyzes complaints in real-time, categorizes civic issues, sets priorities, and provides an intelligent chatbot assistant. It has separate citizen and admin interfaces with role-based access, image uploads to Supabase, and analytics dashboards."

---

## License
MIT

