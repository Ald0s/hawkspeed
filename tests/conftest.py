import os
import random
import hashlib
import pandas
import gpxpy
import mimetypes
import simplejson as json
from datetime import datetime, date

from flask import g
from flask.testing import FlaskClient
from flask_login import FlaskLoginClient
from flask_testing import TestCase
from werkzeug.datastructures import FileStorage

from app import create_app, db, models, config, factory, error, compat, world, races


class BaseCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        """Delete all test userdata items when tests are complete within a class."""
        userdata_directory = os.path.join(os.getcwd(), config.USERDATA_PATH)
        userdata_files = os.listdir(userdata_directory)
        for file in userdata_files:
            file_to_delete = os.path.join(userdata_directory, file)
            # Delete the file.
            os.remove(file_to_delete)

    def setUp(self):
        db.create_all()
        self.used_test_names = []
        self.mocked_file_uploads = []
        try:
            models.ServerConfiguration.get()
        except error.NoServerConfigurationError as nse:
            models.ServerConfiguration.new()
        db.session.flush()

    def tearDown(self):
        # Clear test names.
        self.used_test_names.clear()
        if "date_today" in g:
            g.date_today = None
        if "timestamp_now" in g:
            g.timestamp_now = None
        if "datetime_now" in g:
            g.datetime_now = None
        db.session.remove()
        db.drop_all()
        # Destroy all mocked files.
        for mocked in self.mocked_file_uploads:
            mocked.close()

    def create_app(self):
        test_app = create_app()
        with test_app.app_context():
            # If PostGIS enabled and dialect is SQLite, we require SpatiaLite.
            if db.engine.dialect.name == "sqlite":
                compat.should_load_spatialite_sync(db.engine)
        return test_app

    def get_random_identity(self):
        return factory.get_random_identity()

    def get_random_user(self):
        return factory.get_random_user()

    def set_date_today(self, date):
        g.date_today = date

    def set_datetime_now(self, datetime):
        g.datetime_now = datetime
        g.timestamp_now = datetime.timestamp()

    def set_timestamp_now(self, timestamp):
        g.timestamp_now = timestamp
        g.datetime_now = datetime.fromtimestamp(timestamp)

    def ensure_media_item_deleted_by_uid(self, uid_media):
        # Find the Media item for the UID.
        media = models.TemporaryMedia.get_by_uid(uid_media)
        if media:
            self.assertEqual(os.path.exists(media.fully_qualified_path), False)

    def ensure_media_item_deleted(self, media):
        self.assertEqual(os.path.exists(media.fully_qualified_path), False)

    def create_mocked_file_upload(self, directory_relative_to_import, filename):
        """
        Creates and returns a FileStorage instance referring to the given file to upload.
        The content type will be automatically guessed. The created file storage will be added to a list, and on test teardown will be cleared.
        """
        absolute_file_path = os.path.join(config.IMPORTS_PATH, directory_relative_to_import, filename)
        if not os.path.isfile(absolute_file_path):
            raise Exception(f"{absolute_file_path} is not a valid file.")
        # Guess the mimetype & encoding.
        type, encoding = mimetypes.guess_type(filename)
        if not type:
            raise Exception(f"Failed to determine mimetype for filename; {filename}")
        # Create a new FileStorage.
        new_mocked_file = FileStorage(
            stream = open(absolute_file_path, "rb"),
            filename = filename,
            content_type = type,
            headers = {
                "Content-Encoding": encoding
            }
        )
        # Add mocked file to tracking list.
        self.mocked_file_uploads.append(new_mocked_file)
        # And return.
        return new_mocked_file

    def simulate_entire_race(self, user, track, gpx_absolute_path, **kwargs):
        race_simulator = PlayerRaceGPXSimulator(user, gpx_absolute_path)
        race = race_simulator.new_race(track)
        db.session.add(race)
        db.session.flush()

        for user_location_d in race_simulator.step(**kwargs):
            player_update_result = world.parse_player_update(user, user_location_d)
            db.session.flush()
            if user.has_ongoing_race:
                update_race_participation_result = races.update_race_participation_for(user, player_update_result)
                db.session.flush()
        self.assertEqual(race.is_finished, True)
        return race


