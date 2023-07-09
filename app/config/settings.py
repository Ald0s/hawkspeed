import os
from instance import settings as private


class CeleryConfig():
    # A boolean; set to True to enable Google Maps API functionality. Note; setting this to False will skip the 'snap to roads' verification step that
    # is usually part of the new track process.
    USE_GOOGLE_MAPS_API = False
    # The base URL for the Snap To Roads API.
    SNAP_TO_ROADS_BASE_URL = "https://roads.googleapis.com/v1/snapToRoads?"
    # The number of points that can be sent in a single batch to Roads API snap to road. Maximum is 100 as per API docs.
    NUM_POINTS_PER_SNAP_BATCH = 100


class SocketConfig():
    SOCKETIO_MESSAGE_QUEUE = "redis://"
    SOCKETIO_PATH = "socket.io"
    SOCKETIO_ENGINEIO_LOGGER = False
    # True if, universally, updates should be sent via the SocketIO system where applicable.
    SHOULD_SEND_SOCKETIO_UPDATES = True


class TrackConfigurationMixin():
    # A boolean; set to True to require snap-to-roads be executed prior to verification of a new Track.
    REQUIRE_SNAP_TO_ROADS = False
    # A boolean; set to True to require admin approvals for new tracks, after they've been snapped to road.
    REQUIRE_ADMIN_APPROVALS = False


class RaceConfigurationMixin():
    # The maximum percentage of track path not driven to disqualify the race.
    MAX_PERCENT_PATH_MISSED_DISQUALIFY = 7
    # The number of meters by which to buffer a Player's progress through a track, to ensure inaccuracies do not affect their ability to tag checkpoints.
    NUM_METERS_BUFFER_PLAYER_PROGRESS = 10
    # The maximum distance (in meters) that the Player can deviate from the track's linestring before being disqualified for leaving the track.
    NUM_METERS_MAX_DEVIATION_DISQUALIFY = 50
    # The minimum distance (in meters) a new Track's start point must be away from all other Track points.
    NUM_METERS_MIN_FOR_NEW_TRACK_START = 20


class GeospatialConfigurationMixin():
    # The CRS in use by this server. This shall be taken from the world configuration file at launch.
    WORLD_CONFIGURATION_CRS = 3112
    # The number of player updates to retain per User.
    NUM_PLAYER_UPDATES_RETAIN = 100
    # Radius in meters around a Player from which world objects will be collected.
    NUM_METERS_PLAYER_PROXIMITY = 150


class BaseConfig(private.PrivateBaseConfig, RaceConfigurationMixin, TrackConfigurationMixin, GeospatialConfigurationMixin, SocketConfig, CeleryConfig):
    TESTING = False
    DEBUG = False
    SQLALCHEMY_SESSION_OPTS = {}
    SQLALCHEMY_ENGINE_OPTS = {}
    ALLOW_TEST_ROUTES = False
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Management can be False by default.
    POSTGIS_MANAGEMENT = False

    #HOST = "0.0.0.0"
    #PORT = 5000
    #SERVER_NAME = f"localhost.localdomain:{PORT}"
    #SERVER_NAME = f"127.0.0.1:{PORT}"
    SERVER_URL = "http://192.168.0.253:5000"

    SERVER_VERSION_TEXT = "0.01.00"
    SERVER_VERSION_CODE = 8

    # Streaming configuration.
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024 # 16 MB
    STREAM_CHUNK_SZ = 10 * 1024 * 1024 # 10 MB

    # Some import directories.
    ERRORS_PATH = "error"
    IMPORTS_PATH = "imports"
    USERDATA_PATH = "userdata"
    INSTANCE_PATH = "instance"
    # Some directories.
    GPX_ROUTES_DIR = os.path.join(IMPORTS_PATH, "gpx-routes")
    TESTDATA_GPX_ROUTES_DIR = os.path.join(IMPORTS_PATH, "gpx-routes", "test-routes")
    # What timezone should be used to report dates across the app irrespective of their relevant locations? Set to None to disable.
    GLOBAL_REPORTING_TIMEZONE = None
    # The amount of time, in seconds, until a new unverified account expires and will be deleted.
    TIME_UNTIL_NEW_ACCOUNT_EXPIRES = 24 * 60 * 60 # 24 hours.

    # Configuration for ProxyFix. As base is the most applicable to our various local/testing routes, our configuration will be 0 by default.
    FORWARDED_FOR = 0
    FORWARDED_PROTO = 0
    FORWARDED_HOST = 0
    FORWARDED_PORT = 0
    FORWARDED_PREFIX = 0

    PAGE_SIZE_LEADERBOARD = 20
    PAGE_SIZE_COMMENTS = 15

    def __init__(self):
        self.make_dirs()

    def make_dirs(self):
        def make_dir(path):
            try:
                os.makedirs(os.path.join(os.getcwd(), path))
            except OSError as o:
                pass
        make_dir(self.IMPORTS_PATH)
        make_dir(self.USERDATA_PATH)
        make_dir(self.ERRORS_PATH)


