import fabric_dw


def test_version_is_non_empty_string() -> None:
    assert isinstance(fabric_dw.__version__, str)
    assert fabric_dw.__version__
