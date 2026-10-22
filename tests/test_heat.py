"""Heat output arithmetic, without HA."""

from custom_components.ctc_bms.heat import heat_output_kw


def test_heat_output_from_flow_and_delta_t():
    # 13.2 l/min across 10 K: 13.2 / 60 * 4.18 * 10
    assert heat_output_kw(13.2, 46.6, 56.6, 43.4) == 9.196


def test_stopped_compressor_delivers_nothing():
    # the charge pump can still run; its delta-T is then noise, not heat
    assert heat_output_kw(13.2, 46.6, 46.8, 0) == 0.0


def test_defrost_reads_negative():
    assert heat_output_kw(12.0, 40.0, 35.0, 30.0) == -4.18


def test_unknown_input_is_unknown():
    assert heat_output_kw(None, 46.6, 56.6, 43.4) is None
    assert heat_output_kw(13.2, None, 56.6, 43.4) is None
    assert heat_output_kw(13.2, 46.6, None, 43.4) is None
    assert heat_output_kw(13.2, 46.6, 56.6, None) is None