class TestConfig(private.PrivateTestConfig, BaseConfig):
    FLASK_ENV = "development"
    FLASK_DEBUG = True
    PRESERVE_CONTEXT_ON_EXCEPTION = False
    TESTING = True
    DEBUG = True
    # Required whenever using SQLite.
    POSTGIS_MANAGEMENT = True

    #CELERY_ALWAYS_EAGER = True
    #TEST_CELERY_TASKS = False # Should celery tasks be tested? This will be done eager as per CELERY_ALWAYS_EAGER but still...

    IMPORTS_PATH = os.path.join("imports", "testdata")
    USERDATA_PATH = os.path.join("userdata", "test")
    # Some directories.
    GPX_ROUTES_DIR = os.path.join(IMPORTS_PATH, "gpx-routes")
    TESTDATA_GPX_ROUTES_DIR = os.path.join(IMPORTS_PATH, "gpx-routes", "test-routes")

    # Good middle ground.
    GLOBAL_REPORTING_TIMEZONE = "Etc/GMT"

    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"

    NUM_METERS_PLAYER_PROXIMITY = 150
    
    NUM_PLAYER_UPDATES_RETAIN = 5
    PAGE_SIZE_LEADERBOARD = 5

    def __init__(self):
        super().make_dirs()


class DevelopmentConfig(private.PrivateDevelopmentConfig, BaseConfig):
    FLASK_ENV = "development"
    FLASK_DEBUG = True
    DEBUG = True
    #CELERY_ALWAYS_EAGER = True
    # Required whenever using SQLite.
    POSTGIS_MANAGEMENT = True

    SQLALCHEMY_DATABASE_URI = "sqlite:///hawkspeed.db"

    # Set imports directory to the test data subdirectory within imports. This must contain our test tarballs.
    IMPORTS_PATH = os.path.join("imports", "testdata")
    USERDATA_PATH = os.path.join("userdata", "test")
    # Some directories.
    GPX_ROUTES_DIR = os.path.join(IMPORTS_PATH, "gpx-routes")
    TESTDATA_GPX_ROUTES_DIR = os.path.join(IMPORTS_PATH, "gpx-routes", "test-routes")

    NUM_PLAYER_UPDATES_RETAIN = 5
    PAGE_SIZE_LEADERBOARD = 5

    def __init__(self):
        super().make_dirs()


class LiveDevelopmentConfig(private.PrivateLiveDevelopmentConfig, BaseConfig):
    FLASK_ENV = "development"
    FLASK_DEBUG = True
    DEBUG = True
    #CELERY_ALWAYS_EAGER = True
    # Management not required when not using SQLite.
    POSTGIS_MANAGEMENT = False

    def __init__(self):
        super().make_dirs()


class ProductionConfig(private.PrivateProductionConfig, BaseConfig):
    FLASK_ENV = "production"

    # Management not required when not using SQLite.
    POSTGIS_MANAGEMENT = False

    # A realistic proxy configuration now that should match your architecture.
    FORWARDED_FOR = 2
    FORWARDED_PROTO = 0
    FORWARDED_HOST = 0
    FORWARDED_PORT = 0
    FORWARDED_PREFIX = 0

    def __init__(self):
        super().make_dirs()
