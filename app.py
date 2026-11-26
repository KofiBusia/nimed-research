import json
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_wtf.csrf import CSRFProtect, CSRFError
from functools import wraps
from datetime import datetime
from config import Config
from models import db, User, AuditLog, MarketInput, SharePrice, CompanyFinancials, YieldCurvePoint, PendingAction, CurrencyRate
from data_loader import (
    get_dashboard_data, get_beta_data, get_valuation_data,
    get_credit_risk_data, get_fixed_income_data, get_peer_data,
    get_checks_data, get_inputs_data, get_index_data,
    get_yin_full, get_financial_statements_data,
    get_shareholder_ratios_data, load_workbook_data,
    invalidate_live_cache, get_pc_companies
)

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)
csrf = CSRFProtect(app)


@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    flash('Your session expired. Please try again.', 'warning')
    return redirect(url_for('login'))


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


@app.context_processor
def inject_pending_count():
    if current_user.is_authenticated and current_user.can_approve():
        try:
            count = PendingAction.query.filter_by(status='pending').count()
        except Exception:
            count = 0
        return {'pending_approval_count': count}
    return {'pending_approval_count': 0}


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
                'source': p.source or '',
                'in_curve': 1,
            }
            for p in db_points
        ]
        data['yc_source'] = f'Database ({db_points[0].source or "manual"}) — updated {db_points[0].updated_at.strftime("%Y-%m-%d %H:%M")}'
    else:
        data['yc_source'] = 'T-bills from Bank of Ghana Tender auction of 20 Nov 2026 (official). Bond yields from GFIM trading.'
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


@app.route('/manual')
@login_required
def manual():
    return render_template('manual.html')


@app.route('/settings')
@login_required
@role_required('super_admin', 'head_of_research')
def settings():
    data = get_inputs_data()
    yin = get_yin_full()
    data['yin_market'] = yin.get('market_inputs', {})
    data['yin_target'] = yin.get('target', {})
    data['pc_companies'] = get_pc_companies()
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
    data['pc_companies'] = get_pc_companies()
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


@app.route('/private-capital/company/<ticker>')
@login_required
def pc_company_view(ticker):
    cf = CompanyFinancials.query.filter_by(ticker=ticker).first()
    if not cf or not cf.is_private:
        flash('Company not found or not in Private Capital.', 'warning')
        return redirect(url_for('yin_dashboard'))
    fs = cf.get_data()
    fs['company_name'] = cf.company_name
    fs['currency'] = cf.currency or 'GHS'
    fs['industry'] = cf.industry or ''
    pc_cos = get_pc_companies()
    return render_template('pc_company.html', selected=fs, selected_ticker=ticker, pc_companies=pc_cos)


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


