"""Shared Jinja2 environment. Money is formatted only here, at the UI edge."""
import os

from fastapi.templating import Jinja2Templates

TEMPLATES = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


def dollars(cents):
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return "%s$%d.%02d" % (sign, cents // 100, cents % 100)


TEMPLATES.env.filters["dollars"] = dollars
