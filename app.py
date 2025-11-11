from flask import Flask, render_template, request, redirect, url_for, session, g, flash, jsonify
import os
import sqlite3
from datetime import datetime, timedelta

DATABASE = os.path.join(os.path.dirname(__file__), 'dentalcare.db')
SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'change-me')

# ---- DB Helpers ----

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    with open(os.path.join(os.path.dirname(__file__), 'schema.sql'), 'r', encoding='utf-8') as f:
        db.executescript(f.read())
    db.commit()


# ---- App Factory ----

def create_app():
    app = Flask(__name__, template_folder='templates', static_folder='static')
    app.secret_key = SECRET_KEY

    @app.before_request
    def before_request():
        get_db()

    @app.teardown_appcontext
    def teardown_db(_):
        close_db()

    # Ensure DB exists with tables
    if not os.path.exists(DATABASE) or os.path.getsize(DATABASE) == 0:
        with app.app_context():
            init_db()

    # ---- Utility ----
    def current_user():
        uid = session.get('user_id')
        if not uid:
            return None
        cur = g.db.execute("SELECT * FROM tbl_accounts WHERE acc_id = ?", (uid,))
        row = cur.fetchone()
        return row

    def require_role(roles):
        def wrapper(fn):
            def inner(*args, **kwargs):
                user = current_user()
                if user is None or (roles and user['acc_role'] not in roles):
                    flash('Unauthorized', 'error')
                    return redirect(url_for('login'))
                return fn(*args, **kwargs)
            inner.__name__ = fn.__name__
            return inner
        return wrapper

    def log_action(actor, action, details=""):
        try:
            g.db.execute(
                "INSERT INTO tbl_logs (actor_id, actor_role, action, details, created_at) VALUES (?, ?, ?, ?, datetime('now'))",
                (actor['acc_id'] if actor else None, (actor and actor['acc_role']) or 'Public', action, details)
            )
            g.db.commit()
        except Exception:
            pass

    # ---- Routes: Public ----
    @app.route('/')
    def home():
        return render_template('index.html', user=current_user())

    @app.route('/about')
    def about():
        return render_template('about.html', user=current_user())

    # ---- Public booking (client) ----
    @app.route('/request', methods=['GET', 'POST'])
    def public_request():
        if request.method == 'POST':
            name = request.form.get('name','').strip()
            age = request.form.get('age', type=int)
            sex = request.form.get('sex','M')
            contact = request.form.get('contact','').strip()
            address = request.form.get('address','').strip()
            if not name or not age or not address or not contact:
                flash('Please complete all required fields.', 'error')
                return render_template('request.html', user=current_user(), form=request.form)
            g.db.execute(
                "INSERT INTO tbl_patients (pat_name, pat_age, pat_sex, pat_contact, pat_address) VALUES (?, ?, ?, ?, ?)",
                (name, age, sex, contact, address)
            )
            g.db.commit()
            log_action(None, 'patient_request', name)
            flash('Thank you. Our staff will select the service, date, and time and contact you to confirm.', 'success')
            return redirect(url_for('public_request'))
        return render_template('request.html', user=current_user())

    def get_available_times(dentist_id, app_date):
        """Get available time slots for a dentist on a given date"""
        # Get dentist's working hours
        den = g.db.execute("SELECT work_start, work_end, work_days FROM tbl_dentists WHERE dentist_id=?", (dentist_id,)).fetchone()
        if not den:
            return []

        # Check if dentist works on this day
        day_of_week = datetime.strptime(app_date, '%Y-%m-%d').strftime('%A')
        if day_of_week not in (den['work_days'] or 'Monday,Tuesday,Wednesday,Thursday,Friday'):
            return []

        # Get all booked times for this dentist on this date
        booked = g.db.execute(
            "SELECT app_time FROM tbl_appointments WHERE dentist_id=? AND app_date=? AND app_status IN ('Approved', 'Scheduled')",
            (dentist_id, app_date)
        ).fetchall()
        booked_times = {b['app_time'] for b in booked}

        # Generate all possible times between work_start and work_end (30-min intervals)
        all_times = [
            '09:00', '09:30', '10:00', '10:30', '11:00', '11:30',
            '13:00', '13:30', '14:00', '14:30', '15:00', '15:30'
        ]

        # Filter to only times within working hours and not booked
        work_start = den['work_start'] or '08:00'
        work_end = den['work_end'] or '17:00'
        available = [t for t in all_times if work_start <= t < work_end and t not in booked_times]
        return available

    @app.route('/book', methods=['GET', 'POST'])
    def book_appointment():
        user = current_user()

        # Restrict certain roles
        if user and user['acc_role'] in ['Dentist', 'Staff', 'Super Admin', 'Admin']:
            flash('You cannot book appointments. Only customers and guests can book.', 'error')
            return redirect(url_for('home'))

        cid = user['acc_id'] if user and user['acc_role'] == 'Customer' else None

        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            age = request.form.get('age', type=int)
            contact = request.form.get('contact', '').strip()
            address = request.form.get('address', '').strip()
            dentist_id = request.form.get('dentist_id', type=int)
            app_date = request.form.get('app_date', '').strip()
            app_time = request.form.get('app_time', '').strip()
            app_service = request.form.get('app_service', 'Dental Checkup')

            if not all([name, age, contact, address, dentist_id, app_date, app_time]):
                flash('All fields are required.', 'error')
            else:
                try:
                    # Validate dentist schedule
                    den = g.db.execute(
                        "SELECT work_start, work_end, work_days FROM tbl_dentists WHERE dentist_id=?",
                        (dentist_id,)
                    ).fetchone()

                    if not den:
                        flash('Dentist schedule not configured', 'error')
                    else:
                        day_of_week = datetime.strptime(app_date, '%Y-%m-%d').strftime('%A')
                        if day_of_week not in (den['work_days'] or ''):
                            flash(f'Selected dentist does not work on {day_of_week}', 'error')
                        else:
                            # Check time within working hours
                            app_time_obj = datetime.strptime(app_time, '%H:%M')
                            work_start_obj = datetime.strptime(den['work_start'], '%H:%M')
                            work_end_obj = datetime.strptime(den['work_end'], '%H:%M')

                            if not (work_start_obj <= app_time_obj < work_end_obj):
                                flash(f"Dentist available only between {den['work_start']} and {den['work_end']}", 'error')
                            else:
                                # Get service price
                                service_row = g.db.execute(
                                    "SELECT service_price FROM tbl_services WHERE service_name = ?",
                                    (app_service,)
                                ).fetchone()
                                service_price = service_row['service_price'] if service_row else 50.00

                                # Insert patient (linked to customer if logged in)
                                g.db.execute(
                                    "INSERT INTO tbl_patients (pat_name, pat_age, pat_sex, pat_contact, pat_address, customer_id) VALUES (?, ?, ?, ?, ?, ?)",
                                    (name, age, 'M', contact, address, cid)
                                )
                                g.db.commit()

                                # Get new patient ID
                                pat = g.db.execute(
                                    "SELECT pat_id FROM tbl_patients WHERE pat_name = ? AND pat_contact = ? ORDER BY pat_id DESC LIMIT 1",
                                    (name, contact)
                                ).fetchone()

                                if pat:
                                    # Insert appointment
                                    g.db.execute(
                                        "INSERT INTO tbl_appointments (pat_id, dentist_id, app_date, app_time, app_service, app_service_price, app_status, payment_status) VALUES (?, ?, ?, ?, ?, ?, 'Pending', 'Unpaid')",
                                        (pat['pat_id'], dentist_id, app_date, app_time, app_service, service_price)
                                    )
                                    g.db.commit()

                                    # Log and redirect
                                    app = g.db.execute(
                                        "SELECT app_id FROM tbl_appointments WHERE pat_id = ? ORDER BY app_id DESC LIMIT 1",
                                        (pat['pat_id'],)
                                    ).fetchone()

                                    log_action(cid, 'appointment_book', f"pat:{pat['pat_id']} dentist:{dentist_id} {app_date} {app_time}")
                                    session['pending_appointment'] = app['app_id']
                                    return redirect(url_for('appointment_payment'))
                except Exception as e:
                    flash(f'Error booking appointment: {str(e)}', 'error')

        # Fetch form data
        dentists = g.db.execute(
            "SELECT a.acc_id, a.acc_name FROM tbl_accounts a WHERE a.acc_role='Dentist' AND a.acc_status='Approved' ORDER BY a.acc_name"
        ).fetchall()
        services = g.db.execute("SELECT service_name, service_price FROM tbl_services ORDER BY service_name").fetchall()
        min_date = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
        booking_mode = user['acc_role'] if user else 'guest'

        # Prefill customer info if logged in as Customer
        customer_info = None
        if user and user['acc_role'] == 'Customer':
            customer_info = {
                'name': user['acc_name'],
                'contact': user['acc_contact']
            }

        return render_template(
            'book_appointment.html',
            dentists=dentists,
            services=services,
            min_date=min_date,
            user=current_user(),
            booking_mode=booking_mode,
            customer_info=customer_info
        )


    @app.route('/api/available-times/<int:dentist_id>/<date>')
    def get_available_times_api(dentist_id, date):
        from flask import jsonify
        available = get_available_times(dentist_id, date)
        return jsonify(times=available)

    @app.route('/api/services/<int:dentist_id>')
    def get_services_by_dentist(dentist_id):
        dentist = g.db.execute("SELECT specialty FROM tbl_dentists WHERE dentist_id = ?", (dentist_id,)).fetchone()
        if not dentist or not dentist['specialty']:
            services = g.db.execute("SELECT service_name, service_price FROM tbl_services ORDER BY service_name").fetchall()
        else:
            services = g.db.execute("SELECT service_name, service_price FROM tbl_services WHERE service_specialty = ? ORDER BY service_name", (dentist['specialty'],)).fetchall()
        return jsonify(services=[dict(s) for s in services])

    @app.route('/appointment/payment', methods=['GET', 'POST'])
    def appointment_payment():
        app_id = session.get('pending_appointment')
        if not app_id:
            flash('No pending appointment.', 'error')
            return redirect(url_for('book_appointment'))

        app = g.db.execute(
            "SELECT a.*, p.pat_name, p.pat_contact FROM tbl_appointments a JOIN tbl_patients p ON a.pat_id = p.pat_id WHERE a.app_id = ?",
            (app_id,)
        ).fetchone()

        if not app:
            flash('Appointment not found.', 'error')
            return redirect(url_for('book_appointment'))

        if request.method == 'POST':
            payment_method = request.form.get('payment_method','GCash')
            g.db.execute("UPDATE tbl_appointments SET payment_method = ?, payment_status = 'Paid' WHERE app_id = ?", (payment_method, app_id))
            g.db.commit()
            log_action(None, 'payment_completed', f"app_id:{app_id} method:{payment_method}")
            session.pop('pending_appointment', None)
            return redirect(url_for('booking_confirmation', app_id=app_id))

        return render_template('appointment_payment.html', appointment=app, user=current_user())

    @app.route('/booking/confirmation/<int:app_id>')
    def booking_confirmation(app_id):
        app = g.db.execute(
            "SELECT a.*, p.pat_name, p.pat_contact, d.acc_name as dentist_name FROM tbl_appointments a JOIN tbl_patients p ON a.pat_id = p.pat_id JOIN tbl_accounts d ON a.dentist_id = d.acc_id WHERE a.app_id = ?",
            (app_id,)
        ).fetchone()

        if not app:
            flash('Appointment not found.', 'error')
            return redirect(url_for('home'))

        return render_template('booking_confirmation.html', appointment=app, user=current_user())

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if request.method == 'POST':
            name = request.form.get('name','').strip()
            email = request.form.get('email','').strip().lower()
            password = request.form.get('password','')
            contact = request.form.get('contact','').strip()
            role = request.form.get('role','Customer')
            create_from_booking = request.form.get('create_from_booking')

            if not name or not email or not password:
                flash('All fields are required.', 'error')
                return render_template('auth_register.html', user=current_user())

            existing = g.db.execute("SELECT acc_id FROM tbl_accounts WHERE LOWER(acc_email) = ?", (email.lower(),)).fetchone()
            if existing:
                flash('Email already registered.', 'error')
                return render_template('auth_register.html', user=current_user())

            status = 'Approved' if role == 'Customer' else 'Pending Approval'
            try:
                g.db.execute(
                    "INSERT INTO tbl_accounts (acc_name, acc_email, acc_pass, acc_contact, acc_role, acc_status) VALUES (?, ?, ?, ?, ?, ?)",
                    (name, email, password, contact, role, status)
                )
                g.db.commit()
                cur = g.db.execute("SELECT acc_id FROM tbl_accounts WHERE LOWER(acc_email) = ?", (email.lower(),))
                acc = cur.fetchone()
                if acc:
                    if role == 'Dentist':
                        g.db.execute("INSERT INTO tbl_dentists (dentist_id, specialty) VALUES (?, ?)", (acc['acc_id'], request.form.get('specialty','General Dentistry')))
                        g.db.commit()
                    elif role == 'Customer' and create_from_booking:
                        app_id = int(create_from_booking)
                        app = g.db.execute("SELECT pat_id FROM tbl_appointments WHERE app_id = ?", (app_id,)).fetchone()
                        if app:
                            g.db.execute("UPDATE tbl_patients SET customer_id = ? WHERE pat_id = ?", (acc['acc_id'], app['pat_id']))
                            g.db.commit()
                            log_action(None, 'customer_booking_claimed', f"app_id:{app_id} customer_id:{acc['acc_id']}")
                flash('Registration successful.', 'success')
                return redirect(url_for('login'))
            except sqlite3.IntegrityError as e:
                flash('Registration failed. Please try again.', 'error')
        return render_template('auth_register.html', user=current_user())

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            email = request.form.get('email','').strip()
            password = request.form.get('password','')
            cur = g.db.execute("SELECT * FROM tbl_accounts WHERE acc_email = ? AND acc_pass = ?", (email, password))
            user = cur.fetchone()
            if not user:
                flash('Invalid credentials', 'error')
            elif user['acc_status'] != 'Approved':
                flash('Account not approved yet', 'error')
            else:
                session['user_id'] = user['acc_id']
                log_action(user, 'login', user['acc_email'])
                return redirect(url_for('portal'))
        return render_template('auth_login.html', user=current_user())

    @app.route('/logout')
    def logout():
        u = current_user()
        if u:
            log_action(u, 'logout', u['acc_email'])
        session.clear()
        return redirect(url_for('home'))

    @app.route('/portal')
    def portal():
        user = current_user()
        if not user:
            return redirect(url_for('login'))
        if user['acc_role'] == 'Super Admin':
            return redirect(url_for('super_admin_dashboard'))
        if user['acc_role'] == 'Admin':
            return redirect(url_for('admin_dashboard'))
        if user['acc_role'] == 'Staff':
            return redirect(url_for('staff_dashboard'))
        if user['acc_role'] == 'Dentist':
            return redirect(url_for('dentist_dashboard'))
        if user['acc_role'] == 'Customer':
            return redirect(url_for('customer_dashboard'))
        return redirect(url_for('home'))

    # ---- Super Admin ----
    @app.route('/super-admin')
    @require_role(['Super Admin'])
    def super_admin_dashboard():
        counts = {}
        for table in ['tbl_accounts','tbl_patients','tbl_dentists','tbl_appointments']:
            cur = g.db.execute(f"SELECT COUNT(*) as c FROM {table}")
            counts[table] = cur.fetchone()['c']
        return render_template('dashboard_superadmin.html', counts=counts, user=current_user())

    @app.post('/super-admin/reset')
    @require_role(['Super Admin'])
    def super_admin_reset():
        g.db.execute("DELETE FROM tbl_appointments")
        g.db.execute("DELETE FROM tbl_dentists")
        g.db.execute("DELETE FROM tbl_patients")
        g.db.execute("DELETE FROM tbl_accounts WHERE acc_role != 'Super Admin'")
        g.db.commit()
        log_action(current_user(), 'reset_all', '')
        flash('All data wiped except Super Admin.', 'success')
        return redirect(url_for('super_admin_dashboard'))

    @app.route('/super-admin/accounts')
    @require_role(['Super Admin'])
    def super_admin_accounts():
        role = request.args.get('role')
        if role:
            cur = g.db.execute("SELECT acc_id, acc_name, acc_email, acc_role, acc_status FROM tbl_accounts WHERE acc_role = ? AND acc_role != 'Super Admin' ORDER BY acc_name", (role,))
        else:
            cur = g.db.execute("SELECT acc_id, acc_name, acc_email, acc_role, acc_status FROM tbl_accounts WHERE acc_role != 'Super Admin' ORDER BY acc_role, acc_name")
        users = cur.fetchall()
        return render_template('superadmin_accounts.html', users=users, user=current_user())

    @app.post('/super-admin/approve')
    @require_role(['Super Admin'])
    def super_admin_approve():
        acc_id = request.form.get('acc_id', type=int)
        action = request.form.get('action')
        role_row = g.db.execute("SELECT acc_role FROM tbl_accounts WHERE acc_id=?", (acc_id,)).fetchone()
        if not role_row or role_row['acc_role'] == 'Super Admin':
            flash('Operation not allowed', 'error')
            return redirect(url_for('super_admin_accounts'))
        if action == 'Approve':
            g.db.execute("UPDATE tbl_accounts SET acc_status='Approved' WHERE acc_id=?", (acc_id,))
        elif action == 'Reject':
            g.db.execute("UPDATE tbl_accounts SET acc_status='Rejected' WHERE acc_id=?", (acc_id,))
        elif action == 'Deactivate':
            g.db.execute("UPDATE tbl_accounts SET acc_status='Deactivated' WHERE acc_id=?", (acc_id,))
        elif action == 'Reactivate':
            g.db.execute("UPDATE tbl_accounts SET acc_status='Approved' WHERE acc_id=?", (acc_id,))
        g.db.commit()
        log_action(current_user(), 'account_status_change', f"{acc_id}:{action}")
        return redirect(url_for('super_admin_accounts'))

    @app.post('/super-admin/delete')
    @require_role(['Super Admin'])
    def super_admin_delete():
        acc_id = request.form.get('acc_id', type=int)
        role_row = g.db.execute("SELECT acc_role FROM tbl_accounts WHERE acc_id=?", (acc_id,)).fetchone()
        if not role_row or role_row['acc_role'] == 'Super Admin':
            flash('Operation not allowed', 'error')
            return redirect(url_for('super_admin_accounts'))
        g.db.execute("DELETE FROM tbl_accounts WHERE acc_id=?", (acc_id,))
        g.db.commit()
        log_action(current_user(), 'delete_account', str(acc_id))
        flash('Account deleted', 'success')
        return redirect(url_for('super_admin_accounts'))

    @app.route('/super-admin/data')
    @require_role(['Super Admin'])
    def super_admin_data():
        counts = {
            'accounts': g.db.execute("SELECT COUNT(*) c FROM tbl_accounts").fetchone()['c'],
            'patients': g.db.execute("SELECT COUNT(*) c FROM tbl_patients").fetchone()['c'],
            'dentists': g.db.execute("SELECT COUNT(*) c FROM tbl_dentists").fetchone()['c'],
        }
        appointments = g.db.execute("SELECT a.app_id, p.pat_name, d.acc_name AS dentist_name, a.app_date, a.app_time, a.app_status FROM tbl_appointments a LEFT JOIN tbl_patients p ON a.pat_id=p.pat_id LEFT JOIN tbl_accounts d ON a.dentist_id=d.acc_id ORDER BY a.app_date DESC, a.app_time DESC LIMIT 25").fetchall()
        return render_template('superadmin_data.html', counts=counts, appointments=appointments, user=current_user())

    @app.route('/super-admin/logs')
    @require_role(['Super Admin'])
    def super_admin_logs():
        role = request.args.get('role')
        action = request.args.get('action')
        base = "SELECT l.*, a.acc_name FROM tbl_logs l LEFT JOIN tbl_accounts a ON l.actor_id=a.acc_id"
        where = []
        params = []
        if role:
            where.append("l.actor_role = ?")
            params.append(role)
        if action:
            where.append("l.action LIKE ?")
            params.append(f"%{action}%")
        if where:
            base += " WHERE " + " AND ".join(where)
        base += " ORDER BY l.created_at DESC LIMIT 200"
        logs = g.db.execute(base, params).fetchall()
        return render_template('superadmin_logs.html', logs=logs, user=current_user())

    # ---- Admin ----
    @app.route('/admin')
    @require_role(['Admin'])
    def admin_dashboard():
        cur = g.db.execute("SELECT acc_id, acc_name, acc_email, acc_role, acc_status FROM tbl_accounts WHERE acc_role != 'Super Admin' ORDER BY acc_role, acc_name")
        users = cur.fetchall()
        cur = g.db.execute("SELECT * FROM tbl_appointments ORDER BY app_date, app_time")
        apps = cur.fetchall()
        return render_template('dashboard_admin.html', users=users, apps=apps, user=current_user())

    @app.post('/admin/approve')
    @require_role(['Admin'])
    def admin_approve():
        acc_id = request.form.get('acc_id', type=int)
        action = request.form.get('action')  # Approve/Reject/Deactivate
        if action == 'Approve':
            g.db.execute("UPDATE tbl_accounts SET acc_status = 'Approved' WHERE acc_id = ?", (acc_id,))
        elif action == 'Reject':
            g.db.execute("UPDATE tbl_accounts SET acc_status = 'Rejected' WHERE acc_id = ?", (acc_id,))
        elif action == 'Deactivate':
            g.db.execute("UPDATE tbl_accounts SET acc_status = 'Deactivated' WHERE acc_id = ?", (acc_id,))
        g.db.commit()
        return redirect(url_for('admin_dashboard'))

    # ---- Account Center ----
    @app.route('/account', methods=['GET','POST'])
    @require_role(['Super Admin','Admin','Staff','Dentist'])
    def account_center():
        user = current_user()
        if request.method == 'POST':
            name = request.form.get('name','').strip()
            contact = request.form.get('contact','').strip()
            if not name:
                flash('Name is required.', 'error')
            else:
                g.db.execute("UPDATE tbl_accounts SET acc_name=?, acc_contact=? WHERE acc_id=?", (name, contact, user['acc_id']))
                g.db.commit()
                flash('Profile updated.', 'success')
                return redirect(url_for('account_center'))
        return render_template('account.html', user=user)

    # ---- Account: Change Password ----
    @app.route('/account/password', methods=['GET','POST'])
    @require_role(['Super Admin','Admin','Staff','Dentist'])
    def change_password():
        user = current_user()
        if request.method == 'POST':
            current = request.form.get('current','')
            new = request.form.get('new','')
            confirm = request.form.get('confirm','')
            if not current or not new or not confirm:
                flash('All fields are required.', 'error')
            elif new != confirm:
                flash('New passwords do not match.', 'error')
            else:
                row = g.db.execute("SELECT acc_pass FROM tbl_accounts WHERE acc_id=?", (user['acc_id'],)).fetchone()
                if not row or row['acc_pass'] != current:
                    flash('Current password is incorrect.', 'error')
                else:
                    g.db.execute("UPDATE tbl_accounts SET acc_pass=? WHERE acc_id=?", (new, user['acc_id']))
                    g.db.commit()
                    flash('Password updated successfully.', 'success')
                    return redirect(url_for('change_password'))
        return render_template('change_password.html', user=user)

    # ---- Staff ----
    @app.route('/staff')
    @require_role(['Staff'])
    def staff_dashboard():
        return render_template('dashboard_staff.html', user=current_user())

    # Patients CRUD
    @app.route('/staff/patients')
    @require_role(['Staff'])
    def patients_list():
        cur = g.db.execute("SELECT * FROM tbl_patients ORDER BY pat_name")
        return render_template('patients_list.html', patients=cur.fetchall(), user=current_user())

    @app.route('/staff/patients/add', methods=['GET','POST'])
    @require_role(['Staff'])
    def patient_add():
        if request.method == 'POST':
            name = request.form['name']
            age = request.form.get('age', type=int)
            sex = request.form['sex']
            contact = request.form['contact']
            address = request.form['address']
            g.db.execute("INSERT INTO tbl_patients (pat_name, pat_age, pat_sex, pat_contact, pat_address) VALUES (?, ?, ?, ?, ?)", (name, age, sex, contact, address))
            g.db.commit()
            return redirect(url_for('patients_list'))
        return render_template('patient_form.html', patient=None, user=current_user())

    @app.route('/staff/patients/<int:pid>/edit', methods=['GET','POST'])
    @require_role(['Staff'])
    def patient_edit(pid):
        if request.method == 'POST':
            name = request.form['name']
            age = request.form.get('age', type=int)
            sex = request.form['sex']
            contact = request.form['contact']
            address = request.form['address']
            g.db.execute("UPDATE tbl_patients SET pat_name=?, pat_age=?, pat_sex=?, pat_contact=?, pat_address=? WHERE pat_id=?", (name, age, sex, contact, address, pid))
            g.db.commit()
            return redirect(url_for('patients_list'))
        cur = g.db.execute("SELECT * FROM tbl_patients WHERE pat_id = ?", (pid,))
        return render_template('patient_form.html', patient=cur.fetchone(), user=current_user())

    @app.post('/staff/patients/<int:pid>/delete')
    @require_role(['Staff'])
    def patient_delete(pid):
        g.db.execute("DELETE FROM tbl_patients WHERE pat_id = ?", (pid,))
        g.db.commit()
        return redirect(url_for('patients_list'))

    # Dentist Schedules
    @app.route('/staff/dentists')
    @require_role(['Staff'])
    def dentist_schedules():
        cur = g.db.execute("SELECT a.acc_id, a.acc_name, d.specialty, d.work_start, d.work_end, d.work_days FROM tbl_accounts a LEFT JOIN tbl_dentists d ON a.acc_id = d.dentist_id WHERE a.acc_role = 'Dentist' ORDER BY a.acc_name")
        return render_template('dentists_schedules.html', dentists=cur.fetchall(), user=current_user(), is_staff_view=True)

    @app.route('/dentist/schedule', methods=['GET','POST'])
    @require_role(['Dentist'])
    def dentist_schedule_edit():
        did = current_user()['acc_id']
        if request.method == 'POST':
            specialty = request.form.get('specialty','')
            work_start = request.form.get('work_start','08:00')
            work_end = request.form.get('work_end','17:00')
            work_days = request.form.get('work_days','Monday,Tuesday,Wednesday,Thursday,Friday')
            cur = g.db.execute("SELECT 1 FROM tbl_dentists WHERE dentist_id=?", (did,)).fetchone()
            if cur:
                g.db.execute("UPDATE tbl_dentists SET specialty=?, work_start=?, work_end=?, work_days=? WHERE dentist_id=?", (specialty, work_start, work_end, work_days, did))
            else:
                g.db.execute("INSERT INTO tbl_dentists (dentist_id, specialty, work_start, work_end, work_days) VALUES (?, ?, ?, ?, ?)", (did, specialty, work_start, work_end, work_days))
            g.db.commit()
            log_action(current_user(), 'own_schedule_update', str(did))
            flash('Your duty schedule has been updated successfully.', 'success')
            return redirect(url_for('dentist_dashboard'))
        cur = g.db.execute("SELECT a.acc_id, a.acc_name, d.* FROM tbl_accounts a LEFT JOIN tbl_dentists d ON a.acc_id = d.dentist_id WHERE a.acc_id=?", (did,))
        return render_template('dentist_schedule_form.html', dentist=cur.fetchone(), is_self=True, user=current_user())

    # Appointments
    @app.route('/staff/appointments')
    @require_role(['Staff'])
    def appointments_list():
        cur = g.db.execute("SELECT a.app_id, p.pat_name, d.acc_name AS dentist_name, a.app_date, a.app_time, a.app_service, a.app_status FROM tbl_appointments a LEFT JOIN tbl_patients p ON a.pat_id=p.pat_id LEFT JOIN tbl_accounts d ON a.dentist_id=d.acc_id ORDER BY a.app_date, a.app_time")
        return render_template('appointments_list.html', apps=cur.fetchall(), user=current_user())

    @app.route('/staff/appointments/schedule', methods=['GET','POST'])
    @require_role(['Staff'])
    def appointment_schedule():
        if request.method == 'POST':
            pid = request.form.get('patient_id', type=int)
            did = request.form.get('dentist_id', type=int)
            app_date = request.form.get('app_date')
            app_time_str = request.form.get('app_time')
            app_service = request.form.get('app_service','Dental Checkup')

            # Validate dentist working day and time
            den = g.db.execute("SELECT work_start, work_end, work_days FROM tbl_dentists WHERE dentist_id=?", (did,)).fetchone()
            if not den:
                flash('Dentist schedule not set', 'error')
                return redirect(url_for('appointment_schedule'))

            day_of_week = datetime.strptime(app_date, '%Y-%m-%d').strftime('%A')
            if day_of_week not in (den['work_days'] or ''):
                flash(f'Dentist does not work on {day_of_week}', 'error')
                return redirect(url_for('appointment_schedule'))

            # Check if appointment time is within dentist's working hours
            app_time_obj = datetime.strptime(app_time_str, '%H:%M')
            work_start_obj = datetime.strptime(den['work_start'], '%H:%M')
            work_end_obj = datetime.strptime(den['work_end'], '%H:%M')
            if not (work_start_obj <= app_time_obj < work_end_obj):
                flash(f"Appointment time must be between {den['work_start']} and {den['work_end']}", 'error')
                return redirect(url_for('appointment_schedule'))

            # Check conflicts
            exists = g.db.execute("SELECT 1 FROM tbl_appointments WHERE dentist_id=? AND app_date=? AND app_time=? AND app_status IN ('Pending','Approved','Scheduled','Confirmed')", (did, app_date, app_time_str)).fetchone()
            if exists:
                flash('Slot already booked', 'error')
                return redirect(url_for('appointment_schedule'))

            # Get service price
            service_price = 500.00
            service_row = g.db.execute("SELECT service_price FROM tbl_services WHERE service_name=?", (app_service,)).fetchone()
            if service_row:
                service_price = service_row['service_price']

            g.db.execute("INSERT INTO tbl_appointments (pat_id, dentist_id, app_date, app_time, app_service, app_service_price, app_status, payment_status) VALUES (?, ?, ?, ?, ?, ?, 'Scheduled', 'Paid')",
                         (pid, did, app_date, app_time_str, app_service, service_price))
            g.db.commit()
            log_action(current_user(), 'appointment_schedule', f"pat:{pid} dentist:{did} {app_date} {app_time_str}")
            flash('Appointment scheduled and sent to dentist.', 'success')
            return redirect(url_for('appointments_list'))

        patients = g.db.execute("SELECT pat_id, pat_name FROM tbl_patients ORDER BY pat_name").fetchall()
        dentists = g.db.execute("SELECT a.acc_id, a.acc_name FROM tbl_accounts a WHERE a.acc_role='Dentist' AND a.acc_status='Approved' ORDER BY a.acc_name").fetchall()
        services = g.db.execute("SELECT service_name, service_price FROM tbl_services ORDER BY service_name").fetchall()
        return render_template('appointment_schedule.html', patients=patients, dentists=dentists, services=services, user=current_user())

    @app.post('/staff/appointments/<int:aid>/cancel')
    @require_role(['Staff'])
    def appointment_cancel(aid):
        g.db.execute("UPDATE tbl_appointments SET app_status='Cancelled' WHERE app_id=?", (aid,))
        g.db.commit()
        log_action(current_user(), 'appointment_cancel', str(aid))
        return redirect(url_for('appointments_list'))

    @app.route('/staff/bookings')
    @require_role(['Staff'])
    def staff_bookings():
        cur = g.db.execute("SELECT a.app_id, p.pat_name, p.pat_contact, d.acc_name AS dentist_name, a.app_date, a.app_time, a.app_service, a.app_status, a.payment_status FROM tbl_appointments a LEFT JOIN tbl_patients p ON a.pat_id=p.pat_id LEFT JOIN tbl_accounts d ON a.dentist_id=d.acc_id WHERE a.app_status='Pending' ORDER BY a.created_at DESC")
        return render_template('staff_bookings.html', bookings=cur.fetchall(), user=current_user())

    @app.post('/staff/appointments/<int:aid>/approve')
    @require_role(['Staff'])
    def appointment_approve(aid):
        g.db.execute("UPDATE tbl_appointments SET app_status='Scheduled' WHERE app_id=?", (aid,))
        g.db.commit()
        log_action(current_user(), 'appointment_approved', str(aid))
        flash('Appointment approved and scheduled.', 'success')
        return redirect(url_for('appointments_list'))

    @app.post('/staff/bookings/<int:aid>/approve')
    @require_role(['Staff'])
    def booking_approve(aid):
        g.db.execute("UPDATE tbl_appointments SET app_status='Scheduled' WHERE app_id=?", (aid,))
        g.db.commit()
        log_action(current_user(), 'booking_approved', str(aid))
        flash('Booking approved and scheduled.', 'success')
        return redirect(url_for('staff_bookings'))

    @app.post('/staff/appointments/<int:aid>/reject')
    @require_role(['Staff'])
    def appointment_reject(aid):
        g.db.execute("UPDATE tbl_appointments SET app_status='Cancelled' WHERE app_id=?", (aid,))
        g.db.commit()
        log_action(current_user(), 'appointment_rejected', str(aid))
        flash('Appointment rejected.', 'success')
        return redirect(url_for('appointments_list'))

    @app.post('/staff/bookings/<int:aid>/reject')
    @require_role(['Staff'])
    def booking_reject(aid):
        g.db.execute("UPDATE tbl_appointments SET app_status='Cancelled' WHERE app_id=?", (aid,))
        g.db.commit()
        log_action(current_user(), 'booking_rejected', str(aid))
        flash('Booking rejected.', 'success')
        return redirect(url_for('staff_bookings'))

    # ---- Dentist ----
    # ---- Customer ----
    @app.route('/customer')
    @require_role(['Customer'])
    def customer_dashboard():
        cid = current_user()['acc_id']
        cur = g.db.execute(
            "SELECT a.app_id, p.pat_name, a.app_date, a.app_time, a.app_service, a.app_service_price, a.app_status, a.payment_status, d.acc_name as dentist_name FROM tbl_appointments a JOIN tbl_patients p ON a.pat_id = p.pat_id JOIN tbl_accounts d ON a.dentist_id = d.acc_id WHERE p.customer_id=? ORDER BY a.app_date DESC, a.app_time DESC",
            (cid,)
        )
        return render_template('dashboard_customer.html', appointments=cur.fetchall(), user=current_user())

    @app.route('/customer/appointment/<int:app_id>/receipt')
    @require_role(['Customer'])
    def customer_receipt(app_id):
        cid = current_user()['acc_id']
        app = g.db.execute(
            "SELECT a.app_id, p.pat_name, p.pat_contact, p.pat_address, a.app_date, a.app_time, a.app_service, a.app_service_price, a.payment_method, a.payment_status, a.created_at, d.acc_name as dentist_name, d.acc_contact as dentist_contact FROM tbl_appointments a JOIN tbl_patients p ON a.pat_id = p.pat_id JOIN tbl_accounts d ON a.dentist_id = d.acc_id WHERE a.app_id=? AND p.customer_id=?",
            (app_id, cid)
        ).fetchone()
        if not app:
            flash('Appointment not found.', 'error')
            return redirect(url_for('customer_dashboard'))
        return render_template('customer_receipt.html', appointment=app, user=current_user())

    @app.route('/api/customer/upcoming-reminders')
    @require_role(['Customer'])
    def get_upcoming_reminders():
        cid = current_user()['acc_id']
        tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
        today = datetime.now().strftime('%Y-%m-%d')
        cur = g.db.execute(
            "SELECT a.app_id, p.pat_name, a.app_date, a.app_time, a.app_service, d.acc_name as dentist_name FROM tbl_appointments a JOIN tbl_patients p ON a.pat_id = p.pat_id JOIN tbl_accounts d ON a.dentist_id = d.acc_id WHERE p.customer_id=? AND a.app_date = ? AND a.app_status IN ('Approved', 'Scheduled')",
            (cid, tomorrow)
        )
        reminders = cur.fetchall()
        return jsonify(reminders=[dict(r) for r in reminders])

    @app.route('/dentist')
    @require_role(['Dentist'])
    def dentist_dashboard():
        did = current_user()['acc_id']
        cur = g.db.execute("SELECT a.app_id, p.pat_name, a.app_date, a.app_time, a.app_service, a.app_status FROM tbl_appointments a JOIN tbl_patients p ON a.pat_id = p.pat_id WHERE a.dentist_id=? AND a.app_status IN ('Approved', 'Scheduled', 'Confirmed') ORDER BY a.app_date, a.app_time", (did,))
        return render_template('dashboard_dentist.html', apps=cur.fetchall(), user=current_user())

    @app.post('/dentist/complete')
    @require_role(['Dentist'])
    def dentist_complete():
        did = current_user()['acc_id']
        app_id = request.form.get('app_id', type=int)
        notes = request.form.get('notes','N/A')
        g.db.execute("UPDATE tbl_appointments SET app_status='Completed', app_notes=? WHERE app_id=? AND dentist_id=?", (notes, app_id, did))
        g.db.commit()
        log_action(current_user(), 'appointment_complete', str(app_id))
        return redirect(url_for('dentist_dashboard'))

    @app.route('/dentist/completed')
    @require_role(['Dentist'])
    def dentist_completed():
        did = current_user()['acc_id']
        cur = g.db.execute("SELECT a.app_id, p.pat_name, a.app_date, a.app_time, a.app_service, a.app_notes FROM tbl_appointments a JOIN tbl_patients p ON a.pat_id = p.pat_id WHERE a.dentist_id=? AND a.app_status='Completed' ORDER BY a.app_date DESC, a.app_time DESC", (did,))
        return render_template('dentist_completed.html', apps=cur.fetchall(), user=current_user())

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, host='0.0.0.0', port=5000)
