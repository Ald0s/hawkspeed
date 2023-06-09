import os
import time
import json
import base64

from datetime import date, datetime, timedelta
from flask import url_for
from unittests.conftest import BaseAPICase

from app import db, config, factory, models, login_manager, users


class TestLoginLogout(BaseAPICase):
    def _make_authorization(self, **kwargs):
        username = kwargs.get("email_address")
        password = kwargs.get("password")

        credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
        return f"Basic {credentials}"

    def test_authenticate_login_validation(self):
        """Create a user.
        Ensure attempting to log in without email address gets a validation-error for invalid-email-address
        Ensure attempting to log in with invalid email address gets a validation-error for invalid-email-address
        Ensure attempting to log in with empty password gets validation-error for password-too-short
        Ensure attempting to log in with non-existent user gets unauthorised request fail for 'incorrect-login'
        Ensure attempting to log into the aforementioned user with an incorrect password gets unauthorised request fail for 'incorrect-login'"""
        # Create a new user.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden", vehicle = "1994 Toyota Supra")
        db.session.flush()
        # Ensure attempting to log in without email address gets invalid-email-address.
        login_request = self.client.post(url_for("api.authenticate"),
            headers = {"Authorization": self._make_authorization( email_address = "", password = "password" )},
            content_type = "application/json"
        )
        # Ensure validation error that contains invalid-email
        self.ensure_validation_failed(login_request, { "email_address": ["invalid-email-address"] })
        # Ensure attempting to log in with invalid email address gets invalid-email-address.
        login_request = self.client.post(url_for("api.authenticate"),
            headers = {"Authorization": self._make_authorization( email_address = "aldenmail.com", password = "password" )},
            content_type = "application/json"
        )
        # Ensure validation error that contains invalid-email-address
        self.ensure_validation_failed(login_request, { "email_address": ["invalid-email-address"] })
        # Ensure attempting to log in with empty password gets password-too-short
        login_request = self.client.post(url_for("api.authenticate"),
            headers = {"Authorization": self._make_authorization( email_address = "alden@mail.com", password = "" )},
            content_type = "application/json"
        )
        # Ensure validation error that contains password-too-short
        self.ensure_validation_failed(login_request, { "password": ["password-too-short"] })
        # Ensure attempting to log in to non-existent user gets 'incorrect-login'
        login_request = self.client.post(url_for("api.authenticate"),
            headers = {"Authorization": self._make_authorization( email_address = "emily@mail.com", password = "password" )},
            content_type = "application/json"
        )
        # Ensure bad arg that contains incorrect-login
        self.ensure_unauthorised_request(login_request, "incorrect-login")
        # Ensure attempting to log in to the other valid user, with an incorrect password gets 'incorrect-login'
        login_request = self.client.post(url_for("api.authenticate"),
            headers = {"Authorization": self._make_authorization( email_address = "alden@mail.com", password = "this_is_a_password" )},
            content_type = "application/json"
        )
        # Ensure bad arg error that contains incorrect-login
        self.ensure_unauthorised_request(login_request, "incorrect-login")

    def test_login_authenticate(self):
        """Create a new account, not setup, enabled or verified.
        Ensure that when we log in, we get an account-issue failure for reason disabled.
        Enable the user.
        Ensure that when we log in, we get an account-issue failure for reason account-not-verified
        Verify the user.
        Ensure that when we log in, we get an account-issue failure for reason setup-social
        Setup the user's social.
        Ensure that when we log in, we get an account-issue failure for reason configure-game"""
        # Create a new account, not setup, enabled or verified.
        aldos = factory.create_user("alden@mail.com", "password",
            verified = False, enabled = False)
        db.session.flush()
        # Ensure when we login, we get an account-issue failure for reason disabled.
        login_request = self.client.post(url_for("api.authenticate"),
            headers = {"Authorization": self._make_authorization( email_address = "alden@mail.com", password = "password" )},
            content_type = "application/json"
        )
        self.ensure_account_issue(login_request, "disabled")
        # Enable the user.
        aldos.set_enabled(True)
        db.session.flush()
        # Ensure when we login, we get an account-issue failure for reason account-not-verified
        login_request = self.client.post(url_for("api.authenticate"),
            headers = {"Authorization": self._make_authorization( email_address = "alden@mail.com", password = "password" )},
            content_type = "application/json"
        )
        # Ensure request was successful.
        self.assertEqual(login_request.status_code, 200)
        self.assertEqual(login_request.json["is_profile_setup"], False)
        # Verify the user.
        aldos.set_verified(True)
        db.session.flush()
        # Log the user out.
        logout_request = self.client.post(url_for("api.logout"))


