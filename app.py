from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from functools import wraps
from datetime import datetime
from config import Config
from models import db, User, AuditLog
from data_loader import (
    get_dashboard_data, get_beta_data, get_valuation_data,
    get_credit_risk_data, get_fixed_income_data, get_peer_data,
    get_checks_data, get_inputs_data, get_index_data,
    get_yin_full
)

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('login'))
            if current_user.role not in roles:
                flash('You do not have permission to access this page.', 'danger')
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def log_action(user_id, action, details=None):
    log = AuditLog(user_id=user_id, action=action, details=details)
    db.session.add(log)
    db.session.commit()


@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password) and user.is_active_user:
            login_user(user, remember=request.form.get('remember'))
            user.last_login = datetime.utcnow()
            db.session.commit()
            log_action(user.id, 'login')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('dashboard'))
        flash('Invalid username or password.', 'danger')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    log_action(current_user.id, 'logout')
    logout_user()
    return redirect(url_for('login'))


@app.route('/dashboard')
@login_required
def dashboard():
    data = get_dashboard_data()
    return render_template('dashboard.html', data=data)


@app.route('/beta')
@login_required
def beta():
    data = get_beta_data()
    return render_template('beta.html', data=data)


@app.route('/valuation')
@login_required
def valuation():
    data = get_valuation_data()
    return render_template('valuation.html', data=data)


@app.route('/credit-risk')
@login_required
def credit_risk():
    data = get_credit_risk_data()
    return render_template('credit_risk.html', data=data)


@app.route('/fixed-income')
@login_required
def fixed_income():
    data = get_fixed_income_data()
    return render_template('fixed_income.html', data=data)


@app.route('/peers')
@login_required
def peers():
    data = get_peer_data()
    return render_template('peers.html', data=data)


@app.route('/nimed-index')
@login_required
def nimed_index():
    data = get_index_data()
    return render_template('nimed_index.html', data=data)


@app.route('/checks')
@login_required
def checks():
    data = get_checks_data()
    return render_template('checks.html', data=data)


@app.route('/settings')
@login_required
@role_required('super_admin', 'head_of_research')
def settings():
    data = get_inputs_data()
    return render_template('settings.html', data=data)


@app.route('/users')
@login_required
@role_required('super_admin', 'head_of_research')
def users():
    all_users = User.query.order_by(User.created_at.desc()).all()
    return render_template('users.html', users=all_users)


@app.route('/users/create', methods=['GET', 'POST'])
@login_required
@role_required('super_admin', 'head_of_research')
def create_user():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        full_name = request.form.get('full_name', '').strip()
        password = request.form.get('password', '')
        role = request.form.get('role', 'analyst')

        if current_user.role != 'super_admin' and role in ('super_admin', 'head_of_research'):
            flash('Only Super Admins can create admin-level users.', 'danger')
            return redirect(url_for('create_user'))

        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'danger')
            return redirect(url_for('create_user'))

        if User.query.filter_by(email=email).first():
            flash('Email already exists.', 'danger')
            return redirect(url_for('create_user'))

        user = User(
            username=username, email=email, full_name=full_name,
            role=role, created_by=current_user.id
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        log_action(current_user.id, 'create_user', f'Created user {username} with role {role}')
        flash(f'User {username} created successfully.', 'success')
        return redirect(url_for('users'))
    return render_template('create_user.html')


@app.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('super_admin', 'head_of_research')
def edit_user(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash('User not found.', 'danger')
        return redirect(url_for('users'))

    if user.role == 'super_admin' and current_user.role != 'super_admin':
        flash('Only Super Admins can edit other Super Admins.', 'danger')
        return redirect(url_for('users'))

    if request.method == 'POST':
        user.full_name = request.form.get('full_name', user.full_name).strip()
        user.email = request.form.get('email', user.email).strip()
        new_role = request.form.get('role', user.role)
        if current_user.role == 'super_admin' or new_role not in ('super_admin',):
            user.role = new_role
        user.is_active_user = request.form.get('is_active') == 'on'

        new_password = request.form.get('password', '').strip()
        if new_password:
            user.set_password(new_password)

        db.session.commit()
        log_action(current_user.id, 'edit_user', f'Edited user {user.username}')
        flash(f'User {user.username} updated successfully.', 'success')
        return redirect(url_for('users'))
    return render_template('edit_user.html', user=user)


@app.route('/users/<int:user_id>/delete', methods=['POST'])
@login_required
@role_required('super_admin')
def delete_user(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash('User not found.', 'danger')
        return redirect(url_for('users'))
    if user.id == current_user.id:
        flash('You cannot delete your own account.', 'danger')
        return redirect(url_for('users'))
    username = user.username
    db.session.delete(user)
    db.session.commit()
    log_action(current_user.id, 'delete_user', f'Deleted user {username}')
    flash(f'User {username} deleted.', 'success')
    return redirect(url_for('users'))


@app.route('/audit-log')
@login_required
@role_required('super_admin', 'head_of_research')
def audit_log():
    logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(200).all()
    return render_template('audit_log.html', logs=logs)


@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        current_user.full_name = request.form.get('full_name', current_user.full_name).strip()
        new_password = request.form.get('new_password', '').strip()
        if new_password:
            current_password = request.form.get('current_password', '')
            if not current_user.check_password(current_password):
                flash('Current password is incorrect.', 'danger')
                return redirect(url_for('profile'))
            current_user.set_password(new_password)
        db.session.commit()
        flash('Profile updated successfully.', 'success')
        return redirect(url_for('profile'))
    return render_template('profile.html')


# API endpoints for charts
@app.route('/api/index-timeseries')
@login_required
def api_index_timeseries():
    data = get_index_data()
    return jsonify(data['index_timeseries'])


@app.route('/api/valuations')
@login_required
def api_valuations():
    data = get_valuation_data()
    return jsonify(data['valuations'])


@app.route('/api/betas')
@login_required
def api_betas():
    data = get_beta_data()
    return jsonify(data['betas'])


@app.route('/api/credit-risk')
@login_required
def api_credit_risk():
    data = get_credit_risk_data()
    return jsonify(data['bank_credit_risk'])


@app.route('/yin')
@login_required
def yin_dashboard():
    data = get_yin_full()
    return render_template('yin_dashboard.html', data=data)


def init_db():
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(username='admin').first():
            admin = User(
                username='admin',
                email='admin@nimedcapital.com',
                full_name='Super Administrator',
                role='super_admin'
            )
            admin.set_password('NimedAdmin2024!')
            db.session.add(admin)
            db.session.commit()
            print("Super Admin created: admin / NimedAdmin2024!")


if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)
