import sys
import logging

from sqlalchemy import event, inspect
from sqlalchemy.types import TypeDecorator, BINARY
from sqlalchemy.dialects.postgresql import UUID as PostUUID

from . import config

LOG = logging.getLogger("hawkspeed.compat")
LOG.setLevel( logging.DEBUG )


if config.APP_ENV == "Production" or config.APP_ENV == "LiveDevelopment":
    from sqlalchemy.dialects.postgresql import insert as insert_
    LOG.debug(f"Engine dialect is not SQLite, assumed PostgreSQL. Using postgresql UUID.")
    UUID = PostUUID
elif config.APP_ENV == "Test" or config.APP_ENV == "Development":
    from sqlalchemy.dialects.sqlite import insert as insert_
    LOG.debug(f"Engine dialect is SQLite. Using custom UUID for the UUID mixin.")
    class UUID(TypeDecorator):
        impl = BINARY
        cache_ok = True

        def __init__(self, *args, **kwargs):
            kwargs.pop("as_uuid", None)
            super().__init__(*args, **kwargs)

        def load_dialect_impl(self, dialect):
            return dialect.type_descriptor(BINARY(16))

        def process_bind_param(self, value, dialect=None):
            if value and isinstance(value, uuid.UUID):
                return value.bytes
            elif value and isinstance(value, str):
                return uuid.UUID(value).bytes
            elif value:
                raise ValueError('value %s is not a valid uuid.UUId' % value)
            else:
                return None

        def process_result_value(self, value, dialect):
            if value is None:
                return value
            else:
                if not isinstance(value, uuid.UUID):
                    value = uuid.UUID(bytes = value)
                return value
else:
    raise Exception("unknown app env")
insert = insert_


def monkey_patch_sqlite():
    try:
        # First, attempt to import sqlite3, and from it, connect to a memory database. On the database connection, attempt to get enable_load_extension.
        import sqlite3
        con = sqlite3.connect(":memory:")
        con.enable_load_extension
    except AttributeError as ae:
        # If this does not exist, this will raise an AttributeError, we will then monkey patch the sqlite3 module with the imported pysqlite3 module.
        LOG.warning(f"Attempt to find enable_load_extension in sqlite3 failed, using pysqlite3 instead!")
        """
        Amazing! Why could no one else find this for me?
        https://stackoverflow.com/a/65198886
        """
        sys.modules["sqlite3"] = __import__("pysqlite3")


def should_load_spatialite_sync(engine):
    try:
        # Attempt to open a connection for this engine, so we can enable extension loading, load spatialite and setup metadata for it all.
        def load_spatialite(dbapi_conn, connection_record):
            # Enable load extension and load by both function and SQL. Just in case.
            dbapi_conn.enable_load_extension(True)
            dbapi_conn.load_extension("mod_spatialite")
            dbapi_conn.execute("SELECT load_extension(\"mod_spatialite\");")
            # We can now disable extension loading.
            dbapi_conn.enable_load_extension(False)
            # We'll now check for the metadata table, and init it if it does not exist.
            try:
                dbapi_conn.execute("SELECT COUNT(*) FROM spatial_ref_sys")
                # If this succeeded, there's no need to load.
            except Exception as e:
                # We require spatialite to be loaded.
                dbapi_conn.execute("SELECT InitSpatialMetaData(1);")
                LOG.debug(f"Successfully loaded SpatiaLite extension and ran init metadata!")
        event.listen(engine, "connect", load_spatialite)
    except AttributeError as ae:
        LOG.error(f"Failed to load spatialite extension, but it is required for your configuration! Original error as follows...")
        LOG.error(ae, exc_info = True)
        raise NotImplementedError()
    except Exception as e:
        raise e


async def should_load_spatialite_async(engine):
    try:
        def load_spatialite(dbapi_conn, connection_record):
            # Enable load extension and load by both function and SQL. Just in case.
            dbapi_conn.run_async(lambda con: con.enable_load_extension(True))
            dbapi_conn.run_async(lambda con: con.load_extension("mod_spatialite"))
            dbapi_conn.run_async(lambda con: con.execute("SELECT load_extension(\"mod_spatialite\");"))
            # We can now disable extension loading.
            dbapi_conn.run_async(lambda con: con.enable_load_extension(False))
            # We'll now check for the metadata table, and init it if it does not exist.
            try:
                dbapi_conn.run_async(lambda con: con.execute("SELECT COUNT(*) FROM spatial_ref_sys"))
                # If this succeeded, there's no need to load.
            except Exception as e:
                # We require spatialite metadata tables to be created.
                dbapi_conn.run_async(lambda con: con.execute("SELECT InitSpatialMetaData(1);"))
                LOG.debug(f"Successfully loaded SpatiaLite extension and ran init metadata asynchronously!")
        event.listen(engine.sync_engine, "connect", load_spatialite)
    except AttributeError as ae:
        LOG.error(f"[ASYNC SPATIALITE] Failed to load spatialite extension, but it is required for your configuration! Original error as follows...")
        LOG.error(ae, exc_info = True)
        raise NotImplementedError()
    except Exception as e:
        raise e
