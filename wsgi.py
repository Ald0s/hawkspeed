import eventlet
eventlet.monkey_patch()

from app import create_app
application = create_app()
