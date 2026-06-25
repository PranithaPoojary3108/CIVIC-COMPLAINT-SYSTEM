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

def ai_generate_chatbot_response(user_message, complaint_context=None):
    """AI chatbot for complaint assistance."""
    try:
        import google.generativeai as genai
        api_key = os.environ.get('GEMINI_API_KEY')
        if not api_key:
            return "I'm here to help! Please describe your civic issue and I'll guide you through filing a complaint."
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        context = ""
        if complaint_context:
            context = f"\nUser's complaint context: {complaint_context}"
        prompt = f"""You are a helpful civic complaint assistant for CivicPulse, a city issue reporting platform.
Help citizens file complaints, understand the process, or check status.
Be concise, friendly, and helpful. Max 2 sentences.{context}

User: {user_message}
Assistant:"""
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Chatbot error: {e}")
        return "I'm here to help you file and track civic complaints. What issue would you like to report?"

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

def db_find_duplicate(title, description, location, exclude_id=None):
    sb = get_supabase()
    if not sb:
        return None
    all_c = sb.table('complaints').select('id, title, description, location, ref_id').execute().data or []
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

def db_create_user(name, email, password, role='user', phone=''):
    sb = get_supabase()
    if not sb: return None
    data = {'id': str(uuid.uuid4()), 'name': name, 'email': email,
            'password': hash_password(password), 'role': role,
            'phone': phone, 'is_active': True, 'created_at': datetime.utcnow().isoformat()}
    r = sb.table('users').insert(data).execute()
    return r.data[0] if r.data else None

def db_get_all_users():
    sb = get_supabase()
    if not sb: return []
    return sb.table('users').select('id,name,email,role,is_active,created_at,phone').order('created_at', desc=True).execute().data or []

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
            str(c.get('location','')), str(c.get('ref_id',''))
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

def db_get_notifications(user_id, limit=20):
    sb = get_supabase()
    if not sb: return []
    try:
        r = sb.table('notifications').select('*').eq('user_id', user_id).order('created_at', desc=True).limit(limit).execute()
        return r.data or []
    except Exception:
        return []

def db_create_notification(user_id, title, message, complaint_id=None):
    sb = get_supabase()
    if not sb: return
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
    except Exception as e:
        print(f"Notification error: {e}")

def db_get_unread_notification_count(user_id):
    sb = get_supabase()
    if not sb: return 0
    try:
        r = sb.table('notifications').select('id').eq('user_id', user_id).eq('is_read', False).execute()
        return len(r.data or [])
    except Exception:
        return 0

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
            file.seek(0)
    upload_dir = os.path.join(
        app.root_path,
        "static",
        "uploads"
    )
    os.makedirs(upload_dir, exist_ok=True)
    filepath = os.path.join(upload_dir, filename)
    file.save(filepath)
    # Do not expose local /static/uploads links in the UI — return None so templates won't render them
    return None

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
                sb.table('users').update({'name': name, 'phone': phone}).eq('id', session['user_id']).execute()
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
            user_email = session.get('user_email')
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
    sb = get_supabase()
    notifs = db_get_notifications(session['user_id'], limit=50)
    if sb:
        try:
            sb.table('notifications').update({'is_read': True}).eq('user_id', session['user_id']).eq('is_read', False).execute()
        except Exception:
            pass
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
    new_status = not user.get('is_active', True)
    sb.table('users').update({'is_active': new_status}).eq('id', user_id).execute()
    action = 'enabled' if new_status else 'disabled'
    db_log_activity(f'user_{action}', 'user', user_id, session['user_id'], f"User {user['email']} {action}")
    flash(f"User account {action}.", 'success')
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
    app.run(debug=True)