@app.route('/recommendation/<ticker>')
@login_required
def recommendation(ticker):
    from compute import extract_key_metrics, _safe
    display_ccy = request.args.get('ccy', 'USD').upper()
    rates = {}
    for cr in CurrencyRate.query.all():
        rates[cr.code] = cr.rate_to_usd
    if display_ccy not in rates:
        display_ccy = 'USD'
    available_ccys = sorted(rates.keys())

    def convert_from_ghs(val, to_ccy='USD'):
        if val is None or not isinstance(val, (int, float)):
            return val
        ghs_to_usd = rates.get('GHS', 0.0625)
        target_to_usd = rates.get(to_ccy, 1.0)
        if target_to_usd == 0:
            return val
        return val * ghs_to_usd / target_to_usd

    cf = CompanyFinancials.query.filter_by(ticker=ticker).first()
    if cf:
        fs = cf.get_data()
        company_name = cf.company_name or ticker
        currency = cf.currency or 'GHS'
        is_private = getattr(cf, 'is_private', False) or False
        industry = getattr(cf, 'industry', None) or ''
    else:
        fs_data = get_financial_statements_data()
        if ticker not in fs_data:
            flash('Company not found.', 'warning')
            return redirect(url_for('dashboard'))
        fs = fs_data[ticker]
        company_name = fs.get('company_name', ticker)
        currency = fs.get('currency', 'GHS')
        is_private = False
        industry = fs.get('industry', '')

    items = fs.get('items', [])
    periods = fs.get('periods', [])

    if is_private:
        back_url = url_for('pc_company_view', ticker=ticker)
    else:
        back_url = url_for('financial_statements', ticker=ticker)

    fy_periods = [p for p in periods if p.startswith('FY')]

    label_map = {}
    for item in items:
        label = (item.get('item') or '').strip()
        label_map[label.lower()] = item

    # Find the latest FY that has actual data (not empty projections)
    latest_fy = ''
    for p in reversed(fy_periods):
        has_data = False
        for item in items:
            if item.get('stmt') in ('IS', 'BS', 'CF', ''):
                v = item.get(p)
                if v is not None and v != 0 and v != '':
                    has_data = True
                    break
        if has_data:
            latest_fy = p
            break
    if not latest_fy:
        latest_fy = fy_periods[-1] if fy_periods else (periods[-1] if periods else '')

    fy_idx = fy_periods.index(latest_fy) if latest_fy in fy_periods else -1
    prev_fy = fy_periods[fy_idx - 1] if fy_idx > 0 else None

    def find_val(fragments, period=None):
        period = period or latest_fy
        if not period:
            return None
        for frag in fragments:
            for lbl, item in label_map.items():
                if frag.lower() in lbl:
                    v = item.get(period)
                    if v is not None:
                        return _safe(v)
        return None

    revenue = find_val(['revenue', 'turnover', 'net interest income', 'gross earned premium'])
    cost_of_sales = find_val(['cost of sales', 'cost of goods'])
    operating_income = find_val(['operating income', 'operating profit', 'profit from operations'])
    net_income = find_val(['net income', 'net profit attributable', 'profit after tax', 'profit for the year'])
    total_assets = find_val(['total assets'])
    total_equity = find_val(['total equity', 'total shareholders'])
    total_liabilities = find_val(['total liabilities'])
    cash = find_val(['cash and cash equivalents', 'cash and bank'])
    total_debt = find_val(['total borrowings', 'borrowings', 'loans and borrowings'])
    ebitda = find_val(['ebitda', 'reported ebitda'])

    rev_prev = find_val(['revenue', 'turnover', 'net interest income', 'gross earned premium'], prev_fy) if prev_fy else None
    ni_prev = find_val(['net income', 'net profit attributable', 'profit after tax', 'profit for the year'], prev_fy) if prev_fy else None
    ta_prev = find_val(['total assets'], prev_fy) if prev_fy else None

    def growth(curr, prev):
        if curr is not None and prev is not None and prev != 0:
            return (curr - prev) / abs(prev)
        return None

    rev_growth = growth(revenue, rev_prev)
    ni_growth = growth(net_income, ni_prev)
    ta_growth = growth(total_assets, ta_prev)

    gross_margin = ((revenue - cost_of_sales) / revenue) if revenue and cost_of_sales and revenue != 0 else None
    operating_margin = (operating_income / revenue) if operating_income and revenue and revenue != 0 else None
    net_margin = (net_income / revenue) if net_income and revenue and revenue != 0 else None
    roe = (net_income / total_equity) if net_income and total_equity and total_equity != 0 else None
    roa = (net_income / total_assets) if net_income and total_assets and total_assets != 0 else None
    debt_equity = (total_liabilities / total_equity) if total_liabilities and total_equity and total_equity != 0 else None

    highlights = []
    if revenue is not None:
        highlights.append({'label': 'Revenue / Interest Income', 'value': revenue, 'fmt': 'money', 'growth': rev_growth})
    if net_income is not None:
        highlights.append({'label': 'Net Income', 'value': net_income, 'fmt': 'money', 'growth': ni_growth})
    if total_assets is not None:
        highlights.append({'label': 'Total Assets', 'value': total_assets, 'fmt': 'money', 'growth': ta_growth})
    if ebitda is not None:
        highlights.append({'label': 'EBITDA', 'value': ebitda, 'fmt': 'money'})
    if total_equity is not None:
        highlights.append({'label': 'Total Equity', 'value': total_equity, 'fmt': 'money'})
    if cash is not None:
        highlights.append({'label': 'Cash & Equivalents', 'value': cash, 'fmt': 'money'})

    summary_labels = [
        ('Revenue / Interest Income', ['revenue', 'turnover', 'net interest income', 'gross earned premium'], False, True),
        ('Cost of Sales / Interest Expense', ['cost of sales', 'interest expense', 'cost of goods'], True, False),
        ('Gross Profit', ['gross profit'], False, True),
        ('Operating Expenses', ['operating expenses', 'total operating expenses', 'admin expenses'], True, False),
        ('Operating Income', ['operating income', 'operating profit', 'profit from operations'], False, True),
        ('Net Income', ['net income', 'net profit attributable', 'profit after tax', 'profit for the year'], False, True),
        ('Total Assets', ['total assets'], False, True),
        ('Total Liabilities', ['total liabilities'], False, False),
        ('Total Equity', ['total equity', 'total shareholders'], False, True),
    ]

    # Use up to 3 FY periods ending at latest_fy (not empty projection years)
    if latest_fy in fy_periods:
        end_idx = fy_periods.index(latest_fy) + 1
        start_idx = max(0, end_idx - 3)
        summary_periods = fy_periods[start_idx:end_idx]
    else:
        summary_periods = fy_periods[-3:] if len(fy_periods) >= 3 else fy_periods
    summary_rows = []
    for label, frags, indent, bold in summary_labels:
        values = {}
        for p in summary_periods:
            v = find_val(frags, p)
            if v is not None:
                values[p] = v
        if values:
            summary_rows.append({'item': label, 'vals': values, 'indent': indent, 'bold': bold})

    ratios = []
    if net_margin is not None:
        ratios.append({'label': 'Net Profit Margin', 'display': '{:.1f}%'.format(net_margin * 100)})
    if gross_margin is not None:
        ratios.append({'label': 'Gross Margin', 'display': '{:.1f}%'.format(gross_margin * 100)})
    if operating_margin is not None:
        ratios.append({'label': 'Operating Margin', 'display': '{:.1f}%'.format(operating_margin * 100)})
    if roe is not None:
        ratios.append({'label': 'Return on Equity (ROE)', 'display': '{:.1f}%'.format(roe * 100)})
    if roa is not None:
        ratios.append({'label': 'Return on Assets (ROA)', 'display': '{:.1f}%'.format(roa * 100)})
    if debt_equity is not None:
        ratios.append({'label': 'Debt-to-Equity', 'display': '{:.2f}x'.format(debt_equity)})
    if revenue and total_assets and total_assets != 0:
        ratios.append({'label': 'Asset Turnover', 'display': '{:.2f}x'.format(revenue / total_assets)})
    if ebitda and total_debt and total_debt != 0:
        ratios.append({'label': 'Debt/EBITDA', 'display': '{:.1f}x'.format(total_debt / ebitda)})

    # ── Gaming-specific KPIs ──
    gaming_kpis = []
    is_gaming = 'gaming' in (company_name or '').lower() or 'gaming' in (industry or '').lower()
    if is_gaming:
        ggr = find_val(['gross gaming revenue', 'revenue'])
        ggr_prev = find_val(['gross gaming revenue', 'revenue'], prev_fy) if prev_fy else None
        cos = find_val(['cost of revenue', 'direct costs', 'cost of sales'])
        opex = find_val(['operating expenses', 'administration expenses'])
        pbt = find_val(['profit', 'loss) before tax'])
        pat = find_val(['profit', 'loss) after tax', 'net income'])
        if ggr is not None:
            gaming_kpis.append({'label': 'Gross Gaming Revenue (GGR)', 'value': ggr, 'fmt': 'money',
                                'growth': growth(ggr, ggr_prev)})
        if ggr and cos is not None:
            gm = (ggr - abs(cos)) / ggr if ggr != 0 else None
            if gm is not None:
                gaming_kpis.append({'label': 'GGR Margin', 'value': gm, 'fmt': 'pct'})
                gaming_kpis.append({'label': 'Direct Cost Ratio', 'value': abs(cos) / ggr, 'fmt': 'pct'})
        if ggr and opex is not None:
            gaming_kpis.append({'label': 'OpEx / GGR', 'value': abs(opex) / ggr if ggr != 0 else 0, 'fmt': 'pct'})
        if ggr and pat is not None:
            gaming_kpis.append({'label': 'Net Income Margin', 'value': pat / ggr if ggr != 0 else 0, 'fmt': 'pct'})
        if cash is not None and ggr:
            gaming_kpis.append({'label': 'Cash / GGR', 'value': cash / ggr if ggr != 0 else 0, 'fmt': 'pct'})
        if ebitda is not None and ggr:
            gaming_kpis.append({'label': 'EBITDA Margin', 'value': ebitda / ggr if ggr != 0 else 0, 'fmt': 'pct'})

    # ── Valuation bridge ──
    valuation_bridge = []
    blended_value = None
    fair_value_low = None
    fair_value_high = None
    valuation_unit = ''
    price_now = None

    if not is_private:
        from compute import compute_valuation_models, compute_cost_of_equity
        from data_loader import get_live_data
        live = get_live_data()
        workbook = load_workbook_data()
        betas = workbook.get('betas', [])
        sector_weights = workbook.get('sector_weights', [])
        all_peers = live.get('all_peers', {})
        beta_info = {}
        for b in betas:
            if b.get('ticker', '') == ticker:
                beta_info = b
                break
        if beta_info:
            beta_used = _safe(beta_info.get('beta_used', 1))
            sector = beta_info.get('category', 'Other')
            inputs_data = live.get('inputs', workbook.get('inputs', {}))
            live_fs = live.get('financial_statements', {}).get(ticker, {})
            val_items = live_fs.get('items', items)
            val_periods = live_fs.get('periods', periods)
            metrics_for_val = extract_key_metrics(val_items, val_periods)
            sp = SharePrice.query.filter_by(ticker=ticker).first()
            shares_m = (sp.shares_m if sp else None) or _safe(beta_info.get('shares_m', 0))
            if metrics_for_val:
                metrics_for_val['shares_m'] = shares_m
                peer_pe_by_sector = {}
                peer_pb_by_sector = {}
                for sn, peers in all_peers.items():
                    pes = [_safe(p.get('P/E')) for p in peers if p.get('P/E') and _safe(p.get('P/E')) > 0]
                    pbs = [_safe(p.get('P/B')) for p in peers if p.get('P/B') and _safe(p.get('P/B')) > 0]
                    if pes:
                        peer_pe_by_sector[sn] = sorted(pes)[len(pes) // 2]
                    if pbs:
                        peer_pb_by_sector[sn] = sorted(pbs)[len(pbs) // 2]
                peer_meds = {'pe': peer_pe_by_sector.get(sector), 'pb': peer_pb_by_sector.get(sector)}
                models, ke_val = compute_valuation_models(metrics_for_val, inputs_data, beta_used, sector, sector_weights, peer_meds)

                sw = {}
                for w in sector_weights:
                    if w.get('sector', '').lower() == sector.lower():
                        sw = w
                        break
                weight_map = {
                    'ddm2': sw.get('ddm2', 0.125),
                    'ddm3': sw.get('ddm3', 0.125),
                    'ri': sw.get('ri', 0.25),
                    'comps': sw.get('comps', 0.125),
                    'justified': sw.get('justified', 0.1),
                }
                model_labels = {
                    'ddm2': 'DDM (2-Stage)',
                    'ddm3': 'DDM (3-Stage)',
                    'ri': 'Residual Income',
                    'comps': 'Comparable Companies',
                    'justified': 'Justified Multiples',
                }
                vals_list = []
                for mk in ('ddm2', 'ddm3', 'ri', 'comps', 'justified'):
                    mv = models.get(mk)
                    mw = weight_map.get(mk, 0)
                    if mv is not None and mw > 0:
                        cv = convert_from_ghs(mv, display_ccy)
                        valuation_bridge.append({
                            'method': model_labels.get(mk, mk),
                            'value': cv,
                            'weight': mw,
                        })
                        vals_list.append(cv)
                raw_blended = models.get('weighted_value')
                blended_value = convert_from_ghs(raw_blended, display_ccy)
                if vals_list:
                    fair_value_low = min(vals_list)
                    fair_value_high = max(vals_list)
                valuation_unit = 'per share'
                if sp:
                    price_now = convert_from_ghs(sp.price_ghs, display_ccy)
                else:
                    price_now = None
    else:
        yin = get_yin_full()
        pev = yin.get('pe_valuation', [])
        dash = yin.get('dashboard', {})
        ev_dcf = None
        ev_comps = None
        ev_prec = None
        eq_dcf = None
        eq_comps = None
        eq_prec = None
        weights = [0.5, 0.25, 0.25]
        concluded_equity = None
        for item in pev:
            c1 = (item.get('c1') or '')
            c1_low = c1.lower()
            if 'enterprise value' in c1_low and 'dcf' in c1_low and 'sensitivity' not in c1_low and 'fy0' not in c1_low:
                ev_dcf = item.get('c3')
            elif 'enterprise value' in c1_low and 'trading' in c1_low:
                ev_comps = item.get('c3')
            elif 'enterprise value' in c1_low and 'precedent' in c1_low:
                ev_prec = item.get('c3')
            elif 'equity value' in c1_low and '100%' in c1_low and 'private' in c1_low:
                eq_dcf = item.get('c3')
                eq_comps = item.get('c4')
                eq_prec = item.get('c5')
            elif c1_low.startswith('weight') or 'effective weight' in c1_low:
                w3 = item.get('c3')
                w4 = item.get('c4')
                w5 = item.get('c5')
                if w3 is not None:
                    weights = [_safe(w3), _safe(w4 or 0), _safe(w5 or 0)]
            elif 'concluded equity value' in c1_low and '100%' in c1_low:
                concluded_equity = item.get('c3')
        if ev_dcf is not None:
            valuation_bridge.append({'method': 'DCF (Income Approach)',
                'ev': convert_from_ghs(ev_dcf, display_ccy), 'equity': convert_from_ghs(eq_dcf, display_ccy), 'weight': weights[0]})
        if ev_comps is not None:
            valuation_bridge.append({'method': 'Trading Comparables',
                'ev': convert_from_ghs(ev_comps, display_ccy), 'equity': convert_from_ghs(eq_comps, display_ccy), 'weight': weights[1]})
        if ev_prec is not None:
            valuation_bridge.append({'method': 'Precedent Transactions',
                'ev': convert_from_ghs(ev_prec, display_ccy), 'equity': convert_from_ghs(eq_prec, display_ccy), 'weight': weights[2]})
        if concluded_equity is not None:
            blended_value = convert_from_ghs(concluded_equity, display_ccy)
        vals_list = [v.get('equity') for v in valuation_bridge if v.get('equity') is not None]
        if vals_list:
            fair_value_low = min(vals_list)
            fair_value_high = max(vals_list)
        valuation_unit = "'000"
        price_now = None
        vps = dash.get('Value per share')
        if vps and isinstance(vps, dict):
            price_now = convert_from_ghs(vps.get('value'), display_ccy)

    # Determine recommendation signal
    score = 0
    reasons = []
    if rev_growth is not None:
        if rev_growth > 0.10:
            score += 2; reasons.append('Strong revenue growth ({:.0f}%)'.format(rev_growth * 100))
        elif rev_growth > 0:
            score += 1; reasons.append('Positive revenue growth ({:.0f}%)'.format(rev_growth * 100))
        else:
            score -= 1; reasons.append('Revenue declined ({:.0f}%)'.format(rev_growth * 100))
    if ni_growth is not None:
        if ni_growth > 0.10:
            score += 2
        elif ni_growth > 0:
            score += 1
        else:
            score -= 1
    if roe is not None:
        if roe > 0.15:
            score += 2; reasons.append('ROE of {:.1f}% exceeds 15% threshold'.format(roe * 100))
        elif roe > 0.08:
            score += 1
        elif roe > 0:
            score += 0
        else:
            score -= 1; reasons.append('Negative ROE indicates losses')
    if net_margin is not None:
        if net_margin > 0.15:
            score += 1
        elif net_margin < 0:
            score -= 2
    if debt_equity is not None:
        if debt_equity < 1.5:
            score += 1
        elif debt_equity > 3:
            score -= 1; reasons.append('High leverage (D/E {:.1f}x)'.format(debt_equity))

    if score >= 4:
        signal = 'BUY'
    elif score >= 1:
        signal = 'HOLD'
    else:
        signal = 'SELL'

    signal_rationale = '. '.join(reasons[:3]) + '.' if reasons else 'Assessment based on available financial data.'

    # Auto-generate strengths and risks
    strengths = []
    risks = []
    if rev_growth is not None and rev_growth > 0.05:
        strengths.append('Revenue growing at {:.0f}% year-over-year'.format(rev_growth * 100))
    if roe is not None and roe > 0.12:
        strengths.append('Strong return on equity ({:.1f}%)'.format(roe * 100))
    if net_margin is not None and net_margin > 0.10:
        strengths.append('Healthy net profit margin ({:.1f}%)'.format(net_margin * 100))
    if cash is not None and total_assets and total_assets != 0:
        cash_ratio = cash / total_assets
        if cash_ratio > 0.10:
            strengths.append('Strong cash position ({:.0f}% of assets)'.format(cash_ratio * 100))
    if debt_equity is not None and debt_equity < 1.0:
        strengths.append('Conservative leverage (D/E {:.2f}x)'.format(debt_equity))

    if rev_growth is not None and rev_growth < 0:
        risks.append('Revenue decline may signal market or operational challenges')
    if net_margin is not None and net_margin < 0.05:
        risks.append('Thin profit margins leave limited buffer')
    if debt_equity is not None and debt_equity > 2.0:
        risks.append('Elevated leverage increases financial risk')
    if ni_growth is not None and ni_growth < -0.10:
        risks.append('Significant decline in profitability ({:.0f}%)'.format(ni_growth * 100))
    if not strengths:
        strengths.append('Financial data available for detailed due diligence')
    if not risks:
        risks.append('Standard market and operational risks apply')

    return render_template('recommendation.html',
        ticker=ticker, company_name=company_name, currency=currency,
        is_private=is_private, industry=industry,
        latest_fy=latest_fy, today=datetime.utcnow().strftime('%d %B %Y'),
        signal=signal, signal_rationale=signal_rationale,
        highlights=highlights, summary_periods=summary_periods,
        summary_rows=summary_rows, ratios=ratios,
        strengths=strengths, risks=risks, back_url=back_url,
        valuation_bridge=valuation_bridge, blended_value=blended_value,
        fair_value_low=fair_value_low, fair_value_high=fair_value_high,
        valuation_unit=valuation_unit, price_now=price_now,
        gaming_kpis=gaming_kpis, is_gaming=is_gaming,
        display_ccy=display_ccy, available_ccys=available_ccys)


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
            if current_user.needs_approval():
                pa = PendingAction(
                    action_type='update_price', ticker=ticker,
                    description=f'Update price for {ticker}: GHS {price}',
                    submitted_by=current_user.id)
                pa.set_data({'price_ghs': float(price), 'shares_m': float(shares) if shares else None,
                             'company_name': company or ticker, 'price_date': pdate})
                db.session.add(pa)
                db.session.commit()
                log_action(current_user.id, 'submit_price_approval', f'{ticker}: GHS {price}')
                flash(f'Price update for {ticker} submitted for approval.', 'info')
            else:
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
    fs_tickers = set(base.get('financial_statements', {}).keys())
    all_peers = base.get('all_peers', {})
    base_tickers = {}
    for sector, peers in all_peers.items():
        for p in peers:
            t = p.get('Ticker', '')
            if not t or t not in fs_tickers:
                continue
            price = p.get('Last close (GHS)')
            shares = p.get('Shares (m)')
            if t not in base_tickers:
                base_tickers[t] = {
                    'company': p.get('Company', t),
                    'price': price if price is not None else 0,
                    'shares_m': shares if shares is not None else 0,
                }
    for sp in prices:
        if sp.ticker not in base_tickers:
            base_tickers[sp.ticker] = {
                'company': sp.company_name or sp.ticker,
                'price': sp.price_ghs or 0,
                'shares_m': sp.shares_m or 0,
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
    bulk_prices = []
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

        bulk_prices.append({'ticker': ticker, 'price_ghs': price, 'price_date': price_date_str})
        count += 1

    if not bulk_prices:
        flash('No valid prices found in file.', 'danger')
        return redirect(url_for('share_prices'))

    if current_user.needs_approval():
        pa = PendingAction(
            action_type='bulk_price', ticker='BULK',
            description=f'Bulk price upload: {count} prices from {f.filename}',
            filename=f.filename, submitted_by=current_user.id)
        pa.set_data({'prices': bulk_prices})
        db.session.add(pa)
        db.session.commit()
        log_action(current_user.id, 'submit_bulk_price_approval', f'{count} prices from {f.filename}')
        flash(f'Bulk upload ({count} prices) submitted for approval by Head of Research.', 'info')
        return redirect(url_for('share_prices'))

    for bp in bulk_prices:
        sp = SharePrice.query.filter_by(ticker=bp['ticker']).first()
        if not sp:
            sp = SharePrice(ticker=bp['ticker'])
            db.session.add(sp)
        sp.price_ghs = bp['price_ghs']
        sp.company_name = sp.company_name or bp['ticker']
        sp.price_date = bp['price_date']
        sp.source = f'GSE Daily: {f.filename}'
        sp.updated_by = current_user.id
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
    new_ticker = request.form.get('new_ticker', '').strip().upper()
    new_company = request.form.get('new_company_name', '').strip()
    if new_ticker:
        ticker = new_ticker
    if not f or not ticker:
        flash('Please select a ticker and upload a file.', 'danger')
        return redirect(url_for('financial_statements'))
    if not f.filename.endswith(('.xlsx', '.xls', '.csv')):
        flash('Please upload an Excel (.xlsx) or CSV (.csv) file.', 'danger')
        return redirect(url_for('financial_statements'))

    parsed_rows = []
    if f.filename.endswith('.csv'):
        import csv as csv_mod, io
        text = io.TextIOWrapper(f, encoding='utf-8-sig')
        reader = csv_mod.reader(text)
        for row in reader:
            parsed_rows.append(row)
    else:
        import openpyxl
        wb = openpyxl.load_workbook(f, data_only=True)
        ws = None
        for name in wb.sheetnames:
            if 'financial' in name.lower() or 'statement' in name.lower():
                ws = wb[name]
                break
        if ws is None:
            ws = wb.active
        for row in ws.iter_rows(values_only=True):
            parsed_rows.append(list(row))

    if not parsed_rows:
        flash('Empty file.', 'danger')
        return redirect(url_for('financial_statements'))

    header = parsed_rows[0]
    periods = [str(h).strip() for h in header[2:] if h]
    items = []
    for row in parsed_rows[1:]:
        cells = list(row)
        if not cells or not cells[0]:
            continue
        item_name = str(cells[0]).strip()
        if item_name.startswith('---') or not item_name:
            continue
        stmt_type = str(cells[1]).strip() if len(cells) > 1 and cells[1] else ''
        if stmt_type not in ('IS', 'BS', 'CF', 'META'):
            continue
        item = {'item': item_name, 'stmt': stmt_type}
        for i, p in enumerate(periods):
            val = cells[i + 2] if i + 2 < len(cells) else None
            if val is not None and val != '':
                try:
                    val = float(val)
                except (ValueError, TypeError):
                    pass
            else:
                val = None
            item[p] = val
        items.append(item)

    if not items:
        flash('No valid financial statement items found. Check that Column B has IS/BS/CF/META.', 'danger')
        return redirect(url_for('financial_statements'))

    company_name = new_company or request.form.get('company_name', '') or ticker

    detected_currency = 'GHS'
    for item in items:
        if item.get('item', '').lower() == 'currency' and item.get('stmt') == 'META':
            for p in periods:
                v = item.get(p)
                if v and isinstance(v, str) and len(v) <= 10:
                    detected_currency = v.strip().upper()
                    break
            break

    _ensure_currency(detected_currency)

    payload = {'periods': periods, 'items': items, 'company_name': company_name, 'currency': detected_currency}

    if current_user.needs_approval():
        pa = PendingAction(
            action_type='upload_fs', ticker=ticker,
            description=f'Upload financial statements for {ticker} ({company_name}): {len(items)} items, {len(periods)} periods',
            filename=f.filename, submitted_by=current_user.id)
        pa.set_data(payload)
        db.session.add(pa)
        db.session.commit()
        log_action(current_user.id, 'submit_fs_approval', f'{ticker}: {len(items)} items')
        flash(f'Financial statements for {ticker} submitted for approval by Head of Research.', 'info')
        return redirect(url_for('financial_statements', ticker=ticker))

    cf = CompanyFinancials.query.filter_by(ticker=ticker).first()
    if not cf:
        cf = CompanyFinancials(ticker=ticker)
        db.session.add(cf)
    cf.company_name = company_name
    cf.currency = detected_currency
    cf.set_data({'periods': periods, 'items': items})
    cf.source = f'Upload: {f.filename}'
    cf.updated_by = current_user.id
    db.session.commit()
    invalidate_live_cache()
    log_action(current_user.id, 'upload_fs', f'{ticker}: {len(items)} items, {len(periods)} periods ({detected_currency})')
    flash(f'Financial statements uploaded for {ticker} ({company_name}): {len(items)} items across {len(periods)} periods. Currency: {detected_currency}', 'success')
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

    if current_user.needs_approval():
        item_count = len(data.get('items', []))
        pa = PendingAction(
            action_type='edit_fs', ticker=ticker,
            description=f'Edit financial statements for {ticker}: {item_count} items',
            submitted_by=current_user.id)
        pa.set_data(data)
        db.session.add(pa)
        db.session.commit()
        log_action(current_user.id, 'submit_fs_edit_approval', f'{ticker}')
        flash(f'Edits to {ticker} submitted for approval by Head of Research.', 'info')
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


@app.route('/financial-statements/clear', methods=['POST'])
@login_required
@role_required('super_admin', 'head_of_research')
def clear_financial_statements():
    ticker = request.form.get('ticker', '').strip().upper()
    clear_all = request.form.get('clear_all') == '1'
    if clear_all:
        count = CompanyFinancials.query.count()
        CompanyFinancials.query.delete()
        db.session.commit()
        invalidate_live_cache()
        log_action(current_user.id, 'clear_all_fs', f'Cleared {count} companies')
        flash(f'All financial statements cleared ({count} companies removed).', 'warning')
        return redirect(url_for('financial_statements'))
    if not ticker:
        flash('No ticker specified.', 'danger')
        return redirect(url_for('financial_statements'))
    cf = CompanyFinancials.query.filter_by(ticker=ticker).first()
    if cf:
        db.session.delete(cf)
        db.session.commit()
        invalidate_live_cache()
        log_action(current_user.id, 'clear_fs', f'Cleared {ticker}')
        flash(f'Financial statements cleared for {ticker}.', 'warning')
    else:
        flash(f'No data found for {ticker}.', 'info')
    return redirect(url_for('financial_statements'))


@app.route('/financial-statements/create-company', methods=['POST'])
@login_required
@role_required('super_admin', 'head_of_research')
def create_company():
    ticker = request.form.get('ticker', '').strip().upper()
    company_name = request.form.get('company_name', '').strip()
    industry = request.form.get('industry', '').strip()
    if not ticker or not company_name:
        flash('Ticker and company name are required.', 'danger')
        return redirect(url_for('financial_statements'))
    existing = CompanyFinancials.query.filter_by(ticker=ticker).first()
    if existing:
        flash(f'Ticker {ticker} already exists ({existing.company_name}).', 'warning')
        return redirect(url_for('financial_statements', ticker=ticker))
    cf = CompanyFinancials(ticker=ticker)
    cf.company_name = company_name
    periods = ['FY2022', 'FY2023', 'FY2024']
    cf.set_data({'periods': periods, 'items': [], 'industry': industry})
    cf.source = 'Created by admin'
    cf.updated_by = current_user.id
    db.session.add(cf)
    db.session.commit()
    invalidate_live_cache()
    log_action(current_user.id, 'create_company', f'{ticker}: {company_name} ({industry})')
    flash(f'Company created: {ticker} — {company_name}. Upload financial statements or edit inline.', 'success')
    return redirect(url_for('financial_statements', ticker=ticker))


@app.route('/api/company/<ticker>/toggle-private', methods=['POST'])
@login_required
@role_required('super_admin', 'head_of_research')
def toggle_private(ticker):
    cf = CompanyFinancials.query.filter_by(ticker=ticker).first()
    if not cf:
        return jsonify({'error': 'Company not found'}), 404
    cf.is_private = not (cf.is_private or False)
    cf.updated_by = current_user.id
    db.session.commit()
    invalidate_live_cache()
    log_action(current_user.id, 'toggle_private', f'{ticker}: is_private={cf.is_private}')
    return jsonify({'status': 'ok', 'ticker': ticker, 'is_private': cf.is_private})


@app.route('/api/pc-companies')
@login_required
def api_pc_companies():
    pcs = CompanyFinancials.query.filter_by(is_private=True).all()
    result = {}
    for cf in pcs:
        d = cf.get_data()
        result[cf.ticker] = {
            'company_name': cf.company_name,
            'currency': cf.currency or 'GHS',
            'industry': cf.industry or '',
            'periods': d.get('periods', []),
            'item_count': len(d.get('items', [])),
        }
    return jsonify(result)


@app.route('/fixed-income/upload', methods=['POST'])
@login_required
@role_required('super_admin', 'head_of_research', 'senior_analyst')
def upload_yield_curve():
    import re as _re
    f = request.files.get('yc_file')
    if not f or not f.filename.endswith(('.xlsx', '.xls')):
        flash('Please upload an Excel file (.xlsx).', 'danger')
        return redirect(url_for('fixed_income'))
    import openpyxl
    wb = openpyxl.load_workbook(f, data_only=True)

    tbill_groups = {}
    ddep_gc = {}

    for ws in wb.worksheets:
        sheet_name = ws.title.upper()
        header_row = None
        col_map = {}
        for r in range(1, min(ws.max_row + 1, 200)):
            row_vals = [ws.cell(r, c).value for c in range(1, min(ws.max_column + 1, 20))]
            row_str = [str(v).strip().upper() if v else '' for v in row_vals]

            if any('YIELD' in s for s in row_str) and any('SECURITY' in s or 'TENOR' in s for s in row_str):
                header_row = r
                col_map = {}
                for ci, hdr in enumerate(row_str):
                    if 'SECURITY DESC' in hdr:
                        col_map['desc'] = ci
                    elif 'TENOR' in hdr and 'desc' not in col_map:
                        col_map['tenor'] = ci
                    if 'CLOSING' in hdr and 'YIELD' in hdr:
                        col_map['yield'] = ci
                    elif hdr.strip() == 'YIELD' or ('YIELD' in hdr and 'OPEN' not in hdr and 'yield' not in col_map):
                        col_map.setdefault('yield', ci)
                    if ('END OF DAY' in hdr and 'PRICE' in hdr) or ('CLOSING' in hdr and 'PRICE' in hdr):
                        col_map['price'] = ci
                    elif 'WEIGHTED' in hdr and 'PRICE' in hdr:
                        col_map.setdefault('price', ci)
                    if 'DAYS' in hdr and 'MATURITY' in hdr:
                        col_map['days'] = ci
                    if 'MATURITY' in hdr and 'DATE' in hdr:
                        col_map['matdate'] = ci
                continue
            if header_row is None:
                continue

            if 'TREASURY' in sheet_name:
                tenor_col2 = row_vals[2] if len(row_vals) > 2 else None
                if tenor_col2 and isinstance(tenor_col2, str):
                    up = tenor_col2.strip().upper()
                    if '91-DAY' in up:
                        tbill_groups.setdefault('_current', [None])
                        tbill_groups['_current'][0] = '91'
                    elif '182-DAY' in up:
                        tbill_groups['_current'][0] = '182'
                    elif '364-DAY' in up:
                        tbill_groups['_current'][0] = '364'
                cur_grp = tbill_groups.get('_current', [None])[0]
                if not cur_grp:
                    continue
                yield_ci = col_map.get('yield', 9)
                price_ci = col_map.get('price', 8)
                days_ci = col_map.get('days', 14)
                close_yield = row_vals[yield_ci] if yield_ci < len(row_vals) else None
                close_price = row_vals[price_ci] if price_ci < len(row_vals) else None
                days_val = row_vals[days_ci] if days_ci < len(row_vals) else None
                if close_yield is not None and days_val is not None:
                    try:
                        yld = float(close_yield)
                        prc = float(close_price) if close_price else None
                        days = int(float(days_val))
                    except (ValueError, TypeError):
                        continue
                    tbill_groups.setdefault(cur_grp, []).append({
                        'yield_pct': yld / 100,
                        'price': prc,
                        'days': days,
                    })
            elif 'DDEP' in sheet_name:
                tenor_ci = col_map.get('tenor', 1)
                tenor_val = row_vals[tenor_ci] if tenor_ci < len(row_vals) else None
                if not tenor_val:
                    continue
                tenor_str = str(tenor_val).strip().upper()
                if 'GC-' not in tenor_str:
                    continue
                gc_match = _re.search(r'GC-(\d+)', tenor_str)
                if not gc_match:
                    continue
                gc_num = gc_match.group(1)
                desc_ci = col_map.get('desc', 2)
                desc_val = str(row_vals[desc_ci]).strip() if desc_ci < len(row_vals) and row_vals[desc_ci] else ''
                cpn_match = _re.search(r'-(\d+\.\d+)$', desc_val)
                coupon = float(cpn_match.group(1)) / 100 if cpn_match else None
                close_yield = None
                if 'yield' in col_map:
                    yv = row_vals[col_map['yield']]
                    if yv is not None:
                        try:
                            close_yield = float(yv) / 100
                        except (ValueError, TypeError):
                            pass
                close_price = None
                if 'price' in col_map:
                    pv = row_vals[col_map['price']]
                    if pv is not None:
                        try:
                            close_price = float(pv)
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
                        mat_val = mv.strftime('%Y-%m-%d') if hasattr(mv, 'strftime') else str(mv).strip()

                cpn_label = f'{coupon*100:.2f}' if coupon else gc_num
                mat_short = mat_val[:7] if mat_val and len(mat_val) >= 7 else ''
                label = f'DDEP {cpn_label}% {mat_short}'.strip()
                if not label.endswith('%'):
                    label = f'DDEP GC-{gc_num}'

                ddep_gc[gc_num] = {
                    'tenor': label,
                    'tenor_days': days_val,
                    'yield_pct': close_yield,
                    'coupon_pct': coupon,
                    'price': close_price,
                    'maturity_date': mat_val or '',
                }

    tbill_groups.pop('_current', None)
    yc_points = []
    for key in sorted(tbill_groups.keys(), key=int):
        bills = tbill_groups[key]
        if not bills:
            continue
        target_days = int(key)
        best = min(bills, key=lambda b: abs(b['days'] - target_days))
        yc_points.append({
            'tenor': f'{key}-day T-Bill', 'tenor_days': best['days'],
            'yield_pct': best['yield_pct'], 'coupon_pct': 0,
            'price': best['price'], 'maturity_date': '',
        })
    for key in sorted(ddep_gc.keys(), key=lambda x: int(x)):
        pt_data = ddep_gc[key]
        yc_points.append({
            'tenor': pt_data['tenor'], 'tenor_days': pt_data['tenor_days'],
            'yield_pct': pt_data['yield_pct'], 'coupon_pct': pt_data['coupon_pct'],
            'price': pt_data['price'], 'maturity_date': pt_data.get('maturity_date', ''),
        })

    if not yc_points:
        flash('No valid yield curve data found in file.', 'danger')
        return redirect(url_for('fixed_income'))

    if current_user.needs_approval():
        pa = PendingAction(
            action_type='upload_yc', ticker='YC',
            description=f'Upload yield curve: {len(yc_points)} market points from {f.filename}',
            filename=f.filename, submitted_by=current_user.id)
        pa.set_data({'points': yc_points})
        db.session.add(pa)
        db.session.commit()
        log_action(current_user.id, 'submit_yc_approval', f'{len(yc_points)} points from {f.filename}')
        flash(f'Yield curve upload ({len(yc_points)} points) submitted for approval.', 'info')
        return redirect(url_for('fixed_income'))

    YieldCurvePoint.query.delete()
    for pt_data in yc_points:
        pt = YieldCurvePoint(
            tenor=pt_data['tenor'], tenor_days=pt_data['tenor_days'],
            yield_pct=pt_data['yield_pct'], coupon_pct=pt_data['coupon_pct'],
            price=pt_data['price'], maturity_date=pt_data['maturity_date'],
            source=f'GFIM: {f.filename}', updated_by=current_user.id,
        )
        db.session.add(pt)
    db.session.commit()
    log_action(current_user.id, 'upload_yield_curve', f'{len(yc_points)} market points from {f.filename}')
    flash(f'Yield curve updated: {len(yc_points)} market points from GFIM report.', 'success')
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
    valid_rows = [r for r in rows if r.get('tenor')]
    if current_user.needs_approval():
        pa = PendingAction(
            action_type='edit_yc', ticker='YC',
            description=f'Edit yield curve: {len(valid_rows)} instruments',
            submitted_by=current_user.id)
        pa.set_data({'points': valid_rows})
        db.session.add(pa)
        db.session.commit()
        log_action(current_user.id, 'submit_yc_edit_approval', f'{len(valid_rows)} points')
        flash(f'Yield curve edits ({len(valid_rows)} instruments) submitted for approval.', 'info')
        return redirect(url_for('fixed_income'))

    YieldCurvePoint.query.delete()
    for r in valid_rows:
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
    db.session.commit()
    log_action(current_user.id, 'save_yield_curve', f'{len(valid_rows)} points manually edited')
    flash(f'Yield curve saved: {len(valid_rows)} instruments.', 'success')
    return redirect(url_for('fixed_income'))


@app.route('/approvals')
@login_required
@role_required('super_admin', 'head_of_research')
def approvals():
    status_filter = request.args.get('status', 'pending')
    if status_filter == 'all':
        actions = PendingAction.query.order_by(PendingAction.submitted_at.desc()).limit(100).all()
    else:
        actions = PendingAction.query.filter_by(status=status_filter).order_by(PendingAction.submitted_at.desc()).all()
    return render_template('approvals.html', actions=actions, status_filter=status_filter)


@app.route('/approvals/<int:action_id>')
@login_required
@role_required('super_admin', 'head_of_research')
def approval_detail(action_id):
    pa = PendingAction.query.get_or_404(action_id)
    data = pa.get_data()
    preview = {}
    if pa.action_type in ('upload_fs', 'edit_fs'):
        preview['periods'] = data.get('periods', [])[:5]
        preview['items'] = data.get('items', [])[:20]
        preview['total_items'] = len(data.get('items', []))
    elif pa.action_type == 'update_price':
        preview = data
    elif pa.action_type == 'bulk_price':
        preview['prices'] = data.get('prices', [])[:20]
        preview['total_prices'] = len(data.get('prices', []))
    elif pa.action_type in ('upload_yc', 'edit_yc'):
        preview['points'] = data.get('points', [])[:15]
        preview['total_points'] = len(data.get('points', []))
    return render_template('approval_detail.html', action=pa, preview=preview)


@app.route('/approvals/<int:action_id>/approve', methods=['POST'])
@login_required
@role_required('super_admin', 'head_of_research')
def approve_action(action_id):
    pa = PendingAction.query.get_or_404(action_id)
    if pa.status != 'pending':
        flash('This action has already been processed.', 'warning')
        return redirect(url_for('approvals'))

    note = request.form.get('note', '').strip()
    data = pa.get_data()

    if pa.action_type in ('upload_fs', 'edit_fs'):
        cf = CompanyFinancials.query.filter_by(ticker=pa.ticker).first()
        if not cf:
            cf = CompanyFinancials(ticker=pa.ticker)
            db.session.add(cf)
        cf.company_name = data.get('company_name', pa.ticker)
        cf.currency = data.get('currency', 'GHS')
        cf.set_data({'periods': data.get('periods', []), 'items': data.get('items', [])})
        cf.source = f'Approved: {pa.description[:100]}'
        cf.updated_by = pa.submitted_by
        invalidate_live_cache()
    elif pa.action_type == 'update_price':
        sp = SharePrice.query.filter_by(ticker=pa.ticker).first()
        if not sp:
            sp = SharePrice(ticker=pa.ticker)
            db.session.add(sp)
        sp.price_ghs = data['price_ghs']
        if data.get('shares_m'):
            sp.shares_m = data['shares_m']
        sp.company_name = data.get('company_name', pa.ticker)
        sp.price_date = data.get('price_date', '')
        sp.source = f'Approved: manual update'
        sp.updated_by = pa.submitted_by
        invalidate_live_cache()
    elif pa.action_type == 'bulk_price':
        for bp in data.get('prices', []):
            sp = SharePrice.query.filter_by(ticker=bp['ticker']).first()
            if not sp:
                sp = SharePrice(ticker=bp['ticker'])
                db.session.add(sp)
            sp.price_ghs = bp['price_ghs']
            sp.company_name = sp.company_name or bp['ticker']
            sp.price_date = bp.get('price_date', '')
            sp.source = f'Approved: bulk upload'
            sp.updated_by = pa.submitted_by
        invalidate_live_cache()
    elif pa.action_type in ('upload_yc', 'edit_yc'):
        YieldCurvePoint.query.delete()
        for pt_data in data.get('points', []):
            pt = YieldCurvePoint(
                tenor=pt_data['tenor'], tenor_days=pt_data.get('tenor_days'),
                yield_pct=pt_data.get('yield_pct'), coupon_pct=pt_data.get('coupon_pct'),
                price=pt_data.get('price'), maturity_date=pt_data.get('maturity_date', ''),
                source=f'Approved: {pa.filename or "manual edit"}',
                updated_by=pa.submitted_by,
            )
            db.session.add(pt)
        invalidate_live_cache()

    pa.status = 'approved'
    pa.reviewed_by = current_user.id
    pa.reviewed_at = datetime.utcnow()
    pa.review_note = note
    db.session.commit()
    log_action(current_user.id, 'approve_action', f'Approved #{pa.id}: {pa.description}')
    flash(f'Approved: {pa.description}', 'success')
    return redirect(url_for('approvals'))


@app.route('/approvals/<int:action_id>/reject', methods=['POST'])
@login_required
@role_required('super_admin', 'head_of_research')
def reject_action(action_id):
    pa = PendingAction.query.get_or_404(action_id)
    if pa.status != 'pending':
        flash('This action has already been processed.', 'warning')
        return redirect(url_for('approvals'))
    note = request.form.get('note', '').strip()
    pa.status = 'rejected'
    pa.reviewed_by = current_user.id
    pa.reviewed_at = datetime.utcnow()
    pa.review_note = note or 'Rejected'
    db.session.commit()
    log_action(current_user.id, 'reject_action', f'Rejected #{pa.id}: {pa.description}')
    flash(f'Rejected: {pa.description}', 'warning')
    return redirect(url_for('approvals'))


@app.route('/api/recalculate', methods=['POST'])
@login_required
@role_required('super_admin', 'head_of_research')
def api_recalculate():
    invalidate_live_cache()
    from data_loader import get_live_data
    get_live_data()
    log_action(current_user.id, 'recalculate', 'Triggered full recalculation')
    return jsonify({'status': 'ok', 'message': 'Recalculation complete'})


def _ensure_currency(code):
    """Make sure a currency code exists in the rates table. If not, add it with rate 0 so the user can fill it in."""
    if not code or code == 'USD':
        return
    existing = CurrencyRate.query.filter_by(code=code).first()
    if not existing:
        defaults = CurrencyRate.DEFAULTS.get(code)
        if defaults:
            name, rate = defaults
        else:
            name, rate = code, 0.0
        db.session.add(CurrencyRate(code=code, name=name, rate_to_usd=rate))
        db.session.commit()


@app.route('/api/currencies')
@login_required
def api_currencies():
    rates = CurrencyRate.query.all()
    result = {}
    for r in rates:
        result[r.code] = {'name': r.name, 'rate_to_usd': r.rate_to_usd}
    company_currencies = {}
    try:
        for cf in CompanyFinancials.query.all():
            company_currencies[cf.ticker] = cf.currency or 'GHS'
    except Exception:
        pass
    return jsonify({'rates': result, 'company_currencies': company_currencies})


@app.route('/api/currencies/save', methods=['POST'])
@login_required
@role_required('super_admin', 'head_of_research')
def api_save_currencies():
    data = request.get_json(silent=True) or {}
    rates = data.get('rates', {})
    updated = 0
    for code, rate_val in rates.items():
        cr = CurrencyRate.query.filter_by(code=code).first()
        if cr:
            try:
                cr.rate_to_usd = float(rate_val)
                cr.updated_by = current_user.id
                updated += 1
            except (ValueError, TypeError):
                pass
    db.session.commit()
    invalidate_live_cache()
    log_action(current_user.id, 'update_fx_rates', f'Updated {updated} currency rates')
    return jsonify({'status': 'ok', 'updated': updated})


@app.route('/api/currencies/add', methods=['POST'])
@login_required
@role_required('super_admin', 'head_of_research')
def api_add_currency():
    data = request.get_json(silent=True) or {}
    code = data.get('code', '').strip().upper()
    name = data.get('name', '').strip()
    rate = data.get('rate_to_usd', 0)
    if not code or len(code) > 10:
        return jsonify({'status': 'error', 'message': 'Invalid currency code'}), 400
    existing = CurrencyRate.query.filter_by(code=code).first()
    if existing:
        return jsonify({'status': 'error', 'message': f'{code} already exists'}), 400
    try:
        rate = float(rate)
    except (ValueError, TypeError):
        rate = 0.0
    cr = CurrencyRate(code=code, name=name or code, rate_to_usd=rate, updated_by=current_user.id)
    db.session.add(cr)
    db.session.commit()
    log_action(current_user.id, 'add_currency', f'{code} ({name}) = {rate} USD')
    return jsonify({'status': 'ok', 'code': code})


def init_db():
    with app.app_context():
        db.create_all()
        try:
            db.session.execute(db.text(
                "ALTER TABLE company_financials ADD COLUMN IF NOT EXISTS currency VARCHAR(10) DEFAULT 'GHS'"
            ))
            db.session.execute(db.text(
                "ALTER TABLE company_financials ADD COLUMN IF NOT EXISTS is_private BOOLEAN DEFAULT FALSE"
            ))
            db.session.execute(db.text(
                "ALTER TABLE company_financials ADD COLUMN IF NOT EXISTS industry VARCHAR(100)"
            ))
            db.session.commit()
        except Exception:
            db.session.rollback()
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
        stale = YieldCurvePoint.query.filter(
            YieldCurvePoint.source.like('%GFIM%')
        ).count()
        if stale > 0:
            YieldCurvePoint.query.filter(
                YieldCurvePoint.source.like('%GFIM%')
            ).delete(synchronize_session=False)
            db.session.commit()
            print("Cleared %d stale GFIM yield curve points" % stale)
        if CurrencyRate.query.count() == 0:
            for code, (name, rate) in CurrencyRate.DEFAULTS.items():
                db.session.add(CurrencyRate(code=code, name=name, rate_to_usd=rate))
            db.session.commit()
            print("Seeded %d default currency rates" % len(CurrencyRate.DEFAULTS))
        currency_map = {'CLG-RW': 'RWF', 'CLG-TZ': 'TZS', 'CLG-UG': 'UGX'}
        for tick, cur in currency_map.items():
            cf = CompanyFinancials.query.filter_by(ticker=tick).first()
            if cf and (not cf.currency or cf.currency == 'GHS'):
                cf.currency = cur
        db.session.commit()


init_db()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
