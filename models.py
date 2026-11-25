from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

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


class AuditLog(db.Model):
    __tablename__ = 'audit_log'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    action = db.Column(db.String(200), nullable=False)
    details = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='audit_logs')
