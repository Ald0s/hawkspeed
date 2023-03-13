import os
import time
import json
import base64

from datetime import date, datetime, timedelta
from flask import url_for
from tests.conftest import BaseAPICase

from app import db, config, factory, models, login_manager, tracks


class TestLoginLogout(BaseAPICase):
    def _make_authorization(self, **kwargs):
        username = kwargs.get("email_address")
        password = kwargs.get("password")

        credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
        return f"Basic {credentials}"

    def test_authenticate_login_validation(self):
        """
        Create a user.
        Ensure attempting to log in without email address gets a validation-error for invalid-email-address
        Ensure attempting to log in with invalid email address gets a validation-error for invalid-email-address
        Ensure attempting to log in with empty password gets validation-error for password-too-short
        Ensure attempting to log in with non-existent user gets unauthorised request fail for 'incorrect-login'
        Ensure attempting to log into the aforementioned user with an incorrect password gets unauthorised request fail for 'incorrect-login'
        """
        # Create a new user.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden")
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
        """
        Create a new account, not setup, enabled or verified.
        Ensure that when we log in, we get an account-issue failure for reason disabled.
        Enable the user.
        Ensure that when we log in, we get an account-issue failure for reason account-not-verified
        Verify the user.
        Ensure that when we log in, we get an account-issue failure for reason setup-social
        Setup the user's social.
        Ensure that when we log in, we get an account-issue failure for reason configure-game
        """
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
        self.assertEqual(login_request.json["profile_setup"], False)
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
        """
        Ensure all errors are raised where appropriately when invalid or insufficient data is supplied to the local registration endpoint.
        Ensure we get email too short if no email provided.
        Ensure we get invalid email if invalid email provided.
        Ensure we get password not complex if the given password is not complex enough.
        Ensure we get passwords dont match if the given password and confirm passwords dont match.
        """
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
        aldos = factory.create_user("alden@gmail.com", "password", verified = True)
        emily = factory.create_user("emily@mail.com", "password", verified = True, username = "emily")
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
        """
        Ensure a new user can be registered if correct data is supplied.
        Ensure the user is unverified.
        Ensure attempting to create a new user with the same email at this stage results in a ValidationError on email for reason 'email-address-registered'
        Now, when the User attempts to setup their social, this should fail with an account-issue type error, specifically because account-not-verfied
        Then, perform a request wishing to verify the account, ensure the open new-account UserVerify is set verified.
        Now, when the User wishes to setup their social, it should be allowed.
        Ensure attempting to create a new user with the same info at this stage results in a ValidationError on email for reason 'email-address-registered-verified'
        """
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
                bio = None
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
        aldos = factory.create_user("alden@gmail.com", "password", verified = True)
        db.session.flush()
        # Log aldos in.
        with self.app.test_client(user = aldos) as client:
            setup_profile_response = client.post(url_for("api.setup_profile"),
                data = json.dumps(dict(username = "aldos", bio = "This is a bio.")),
                content_type = "application/json")
            # Should have succeeded.
            self.assertEqual(setup_profile_response.status_code, 200)
            # Get the account.
            account_d = setup_profile_response.json
            # Confirm the username and bio matches.
            self.assertEqual(account_d["username"], "aldos")
            self.assertEqual(aldos.bio, "This is a bio.")
            # Finally, confirm that profile is setup.
            self.assertEqual(account_d["profile_setup"], True)


class TestTrackAPI(BaseAPICase):
    def test_query_track_path(self):
        """Import a test GPX route.
        Create a new User.
        Perform a query for the track's path.
        Ensure request was successful, UIDs matched and the number of points is not 0."""
        # Create a new User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden", verified = True)
        # Test that we can load a track from GPX.
        track_from_gpx = tracks.create_track_from_gpx("example1.gpx",
            intersection_check = False)
        db.session.flush()
        # Now that we're here, start a test client and log aldos in.
        with self.app.test_client(user = aldos) as client:
            track_path_response = client.get(url_for("api.get_track_path", track_uid = track_from_gpx.uid))
            # Ensure this request was successful.
            self.assertEqual(track_path_response.status_code, 200)
            track_path_json = track_path_response.json
            # Ensure the UID matches.
            self.assertEqual(track_path_json["track_uid"], track_from_gpx.uid)
            # Ensure there are more than 0 points in the response.
            self.assertNotEqual(len(track_path_json["points"]), 0)
