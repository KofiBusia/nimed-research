from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import json as _json

db = SQLAlchemy()

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    full_name = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(30), nullable=False, default='analyst')
    is_active_user = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    last_login = db.Column(db.DateTime, nullable=True)

    ROLES = {
        'super_admin': {'level': 100, 'label': 'Super Admin'},
        'head_of_research': {'level': 80, 'label': 'Head of Research'},
        'senior_analyst': {'level': 60, 'label': 'Senior Analyst'},
        'analyst': {'level': 40, 'label': 'Analyst'},
        'viewer': {'level': 20, 'label': 'Viewer'},
    }

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def role_level(self):
        return self.ROLES.get(self.role, {}).get('level', 0)

    @property
    def role_label(self):
        return self.ROLES.get(self.role, {}).get('label', self.role)

    def can_manage_users(self):
        return self.role in ('super_admin', 'head_of_research')

    def can_edit_data(self):
        return self.role in ('super_admin', 'head_of_research', 'senior_analyst')

    def can_edit_inputs(self):
        return self.role in ('super_admin', 'head_of_research')

    def can_view_all(self):
        return True

    def can_delete_users(self):
        return self.role == 'super_admin'

    def needs_approval(self):
        return self.role == 'senior_analyst'

    def can_approve(self):
        return self.role in ('super_admin', 'head_of_research')


class AuditLog(db.Model):
    __tablename__ = 'audit_log'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    action = db.Column(db.String(200), nullable=False)
    details = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='audit_logs')


class MarketInput(db.Model):
    __tablename__ = 'market_inputs'
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Float)
    text_value = db.Column(db.String(200))
    label = db.Column(db.String(200))
    input_type = db.Column(db.String(20), default='number')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = db.Column(db.Integer, db.ForeignKey('users.id'))

    INPUT_DEFS = [
        ('risk_free_rate', 'Risk-Free Rate', 'percent', 0.111),
        ('equity_risk_premium', 'Equity Risk Premium', 'percent', 0.07),
        ('long_term_growth', 'Long-Term Growth (g)', 'percent', 0.064),
        ('pre_tax_cost_of_debt', 'Pre-Tax Cost of Debt', 'percent', 0.18),
        ('marginal_tax_rate', 'Marginal Tax Rate', 'percent', 0.25),
        ('usd_ghs_rate', 'USD/GHS Rate', 'number', 11.68),
        ('undervalued_band', 'Undervalued Band', 'percent', 0.15),
        ('blume_weight', 'Blume Weight', 'number', 0.67),
        ('r_squared_threshold', 'R-Squared Threshold', 'number', 0.1),
        ('beta_type', 'Beta Type', 'select', None),
        ('return_frequency', 'Return Frequency', 'select', None),
        ('return_basis', 'Return Basis', 'select', None),
    ]


class SharePrice(db.Model):
    __tablename__ = 'share_prices'
    id = db.Column(db.Integer, primary_key=True)
    ticker = db.Column(db.String(20), nullable=False, unique=True)
    company_name = db.Column(db.String(200))
    price_ghs = db.Column(db.Float, nullable=False)
    shares_m = db.Column(db.Float)
    price_date = db.Column(db.String(20))
    source = db.Column(db.String(200))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = db.Column(db.Integer, db.ForeignKey('users.id'))


class CompanyFinancials(db.Model):
    __tablename__ = 'company_financials'
    id = db.Column(db.Integer, primary_key=True)
    ticker = db.Column(db.String(20), unique=True, nullable=False)
    company_name = db.Column(db.String(200))
    currency = db.Column(db.String(10), default='GHS')
    is_private = db.Column(db.Boolean, default=False)
    industry = db.Column(db.String(100))
    data_json = db.Column(db.Text)
    source = db.Column(db.String(200), default='manual')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = db.Column(db.Integer, db.ForeignKey('users.id'))

    def get_data(self):
        if self.data_json:
            return _json.loads(self.data_json)
        return {'periods': [], 'items': []}

    def set_data(self, data):
        self.data_json = _json.dumps(data)


class YieldCurvePoint(db.Model):
    __tablename__ = 'yield_curve_points'
    id = db.Column(db.Integer, primary_key=True)
    tenor = db.Column(db.String(100), nullable=False)
    tenor_days = db.Column(db.Integer)
    yield_pct = db.Column(db.Float)
    coupon_pct = db.Column(db.Float)
    price = db.Column(db.Float)
    maturity_date = db.Column(db.String(20))
    source = db.Column(db.String(200))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = db.Column(db.Integer, db.ForeignKey('users.id'))


class PendingAction(db.Model):
    __tablename__ = 'pending_actions'
    id = db.Column(db.Integer, primary_key=True)
    action_type = db.Column(db.String(50), nullable=False)
    description = db.Column(db.String(500), nullable=False)
    ticker = db.Column(db.String(20))
    data_json = db.Column(db.Text)
    filename = db.Column(db.String(200))
    status = db.Column(db.String(20), default='pending')
    submitted_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    reviewed_at = db.Column(db.DateTime)
    review_note = db.Column(db.String(500))
    submitter = db.relationship('User', foreign_keys=[submitted_by], backref='submitted_actions')
    reviewer = db.relationship('User', foreign_keys=[reviewed_by])

    def get_data(self):
        if self.data_json:
            return _json.loads(self.data_json)
        return {}

    def set_data(self, data):
        self.data_json = _json.dumps(data)


class CurrencyRate(db.Model):
    __tablename__ = 'currency_rates'
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(10), unique=True, nullable=False)
    name = db.Column(db.String(100))
    rate_to_usd = db.Column(db.Float, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = db.Column(db.Integer, db.ForeignKey('users.id'))

    DEFAULTS = {
        'USD': ('US Dollar', 1.0),
        'GHS': ('Ghanaian Cedi', 0.0625),
        'RWF': ('Rwandan Franc', 0.000714),
        'TZS': ('Tanzanian Shilling', 0.000370),
        'UGX': ('Ugandan Shilling', 0.000266),
        'XOF': ('West African CFA Franc', 0.001554),
        'NGN': ('Nigerian Naira', 0.000606),
        'KES': ('Kenyan Shilling', 0.00772),
        'ZAR': ('South African Rand', 0.0556),
        'GBP': ('British Pound', 1.27),
        'EUR': ('Euro', 1.09),
    }


class ComputedData(db.Model):
    __tablename__ = 'computed_data'
    id = db.Column(db.Integer, primary_key=True)
    data_key = db.Column(db.String(100), unique=True, nullable=False)
    data_json = db.Column(db.Text)
    computed_at = db.Column(db.DateTime, default=datetime.utcnow)
