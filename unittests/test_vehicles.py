import os
import time
import uuid
import json
import base64

from datetime import date, datetime, timedelta
from flask import url_for
from sqlalchemy.exc import IntegrityError
from unittests.conftest import BaseCase

from app import db, config, factory, models, vehicles, error, world


class TestVehicles(BaseCase):
    def test_vehicles_basics(self):
        """Test the basic functionality for importing vehicle data and then searching for a vehicle via the function intended for the API.
        Import all vehicle data from the vehicles test JSON.
        Perform a search for all vehicles with no arguments, to be returned all makes. Expect 2.
        Find toyota. Perform a search for all types within Toyota. Expect 1.
        Find car. Find all models within that type for Toyota. Expect 4.
        Find supra. Find all years within that model and type for Toyota. Expect 18.
        Find 1994. Find all options for supra in 1994. Expect 4."""
        # Load all vehicle data.
        vehicles.load_vehicle_data_from("vehicles.json")
        db.session.flush()
        # Now, search for all vehicle makes by supplying no arguments.
        all_vehicle_makes = vehicles.search_vehicles().all()
        # Ensure result has two results.
        self.assertEqual(len(all_vehicle_makes), 2)
        # Filter all vehicle makes to just Toyota.
        toyota = next(filter(lambda mk: mk.name == "Toyota", all_vehicle_makes))
        # Now, get all types within this make, by supplying the make UID.
        toyota_types = vehicles.search_vehicles(
            make_uid = toyota.uid).all()
        # Ensure result has one result.
        self.assertEqual(len(toyota_types), 1)
        # Filter all vehicle types to car.
        toyota_car = next(filter(lambda t: t.type_id == "car", toyota_types))
        # Now, search for all models of this type from this make.
        toyota_car_models = vehicles.search_vehicles(
            make_uid = toyota.uid, type_id = toyota_car.type_id).all()
        # Ensure result has 4 results.
        self.assertEqual(len(toyota_car_models), 4)
        # Filter all vehicle models to Supra.
        toyota_car_supra = next(filter(lambda m: m.name == "Supra", toyota_car_models))
        # Now search for all available years for this model.
        available_years = vehicles.search_vehicles(
            make_uid = toyota.uid, type_id = toyota_car.type_id, model_uid = toyota_car_supra.uid).all()
        # Ensure result has 18 results.
        self.assertEqual(len(available_years), 18)
        # Filter all vehicle years to 1994.
        toyota_car_supra_1994 = next(filter(lambda y: y.year == 1994, available_years))
        # Now, get all options for supra in 1994.
        all_supra_options = vehicles.search_vehicles(
            make_uid = toyota.uid, type_id = toyota_car.type_id, model_uid = toyota_car_supra.uid, year = toyota_car_supra_1994.year).all()
        # Ensure result has 4 results.
        self.assertEqual(len(all_supra_options), 4)
        
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