class TestRegistrationAndSetup(BaseAPICase):
    def get_registration_data(self, **kwargs):
        # This is our registration data, we will produce a new copy each time.
        return dict(
            email_address = kwargs.get("email_address", "alden@gmail.com"),
            password = kwargs.get("password", "ThisIsAP4$$"),
            confirm_password = kwargs.get("confirm_password", "ThisIsAP4$$")
        )

    def test_local_registration_validation(self):
        """Ensure all errors are raised where appropriately when invalid or insufficient data is supplied to the local registration endpoint.
        Ensure we get email too short if no email provided.
        Ensure we get invalid email if invalid email provided.
        Ensure we get password not complex if the given password is not complex enough.
        Ensure we get passwords dont match if the given password and confirm passwords dont match."""
        # Ensure we get email too short if we do not provide an email address.
        register_user_request = self.client.post(url_for("api.register_local_account"),
            data = json.dumps(self.get_registration_data(email_address = "")),
            content_type = "application/json"
        )
        # Ensure validation error that contains this too short error for email_address.
        self.ensure_validation_failed(register_user_request, { "email_address": ["email-too-short"] })
        # Ensure we get email invalid if we do not provide a valid email address.
        register_user_request = self.client.post(url_for("api.register_local_account"),
            data = json.dumps(self.get_registration_data(email_address = "aldengmail.com")),
            content_type = "application/json"
        )
        # Ensure validation error that contains invalid-email-address
        self.ensure_validation_failed(register_user_request, { "email_address": ["invalid-email-address"] })
        # Ensure we get password-not-complex if we provide an insufficient password.
        register_user_request = self.client.post(url_for("api.register_local_account"),
            data = json.dumps(self.get_registration_data(password = "THisIs")),
            content_type = "application/json"
        )
        # Ensure validation error that contains password-not-complex
        self.ensure_validation_failed(register_user_request, { "password": ["password-not-complex"] })
        # Ensure we get password-not-complex if we provide a confirm password that does not match.
        register_user_request = self.client.post(url_for("api.register_local_account"),
            data = json.dumps(self.get_registration_data(confirm_password = "ThisIsAP4$$$")),
            content_type = "application/json"
        )
        # Ensure validation error that contains passwords-dont-match
        self.ensure_validation_failed(register_user_request, { "confirm_password": ["passwords-dont-match"] })

    def test_check_username_taken(self):
        """Create a user with a username.
        Check whether another username is taken, should be False.
        Check whether the initial username is taken, should be True."""
        aldos = factory.create_user("alden@gmail.com", "password",
            verified = True)
        emily = factory.create_user("emily@mail.com", "password",
            verified = True, username = "emily", vehicle = "1994 Toyota Supra")
        db.session.flush()
        # Log aldos in, however.
        with self.app.test_client(user = aldos) as client:
            check_name_response = client.post(url_for("api.check_username_taken", username = "aldos"))
            # Should have succeeded.
            self.assertEqual(check_name_response.status_code, 200)
            # Get this as json.
            check_name_json = check_name_response.json
            # Ensure the username given back is 'aldos', and is_taken is False.
            self.assertEqual(check_name_json["username"], "aldos")
            self.assertEqual(check_name_json["is_taken"], False)
            # Now, try with an already taken username.
            check_name_response = client.post(url_for("api.check_username_taken", username = "emily"))
            # Should have succeeded.
            self.assertEqual(check_name_response.status_code, 200)
            # Get this as json.
            check_name_json = check_name_response.json
            # Ensure the username given back is 'emily', and is_taken is True.
            self.assertEqual(check_name_json["username"], "emily")
            self.assertEqual(check_name_json["is_taken"], True)

    def test_local_registration(self):
        """Ensure a new user can be registered if correct data is supplied.
        Ensure the user is unverified.
        Ensure attempting to create a new user with the same email at this stage results in a ValidationError on email for reason 'email-address-registered'
        Now, when the User attempts to setup their social, this should fail with an account-issue type error, specifically because account-not-verfied
        Then, perform a request wishing to verify the account, ensure the open new-account UserVerify is set verified.
        Now, when the User wishes to setup their social, it should be allowed.
        Ensure attempting to create a new user with the same info at this stage results in a ValidationError on email for reason 'email-address-registered-verified'"""
        valid_data = self.get_registration_data()
        # Register the account.
        register_user_request = self.client.post(url_for("api.register_local_account"),
            data = json.dumps(valid_data),
            content_type = "application/json"
        )
        self.assertEqual(register_user_request.status_code, 201)
        # Locate this User.
        new_user = models.User.search(email_address = valid_data["email_address"])
        # Ensure the user is unverfied.
        self.assertEqual(new_user.verified, False)
        # Ensure attempting to create another user, identically named, results in a validation error on email for 'email-address-registered'
        register_user_request_a = self.client.post(url_for("api.register_local_account"),
            data = json.dumps(self.get_registration_data()),
            content_type = "application/json"
        )
        self.ensure_validation_failed(register_user_request_a, { "email_address": ["email-address-registered"] })
        # Verify the user.
        # Test client, logged in.
        with self.app.test_client(user = new_user) as client:
            # Try setup profile. We should fail because account-not-verified.
            setup_profile_d = dict(
                username = "Alden",
                bio = None,
                vehicle_information = "1994 Toyota Supra"
            )
            setup_profile_request = client.post(url_for("api.setup_profile"),
                data = json.dumps(setup_profile_d),
                content_type = "application/json"
            )
            self.ensure_account_issue(setup_profile_request, "account-not-verified")
            # Now, get this user's first UserVerify.
            user_verify = new_user.verifies.first()
            # Ensure it isn't None.
            self.assertIsNotNone(user_verify)
            # Now, perform a request to verify this account.
            verify_account_request = client.get(url_for("frontend.verify_account", token = user_verify.token))
            # Ensure 200.
            self.assertEqual(verify_account_request.status_code, 200)
            # Ensure the User is now verified.
            self.assertEqual(new_user.verified, True)
        # Ensure attempting the same routes as above, with the exact same data, now yields the same validation errors, but with -verified appended - denoting the fact
        # that those accounts are locked in and those pieces of data will never be available.
        register_user_request_a = self.client.post(url_for("api.register_local_account"),
            data = json.dumps(self.get_registration_data(phone_number = "61451459885")),
            content_type = "application/json"
        )
        self.ensure_validation_failed(register_user_request_a, { "email_address": ["email-address-registered-verified"] })

    def test_setup_profile(self):
        """Create a User who is verified, but who is not setup.
        Submit a request to set the User's account up with the bio and username.
        Ensure this was successful, and the returned Account instance confirms what was sent, and profile is now setup."""
        aldos = factory.create_user("alden@gmail.com", "password",
            verified = True)
        db.session.flush()
        # Log aldos in.
        with self.app.test_client(user = aldos) as client:
            setup_profile_response = client.post(url_for("api.setup_profile"),
                data = json.dumps(dict(username = "aldos", bio = "This is a bio.", vehicle = dict(text = "1994 Toyota Supra"))),
                content_type = "application/json")
            # Should have succeeded.
            self.assertEqual(setup_profile_response.status_code, 200)
            # Get the account.
            account_d = setup_profile_response.json
            # Confirm the username and bio matches.
            self.assertEqual(account_d["username"], "aldos")
            self.assertEqual(aldos.bio, "This is a bio.")
            # Finally, confirm that profile is setup.
            self.assertEqual(account_d["is_profile_setup"], True)
            """TODO: vehicles here."""
            self.assertEqual(True, False)


