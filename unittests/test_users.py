import os
import time
import json
import base64

from datetime import date, datetime, timedelta
from flask import url_for
from unittests.conftest import BaseCase

from app import db, config, factory, models, users, error


class TestUsers(BaseCase):
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
        # Now, ensure aldos' current vehicle is None.
        self.assertIsNone(aldos.current_vehicle)
        # Set the vehicle as aldos' current vehicle.
        aldos.set_current_vehicle(vehicle)
        db.session.flush()
        # Ensure aldos still has 1 vehicle.
        self.assertEqual(aldos.num_vehicles, 1)
        # Ensure aldos now has a non-None current vehicle.
        self.assertIsNotNone(aldos.current_vehicle)