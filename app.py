import json
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_wtf.csrf import CSRFProtect
from functools import wraps
from datetime import datetime
from config import Config
from models import db, User, AuditLog, MarketInput, SharePrice, CompanyFinancials, YieldCurvePoint
from data_loader import (
    get_dashboard_data, get_beta_data, get_valuation_data,
    get_credit_risk_data, get_fixed_income_data, get_peer_data,
    get_checks_data, get_inputs_data, get_index_data,
    get_yin_full, get_financial_statements_data,
    get_shareholder_ratios_data, load_workbook_data,
    invalidate_live_cache
)

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)
csrf = CSRFProtect(app)


@app.template_filter('sf')
def safe_float_filter(value, default=0):
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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
    db_points = YieldCurvePoint.query.order_by(YieldCurvePoint.tenor_days).all()
    if db_points:
        data['yield_curve'] = [
            {
                'tenor': p.tenor,
                'tenor_days': p.tenor_days,
                'yield_pct': p.yield_pct,
                'coupon_pct': p.coupon_pct,
                'price': p.price,
                'maturity_date': p.maturity_date or '',
            }
            for p in db_points
        ]
        data['yc_source'] = f'Database ({db_points[0].source or "manual"}) — updated {db_points[0].updated_at.strftime("%Y-%m-%d %H:%M")}'
    else:
        data['yc_source'] = 'Base JSON (upload Excel to update)'
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
    yin = get_yin_full()
    data['yin_market'] = yin.get('market_inputs', {})
    data['yin_target'] = yin.get('target', {})
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


@app.route('/financial-statements')
@app.route('/financial-statements/<ticker>')
@login_required
def financial_statements(ticker=None):
    fs_data = get_financial_statements_data()
    tickers = sorted(fs_data.keys())
    if not ticker or ticker not in fs_data:
        ticker = tickers[0] if tickers else None
    selected = fs_data.get(ticker, {}) if ticker else {}
    return render_template('financial_statements.html',
                           fs_data=fs_data, tickers=tickers,
                           selected=selected, selected_ticker=ticker)


@app.route('/shareholder-ratios')
@login_required
def shareholder_ratios():
    data = get_shareholder_ratios_data()
    return render_template('shareholder_ratios.html', ratios=data)


@app.route('/private-capital/valuation')
@login_required
def pc_valuation():
    data = get_yin_full()
    return render_template('pc_valuation.html', data=data)


@app.route('/private-capital/monte-carlo')
@login_required
def pc_monte_carlo():
    data = get_yin_full()
    return render_template('pc_monte_carlo.html', data=data)


@app.route('/private-capital/lbo')
@login_required
def pc_lbo():
    data = get_yin_full()
    return render_template('pc_lbo.html', data=data)


@app.route('/private-capital/vc-method')
@login_required
def pc_vc():
    data = get_yin_full()
    return render_template('pc_vc.html', data=data)


@app.route('/private-capital/fund-economics')
@login_required
def pc_fund():
    data = get_yin_full()
    return render_template('pc_fund.html', data=data)


@app.route('/private-capital/debt-loan')
@login_required
def pc_debt_loan():
    data = get_yin_full()
    return render_template('pc_debt_loan.html', data=data)


@app.route('/private-capital/debt-borrower')
@login_required
def pc_debt_borrower():
    data = get_yin_full()
    return render_template('pc_debt_borrower.html', data=data)


@app.route('/private-capital/debt-distressed')
@login_required
def pc_debt_distressed():
    data = get_yin_full()
    return render_template('pc_debt_distressed.html', data=data)


@app.route('/private-capital/qualitative')
@login_required
def pc_qualitative():
    data = get_yin_full()
    return render_template('pc_qualitative.html', data=data)


@app.route('/private-capital/assumptions')
@login_required
def pc_assumptions():
    data = get_yin_full()
    return render_template('pc_assumptions.html', data=data)


@app.route('/settings/save-inputs', methods=['POST'])
@login_required
@role_required('super_admin', 'head_of_research')
def save_inputs():
    for key, label, itype, default in MarketInput.INPUT_DEFS:
        raw = request.form.get(key, '').strip()
        if not raw:
            continue
        mi = MarketInput.query.filter_by(key=key).first()
        if not mi:
            mi = MarketInput(key=key, label=label, input_type=itype)
            db.session.add(mi)
        if itype == 'select':
            mi.text_value = raw
        else:
            try:
                mi.value = float(raw)
            except (ValueError, TypeError):
                continue
        mi.updated_by = current_user.id
    db.session.commit()
    invalidate_live_cache()
    log_action(current_user.id, 'update_inputs', 'Updated market inputs')
    flash('Market inputs saved. Dashboard will reflect changes.', 'success')
    return redirect(url_for('settings'))


