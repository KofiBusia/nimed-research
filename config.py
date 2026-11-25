import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'nimed-research-secret-key-change-in-production')
    database_url = os.environ.get('DATABASE_URL', 'sqlite:///nimed.db')
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_DATABASE_URI = database_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = True
