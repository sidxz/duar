"""BASE_URL/ADMIN_URL are concatenated as f"{url}/path" everywhere — a trailing
slash (common when deploying under a path prefix like /duar/) must not leak a
"//" into OAuth redirect URIs or the JWT issuer."""

from src.config import Settings


def test_trailing_slash_stripped():
    s = Settings(base_url="https://host/duar/", admin_url="https://host/duar-admin/")
    assert s.base_url == "https://host/duar"
    assert s.admin_url == "https://host/duar-admin"
    assert s.allowed_hosts_list == ["host"]
