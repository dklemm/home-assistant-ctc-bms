"""The controller model table."""

from custom_components.ctc_bms.groups import SUBSYSTEMS
from custom_components.ctc_bms.models import (
    DEFAULT_MODEL,
    MODELS,
    model_for_product_type,
)


def test_product_type_14_is_an_ecologic_m():
    # Read off a live EcoLogic M: sProductType = 14.
    assert model_for_product_type(14) == "ecologic_m"


def test_unknown_product_type_falls_back_to_everything():
    assert model_for_product_type(999) == DEFAULT_MODEL
    assert model_for_product_type(None) == DEFAULT_MODEL
    assert set(MODELS[DEFAULT_MODEL].subsystems) == set(SUBSYSTEMS)


def test_every_model_names_real_subsystems():
    for key, model in MODELS.items():
        assert set(model.subsystems) <= set(SUBSYSTEMS), key
        assert model.name


def test_product_types_are_unique():
    seen: set[int] = set()
    for model in MODELS.values():
        for code in model.product_types:
            assert code not in seen
            seen.add(code)
