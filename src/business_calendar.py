"""The shared business date, independent of the operating system timezone."""
from datetime import datetime, timedelta, timezone

# Vietnam uses UTC+07 without daylight saving; no host tzdata installation required.
BUSINESS_TIMEZONE = timezone(timedelta(hours=7), 'Asia/Ho_Chi_Minh')


def business_today(now=None):
    instant = now if now is not None else datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError('An aware instant is required.')
    return instant.astimezone(BUSINESS_TIMEZONE).date()
