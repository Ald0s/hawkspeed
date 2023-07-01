# Changelog

## Version 0.00.02
* Added versioning with changelog,
* Added better installation instructions for server,
* Removed account schema in favour of view model,
* Added basic Gunicorn config for reverse proxy w/ nginx & gunicorn, with worker class eventlet for optimum socket IO,
* Started socket's error management system. Moved most server-only errors to their most applicable modules instead of error.py,
* Improved track import process,
* Granted track path its own view model,
* Added CRS to track path being delivered,
* Improved track API by returning track view model and track path view model in a single object,
* Changed models.py to adhere to sqlalchemy 2.0 format,
* Added track ratings,
* Added track comments,

## Version 0.00.03
* Added rating set/clear API, viewmodel and module functionality,
* Added comments API and viewmodel functionality,
* Created track comment view model,
* Wrote test case for track rating,
* Fixed track leaderboard test case,
* Added track comments API and viewmodel functionality,
* Added top leaderboard entries to track viewmodel w/ test case,
* Added User API and module,
* Improved race cancellation and race participation updates.

## Version 0.00.04
* Added track type,
* Added vehicle used to leaderboard entries,
* Fixed pagination attempting to paginate with str instead of int,
* Added template private settings,
* Updated README.