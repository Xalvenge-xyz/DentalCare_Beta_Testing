from flask import Flask, render_template, request, redirect, url_for, session, g, flash
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

    @app.route('/book', methods=['GET', 'POST'])
    def book_appointment():
        if request.method == 'POST':
            name = request.form.get('name','').strip()
            age = request.form.get('age', type=int)
            contact = request.form.get('contact','').strip()
            dentist_id = request.form.get('dentist_id', type=int)
            app_date = request.form.get('app_date','').strip()
            app_time = request.form.get('app_time','').strip()
            app_service = request.form.get('app_service','Dental Checkup')

            if not all([name, age, contact, dentist_id, app_date, app_time]):
                flash('All fields are required.', 'error')
                dentists = g.db.execute("SELECT a.acc_id, a.acc_name FROM tbl_accounts a WHERE a.acc_role='Dentist' AND a.acc_status='Approved' ORDER BY a.acc_name").fetchall()
                min_date = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
                return render_template('book_appointment.html', user=current_user(), dentists=dentists, min_date=min_date)

            try:
                g.db.execute(
                    "INSERT INTO tbl_patients (pat_name, pat_age, pat_sex, pat_contact, pat_address) VALUES (?, ?, ?, ?, ?)",
                    (name, age, 'M', contact, '')
                )
                g.db.commit()
                pat = g.db.execute("SELECT pat_id FROM tbl_patients WHERE pat_name = ? AND pat_contact = ? ORDER BY pat_id DESC LIMIT 1", (name, contact)).fetchone()

                if pat:
                    g.db.execute(
                        "INSERT INTO tbl_appointments (pat_id, dentist_id, app_date, app_time, app_service, app_status) VALUES (?, ?, ?, ?, ?, 'Scheduled')",
                        (pat['pat_id'], dentist_id, app_date, app_time, app_service)
                    )
                    g.db.commit()
                    log_action(None, 'appointment_book', f"pat:{pat['pat_id']} dentist:{dentist_id} {app_date} {app_time}")
                    session['pending_appointment'] = pat['pat_id']
                    flash('Appointment details confirmed! Proceed to payment.', 'success')
                    return redirect(url_for('appointment_payment'))
            except Exception as e:
                flash(f'Error booking appointment: {str(e)}', 'error')

        dentists = g.db.execute("SELECT a.acc_id, a.acc_name FROM tbl_accounts a WHERE a.acc_role='Dentist' AND a.acc_status='Approved' ORDER BY a.acc_name").fetchall()
        min_date = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
        return render_template('book_appointment.html', user=current_user(), dentists=dentists, min_date=min_date)

    @app.route('/appointment/payment')
    def appointment_payment():
        pat_id = session.get('pending_appointment')
        if not pat_id:
            flash('No pending appointment.', 'error')
            return redirect(url_for('book_appointment'))
        pat = g.db.execute("SELECT * FROM tbl_patients WHERE pat_id = ?", (pat_id,)).fetchone()
        return render_template('appointment_payment.html', patient=pat, user=current_user())

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if request.method == 'POST':
            name = request.form.get('name','').strip()
            email = request.form.get('email','').strip()
            password = request.form.get('password','')
            contact = request.form.get('contact','').strip()
            role = request.form.get('role','Staff')
            status = 'Pending Approval'
            try:
                g.db.execute(
                    "INSERT INTO tbl_accounts (acc_name, acc_email, acc_pass, acc_contact, acc_role, acc_status) VALUES (?, ?, ?, ?, ?, ?)",
                    (name, email, password, contact, role, status)
                )
                g.db.commit()
                if role == 'Dentist':
                    cur = g.db.execute("SELECT acc_id FROM tbl_accounts WHERE acc_email = ?", (email,))
                    acc = cur.fetchone()
                    if acc:
                        g.db.execute("INSERT INTO tbl_dentists (dentist_id, specialty) VALUES (?, ?)", (acc['acc_id'], request.form.get('specialty','General Dentistry')))
                        g.db.commit()
                flash('Registration successful. Await approval.', 'success')
                return redirect(url_for('login'))
            except sqlite3.IntegrityError:
                flash('Email already registered.', 'error')
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
        return render_template('dentists_schedules.html', dentists=cur.fetchall(), user=current_user())

    @app.route('/staff/dentists/<int:did>/schedule', methods=['GET','POST'])
    @require_role(['Staff'])
    def dentist_schedule_edit(did):
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
            log_action(current_user(), 'dentist_schedule_update', str(did))
            return redirect(url_for('dentist_schedules'))
        cur = g.db.execute("SELECT a.acc_id, a.acc_name, d.* FROM tbl_accounts a LEFT JOIN tbl_dentists d ON a.acc_id = d.dentist_id WHERE a.acc_id=?", (did,))
        return render_template('dentist_schedule_form.html', dentist=cur.fetchone(), user=current_user())

    # Appointments
    @app.route('/staff/appointments')
    @require_role(['Staff'])
    def appointments_list():
        cur = g.db.execute("SELECT a.app_id, p.pat_name, d.acc_name AS dentist_name, a.app_date, a.app_time, a.app_status FROM tbl_appointments a LEFT JOIN tbl_patients p ON a.pat_id=p.pat_id LEFT JOIN tbl_accounts d ON a.dentist_id=d.acc_id ORDER BY a.app_date, a.app_time")
        return render_template('appointments_list.html', apps=cur.fetchall(), user=current_user())

    @app.route('/staff/appointments/schedule', methods=['GET','POST'])
    @require_role(['Staff'])
    def appointment_schedule():
        if request.method == 'POST':
            pid = request.form.get('patient_id', type=int)
            did = request.form.get('dentist_id', type=int)
            app_date = request.form.get('app_date')
            app_time_str = request.form.get('app_time')  # e.g., 14:30

            # Validate dentist working day and time
            den = g.db.execute("SELECT work_start, work_end, work_days FROM tbl_dentists WHERE dentist_id=?", (did,)).fetchone()
            if not den:
                flash('Dentist schedule not set', 'error')
                return redirect(url_for('appointment_schedule'))

            day_of_week = datetime.strptime(app_date, '%Y-%m-%d').strftime('%A')
            if day_of_week not in (den['work_days'] or ''):
                flash(f'Dentist does not work on {day_of_week}', 'error')
                return redirect(url_for('appointment_schedule'))

            # Check conflicts
            exists = g.db.execute("SELECT 1 FROM tbl_appointments WHERE dentist_id=? AND app_date=? AND app_time=? AND app_status='Scheduled'", (did, app_date, app_time_str)).fetchone()
            if exists:
                flash('Slot already booked', 'error')
                return redirect(url_for('appointment_schedule'))

            g.db.execute("INSERT INTO tbl_appointments (pat_id, dentist_id, app_date, app_time, app_service, app_status) VALUES (?, ?, ?, ?, ?, 'Scheduled')",
                         (pid, did, app_date, app_time_str, request.form.get('app_service','Dental Checkup')))
            g.db.commit()
            log_action(current_user(), 'appointment_schedule', f"pat:{pid} dentist:{did} {app_date} {app_time_str}")
            flash('Appointment scheduled', 'success')
            return redirect(url_for('appointments_list'))

        patients = g.db.execute("SELECT pat_id, pat_name FROM tbl_patients ORDER BY pat_name").fetchall()
        dentists = g.db.execute("SELECT a.acc_id, a.acc_name FROM tbl_accounts a WHERE a.acc_role='Dentist' AND a.acc_status='Approved' ORDER BY a.acc_name").fetchall()
        return render_template('appointment_schedule.html', patients=patients, dentists=dentists, user=current_user())

    @app.post('/staff/appointments/<int:aid>/cancel')
    @require_role(['Staff'])
    def appointment_cancel(aid):
        g.db.execute("UPDATE tbl_appointments SET app_status='Cancelled' WHERE app_id=?", (aid,))
        g.db.commit()
        log_action(current_user(), 'appointment_cancel', str(aid))
        return redirect(url_for('appointments_list'))

    # ---- Dentist ----
    @app.route('/dentist')
    @require_role(['Dentist'])
    def dentist_dashboard():
        did = current_user()['acc_id']
        cur = g.db.execute("SELECT a.app_id, p.pat_name, a.app_date, a.app_time, a.app_service, a.app_status FROM tbl_appointments a JOIN tbl_patients p ON a.pat_id = p.pat_id WHERE a.dentist_id=? AND a.app_status IN ('Scheduled', 'Confirmed') ORDER BY a.app_date, a.app_time", (did,))
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
