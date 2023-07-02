import os
import time
import json
import base64

from shapely import geometry

from sqlalchemy import func, asc
from datetime import date, datetime, timedelta
from flask import url_for
from unittests.conftest import BaseMockLoginCase

from app import db, config, models, error, factory, socketio


class TestSocket(BaseMockLoginCase):
    def test_start_cancel_race(self):
        """"""
        # Create a new User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden", vehicle = "1994 Toyota Supra")
        # Create a track.
        track = self.create_track_from_gpx(aldos, "yarra_boulevard.gpx")
        db.session.flush()
        vehicle = aldos.vehicles.first()
        time_now = time.time() * 1000
        # Authenticate the User.
        with self.app.test_client(user = aldos) as client:
            # Create a request to join the world, at the location of the track's very first point.
            track_start_pt = track.geodetic_point
            request_connect_auth_d = dict(                
                latitude = track_start_pt.y, longitude = track_start_pt.x, rotation = 180.0, speed = 70.0, logged_at = time_now)
            # Launch a socket to join the world.
            socket = socketio.test_client(self.app,
                flask_test_client = client, auth = request_connect_auth_d)
            # Ensure the socket is connected.
            self.assertEqual(socket.is_connected(), True)
            # Ensure the User's socket ID is not None.
            self.assertIsNotNone(aldos.socket_id)
            # Now, send a request to start a race.
            # Start with the countdown position.
            countdown_d = dict(                
                latitude = track_start_pt.y, longitude = track_start_pt.x, rotation = 180.0, speed = 70.0, logged_at = time_now+1000)
            # Make the started position.
            started_d = dict(                
                latitude = track_start_pt.y, longitude = track_start_pt.x, rotation = 180.0, speed = 70.0, logged_at = time_now+5000)
            start_race_d = dict(
                track_uid = track.uid, vehicle_uid = vehicle.uid, countdown_position = countdown_d, started_position = started_d)
            # Emit this event to the server.
            start_race_result = socket.emit("start_race", start_race_d,
                callback = True)
            # Ensure the resulting arguments confirm that the race has begun.
            self.assertEqual(start_race_result["is_started"], True)
            # Ensure aldos has an ongoing race.
            self.assertEqual(aldos.has_ongoing_race, True)
            # Cancel the race.
            cancel_race_result = socket.emit("cancel_race", {},
                callback = True)