class TestUserAPI(BaseAPICase):
    def test_get_user(self):
        """"""
        self.assertEqual(True, False)

    def test_get_our_vehicles(self):
        """Test the API for getting the User's current list of Vehicles.
        Create a new User with a single Vehicle.
        Authenticate as that User, then perform a request for all Vehicles.
        Ensure response was successful, and JSON response contains a single Vehicle. Ensure that Vehicle's UID matches the User's first Vehicle.
        Create and add another Vehicle to the User.
        Perform another request, this time, ensure the number of vehicles is 2."""
        # Create a User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden", vehicle = "1994 Toyota Supra")
        db.session.flush()
        # Get all vehicles from aldos.
        all_vehicles = aldos.all_vehicles
        # Now, authenticate as the User.
        with self.app.test_client(user = aldos) as client:
            # Perform a request for our vehicles. Ensure its response is 200, then get its JSON.
            our_vehicles_response = client.get(url_for("api.get_our_vehicles"))
            self.assertEqual(our_vehicles_response.status_code, 200)
            our_vehicles_json = our_vehicles_response.json
            # Ensure there's one item in response, and that one item's UID matches the first item in all vehicles list.
            self.assertEqual(len(our_vehicles_json["items"]), 1)
            self.assertEqual(our_vehicles_json["items"][0]["uid"], all_vehicles[0].uid)
            # Now, add another Vehicle to the User above.
            users.create_vehicle(users.RequestCreateVehicle(text = "1997 Nissan Patrol"),
                user = aldos)
            db.session.flush()
            # Perform a request for our vehicles. Ensure its response is 200, then get its JSON.
            our_vehicles_response = client.get(url_for("api.get_our_vehicles"))
            self.assertEqual(our_vehicles_response.status_code, 200)
            our_vehicles_json = our_vehicles_response.json
            # Ensure there's now two items in response.
            self.assertEqual(len(our_vehicles_json["items"]), 2)

        