class UserAppClient(FlaskLoginClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.override_user_agent = None

    def open(self, *args, **kwargs):
        headers = kwargs.setdefault("headers", {})
        headers.setdefault("User-Agent", "okhttp/4.9.1 (hawkspeed:app-android-1)" if not self.override_user_agent else self.override_user_agent)
        return super().open(*args, **kwargs)

    def set_user_agent(self, user_agent):
        self.override_user_agent = user_agent

    def clear_user_agent(self):
        self.override_user_agent = None

    def set_api_user_agent_ver(self, ver):
        self.override_user_agent = f"okhttp/4.9.1 (hawkspeed:app-android-{ver})"


class BaseMockLoginCase(BaseCase):
    def create_app(self):
        test_app = super().create_app()
        test_app.test_client_class = UserAppClient
        return test_app


class BaseAPICase(BaseMockLoginCase):
    def ensure_validation_failed(self, response, must_have_d):
        """
        Given a response, check that the status_code is 400.
        Then get its JSON object.
        First ensure the object is a validation error.
        Then confirm that the validation error messages contains all keys in must_have_d.
        Then confirm that each item (validation error message) within the list contents of each key in must_have_d is present within the equivalent key in validation messages.
        """
        # Confirm 400.
        self.assertEqual(response.status_code, 400)
        # Get JSON object, which should be validation error.
        validation_error = response.json
        # Confirm validation error.
        self.assertEqual(validation_error["name"], "validation-error")
        for field_name, must_have_messages_list in must_have_d.items():
            # Iterate each field name in must have, extracting the list of errors that must be present.
            # Then, ensure that the field name is in validation -> error -> messages
            self.assertIn(field_name, validation_error["error"]["messages"])
            # Finally, assert that each item in must_have_messages_list is also present in this fields messages within validation error.
            for must_have in must_have_messages_list:
                self.assertIn(must_have, validation_error["error"]["messages"][field_name])
        # Done.

    def ensure_account_action_required(self, response, error_code):
        """
        Given a response, check that the status_code is 303.
        Then get its JSON object.
        First ensure it is an account-action-required.
        Verify that the given error_code matches that in the error.
        """
        # Confirm 303.
        self.assertEqual(response.status_code, 303)
        # Get error obj.
        account_issue = response.json
        # Verify this is an account-action-required.
        self.assertEqual(account_issue["name"], "account-action-required")
        # Verify that the error codes match.
        self.assertEqual(account_issue["error"]["error-code"], error_code)

    def ensure_account_issue(self, response, error_code):
        """
        Given a response, check that the status_code is 401.
        Then get its JSON object.
        First ensure it is an account-issue.
        Verify that the given error_code matches that in the error.
        """
        # Confirm 401.
        self.assertEqual(response.status_code, 401)
        # Get error obj.
        account_issue = response.json
        # Verify this is an account-issue.
        self.assertEqual(account_issue["name"], "account-issue")
        # Verify that the error codes match.
        self.assertEqual(account_issue["error"]["error-code"], error_code)

    def ensure_device_issue(self, response, error_code):
        """
        Given a response, check that the status_code is 400.
        Then get its JSON object.
        First ensure it is an device-issue.
        Verify that the given error_code matches that in the error.
        """
        # Confirm 400.
        self.assertEqual(response.status_code, 400)
        # Get error obj.
        device_issue = response.json
        # Verify this is an device-issue.
        self.assertEqual(device_issue["name"], "device-issue")
        # Verify that the error codes match.
        self.assertEqual(device_issue["error"]["error-code"], error_code)

    def ensure_unauthorised_request(self, response, error_code):
        """
        Given a response, check that the status_code is 403.
        Then get its JSON object.
        First ensure it is an unauthorised-request.
        Verify that the given error_code matches that in the error.
        """
        # Confirm 403.
        self.assertEqual(response.status_code, 403)
        # Get error obj.
        unauthorised_request = response.json
        # Verify this is an unauthorised-request.
        self.assertEqual(unauthorised_request["name"], "unauthorised-request")
        # Verify that the error codes match.
        self.assertEqual(unauthorised_request["error"]["error-code"], error_code)

    def ensure_bad_argument(self, response, error_code):
        """
        Given a response, check that the status_code is 400.
        Then get its JSON object.
        First ensure it is an unauthorised-request.
        Verify that the given error_code matches that in the error.
        """
        # Confirm v.
        self.assertEqual(response.status_code, 400)
        # Get error obj.
        unauthorised_request = response.json
        # Verify this is an bad-request-argument.
        self.assertEqual(unauthorised_request["name"], "bad-request-argument")
        # Verify that the error codes match.
        self.assertEqual(unauthorised_request["error"]["error-code"], error_code)

    def find_object_with_attr(self, l, attr_name, attr_value):
        """
        Given a list of dictionaries, l, locate the entry where the value under attr_name is equal
        to the value given in attr_value and return it. If the attr name can't be found, None will
        be returned.
        """
        for x in l:
            if x[attr_name] == attr_value:
                return x
        return None


class BaseBrowserCase(BaseCase):
    def create_app(self):
        test_app = super().create_app()
        test_app.test_client_class = FlaskLoginClient
        return test_app


class PlayerRaceGPXSimulator():
    """A class that, given a User and a GPX, the programmer can step through each point in the race as if it were being driven in real time."""
    @property
    def user(self):
        return self._user

    def __init__(self, _user, _race_gpx_path, **kwargs):
        self._race_gpx_path = _race_gpx_path
        self._user = _user
        self._race_gpx = None
        # Now, we'll read the contents of this file. But first, ensure it exists.
        if not os.path.isfile(self._race_gpx_path):
            raise Exception(f"No such GPX file {self._race_gpx_path}!")
        # Open the file and read its contents, parse as GPX.
        with open(self._race_gpx_path, "r") as gpx_file:
            self._race_gpx = gpxpy.parse(gpx_file)
        # If multiple tracks, raise exception.
        if len(self._race_gpx.tracks) > 1:
            raise Exception("No more than ONE race is allowed in PlayerRaceGPXSimulator!")
        # Get the start time.
        self._started = (self._race_gpx.tracks[0].segments[0].points[0].time.timestamp()) * 1000

    def new_race(self, _track):
        # Set the User, the Track and the time at which the race started; the first point in the given GPX.
        race = models.TrackUserRace(user = self._user, track = _track, started = self._started)
        race.set_crs(config.WORLD_CONFIGURATION_CRS)
        return race

    def step(self, **kwargs):
        ms_adjustment = kwargs.get("ms_adjustment", 0)
        # Step through each segment, and each point within each segment and produce a UserLocation instance.
        # Yield that.
        for segment in self._race_gpx.tracks[0].segments:
            for point in segment.points:
                yield dict(
                    viewport_minx = 0,
                    viewport_miny = 0,
                    viewport_maxx = 0,
                    viewport_maxy = 0,
                    zoom = 0,
                    latitude = point.latitude,
                    longitude = point.longitude,
                    logged_at = (point.time.timestamp() * 1000) + ms_adjustment,
                    speed = 40,
                    rotation = 180.0
                )
