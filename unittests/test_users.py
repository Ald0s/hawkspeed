import os
import time
import uuid
import json
import base64

from datetime import date, datetime, timedelta
from flask import url_for
from sqlalchemy.exc import IntegrityError
from unittests.conftest import BaseCase

from app import db, config, factory, models, users, error, world


class TestUsers(BaseCase):
    def test_user_player_duplicate(self):
        """Test integrity configuration for user player to ensure that adding a second user player, with different device ID & socket IDs but duplicate User ID
        will still fail."""
        # Create a new User, setup.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden", vehicle = "1994 Toyota Supra")
        db.session.flush()
        # Now, set this User up as if they have a Player.
        _, new_player = self.make_user_player(aldos)
        # Now, manually create a new UserPlayer, and set its key.
        new_player_dup = models.UserPlayer()
        new_player_dup.set_key(aldos, uuid.uuid4().hex.lower(), uuid.uuid4().hex.lower())
        # Now add the new player to the session and flush. This should cause integ error.
        with self.assertRaises(IntegrityError) as ie:
            db.session.add(new_player_dup)
            db.session.flush()

    def test_user_vehicles(self):
        """"""
        # Create a new User, setup.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden", vehicle = "1994 Toyota Supra")
        db.session.flush()
        # Ensure aldos has 1 vehicle.
        self.assertEqual(aldos.num_vehicles, 1)
        # Get that one Vehicle.
        vehicle = aldos.vehicles.first()
        # Now, set this User up as if they have a Player.
        _, new_player = self.make_user_player(aldos)
        db.session.refresh(aldos)
        # Now, ensure aldos' current vehicle is None.
        self.assertIsNone(aldos.current_vehicle)
        # Set the vehicle as aldos' current vehicle.
        aldos.set_current_vehicle(vehicle)
        db.session.flush()
        # Ensure aldos still has 1 vehicle.
        self.assertEqual(aldos.num_vehicles, 1)
        # Ensure aldos now has a non-None current vehicle.
        self.assertIsNotNone(aldos.current_vehicle)