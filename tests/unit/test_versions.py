# cspell:words Nubu

from custom_components.docker_apps._versions import (
    is_floating,
    newest_tag,
    parse_tag,
    short_digest,
)


def test__parse_tag_should_split_shape_and_numbers():
    assert parse_tag("v2.1.0") == ("vN.N.N", (2, 1, 0))
    assert parse_tag("12.2ubu2604-ls53") == ("N.NubuN-lsN", (12, 2, 2604, 53))
    assert parse_tag("latest") is None


def test__newest_tag_should_pick_highest_same_shape():
    tags = ["v2.0.0", "v2.1.0", "v2.2.0", "v2.10.1", "v3.0.0-rc1", "latest", "release"]
    assert newest_tag("v2.1.0", tags) == "v2.10.1"


def test__newest_tag_should_ignore_prereleases_and_other_variants():
    tags = ["v2.2.0-rc1", "v2.2.0-rknn", "v2.1.0-rknn", "v2.3.0"]
    assert newest_tag("v2.1.0-rknn", tags) == "v2.2.0-rknn"


def test__newest_tag_should_return_none_when_current_is_newest():
    assert newest_tag("v2.2.0", ["v2.0.0", "v2.2.0"]) is None


def test__newest_tag_should_honor_include_and_exclude():
    tags = ["1.2.3", "1.3.0", "2.0.0"]
    assert newest_tag("1.2.3", tags, include=r"^1\.") == "1.3.0"
    assert newest_tag("1.2.3", tags, exclude=r"^2\.") == "1.3.0"


def test__newest_tag_should_follow_linuxserver_tags():
    tags = ["version-12.2ubu2604", "version-12.3ubu2604", "12.3ubu2604-ls60"]
    assert newest_tag("version-12.2ubu2604", tags) == "version-12.3ubu2604"


def test__is_floating():
    assert is_floating("latest")
    assert is_floating(None)
    assert is_floating("16")
    assert not is_floating("16.4")
    assert not is_floating("v2.1.0")


def test__short_digest():
    assert short_digest("sha256:0123456789abcdef") == "0123456789ab"
    assert short_digest(None) is None
