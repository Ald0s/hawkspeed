

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
    # Configurations for Google Maps API. Set this to None to disable using Google Maps API.
    """TODO"""
    GOOGLE_MAPS_API_KEY = None


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
