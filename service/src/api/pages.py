"""Jinja environment shared by every server-rendered HTML page (the hosted
/onboard flow and the auth error pages) so they all extend one base."""

from pathlib import Path

from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(
    env=Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
)