@app.route('/prices', methods=['GET', 'POST'])
@login_required
def share_prices():
    if request.method == 'POST' and current_user.can_edit_data():
        ticker = request.form.get('ticker', '').strip().upper()
        price = request.form.get('price_ghs', '').strip()
        shares = request.form.get('shares_m', '').strip()
        company = request.form.get('company_name', '').strip()
        pdate = request.form.get('price_date', '').strip()
        if ticker and price:
            sp = SharePrice.query.filter_by(ticker=ticker).first()
            if not sp:
                sp = SharePrice(ticker=ticker)
                db.session.add(sp)
            sp.price_ghs = float(price)
            if shares:
                sp.shares_m = float(shares)
            sp.company_name = company or ticker
            sp.price_date = pdate
            sp.source = 'Manual update'
            sp.updated_by = current_user.id
            db.session.commit()
            invalidate_live_cache()
            log_action(current_user.id, 'update_price', f'{ticker}: GHS {price}')
            flash(f'Price updated for {ticker}.', 'success')
        return redirect(url_for('share_prices'))

    prices = SharePrice.query.order_by(SharePrice.ticker).all()
    base = load_workbook_data()
    all_peers = base.get('all_peers', {})
    base_tickers = {}
    for sector, peers in all_peers.items():
        for p in peers:
            t = p.get('Ticker', '')
            if t:
                base_tickers[t] = {
                    'company': p.get('Company', t),
                    'price': p.get('Last close (GHS)', 0),
                    'shares_m': p.get('Shares (m)', 0),
                }
    return render_template('prices.html', prices=prices, base_tickers=base_tickers)


@app.route('/prices/bulk', methods=['POST'])
@login_required
@role_required('super_admin', 'head_of_research', 'senior_analyst')
def bulk_price_upload():
    f = request.files.get('price_file')
    if not f or not f.filename.endswith(('.xlsx', '.xls')):
        flash('Please upload an Excel file (.xlsx).', 'danger')
        return redirect(url_for('share_prices'))
    import openpyxl
    wb = openpyxl.load_workbook(f, data_only=True)
    ws = wb.active
    header = [str(ws.cell(1, c).value or '').strip().upper() for c in range(1, ws.max_column + 1)]

    col_date = col_ticker = col_close = col_volume = None
    for i, h in enumerate(header):
        if 'DATE' in h and 'DAILY' in h:
            col_date = i
        elif 'SHARE CODE' in h or h == 'TICKER':
            col_ticker = i
        elif ('CLOSING' in h and 'VWAP' in h) or h == 'CLOSING PRICE':
            col_close = i
        elif 'TOTAL SHARES' in h or h == 'VOLUME':
            col_volume = i

    if col_ticker is None:
        for i, h in enumerate(header):
            if 'SHARE' in h and 'CODE' in h:
                col_ticker = i
            elif i == 1:
                col_ticker = i
    if col_close is None:
        for i, h in enumerate(header):
            if 'CLOSING' in h and 'PRICE' in h:
                col_close = i
            elif 'CLOSE' in h:
                col_close = i
    if col_ticker is None or col_close is None:
        for i, h in enumerate(header):
            if i == 0 and col_ticker is None:
                col_ticker = 0
            if i == 1 and col_close is None:
                col_close = 1

    count = 0
    price_date_str = ''
    for row in ws.iter_rows(min_row=2, values_only=False):
        cells = [c.value for c in row]
        if len(cells) <= max(col_ticker, col_close):
            continue
        ticker_raw = cells[col_ticker]
        price_raw = cells[col_close]
        if not ticker_raw or not price_raw:
            continue
        ticker = str(ticker_raw).strip().upper().replace('*', '')
        if not ticker or ticker in ('TOTAL', 'GRAND TOTAL', ''):
            continue
        try:
            price = float(price_raw)
        except (ValueError, TypeError):
            continue
        if price <= 0:
            continue

        if col_date is not None and cells[col_date]:
            dv = cells[col_date]
            if hasattr(dv, 'strftime'):
                price_date_str = dv.strftime('%Y-%m-%d')
            else:
                price_date_str = str(dv).strip()

        volume = None
        if col_volume is not None and cells[col_volume]:
            try:
                volume = float(cells[col_volume])
            except (ValueError, TypeError):
                pass

        sp = SharePrice.query.filter_by(ticker=ticker).first()
        if not sp:
            sp = SharePrice(ticker=ticker)
            db.session.add(sp)
        sp.price_ghs = price
        sp.company_name = sp.company_name or ticker
        sp.price_date = price_date_str
        sp.source = f'GSE Daily: {f.filename}'
        sp.updated_by = current_user.id
        count += 1
    db.session.commit()
    invalidate_live_cache()
    log_action(current_user.id, 'bulk_price_upload', f'{count} prices from {f.filename}')
    flash(f'{count} share prices uploaded from GSE daily report.', 'success')
    return redirect(url_for('share_prices'))


