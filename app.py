from flask import Flask, render_template, request, redirect, url_for, session, flash, make_response, send_file, current_app
import sqlite3
import os
from io import BytesIO
from xhtml2pdf import pisa
import requests

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your_fallback_secret_key_here')
PAYSTACK_SECRET_KEY = os.environ.get('PAYSTACK_SECRET_KEY')
# Default active term fallback for school subscriptions
DEFAULT_CURRENT_TERM = "First Term 2026/2027"

def get_db_connection():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            school_name TEXT,
            address TEXT,
            motto TEXT,
            email TEXT,
            phone TEXT,
            logo TEXT,
            vacation_date TEXT,
            resumption_date TEXT,
            academic_session TEXT,
            current_term TEXT
        )
    """)

    # School subscriptions table for termly admin licensing
    conn.execute("""
        CREATE TABLE IF NOT EXISTS school_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            term TEXT NOT NULL,
            status TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    for col, col_type in [
        ('address', 'TEXT'), ('motto', 'TEXT'), ('phone', 'TEXT'), 
        ('email', 'TEXT'), ('logo', 'TEXT'), ('vacation_date', 'TEXT'), 
        ('resumption_date', 'TEXT'), ('academic_session', 'TEXT'), ('current_term', 'TEXT')
    ]:
        try:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError:
            pass

    conn.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            class TEXT NOT NULL,
            roll_number TEXT,
            sex TEXT,
            department TEXT,
            dob TEXT,
            attendance_present INTEGER DEFAULT 0,
            attendance_absent INTEGER DEFAULT 0,
            total_school_days INTEGER DEFAULT 0,
            award_won TEXT,
            bill_debt REAL DEFAULT 0,
            bill_school_fees REAL DEFAULT 0,
            bill_computer REAL DEFAULT 0,
            bill_lessons REAL DEFAULT 0,
            bill_utility REAL DEFAULT 0,
            class_teacher_comment TEXT,
            head_teacher_comment TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS behavior_ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            trait TEXT NOT NULL,
            rating INTEGER DEFAULT 1,
            FOREIGN KEY (student_id) REFERENCES students(id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS marks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            test_score REAL DEFAULT 0,
            exam_score REAL DEFAULT 0,
            last_cumm REAL DEFAULT 0,
            FOREIGN KEY (student_id) REFERENCES students(id)
        )
    """)
    
    conn.commit()
    conn.close()

init_db()

def get_active_term_for_user(user_id):
    """Helper to get user-specific active term and session combo, or fallback to default."""
    conn = get_db_connection()
    user = conn.execute('SELECT current_term, academic_session FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    if user and user['current_term'] and user['academic_session']:
        return f"{user['current_term']} {user['academic_session']}"
    elif user and user['current_term']:
        return user['current_term']
    return DEFAULT_CURRENT_TERM

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('select_term'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        login_input = request.form.get('username') or request.form.get('email') or ''
        password = request.form.get('password', '')
        
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE (username = ? OR email = ?) AND password = ?', (login_input, login_input, password)).fetchone()
        conn.close()
        
        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['email'] = user['email']
            return redirect(url_for('select_term'))
        else:
            flash('Invalid username/email or password')
            
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        school_name = request.form.get('school_name', '')
        email = request.form.get('email', '')
        phone = request.form.get('phone', '')
        password = request.form['password']
        
        conn = get_db_connection()
        try:
            conn.execute('''
                INSERT INTO users (username, password, school_name, email, phone, current_term, academic_session) 
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (username, password, school_name, email, phone, "First Term", "2026/2027"))
            conn.commit()
            flash('Registration successful! Please log in.')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Username already exists.')
        finally:
            conn.close()
            
    return render_template('register.html')


# ==========================================
# SELECT TERM & SESSION CONFIGURATION
# ==========================================

@app.route('/select_term', methods=['GET', 'POST'])
def select_term():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    user_id = session['user_id']
    
    if request.method == 'POST':
        academic_session = request.form.get('academic_session', '2026/2027')
        current_term = request.form.get('current_term', 'First Term')
        
        conn.execute(
            'UPDATE users SET academic_session = ?, current_term = ? WHERE id = ?',
            (academic_session, current_term, user_id)
        )
        conn.commit()
        conn.close()
        return redirect(url_for('dashboard'))
        
    user_row = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    
    user = dict(user_row) if user_row else {}
    return render_template('select_term.html', user=user)


# ==========================================
# PUBLIC STUDENT RESULT CHECKER (Roll No + Session/Term + Name Match)
# ==========================================

@app.route('/check-result', methods=['GET', 'POST'])
def check_result():
    """Allows parents/students to search results using Roll Number, Session, Term, and Name keyword."""
    if request.method == 'POST':
        roll_number = request.form.get('roll_number', '').strip()
        academic_session = request.form.get('academic_session', '').strip()
        current_term = request.form.get('current_term', '').strip()
        name_input = request.form.get('name_input', '').strip().lower()
        
        conn = get_db_connection()
        student = conn.execute('''
            SELECT students.* FROM students 
            JOIN users ON students.user_id = users.id
            WHERE students.roll_number = ? 
              AND LOWER(students.name) LIKE ? 
              AND users.academic_session = ? 
              AND users.current_term = ?
        ''', (roll_number, f'%{name_input}%', academic_session, current_term)).fetchone()
        conn.close()
        
        if student:
            return redirect(url_for('public_report_card', student_id=student['id']))
        else:
            flash('Invalid details, or no result found for the selected Session and Term. Please check and try again.', 'danger')
            
    return render_template('check_result.html')

@app.route('/public_report/<int:student_id>')
def public_report_card(student_id):
    """Renders result for public viewers (students/parents) without requiring admin login."""
    context = get_report_card_context(student_id)
    if not context:
        return redirect(url_for('check_result'))
    return render_template('report_card.html', **context, auto_print=False)


# ==========================================
# SCHOOL ADMIN DASHBOARD & SUBSCRIPTION LOCK
# ==========================================

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session and 'email' not in session:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    
    user_id = session.get('user_id')
    if not user_id:
        user_row = conn.execute('SELECT * FROM users WHERE email = ?', (session.get('email'),)).fetchone()
        if user_row:
            user_id = user_row['id']
            session['user_id'] = user_id
    
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    school_name = user['school_name'] if user and 'school_name' in user.keys() else "School Dashboard"
    
    current_term_label = get_active_term_for_user(user_id)
    
    sub = conn.execute(
        'SELECT * FROM school_subscriptions WHERE user_id = ? AND term = ? AND status = "active"', 
        (user_id, current_term_label)
    ).fetchone()
    
    is_paid = 1 if sub else 0
    
    if not is_paid:
        conn.close()
        return redirect(url_for('payment_portal'))
    
    students_rows = conn.execute('SELECT * FROM students WHERE user_id = ?', (user_id,)).fetchall()
    conn.close()
    
    students = [dict(row) for row in students_rows]
    return render_template('dashboard.html', students=students, school_name=school_name, is_paid=is_paid, user=user)

@app.route('/payment_portal')
def payment_portal():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    user_row = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    conn.close()
    
    user = dict(user_row) if user_row else {}
    return render_template('payment_portal.html', user=user)

@app.route('/student_list')
@app.route('/students')
@app.route('/view_students')
def student_list():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    current_term_label = get_active_term_for_user(session['user_id'])
    sub = conn.execute('SELECT * FROM school_subscriptions WHERE user_id = ? AND term = ? AND status = "active"', (session['user_id'], current_term_label)).fetchone()
    if not sub:
        conn.close()
        return redirect(url_for('payment_portal'))

    students_rows = conn.execute('SELECT * FROM students WHERE user_id = ?', (session['user_id'],)).fetchall()
    conn.close()
    
    students = [dict(row) for row in students_rows]
    try:
        return render_template('student_list.html', students=students)
    except:
        return render_template('dashboard.html', students=students)

@app.route('/settings', methods=['GET', 'POST'])
@app.route('/school_settings', methods=['GET', 'POST'])
@app.route('/school-settings', methods=['GET', 'POST'])
def school_settings():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    current_term_label = get_active_term_for_user(session['user_id'])
    sub = conn.execute('SELECT * FROM school_subscriptions WHERE user_id = ? AND term = ? AND status = "active"', (session['user_id'], current_term_label)).fetchone()
    if not sub:
        conn.close()
        return redirect(url_for('payment_portal'))
    
    if request.method == 'POST':
        school_name = request.form.get('school_name', '')
        address = request.form.get('address', '')
        motto = request.form.get('motto', '')
        phone = request.form.get('phone', '')
        email = request.form.get('email', '')
        vacation_date = request.form.get('vacation_date', '')
        resumption_date = request.form.get('resumption_date', '')
        academic_session = request.form.get('academic_session', '')
        current_term = request.form.get('current_term', '')
        
        logo_file = request.files.get('logo')
        if logo_file and logo_file.filename != '':
            os.makedirs('static/uploads', exist_ok=True)
            logo_filename = f"logo_user_{session['user_id']}.png"
            logo_path = os.path.join('static/uploads', logo_filename)
            logo_file.save(logo_path)
            logo_value = f"uploads/{logo_filename}"
        else:
            existing_user = conn.execute('SELECT logo FROM users WHERE id = ?', (session['user_id'],)).fetchone()
            logo_value = existing_user['logo'] if existing_user else None
            
        conn.execute('''
            UPDATE users 
            SET school_name = ?, address = ?, motto = ?, phone = ?, email = ?, logo = ?, vacation_date = ?, resumption_date = ?, academic_session = ?, current_term = ? 
            WHERE id = ?
        ''', (school_name, address, motto, phone, email, logo_value, vacation_date, resumption_date, academic_session, current_term, session['user_id']))
                         
        conn.commit()
        conn.close()
        return redirect(url_for('dashboard'))
        
    user_row = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    conn.close()
    
    user = dict(user_row) if user_row else {}
    
    try:
        return render_template('school_settings.html', user=user, settings=user)
    except:
        try:
            return render_template('settings.html', user=user, settings=user)
        except:
            return redirect(url_for('dashboard'))

@app.route('/add_student', methods=['GET', 'POST'])
@app.route('/student_entry', methods=['GET', 'POST'])
@app.route('/students/add', methods=['GET', 'POST'])
def add_student():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    current_term_label = get_active_term_for_user(session['user_id'])
    sub = conn.execute('SELECT * FROM school_subscriptions WHERE user_id = ? AND term = ? AND status = "active"', (session['user_id'], current_term_label)).fetchone()
    if not sub:
        conn.close()
        return redirect(url_for('payment_portal'))

    if request.method == 'POST':
        name = request.form['name']
        student_class = request.form['class']
        roll_number = request.form.get('roll_number', '')
        sex = request.form.get('sex', '')
        department = request.form.get('department', '')
        dob = request.form.get('dob', '')
        
        conn.execute('''
            INSERT INTO students (user_id, name, class, roll_number, sex, department, dob)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (session['user_id'], name, student_class, roll_number, sex, department, dob))
        conn.commit()
        conn.close()
        
        return redirect(url_for('dashboard'))
        
    conn.close()
    try:
        return render_template('add_student.html')
    except:
        return render_template('student_entry.html')

@app.route('/students/<int:student_id>/marks', methods=['GET', 'POST'])
@app.route('/marks_entry/<int:student_id>', methods=['GET', 'POST'])
def marks_entry(student_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    current_term_label = get_active_term_for_user(session['user_id'])
    sub = conn.execute('SELECT * FROM school_subscriptions WHERE user_id = ? AND term = ? AND status = "active"', (session['user_id'], current_term_label)).fetchone()
    if not sub:
        conn.close()
        return redirect(url_for('payment_portal'))

    student_row = conn.execute('SELECT * FROM students WHERE id = ?', (student_id,)).fetchone()
    student = dict(student_row) if student_row else {}

    class_name = student.get('class', '').upper()
    if 'JS' in class_name or 'JSS' in class_name:
        subjects = [
            'English language', 'Mathematics', 'Physical and health education (PHE)', 'Social studies', 
            'Business studies', 'Basic Technology', 'Basic Science', 'Lit in English', 
            'Cultural and creative art (CCA)', 'Yoruba/Hausa/Igbo', 'Agric science', 'Home Economics', 
            'Computer studies/ICT', 'Data processing', 'Civic Education', 'CRS / IRK', 
            'French', 'Trade subject', 'Fine art', 'Food & Nutrition'
        ]
    else:
        subjects = [
            'English Language', 'Lit-in-English', 'Mathematics', 'Physics', 'Chemistry', 
            'Biology', 'Agric Science', 'Geography', 'Account', 'Commerce', 
            'Further Maths', 'Economics', 'Marketing', 'Government', 'ICT', 
            'Data Processing', 'Civic Education', 'Yoruba', 'C.R.S.', 'Music'
        ]
    
    if request.method == 'POST':
        present = request.form.get("attendance_present", 0)
        absent = request.form.get("attendance_absent", 0)
        total_days = request.form.get("total_school_days", 0)
        tc_comment = request.form.get("class_teacher_comment", "")
        hc_comment = request.form.get("head_teacher_comment", "")
        
        award_won = request.form.get("award_won", "")
        bill_debt = float(request.form.get("bill_debt", 0) or 0)
        bill_school_fees = float(request.form.get("bill_school_fees", 0) or 0)
        bill_computer = float(request.form.get("bill_computer", 0) or 0)
        bill_lessons = float(request.form.get("bill_lessons", 0) or 0)
        bill_utility = float(request.form.get("bill_utility", 0) or 0)

        conn.execute("""
            UPDATE students 
            SET attendance_present = ?, attendance_absent = ?, total_school_days = ?, 
                award_won = ?, bill_debt = ?, bill_school_fees = ?, bill_computer = ?, 
                bill_lessons = ?, bill_utility = ?,
                class_teacher_comment = ?, head_teacher_comment = ?
            WHERE id = ?
        """, (present, absent, total_days, award_won, bill_debt, bill_school_fees, bill_computer, bill_lessons, bill_utility, tc_comment, hc_comment, student_id))

        conn.execute("DELETE FROM marks WHERE student_id = ?", (student_id,))
        
        for subj in subjects:
            key_slug = subj.lower().replace(" ", "_").replace("-", "_").replace("(", "").replace(")", "").replace("/", "_")
            
            test_val_str = request.form.get(f"test_{key_slug}", "").strip()
            exam_val_str = request.form.get(f"exam_{key_slug}", "").strip()
            last_val_str = request.form.get(f"last_{key_slug}", "").strip()
            
            if test_val_str != "" or exam_val_str != "" or last_val_str != "":
                test_val = float(test_val_str or 0)
                exam_val = float(exam_val_str or 0)
                last_val = float(last_val_str or 0)
                
                conn.execute(
                    "INSERT INTO marks (student_id, subject, test_score, exam_score, last_cumm) VALUES (?, ?, ?, ?, ?)",
                    (student_id, subj, test_val, exam_val, last_val)
                )

        conn.execute("DELETE FROM behavior_ratings WHERE student_id = ?", (student_id,))
        all_traits = [
            'Creative', 'Verbal Fluency', 'Games', 'Sports', 'Handling tools', 'Drawing & Painting', 'Music Skills',
            'Punctuality', 'Neatness', 'Politeness', 'Honesty', 'Relationship with others', 'Leadership', 'Emotional Stability', 'Attitude to school', 'Attentiveness', 'Perseverance'
        ]
        
        for trait in all_traits:
            trait_key = trait.lower().replace(" ", "_").replace("&", "and")
            rating = request.form.get(trait_key, "1")
            conn.execute(
                "INSERT INTO behavior_ratings (student_id, trait, rating) VALUES (?, ?, ?)",
                (student_id, trait, int(rating))
            )

        conn.commit()
        conn.close()
        return redirect(url_for('report_card', student_id=student_id))

    marks = conn.execute('SELECT * FROM marks WHERE student_id = ?', (student_id,)).fetchall()
    behavior_rows = conn.execute('SELECT * FROM behavior_ratings WHERE student_id = ?', (student_id,)).fetchall()
    conn.close()
    
    marks_dict = {row['subject']: dict(row) for row in marks}
    behavior_dict = {row['trait']: row['rating'] for row in behavior_rows}
    
    return render_template('marks_entry.html', student=student, subjects=subjects, marks_dict=marks_dict, behavior_dict=behavior_dict)

@app.route('/report_card/<int:student_id>/pdf')
def report_card_pdf(student_id):
    context = get_report_card_context(student_id)
    if not context:
        return "Student not found", 404
        
    html = render_template('report_card.html', **context, auto_print=False)
    
    pdf_output = BytesIO()
    pisa_status = pisa.CreatePDF(BytesIO(html.encode('utf-8')), dest=pdf_output)
    
    if pisa_status.err:
        return "An error occurred while generating the PDF", 500
    
    pdf_output.seek(0)
    student_name = context['student'].get('name', 'student').replace(' ', '_')
    filename = f"report_card_{student_name}.pdf"
    
    return send_file(
        pdf_output,
        as_attachment=True,
        download_name=filename,
        mimetype='application/pdf'
    )

@app.route('/report_card/<int:student_id>')
@app.route('/view_report/<int:student_id>')
@app.route('/report/<int:student_id>')
@app.route('/print_report/<int:student_id>')
@app.route('/generate_pdf/<int:student_id>')
def report_card(student_id):
    context = get_report_card_context(student_id)
    if not context:
        return redirect(url_for('check_result'))
        
    return render_template(
        'report_card.html',
        **context,
        auto_print=False
    )

def get_report_card_context(student_id):
    conn = get_db_connection()
    student_row = conn.execute('SELECT * FROM students WHERE id = ?', (student_id,)).fetchone()
    if not student_row:
        conn.close()
        return None
        
    student = dict(student_row)
    
    user_row = conn.execute('SELECT * FROM users WHERE id = ?', (student['user_id'],)).fetchone()
    raw_marks = conn.execute('SELECT * FROM marks WHERE student_id = ?', (student_id,)).fetchall()
    behavior_ratings = conn.execute('SELECT * FROM behavior_ratings WHERE student_id = ?', (student_id,)).fetchall()
    conn.close()
    
    user = dict(user_row) if user_row else {}
    
    processed_marks = []
    total_obtained = 0
    
    for m in raw_marks:
        test = m['test_score'] or 0
        exam = m['exam_score'] or 0
        total = test + exam
        last_cumm = m['last_cumm'] or 0
        cumm = total + last_cumm if last_cumm > 0 else total
        
        if total >= 75: grade, remark = 'A1', 'Excellent'
        elif total >= 70: grade, remark = 'B2', 'Very Good'
        elif total >= 65: grade, remark = 'B3', 'Good'
        elif total >= 60: grade, remark = 'C4', 'Credit'
        elif total >= 55: grade, remark = 'C5', 'Credit'
        elif total >= 50: grade, remark = 'C6', 'Average'
        elif total >= 45: grade, remark = 'D7', 'Fair'
        elif total >= 40: grade, remark = 'E8', 'Pass'
        else: grade, remark = 'F9', 'Fail'
        
        total_obtained += total
        processed_marks.append({
            'subject': m['subject'],
            'test_score': test,
            'exam_score': exam,
            'total': total,
            'last_cumm': last_cumm,
            'cumm': cumm,
            'grade': grade,
            'remark': remark
        })
        
    max_possible = len(processed_marks) * 100 if processed_marks else 1
    percentage = (total_obtained / max_possible) * 100 if max_possible > 0 else 0
    
    if percentage >= 75: student_grade = 'A'
    elif percentage >= 70: student_grade = 'B'
    elif percentage >= 60: student_grade = 'C'
    elif percentage >= 50: student_grade = 'D'
    else: student_grade = 'F'

    return {
        'student': student,
        'user': user,
        'marks': processed_marks,
        'behavior_ratings': behavior_ratings,
        'total_obtained': total_obtained,
        'percentage': percentage,
        'student_grade': student_grade,
        'static_folder': os.path.join(current_app.root_path, 'static')
    }

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/pay', methods=['POST'])
def pay():
    try:
        user_id = session.get('user_id')
        if not user_id:
            return redirect(url_for('login'))
            
        conn = get_db_connection()
        user_row = conn.execute('SELECT email FROM users WHERE id = ?', (user_id,)).fetchone()
        conn.close()
        
        email = user_row['email'] if user_row and user_row['email'] else "yunusasaheed5@gmail.com"
        
        # Capture selected coverage type from payment portal dropdown
        coverage_type = request.form.get('coverage_type', 'Junior')
        
        # Promotional Pricing Logic
        if coverage_type == 'Senior':
            amount_naira = 20000
        elif coverage_type == 'Both':
            amount_naira = 30000
        elif coverage_type == 'Session_Both':
            amount_naira = 80000
        else:
            amount_naira = 15000  # Junior Default
            
        amount_kobo = amount_naira * 100  # Convert to kobo for Paystack
        
        url = "https://api.paystack.co/transaction/initialize"
        headers = {
            "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "email": email,
            "amount": amount_kobo,
            "callback_url": url_for('verify_payment', _external=True)
        }
        
        response = requests.post(url, json=data, headers=headers)
        res_data = response.json()
        
        if res_data.get('status'):
            auth_url = res_data['data']['authorization_url']
            return redirect(auth_url)
            
        print("Paystack Error Response:", res_data)
        return f"Payment initialization failed: {res_data.get('message', 'Unknown error')}", 400

    except Exception as e:
        print("Python Exception in /pay route:", str(e))
        return f"Internal Server Error: {str(e)}", 500


@app.route('/verify')
def verify_payment():
    try:
        reference = request.args.get('reference')
        if not reference:
            return "Payment reference missing.", 400
            
        url = f"https://api.paystack.co/transaction/verify/{reference}"
        headers = {
            "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}"
        }
        
        response = requests.get(url, headers=headers)
        res_data = response.json()
        
        if res_data.get('status') and res_data['data']['status'] == 'success':
            email = res_data['data']['customer']['email']
            
            conn = get_db_connection()
            user = conn.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()
            
            if not user:
                user_id = session.get('user_id')
            else:
                user_id = user['id']
                
            if user_id:
                current_term_label = get_active_term_for_user(user_id)
                
                existing = conn.execute(
                    'SELECT * FROM school_subscriptions WHERE user_id = ? AND term = ?', 
                    (user_id, current_term_label)
                ).fetchone()
                
                if existing:
                    conn.execute(
                        'UPDATE school_subscriptions SET status = "active" WHERE user_id = ? AND term = ?',
                        (user_id, current_term_label)
                    )
                else:
                    conn.execute(
                        'INSERT INTO school_subscriptions (user_id, term, status) VALUES (?, ?, "active")',
                        (user_id, current_term_label)
                    )
                conn.commit()
            
            conn.close()
            return redirect(url_for('dashboard'))
        
        return "Payment verification failed."
    except Exception as e:
        print("Python Exception in /verify route:", str(e))
        return f"Internal Server Error during verification: {str(e)}", 500

if __name__ == '__main__':
    app.run(debug=True)