import os
import time
import json
import base64

from sqlalchemy import func, asc
from datetime import date, datetime, timedelta
from flask import url_for
from tests.conftest import BaseCase

from app import db, config, factory, models, login_manager, world, tracks


class TestWorld(BaseCase):
    def test_trim_player_location_history(self):
        # Create a new User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden")
        def add_user_locations():
            # Add 6 user locations to the User's history.
            for x in range(6):
                location = world._prepare_user_location(dict(latitude = -37.8490495286849, longitude = 145.00537088213827, logged_at = 1678508080+x, speed = 70.0, rotation = 180.0))
                # Add it.
                aldos.add_location(location)
            db.session.flush()
        # Add user locations.
        add_user_locations()
        # Ensure we have 6.
        self.assertEqual(aldos.location_history.count(), 6)
        # Call the trim function.
        world._trim_player_location_history(aldos)
        db.session.flush()
        # Ensure aldos now only has 5, and ensure the oldest logged at is ..81
        self.assertEqual(aldos.location_history.count(), 5)
        self.assertEqual(aldos.location_history.order_by(asc(models.UserLocation.logged_at)).first().logged_at, 1678508081)
        # Now, delete all user locations.
        for x in aldos.location_history.all():
            db.session.delete(x)
        db.session.flush()
        # Add user locations.
        add_user_locations()
        # Get the oldest user location.
        oldest_location = aldos.location_history.order_by(asc(models.UserLocation.logged_at)).first()
        """Ensure that if a TrackUserRace is attached to the user location, it will not be deleted."""
        self.assertEqual(True, False)
        
    def test_collect_world_objects(self):
        # Create a new User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden")
        # Create a new track from example1.
        tracks.create_track_from_gpx("example1.gpx")
        db.session.flush()
        # Create a viewport, containing example1.
        viewport_1_d = dict(
            viewport_minx = 145.00537088213827,
            viewport_miny = -37.8490495286849,
            viewport_maxx = 145.03657889101794,
            viewport_maxy = -37.83703648343093,
        )
        # Create a viewport, not containing example1.
        viewport_2_d = dict(
            viewport_minx = 145.05104656931678,
            viewport_miny = -37.84497529027691,
            viewport_maxx = 145.07307815713955,
            viewport_maxy = -37.8335477251275,
        )
        # Query all tracks in view of the first viewport, ensure the track is present.
        world_view_1 = world.collect_viewed_objects(aldos, viewport_1_d)
        # Ensure there is a single track in this view object.
        self.assertEqual(len(world_view_1.tracks), 1)
        # Query all tracks in view of the second viewport, ensure the track is not present.
        world_view_2 = world.collect_viewed_objects(aldos, viewport_2_d)
        # Ensure there are 0 tracks in this view object.
        self.assertEqual(len(world_view_2.tracks), 0)
