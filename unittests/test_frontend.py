import os
import time
import json
import base64

from datetime import date, datetime, timedelta
from flask import url_for
from unittests.conftest import BaseBrowserCase

from app import db, config


class TestFrontend(BaseBrowserCase):
    def test_query_media(self):
        """"""
        self.assertEqual(True, False)