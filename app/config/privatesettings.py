

class PrivateBaseConfig():
    # Set the user information for the non-human 'admin' User. This entity will assume responsibility for unclaimed tracks etc.
    """TODO"""
    HAWKSPEED_USER = {
        "email_address": "",
        "username": "",
        "bio": "",
        "password": ""
    }
    # Configurations for Flask.
    """TODO"""
    SECRET_KEY = ""


class PrivateTestConfig():
    pass


class PrivateDevelopmentConfig():
    pass


class PrivateLiveDevelopmentConfig():
    # Set your postgresql database here.
    """TODO"""
    SQLALCHEMY_DATABASE_URI = "postgresql+psycopg2://USERNAME:PASSWORD@localhost:5432/DATABASE"


class PrivateProductionConfig():
    # Set your postgresql database here.
    """TODO"""
    SQLALCHEMY_DATABASE_URI = "postgresql+psycopg2://USERNAME:PASSWORD@localhost:5432/DATABASE"
