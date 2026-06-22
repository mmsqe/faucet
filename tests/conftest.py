"""Shared test configuration: load ``.env`` so tests inherit the package's
RPC/credential configuration."""

from dotenv import load_dotenv

load_dotenv()
