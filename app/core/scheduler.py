"""Shared APScheduler instance.

Module-level so routers can add/remove jobs at runtime (the scraper
schedules do); api.py starts and stops it in the app lifespan.
"""

from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler()
