# CivicPulse 🏙️
### Smart Civic Complaint Management System
*Flask · Supabase · Gemini AI*

A full-stack web application where citizens report civic issues (potholes, garbage, broken streetlights, water leakage) and AI automatically categorizes, prioritizes, and summarizes each complaint in real time.

---

## Features

### Citizen Side
- **Sign up / Login** with email and password
- **Submit complaints** with title, description, location, and photo upload
- **AI analysis** — Gemini categorizes, sets priority, and writes a summary live as you type
- **Track status** — Pending → In Progress → Resolved with a visual timeline

### Admin Side
- **Admin dashboard** with live stats (total, pending, in-progress, resolved)
- **Change complaint status** directly from the table
- **Priority breakdown** and category analytics charts
- **Delete complaints**

### AI Features (Gemini 1.5 Flash)
- Auto-categorizes: Garbage, Pothole, Streetlight, Water Leakage, Road Damage, Public Safety, Noise
- Sets priority: Low / Medium / High
- Generates a one-sentence summary
- **Falls back gracefully** to rule-based logic if API key is missing

---

## Tech Stack

| Layer     | Technology                        |
|-----------|-----------------------------------|
| Backend   | Flask (Python)                    |
| Database  | Supabase (PostgreSQL)             |
| Storage   | Supabase Storage (images)         |
| AI        | Google Gemini 1.5 Flash           |
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

### 5. Get a Gemini API key
- [aistudio.google.com](https://aistudio.google.com) → Get API Key (free tier available)

### 6. Create `.env`
```bash
cp .env.example .env
```
Fill in:
```
SECRET_KEY=any-random-string-here
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_KEY=eyJ...
GEMINI_API_KEY=AIza...
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

---

## How to explain this in your internship interview

> "I built a full-stack civic complaint management system using Flask and Supabase. The interesting part is the AI integration — I used Google Gemini to automatically analyze each complaint when it's submitted. It categorizes the issue (garbage, pothole, etc.), assigns a priority level, and generates a plain-English summary. There's also a live preview in the form that shows you the AI's analysis as you type, using a debounced API call to my backend. The system has separate citizen and admin interfaces, image upload to Supabase Storage, and role-based access control."

---

## License
MIT
