"""Configuration tests for the host-controlled Agora APM switches."""

import pytest


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("true", True), ("TRUE", True), ("1", True), ("yes", True), ("on", True),
        ("false", False), ("FALSE", False), ("0", False), ("no", False), ("off", False),
    ],
)
def test_env_bool_accepts_documented_values(B_mod, monkeypatch, raw, expected):
    monkeypatch.setenv("EBO_TEST_BOOL", raw)
    assert B_mod._env_bool("EBO_TEST_BOOL") is expected


def test_env_bool_rejects_ambiguous_value(B_mod, monkeypatch):
    monkeypatch.setenv("EBO_TEST_BOOL", "maybe")
    with pytest.raises(ValueError, match="EBO_TEST_BOOL must be"):
        B_mod._env_bool("EBO_TEST_BOOL")


@pytest.mark.parametrize(
    "aec,noise_suppression,agc",
    [
        (False, False, False),
        (True, False, False),
        (False, True, False),
        (False, False, True),
        (True, True, True),
    ],
)
def test_apm_config_maps_independent_switches(B_mod, aec, noise_suppression, agc):
    cfg = B_mod._build_apm_config(aec, noise_suppression, agc)

    assert cfg.ai_aec_config.enabled is aec
    assert cfg.ai_ns_config.ns_enabled is noise_suppression
    assert cfg.ai_ns_config.ai_ns_enabled is noise_suppression
    assert cfg.agc_config.enabled is agc
    assert cfg.bghvs_c_config.enabled is False
    assert cfg.enable_dump is False


def test_bridge_derives_apm_master_switch_from_three_features(B_mod, monkeypatch):
    monkeypatch.setenv("EBO_AGORA_AEC_ENABLED", "false")
    monkeypatch.setenv("EBO_AGORA_NOISE_SUPPRESSION_ENABLED", "true")
    monkeypatch.setenv("EBO_AGORA_AGC_ENABLED", "false")

    bridge = B_mod.Bridge(
        {"rtm_user": "u", "sid": "s", "app_id": "a"},
        {"host": "h", "port": 1883},
    )

    assert bridge.agora_aec_enabled is False
    assert bridge.agora_noise_suppression_enabled is True
    assert bridge.agora_agc_enabled is False
    assert bridge.agora_apm_enabled is True


def test_audio_observer_registration_precedes_connect_in_source(B_mod):
    import inspect

    source = inspect.getsource(B_mod.Bridge.connect_agora)
    assert source.index("self._install_apm_filter_result_logger()") < source.index(
        "self._register_audio_diag()"
    )
    assert source.index("self._register_audio_diag()") < source.index("self.rtc.connect(")


def test_apm_filter_result_logger_preserves_return_code(B_mod, monkeypatch):
    monkeypatch.setenv("EBO_AGORA_AEC_ENABLED", "true")
    bridge = B_mod.Bridge(
        {"rtm_user": "u", "sid": "s", "app_id": "a"},
        {"host": "h", "port": 1883},
    )

    class Connection:
        @staticmethod
        def _set_apm_filter_properties(track, uid):
            assert track == "track"
            assert uid == "robot"
            return 0

    bridge.rtc = Connection()
    bridge._install_apm_filter_result_logger()

    assert bridge.rtc._set_apm_filter_properties("track", "robot") == 0
    assert bridge._apm_filter_rc == 0


def test_classic_aec_parameter_is_driven_by_switch(B_mod):
    import inspect

    source = inspect.getsource(B_mod.Bridge.connect_agora)
    assert "true\" if self.agora_aec_enabled else \"false" in source
    assert "'{\"che.audio.aec.enable\":false}'" not in source