class TestTrackAPI(BaseAPICase):
    def test_query_track_with_path(self):
        """Import a test GPX route.
        Create a new User.
        Perform a query for the track with its path.
        Ensure request was successful, UIDs matched and the number of points is not 0."""
        # Create a new User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden", vehicle = "1994 Toyota Supra", verified = True)
        # Test that we can load a track from GPX.
        track = self.create_track_from_gpx(aldos, "example1.gpx",
            intersection_check = False)
        # Now that we're here, start a test client and log aldos in.
        with self.app.test_client(user = aldos) as client:
            track_with_path_response = client.get(url_for("api.get_track_with_path", track_uid = track.uid))
            # Ensure this request was successful.
            self.assertEqual(track_with_path_response.status_code, 200)
            track_with_path_json = track_with_path_response.json
            # Ensure the UID matches.
            self.assertEqual(track_with_path_json["track"]["uid"], track.uid)
            # Ensure there are more than 0 points in the response.
            self.assertNotEqual(len(track_with_path_json["track_path"]["points"]), 0)

    def test_page_race_leaderboard(self):
        """Import a test GPX route.
        Create 2 Users.
        For User1 step through an entire race for the GPX route (successful attempt.)
        For User2 step through the same race but at 500 ms slower at each step.
        For User1 again, step through the same race but at 1000 ms slower at each step."""
        # Create two new Users.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden", vehicle = "1994 Toyota Supra")
        emily = factory.create_user("emily@mail.com", "password",
            username = "emily", vehicle = "1994 Toyota Supra")
        # Create a track.
        track = self.create_track_from_gpx(aldos, "yarra_boulevard.gpx")
        db.session.flush()
        # Now, for User1, step through the entire race yarra_boulevard_good_race_1.
        self.simulate_entire_race(aldos, track, os.path.join(os.getcwd(), config.IMPORTS_PATH, "races", "yarra_boulevard_good_race_1.gpx"))
        db.session.flush()
        # Refresh aldos.
        db.session.refresh(aldos)
        # Now for User2, step through the same race, but at 500 ms slower.
        self.simulate_entire_race(emily, track, os.path.join(os.getcwd(), config.IMPORTS_PATH, "races", "yarra_boulevard_good_race_1.gpx"),
            ms_adjustment = 500)
        db.session.flush()
        # Now for User1 again, step through the same race, but at 1000ms slower.
        self.simulate_entire_race(aldos, track, os.path.join(os.getcwd(), config.IMPORTS_PATH, "races", "yarra_boulevard_good_race_1.gpx"),
            ms_adjustment = 1000)
        db.session.flush()
        # Check there are 3 races logged in the database.
        self.assertEqual(db.session.query(models.TrackUserRace).count(), 3)
        # Now login as aldos, and query the leaderboard for the track above.
        with self.app.test_client(user = aldos) as client:
            leaderboard_response = client.get(url_for("api.page_track_leaderboard", track_uid = track.uid))
            # Ensure the response indicates success.
            self.assertEqual(leaderboard_response.status_code, 200)
            # Get the JSON response.
            leaderboard_json = leaderboard_response.json
            # Ensure there are 3 items.
            self.assertEqual(len(leaderboard_json["items"]), 3)
            # Ensure the very first item's player, is aldos. Ensure this race has finishing place 1.
            self.assertEqual(leaderboard_json["items"][0]["player"]["uid"], aldos.uid)
            self.assertEqual(leaderboard_json["items"][0]["finishing_place"], 1)
            # The second is emily. Ensure this race has finishing place 2.
            self.assertEqual(leaderboard_json["items"][1]["player"]["uid"], emily.uid)
            self.assertEqual(leaderboard_json["items"][1]["finishing_place"], 2)
            # The third is aldos. Ensure this race has finishing place 3.
            self.assertEqual(leaderboard_json["items"][2]["player"]["uid"], aldos.uid)
            self.assertEqual(leaderboard_json["items"][2]["finishing_place"], 3)
            # Now, query track detail for this track. Ensure response is 200 then get its json.
            track_response = client.get(url_for("api.get_track", track_uid = track.uid))
            self.assertEqual(track_response.status_code, 200)
            track_json = track_response.json
            # Ensure there are 3 items in the top leaderboard.
            self.assertEqual(len(track_json["top_leaderboard"]), 3)
            # Ensure the first is aldos, the second emily and the third aldos.
            self.assertEqual(track_json["top_leaderboard"][0]["player"]["uid"], aldos.uid)
            self.assertEqual(track_json["top_leaderboard"][0]["finishing_place"], 1)
            # The second is emily. Ensure this race has finishing place 2.
            self.assertEqual(track_json["top_leaderboard"][1]["player"]["uid"], emily.uid)
            self.assertEqual(track_json["top_leaderboard"][1]["finishing_place"], 2)
            # The third is aldos. Ensure this race has finishing place 3.
            self.assertEqual(track_json["top_leaderboard"][2]["player"]["uid"], aldos.uid)
            self.assertEqual(track_json["top_leaderboard"][2]["finishing_place"], 3)

    def test_track_rating(self):
        """Create a User and and import a test track.
        Authenticate as the User.
        Perform a request for the track by its UID. Ensure that, in the result, our rating is None, there are 0 likes and 0 dislikes."""
        # Create a User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden", vehicle = "1994 Toyota Supra")
        # Create a track.
        track = self.create_track_from_gpx(aldos, "yarra_boulevard.gpx")
        # Now, authenticate as the User.
        with self.app.test_client(user = aldos) as client:
            # Perform a request for the track. Ensure its response is 200, then get its JSON.
            track_response = client.get(url_for("api.get_track", track_uid = track.uid))
            self.assertEqual(track_response.status_code, 200)
            track_json = track_response.json
            # Ensure our rating is None, there are 0 likes and dislikes.
            self.assertEqual(track_json["your_rating"], None)
            self.assertEqual(track_json["ratings"]["num_positive_votes"], 0)
            self.assertEqual(track_json["ratings"]["num_negative_votes"], 0)
            # Now, perform a request to upvote the track. Ensure response is 200, then get its JSON. Ensure our rating is now True, and the track has 1
            # positive and 0 negative votes.
            track_response = client.post(url_for("api.rate_track", track_uid = track.uid),
                data = json.dumps(dict( rating = True )),
                content_type = "application/json")
            self.assertEqual(track_response.status_code, 200)
            track_json = track_response.json
            # Ensure our rating is True, there is 1 like and 0 dislikes.
            self.assertEqual(track_json["your_rating"], True)
            self.assertEqual(track_json["ratings"]["num_positive_votes"], 1)
            self.assertEqual(track_json["ratings"]["num_negative_votes"], 0)
            # Now, perform a request to downvote the track. Ensure response is 200, then get its JSON. Ensure our rating is now False, and the track has 0
            # positive and 1 negative votes.
            track_response = client.post(url_for("api.rate_track", track_uid = track.uid),
                data = json.dumps(dict( rating = False )),
                content_type = "application/json")
            self.assertEqual(track_response.status_code, 200)
            track_json = track_response.json
            # Ensure our rating is False, there is 0 likes and 1 dislike.
            self.assertEqual(track_json["your_rating"], False)
            self.assertEqual(track_json["ratings"]["num_positive_votes"], 0)
            self.assertEqual(track_json["ratings"]["num_negative_votes"], 1)
            # Finally, perform a request to clear the rating. Ensure response is 200, then get its JSON. Ensure our rating is again None and track has 0
            # positive and negative ratings.
            track_response = client.delete(url_for("api.rate_track", track_uid = track.uid))
            self.assertEqual(track_response.status_code, 200)
            track_json = track_response.json
            # Ensure our rating is None, there are 0 likes and dislikes.
            self.assertEqual(track_json["your_rating"], None)
            self.assertEqual(track_json["ratings"]["num_positive_votes"], 0)
            self.assertEqual(track_json["ratings"]["num_negative_votes"], 0)
    
    def test_track_comments(self):
        """"""
        self.assertEqual(True, False)

    def test_get_race(self):
        """"""
        self.assertEqual(True, False)

    def test_get_race_leaderboard(self):
        """"""
        self.assertEqual(True, False)