@app.route('/financial-statements/upload', methods=['POST'])
@login_required
@role_required('super_admin', 'head_of_research', 'senior_analyst')
def upload_financial_statements():
    f = request.files.get('fs_file')
    ticker = request.form.get('ticker', '').strip().upper()
    if not f or not ticker:
        flash('Please select a ticker and upload a file.', 'danger')
        return redirect(url_for('financial_statements'))
    if not f.filename.endswith(('.xlsx', '.xls')):
        flash('Please upload an Excel file (.xlsx).', 'danger')
        return redirect(url_for('financial_statements'))

    import openpyxl
    wb = openpyxl.load_workbook(f, data_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        flash('Empty file.', 'danger')
        return redirect(url_for('financial_statements'))

    header = rows[0]
    periods = [str(h).strip() for h in header[2:] if h]
    items = []
    for row in rows[1:]:
        cells = list(row)
        if not cells or not cells[0]:
            continue
        item_name = str(cells[0]).strip()
        stmt_type = str(cells[1]).strip() if len(cells) > 1 and cells[1] else ''
        item = {'item': item_name, 'stmt': stmt_type}
        for i, p in enumerate(periods):
            val = cells[i + 2] if i + 2 < len(cells) else None
            if val is not None:
                try:
                    val = float(val)
                except (ValueError, TypeError):
                    pass
            item[p] = val
        items.append(item)

    cf = CompanyFinancials.query.filter_by(ticker=ticker).first()
    if not cf:
        cf = CompanyFinancials(ticker=ticker)
        db.session.add(cf)
    cf.company_name = request.form.get('company_name', ticker)
    cf.set_data({'periods': periods, 'items': items})
    cf.source = f'Excel upload: {f.filename}'
    cf.updated_by = current_user.id
    db.session.commit()
    invalidate_live_cache()
    log_action(current_user.id, 'upload_fs', f'{ticker}: {len(items)} items, {len(periods)} periods')
    flash(f'Financial statements uploaded for {ticker}: {len(items)} items across {len(periods)} periods.', 'success')
    return redirect(url_for('financial_statements', ticker=ticker))


@app.route('/financial-statements/save', methods=['POST'])
@login_required
@role_required('super_admin', 'head_of_research', 'senior_analyst')
def save_financial_statements():
    ticker = request.form.get('ticker', '').strip().upper()
    data_raw = request.form.get('fs_data', '{}')
    if not ticker:
        flash('No ticker specified.', 'danger')
        return redirect(url_for('financial_statements'))
    try:
        data = json.loads(data_raw)
    except (json.JSONDecodeError, TypeError):
        flash('Invalid data format.', 'danger')
        return redirect(url_for('financial_statements', ticker=ticker))

    cf = CompanyFinancials.query.filter_by(ticker=ticker).first()
    if not cf:
        cf = CompanyFinancials(ticker=ticker)
        db.session.add(cf)
    cf.company_name = data.get('company_name', ticker)
    cf.set_data(data)
    cf.source = 'Manual edit'
    cf.updated_by = current_user.id
    db.session.commit()
    invalidate_live_cache()
    log_action(current_user.id, 'manual_fs_edit', f'{ticker}')
    flash(f'Financial statements saved for {ticker}.', 'success')
    return redirect(url_for('financial_statements', ticker=ticker))


@app.route('/fixed-income/upload', methods=['POST'])
@login_required
@role_required('super_admin', 'head_of_research', 'senior_analyst')
def upload_yield_curve():
    f = request.files.get('yc_file')
    if not f or not f.filename.endswith(('.xlsx', '.xls')):
        flash('Please upload an Excel file (.xlsx).', 'danger')
        return redirect(url_for('fixed_income'))
    import openpyxl
    from datetime import datetime as dt
    wb = openpyxl.load_workbook(f, data_only=True)
    YieldCurvePoint.query.delete()
    count = 0
    for ws in wb.worksheets:
        header_row = None
        col_map = {}
        for r in range(1, min(ws.max_row + 1, 200)):
            row_vals = [ws.cell(r, c).value for c in range(1, min(ws.max_column + 1, 20))]
            row_str = [str(v).strip().upper() if v else '' for v in row_vals]
            if any('YIELD' in s for s in row_str) and any('SECURITY' in s or 'TENOR' in s for s in row_str):
                header_row = r
                for ci, hdr in enumerate(row_str):
                    if 'SECURITY DESC' in hdr:
                        col_map['desc'] = ci
                    elif 'TENOR' in hdr and 'desc' not in col_map:
                        col_map['tenor'] = ci
                    elif 'YIELD' in hdr and 'OPEN' not in hdr:
                        col_map['yield'] = ci
                    elif 'CLOSING' in hdr and 'YIELD' in hdr:
                        col_map['yield'] = ci
                    elif 'CLOSING' in hdr and 'PRICE' in hdr:
                        col_map['price'] = ci
                    elif 'WEIGHTED' in hdr and 'PRICE' in hdr:
                        col_map['price'] = ci
                    elif 'DAYS' in hdr and 'MATURITY' in hdr:
                        col_map['days'] = ci
                    elif 'MATURITY' in hdr and 'DATE' in hdr:
                        col_map['matdate'] = ci
                    elif 'ISIN' in hdr:
                        col_map['isin'] = ci
                continue
            if header_row is None:
                continue
            desc_ci = col_map.get('desc', col_map.get('tenor'))
            if desc_ci is None:
                continue
            desc_val = row_vals[desc_ci]
            if not desc_val:
                continue
            tenor_label = str(desc_val).strip()
            if not tenor_label or tenor_label.upper() in ('', 'NO.', 'TOTAL', 'GRAND TOTAL'):
                continue
            if any(kw in tenor_label.upper() for kw in ['NEW BONDS', 'DDEP ', 'OLD ', 'CORPORATE', 'SELL', 'COMMERCIAL', 'GREEN']):
                continue
            yield_val = None
            if 'yield' in col_map:
                yv = row_vals[col_map['yield']]
                if yv is not None:
                    try:
                        yield_val = float(yv) / 100
                    except (ValueError, TypeError):
                        pass
            price_val = None
            if 'price' in col_map:
                pv = row_vals[col_map['price']]
                if pv is not None:
                    try:
                        price_val = float(pv)
                    except (ValueError, TypeError):
                        pass
            days_val = None
            if 'days' in col_map:
                dv = row_vals[col_map['days']]
                if dv is not None:
                    try:
                        days_val = int(float(dv))
                    except (ValueError, TypeError):
                        pass
            mat_val = None
            if 'matdate' in col_map:
                mv = row_vals[col_map['matdate']]
                if mv is not None:
                    if hasattr(mv, 'strftime'):
                        mat_val = mv.strftime('%Y-%m-%d')
                    else:
                        mat_val = str(mv).strip()
            coupon_val = None
            import re
            cpn_match = re.search(r'(\d+\.?\d*)\s*$', tenor_label.split('-')[0] if '-' in tenor_label else '')
            if not cpn_match:
                cpn_match = re.search(r'-(\d+\.?\d+)$', tenor_label)
            if cpn_match:
                try:
                    coupon_val = float(cpn_match.group(1)) / 100
                except (ValueError, TypeError):
                    pass
            if yield_val is not None or price_val is not None or days_val is not None:
                pt = YieldCurvePoint(
                    tenor=tenor_label, tenor_days=days_val,
                    yield_pct=yield_val, coupon_pct=coupon_val,
                    price=price_val, maturity_date=mat_val,
                    source=f'GFIM: {f.filename} / {ws.title}',
                    updated_by=current_user.id,
                )
                db.session.add(pt)
                count += 1
    db.session.commit()
    log_action(current_user.id, 'upload_yield_curve', f'{count} instruments from {f.filename}')
    flash(f'Yield curve updated: {count} instruments from GFIM report.', 'success')
    return redirect(url_for('fixed_income'))


@app.route('/fixed-income/save', methods=['POST'])
@login_required
@role_required('super_admin', 'head_of_research', 'senior_analyst')
def save_yield_curve():
    data_raw = request.form.get('yc_data', '[]')
    try:
        rows = json.loads(data_raw)
    except (json.JSONDecodeError, TypeError):
        flash('Invalid data.', 'danger')
        return redirect(url_for('fixed_income'))
    YieldCurvePoint.query.delete()
    count = 0
    for r in rows:
        if not r.get('tenor'):
            continue
        pt = YieldCurvePoint(
            tenor=r['tenor'],
            tenor_days=r.get('tenor_days'),
            yield_pct=r.get('yield_pct'),
            coupon_pct=r.get('coupon_pct'),
            price=r.get('price'),
            maturity_date=r.get('maturity_date'),
            source='Manual edit',
            updated_by=current_user.id,
        )
        db.session.add(pt)
        count += 1
    db.session.commit()
    log_action(current_user.id, 'save_yield_curve', f'{count} points manually edited')
    flash(f'Yield curve saved: {count} instruments.', 'success')
    return redirect(url_for('fixed_income'))


@app.route('/api/recalculate', methods=['POST'])
@login_required
@role_required('super_admin', 'head_of_research')
def api_recalculate():
    invalidate_live_cache()
    from data_loader import get_live_data
    get_live_data()
    log_action(current_user.id, 'recalculate', 'Triggered full recalculation')
    return jsonify({'status': 'ok', 'message': 'Recalculation complete'})


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


init_db()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
