import json

import pytest

from abaqus_sim_agent.coding import model_auth


@pytest.fixture
def aws_files(monkeypatch, tmp_path):
    pytest.importorskip("botocore")
    for name in tuple(model_auth.os.environ):
        if name.startswith("AWS_"):
            monkeypatch.delenv(name)
    credentials = tmp_path / "credentials"
    config = tmp_path / "config"
    credentials.write_text("")
    config.write_text("")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(credentials))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))

    def unexpected(*args, **kwargs):
        pytest.fail("credential discovery must not use HTTP or credential_process")

    monkeypatch.setattr("botocore.httpsession.URLLib3Session.send", unexpected)
    monkeypatch.setattr("botocore.credentials.subprocess.Popen", unexpected)
    return credentials, config


def test_empty_aws_file_is_not_a_credential(aws_files):
    assert model_auth._aws_local_credentials() is False


def test_aws_sdk_honors_selected_profile(aws_files, monkeypatch):
    credentials, _ = aws_files
    credentials.write_text(
        "[valid]\naws_access_key_id=fixture\naws_secret_access_key=fixture\n"
    )
    monkeypatch.setenv("AWS_PROFILE", "missing")
    assert model_auth._aws_local_credentials() is False
    monkeypatch.setenv("AWS_PROFILE", "valid")
    assert model_auth._aws_local_credentials() is True


def test_aws_sdk_rejects_partial_credentials(aws_files, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "fixture")
    assert model_auth._aws_local_credentials() is False
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fixture")
    assert model_auth._aws_local_credentials() is True


def test_discovery_does_not_run_aws_process_or_metadata_chain(aws_files):
    _, config = aws_files
    config.write_text("[default]\ncredential_process=must-not-run\n")
    assert model_auth._aws_local_credentials() is False


@pytest.fixture
def google_adc(monkeypatch, tmp_path):
    pytest.importorskip("google.auth")
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(tmp_path))
    credentials = tmp_path / "application_default_credentials.json"
    credentials.write_text(
        json.dumps(
            {
                "type": "authorized_user",
                "client_id": "fixture.apps.googleusercontent.com",
                "client_secret": "fixture",
                "refresh_token": "fixture",
            }
        )
    )

    def unexpected(*args, **kwargs):
        pytest.fail("ADC discovery must not make a request")

    monkeypatch.setattr("google.auth.transport.requests.Request.__call__", unexpected)
    return credentials


def test_google_sdk_finds_standard_adc_without_explicit_file_variable(google_adc):
    before = google_adc.read_bytes()
    assert model_auth._google_local_credentials() is True
    assert google_adc.read_bytes() == before


def test_google_sdk_validates_credential_contents_and_explicit_precedence(
    google_adc, monkeypatch
):
    invalid = google_adc.parent / "invalid.json"
    invalid.write_text(
        json.dumps({"type": "authorized_user", "refresh_token": "fixture"})
    )
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(invalid))
    assert model_auth._google_local_credentials() is False
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    assert model_auth._google_local_credentials() is True


def test_google_missing_adc_is_not_ready(google_adc):
    google_adc.unlink()
    assert model_auth._google_local_credentials() is False
