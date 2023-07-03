import os
import time
import uuid

from shapely import geometry

from sqlalchemy import func, asc
from datetime import date, datetime, timedelta
from flask import url_for
from unittests.conftest import BaseMockLoginCase

from app import db, config, models, error, world, factory, socketio


class TestSocket(BaseMockLoginCase):
    def test_player_connect_failed_kicked_unsupported_position(self):
        """Create a new User.
        Attempt to join the world from an unsupported location.
        Ensure we receive back a join-world-refused error."""
        # Create a new User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden", vehicle = "1994 Toyota Supra")
        db.session.flush()
        messages_l = [
            dict(device_fid = uuid.uuid4().hex.lower(), latitude = -25.813579, longitude = 28.222248, rotation = 180.0, speed = 70.0, logged_at = time.time() * 1000),
            dict(device_fid = uuid.uuid4().hex.lower(), latitude = -37.782737, longitude = 145.013383, rotation = 180.0, speed = 70.0, logged_at = (time.time() * 1000)+5000),
            dict(latitude = -25.813579, longitude = 28.222248, rotation = 180.0, speed = 70.0, logged_at = (time.time() * 1000)+10000)
        ]
        # Authenticate the User.
        with self.app.test_client(user = aldos) as client:
            # Launch a socket to join the world.
            socket = socketio.test_client(self.app,
                flask_test_client = client, auth = messages_l[1])
            # Ensure the socket is connected.
            self.assertEqual(socket.is_connected(), True)
            # Send an update from an unsupported location.
            socket.emit("player_update", messages_l[2],
                callback = True)
            # Ensure this failed by checking the second index in the socket's queue.
            self.assertEqual(len(socket.queue), 2)
            kicked = socket.queue[1]
            # Ensure theres a single arg.
            self.assertEqual(len(kicked["args"]), 1)
            kicked_from_world = kicked["args"][0]
            # Ensure that single arg has name 'kicked-from-world', reason 'position-not-supported', and that reason is also present inside the error dict.
            self.assertEqual(kicked_from_world["name"], "kicked-from-world")
            self.assertEqual(kicked_from_world["reason"], "position-not-supported")
            self.assertEqual(kicked_from_world["error_dict"]["reason"], "position-not-supported")

    def test_player_connect_failed_connect_from_unsupported_position(self):
        """Create a new User.
        Attempt to join the world from an unsupported location.
        Ensure we receive back a join-world-refused error."""
        # Create a new User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden", vehicle = "1994 Toyota Supra")
        db.session.flush()
        messages_l = [
            dict(device_fid = uuid.uuid4().hex.lower(), latitude = -25.813579, longitude = 28.222248, rotation = 180.0, speed = 70.0, logged_at = time.time() * 1000),
            dict(device_fid = uuid.uuid4().hex.lower(), latitude = -37.782737, longitude = 145.013383, rotation = 180.0, speed = 70.0, logged_at = (time.time() * 1000)+5000),
            dict(latitude = -25.813579, longitude = 28.222248, rotation = 180.0, speed = 70.0, logged_at = (time.time() * 1000)+10000)
        ]
        # Authenticate the User.
        with self.app.test_client(user = aldos) as client:
            # Launch a socket to join the world.
            socket = socketio.test_client(self.app,
                flask_test_client = client, auth = messages_l[0])
            # Ensure the socket is NOT connected.
            self.assertEqual(socket.is_connected(), False)

    def test_player_connect_disconnect(self):
        """"""
        self.assertEqual(True, False)
        
    def test_player_connect_then_reconnect(self):
        """Ensure that a User can successfully overtake an existing Player connection if they reconnect while that connection is still active."""
        # Create a new User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden", vehicle = "1994 Toyota Supra")
        db.session.flush()
        # Authenticate the User.
        with self.app.test_client(user = aldos) as client:
            # Launch a socket to join the world.
            join_world_d = dict(
                device_fid = uuid.uuid4().hex.lower(), latitude = -37.782737, longitude = 145.013383, rotation = 180.0, speed = 70.0, logged_at = time.time() * 1000)
            socket = socketio.test_client(self.app,
                flask_test_client = client, auth = join_world_d)
            # Ensure the socket is connected.
            self.assertEqual(socket.is_connected(), True)
            # Get the current world sessions SID, and save it.
            current_sid = aldos.player.socket_id
            # Create another socket from this client, toward the world namespace.
            socket2 = socketio.test_client(self.app,
                flask_test_client = client, auth = join_world_d)
            # Ensure the second socket is connected and the first socket is no longer connected.
            self.assertEqual(socket2.is_connected(), True)
            self.assertEqual(socket.is_connected(), False)
            # Now, ensure the SID is not equal to the current SID.
            self.assertNotEqual(current_sid, aldos.player.socket_id)

    def test_player_connect_dirty_player_attribute(self):
        """Ensure that a User can successfully overtake an existing Player instance that still may be set on their Player, but for which there are no open connections."""
        # Create a new User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden", vehicle = "1994 Toyota Supra")
        db.session.flush()
        # Create a join world dictionary.
        join_world_d = world.RequestConnectAuthentication(
                device_fid = uuid.uuid4().hex.lower(), latitude = -37.782737, longitude = 145.013383, rotation = 180.0, speed = 70.0, logged_at = time.time() * 1000)
        # Create a dirty UserPlayer instance for this User and add it to session.
        _, dirty_user_player = self.make_user_player(aldos, join_world_d)
        db.session.add(dirty_user_player)
        db.session.flush()
        # Save dirty socket ID.
        dirty_socket_id = dirty_user_player.socket_id
        # Authenticate the User.
        with self.app.test_client(user = aldos) as client:
            # Launch a socket to join the world with a new join world dict, but same FID etc.
            join_world_d = dict(
                device_fid = uuid.uuid4().hex.lower(), latitude = -37.782737, longitude = 145.013383, rotation = 180.0, speed = 70.0, logged_at = (time.time() * 1000)+5000)
            socket = socketio.test_client(self.app,
                flask_test_client = client, auth = join_world_d)
            # Ensure the socket is connected.
            self.assertEqual(socket.is_connected(), True)
            # Get clean socket ID and ensure it is not equal to dirty.
            db.session.refresh(aldos)
            self.assertNotEqual(aldos.player.socket_id, dirty_socket_id)

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
                device_fid = uuid.uuid4().hex.lower(), latitude = track_start_pt.y, longitude = track_start_pt.x, rotation = 180.0, speed = 70.0, logged_at = time_now)
            # Launch a socket to join the world.
            socket = socketio.test_client(self.app,
                flask_test_client = client, auth = request_connect_auth_d)
            # Ensure the socket is connected.
            self.assertEqual(socket.is_connected(), True)
            # Ensure the User's playing, and they have a Player.
            self.assertEqual(aldos.has_player, True)
            self.assertEqual(aldos.is_playing, True)
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
